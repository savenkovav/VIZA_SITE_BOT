import logging
import re

logger = logging.getLogger(__name__)
# Turnstile в closed Shadow DOM: <template shadowrootmode="closed">
# CSS >>> не поддерживается в Chrome 149 — используем только CDP.
_TURNSTILE_CDP_QUERIES = (
    "label.cb-lb input[type='checkbox']",
    "label.cb-lb",
    ".cb-c label",
    ".cb-lb-t",
)

_LOGIN_CAPTCHA_CLICK_QUERIES = (
    "#content",
    "#BbLB6 label.cb-lb",
    "#BbLB6 label.cb-lb input[type='checkbox']",
    "label.cb-lb input[type='checkbox']",
    "label.cb-lb",
    ".cb-c label",
    ".cb-c",
    ".main-wrapper #content",
)
_LOGIN_CAPTCHA_CDP_QUERIES = (
    "div[appcloudflarerecaptcha] label.cb-lb input[type='checkbox']",
    "div[appcloudflarerecaptcha] label.cb-lb",
    "app-cloudflare-captcha-container label.cb-lb input[type='checkbox']",
    "app-cloudflare-captcha-container label.cb-lb",
    "app-cloudflare-captcha-container input[type='checkbox']",
    "app-cloudflare-captcha-container div label input",
)


def shadow_checkbox_present(driver) -> bool:
    try:
        return _find_turnstile_node_via_cdp(driver) is not None
    except Exception as exc:
        logger.debug("Проверка Shadow DOM не удалась: %s", exc)
        return False


_LOGIN_IFRAME_CDP_QUERIES = (
    "div[appcloudflarerecaptcha] iframe",
    "[appcloudflarerecaptcha] iframe",
    "iframe[id^='cf-chl-widget']",
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "iframe[src*='0x4AAAAAA']",
    "app-cloudflare-captcha-container iframe",
)


def get_login_turnstile_iframe_src(driver) -> str | None:
    """src iframe Turnstile внутри closed Shadow DOM (через CDP)."""
    node_id = _find_node_via_cdp(driver, _LOGIN_IFRAME_CDP_QUERIES)
    if node_id is None:
        return None
    try:
        attrs = driver.execute_cdp_cmd("DOM.getAttributes", {"nodeId": node_id})
        pairs = attrs.get("attributes", [])
        for index in range(0, len(pairs), 2):
            if pairs[index] == "src":
                return pairs[index + 1]
    except Exception as exc:
        logger.debug("Не удалось прочитать src iframe Turnstile: %s", exc)
    return None


def parse_sitekey_from_iframe_src(src: str) -> str | None:
    if not src:
        return None
    match = re.search(r"/(0x4[A-Za-z0-9_-]+)/", src)
    if match:
        return match.group(1)
    match = re.search(r"(0x4[A-Za-z0-9_-]{10,})", src)
    if match:
        return match.group(1)
    return None


def login_captcha_widget_present(driver) -> bool:
    return get_login_turnstile_iframe_src(driver) is not None


def login_captcha_block_present(driver) -> bool:
    """Блок div[appcloudflarerecaptcha] на форме входа."""
    try:
        return bool(
            driver.execute_script(
                "return !!document.querySelector('div[appcloudflarerecaptcha]');"
            )
        )
    except Exception:
        return False


def login_captcha_checkbox_present(driver) -> bool:
    try:
        return _find_node_via_cdp(driver, _LOGIN_CAPTCHA_CDP_QUERIES) is not None
    except Exception as exc:
        logger.debug("Проверка login captcha checkbox не удалась: %s", exc)
        return False


def login_captcha_success_visible(driver) -> bool:
    """#success с display:grid — виджет показал «Успешно»."""
    node_id = _find_node_via_cdp(driver, ("#success", "#success-i", "#success-text"))
    if node_id is None:
        return False
    try:
        attrs = driver.execute_cdp_cmd("DOM.getAttributes", {"nodeId": node_id})
        pairs = attrs.get("attributes", [])
        style = ""
        for index in range(0, len(pairs), 2):
            if pairs[index] == "style":
                style = pairs[index + 1].lower()
                break
        if "display: none" in style or "visibility: hidden" in style:
            return False
        if "display: grid" in style or "visibility: visible" in style:
            return True
        return _node_style_visible(driver, node_id)
    except Exception:
        return _widget_visible(driver, ("#success", "#success-text"))


def click_turnstile_shadow(driver, context: str = "") -> bool:
    return click_turnstile_via_cdp(driver, context)


_LOGIN_WIDGET_REFRESH_QUERIES = (
    "a[href='#refresh']",
    "#fr-fail-troubleshoot-link",
    "#fr-troubleshoot-link",
    "#fr-overrun-link",
    "#expired-refresh-link",
    "#timeout-refresh-link",
    "a.cf-troubleshoot[href='#refresh']",
)


def _node_style_visible(driver, node_id: int) -> bool:
    try:
        attrs = driver.execute_cdp_cmd("DOM.getAttributes", {"nodeId": node_id})
        pairs = attrs.get("attributes", [])
        style = ""
        for index in range(0, len(pairs), 2):
            if pairs[index] == "style":
                style = pairs[index + 1].lower()
                break
        if not style:
            return True
        if "display: none" in style or "visibility: hidden" in style:
            return False
        return True
    except Exception:
        return False


def _widget_visible(driver, selectors: tuple[str, ...]) -> bool:
    node_id = _find_node_via_cdp(driver, selectors)
    if node_id is None:
        return False
    return _node_style_visible(driver, node_id)


def login_captcha_checkbox_ready(driver) -> bool:
    return _widget_visible(
        driver,
        (
            "#BbLB6 label.cb-lb",
            "#BbLB6 label.cb-lb input[type='checkbox']",
            "label.cb-lb input[type='checkbox']",
            "label.cb-lb",
            ".cb-c label",
        ),
    )


def login_captcha_succeeded(driver) -> bool:
    return _widget_visible(driver, ("#success", "#success-text"))


def login_captcha_needs_refresh(driver) -> bool:
    if login_captcha_failed(driver):
        return True
    return _widget_visible(
        driver,
        ("#expired", "#timeout", "#challenge-error", "#fail"),
    )


def login_captcha_verifying(driver) -> bool:
    return _widget_visible(driver, ("#verifying", "#verifying-text"))


def refresh_login_captcha_widget(driver) -> bool:
    for query in _LOGIN_WIDGET_REFRESH_QUERIES:
        node_id = _find_node_via_cdp(driver, (query,))
        if node_id is not None and _click_node_via_cdp(driver, node_id, "login-captcha-refresh"):
            logger.info("Виджет капчи на форме входа обновлён (%s)", query)
            return True
    return False


def login_captcha_failed(driver) -> bool:
    return _widget_visible(driver, ("#fail", "#fail-text"))


def click_login_captcha_checkbox(driver) -> bool:
    """Fallback: клик по чекбоксу (не использовать после RuCaptcha)."""
    node_id = _find_node_via_cdp(driver, _LOGIN_CAPTCHA_CDP_QUERIES)
    if node_id is not None:
        return _click_node_via_cdp(driver, node_id, "login-captcha")
    return click_turnstile_via_cdp(driver, "login-captcha")


def click_login_captcha_widget(driver) -> bool:
    """ЛКМ по блоку Turnstile на форме входа (div[appcloudflarerecaptcha] / iframe)."""
    _scroll_login_captcha_block(driver)

    node_id = _find_node_via_cdp(driver, _LOGIN_IFRAME_CDP_QUERIES)
    if node_id is not None:
        try:
            box = driver.execute_cdp_cmd("DOM.getBoxModel", {"nodeId": node_id})
            content = box["model"]["content"]
            height = content[5] - content[1]
            if _click_node_via_cdp(
                driver,
                node_id,
                "login-widget:iframe-checkbox",
                offset_x=35,
                offset_y=height / 2,
            ):
                logger.info("ЛКМ по iframe Turnstile (область чекбокса)")
                return True
        except Exception as exc:
            logger.debug("Клик по iframe не удался: %s", exc)

    for query in _LOGIN_CAPTCHA_CLICK_QUERIES:
        node_id = _find_node_via_cdp(driver, (query,))
        if node_id is not None and _click_node_via_cdp(
            driver, node_id, f"login-widget:{query}"
        ):
            logger.info("ЛКМ по блоку Turnstile (%s)", query)
            return True

    try:
        rect = driver.execute_script(
            """
            const el = document.querySelector('div[appcloudflarerecaptcha]');
            if (!el) return null;
            el.scrollIntoView({block: 'center', inline: 'center'});
            const r = el.getBoundingClientRect();
            return {x: r.x, y: r.y, width: r.width, height: r.height};
            """
        )
        if rect and rect.get("width", 0) > 0:
            x = rect["x"] + min(40, rect["width"] * 0.15)
            y = rect["y"] + rect["height"] / 2
            if _dispatch_mouse_click_at_viewport(
                driver, x, y, "login-block:appcloudflarerecaptcha"
            ):
                logger.info("ЛКМ по div[appcloudflarerecaptcha]")
                return True
    except Exception as exc:
        logger.debug("Клик по div[appcloudflarerecaptcha] не удался: %s", exc)

    return click_login_captcha_checkbox(driver)


def _scroll_login_captcha_block(driver) -> None:
    try:
        driver.execute_script(
            """
            const el = document.querySelector('div[appcloudflarerecaptcha]')
                || document.querySelector('app-cloudflare-captcha-container');
            if (el) el.scrollIntoView({block: 'center', inline: 'center'});
            """
        )
    except Exception:
        pass


def click_turnstile_via_cdp(driver, context: str = "") -> bool:
    node_id = _find_turnstile_node_via_cdp(driver)
    if node_id is None:
        return False
    return _click_node_via_cdp(driver, node_id, context or "main")


def _click_node_via_cdp(
    driver,
    node_id: int,
    context: str,
    *,
    offset_x: float | None = None,
    offset_y: float | None = None,
) -> bool:
    try:
        driver.execute_cdp_cmd(
            "DOM.scrollIntoViewIfNeeded", {"nodeId": node_id}
        )
    except Exception:
        pass

    try:
        box = driver.execute_cdp_cmd("DOM.getBoxModel", {"nodeId": node_id})
        content = box["model"]["content"]
        left, top, right, bottom = content[0], content[1], content[2], content[5]
        x = left + offset_x if offset_x is not None else (left + right) / 2
        y = top + offset_y if offset_y is not None else (top + bottom) / 2
        return _dispatch_mouse_click_at_viewport(driver, x, y, context)
    except Exception as exc:
        logger.debug("CDP клик по Shadow DOM не удался (%s): %s", context, exc)
        return False


def _dispatch_mouse_click_at_viewport(
    driver, x: float, y: float, context: str
) -> bool:
    try:
        for event_type in ("mouseMoved", "mousePressed", "mouseReleased"):
            driver.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
            )
        logger.info("ЛКМ через CDP (%s) at (%.0f, %.0f)", context, x, y)
        return True
    except Exception as exc:
        logger.debug("CDP mouse event не удался (%s): %s", context, exc)
        return False


def _find_turnstile_node_via_cdp(driver) -> int | None:
    return _find_node_via_cdp(driver, _TURNSTILE_CDP_QUERIES)


def _find_node_via_cdp(driver, queries: tuple[str, ...]) -> int | None:
    for query in queries:
        search_id = None
        try:
            result = driver.execute_cdp_cmd(
                "DOM.performSearch",
                {"query": query, "includeUserAgentShadowDOM": True},
            )
            search_id = result.get("searchId")
            if not search_id:
                continue
            node_ids = driver.execute_cdp_cmd(
                "DOM.getSearchResults",
                {"searchId": search_id, "fromIndex": 0, "toIndex": 20},
            ).get("nodeIds", [])
            if node_ids:
                return node_ids[0]
        except Exception as exc:
            logger.debug("DOM.performSearch(%s) не удался: %s", query, exc)
        finally:
            if search_id is not None:
                try:
                    driver.execute_cdp_cmd(
                        "DOM.discardSearchResults", {"searchId": search_id}
                    )
                except Exception:
                    pass
    return None
