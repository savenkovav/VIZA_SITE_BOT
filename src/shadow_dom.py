import logging
import re
import time

from src.human_click import human_like_click_at_viewport, human_pause

logger = logging.getLogger(__name__)
CHALLENGE_PRE_CLICK_WAIT_SEC = 5
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


_TURNSTILE_IFRAME_CDP_QUERIES = (
    "iframe[id^='cf-chl-widget']",
    "div[appcloudflarerecaptcha] iframe",
    "[appcloudflarerecaptcha] iframe",
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "iframe[src*='0x4AAAAAA']",
    "app-cloudflare-captcha-container iframe",
)
# обратная совместимость
_LOGIN_IFRAME_CDP_QUERIES = _TURNSTILE_IFRAME_CDP_QUERIES


def get_login_turnstile_iframe_src(driver) -> str | None:
    """src iframe Turnstile внутри closed Shadow DOM (через CDP)."""
    node_id = _find_node_via_cdp(driver, _TURNSTILE_IFRAME_CDP_QUERIES)
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


def turnstile_captcha_block_present(driver) -> bool:
    """Блок Turnstile: div[appcloudflarerecaptcha] или div+shadow iframe (Challenge / вход)."""
    try:
        return bool(
            driver.execute_script(
                """
                return !!(
                    document.querySelector('div[appcloudflarerecaptcha]')
                    || document.querySelector('input[id^="cf-chl-widget"][id$="_response"]')
                    || document.querySelector('input[name="cf-turnstile-response"]')
                );
                """
            )
        )
    except Exception:
        return False


def login_captcha_block_present(driver) -> bool:
    return turnstile_captcha_block_present(driver)


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
    human_pause(0.3, 0.9)
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


def _get_node_viewport_box(driver, node_id: int) -> dict[str, float] | None:
    try:
        driver.execute_cdp_cmd("DOM.scrollIntoViewIfNeeded", {"nodeId": node_id})
        box = driver.execute_cdp_cmd("DOM.getBoxModel", {"nodeId": node_id})
        content = box["model"]["content"]
        left, top, right, bottom = content[0], content[1], content[2], content[5]
        width = right - left
        height = bottom - top
        if width < 10 or height < 10:
            return None
        return {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "right": right,
            "bottom": bottom,
        }
    except Exception:
        return None


def _find_all_nodes_via_cdp(
    driver, queries: tuple[str, ...], *, limit: int = 20
) -> list[int]:
    found: list[int] = []
    seen: set[int] = set()
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
                {"searchId": search_id, "fromIndex": 0, "toIndex": limit},
            ).get("nodeIds", [])
            for node_id in node_ids:
                if node_id not in seen:
                    seen.add(node_id)
                    found.append(node_id)
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
    return found


def _pick_visible_turnstile_iframe(driver) -> int | None:
    """Видимый iframe Turnstile (~300×65) в shadow DOM."""
    for node_id in _find_all_nodes_via_cdp(driver, _TURNSTILE_IFRAME_CDP_QUERIES):
        box = _get_node_viewport_box(driver, node_id)
        if not box:
            continue
        if box["width"] >= 180 and 40 <= box["height"] <= 120:
            logger.debug(
                "Turnstile iframe: %.0fx%.0f at (%.0f, %.0f)",
                box["width"],
                box["height"],
                box["left"],
                box["top"],
            )
            return node_id
    return None


def _click_turnstile_iframe_checkbox(
    driver, node_id: int, context: str
) -> bool:
    box = _get_node_viewport_box(driver, node_id)
    if not box:
        return False
    x = box["left"] + min(35.0, box["width"] * 0.12)
    y = box["top"] + box["height"] / 2
    logger.debug(
        "Клик по iframe: checkbox at (%.0f, %.0f), iframe (%.0f, %.0f, %.0fx%.0f)",
        x,
        y,
        box["left"],
        box["top"],
        box["width"],
        box["height"],
    )
    return _dispatch_mouse_click_at_viewport(driver, x, y, context)


def _get_turnstile_block_click_point(driver, context: str) -> dict[str, float] | None:
    """Fallback-координаты: Challenge — левый верх (300×65), вход — по центру блока."""
    try:
        return driver.execute_script(
            """
            const context = arguments[0];
            const login = document.querySelector('div[appcloudflarerecaptcha]');
            const inp = document.querySelector(
                'input[id^="cf-chl-widget"][id$="_response"]'
            );
            const wrap = login || (inp ? inp.parentElement : null);
            if (!wrap) return null;
            wrap.scrollIntoView({block: 'center', inline: 'nearest'});
            const r = wrap.getBoundingClientRect();
            if (context === 'login') {
                return {
                    x: r.x + Math.min(40, r.width * 0.15),
                    y: r.y + r.height / 2,
                };
            }
            // Challenge: виджет 300×65, слева-сверху в блоке (не по центру страницы)
            return { x: r.x + 35, y: r.y + 32 };
            """,
            context,
        )
    except Exception as exc:
        logger.debug("Не удалось вычислить точку клика (%s): %s", context, exc)
        return None


def click_turnstile_widget_block(driver, context: str = "turnstile") -> bool:
    """
    ЛКМ по блоку Turnstile (форма входа и первый экран Challenge).
    Структура: div > closed shadow > iframe#cf-chl-widget-* + input[name=cf-turnstile-response].
    """
    if context == "challenge":
        logger.info(
            "Ожидание %s с перед кликом по капче (Challenge)...",
            CHALLENGE_PRE_CLICK_WAIT_SEC,
        )
        time.sleep(CHALLENGE_PRE_CLICK_WAIT_SEC)
    else:
        human_pause(0.6, 1.8)
    _scroll_turnstile_captcha_block(driver)
    human_pause(0.2, 0.5)

    iframe_id = _pick_visible_turnstile_iframe(driver)
    if iframe_id is not None and _click_turnstile_iframe_checkbox(
        driver, iframe_id, f"{context}:iframe-checkbox"
    ):
        logger.info("ЛКМ по iframe Turnstile (%.0f×%.0f, %s)", 300, 65, context)
        return True

    for node_id in _find_all_nodes_via_cdp(driver, _TURNSTILE_IFRAME_CDP_QUERIES):
        if _click_turnstile_iframe_checkbox(
            driver, node_id, f"{context}:iframe-any"
        ):
            logger.info("ЛКМ по iframe Turnstile (fallback, %s)", context)
            return True

    for query in _LOGIN_CAPTCHA_CLICK_QUERIES:
        node_id = _find_node_via_cdp(driver, (query,))
        if node_id is not None and _click_node_via_cdp(
            driver, node_id, f"{context}:{query}"
        ):
            logger.info("ЛКМ по блоку Turnstile (%s, %s)", query, context)
            return True

    point = _get_turnstile_block_click_point(driver, context)
    if point and _dispatch_mouse_click_at_viewport(
        driver, point["x"], point["y"], f"{context}:block"
    ):
        logger.info(
            "ЛКМ по блоку Turnstile (viewport %.0f, %.0f, %s)",
            point["x"],
            point["y"],
            context,
        )
        return True

    return click_login_captcha_checkbox(driver)


def click_login_captcha_widget(driver) -> bool:
    """ЛКМ по блоку Turnstile на форме входа."""
    return click_turnstile_widget_block(driver, context="login")


def click_challenge_turnstile_widget(driver) -> bool:
    """ЛКМ по блоку Turnstile на первом экране Cloudflare Challenge."""
    return click_turnstile_widget_block(driver, context="challenge")


def _scroll_turnstile_captcha_block(driver) -> None:
    iframe_id = _pick_visible_turnstile_iframe(driver)
    if iframe_id is not None:
        try:
            driver.execute_cdp_cmd(
                "DOM.scrollIntoViewIfNeeded", {"nodeId": iframe_id}
            )
            return
        except Exception:
            pass
    try:
        driver.execute_script(
            """
            const inp = document.querySelector(
                'input[id^="cf-chl-widget"][id$="_response"]'
            );
            const el = document.querySelector('div[appcloudflarerecaptcha]')
                || (inp ? inp.parentElement : null)
                || document.querySelector('app-cloudflare-captcha-container');
            if (el) el.scrollIntoView({block: 'center', inline: 'nearest'});
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
    return human_like_click_at_viewport(driver, x, y, context)


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
