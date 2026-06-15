import logging

logger = logging.getLogger(__name__)

# Turnstile в closed Shadow DOM: <template shadowrootmode="closed">
# CSS >>> не поддерживается в Chrome 149 — используем только CDP.
_TURNSTILE_CDP_QUERIES = (
    "label.cb-lb input[type='checkbox']",
    "label.cb-lb",
    ".cb-c label",
    ".cb-lb-t",
)

_LOGIN_CAPTCHA_CDP_QUERIES = (
    "app-cloudflare-captcha-container label.cb-lb input[type='checkbox']",
    "app-cloudflare-captcha-container label.cb-lb",
    "app-cloudflare-captcha-container input[type='checkbox']",
    "app-cloudflare-captcha-container div label input",
    "app-cloudflare-captcha-container iframe",
)


def shadow_checkbox_present(driver) -> bool:
    try:
        return _find_turnstile_node_via_cdp(driver) is not None
    except Exception as exc:
        logger.debug("Проверка Shadow DOM не удалась: %s", exc)
        return False


def login_captcha_checkbox_present(driver) -> bool:
    try:
        return _find_node_via_cdp(driver, _LOGIN_CAPTCHA_CDP_QUERIES) is not None
    except Exception as exc:
        logger.debug("Проверка login captcha checkbox не удалась: %s", exc)
        return False


def click_turnstile_shadow(driver, context: str = "") -> bool:
    return click_turnstile_via_cdp(driver, context)


def login_captcha_failed(driver) -> bool:
    try:
        node_id = _find_node_via_cdp(driver, ("#fail", "#fail-text"))
        if node_id is None:
            return False
        attrs = driver.execute_cdp_cmd("DOM.getAttributes", {"nodeId": node_id})
        pairs = attrs.get("attributes", [])
        style = ""
        for index in range(0, len(pairs), 2):
            if pairs[index] == "style":
                style = pairs[index + 1]
                break
        if not style:
            return node_id is not None
        return "visibility: visible" in style and "display: none" not in style
    except Exception as exc:
        logger.debug("Проверка fail Turnstile не удалась: %s", exc)
        return False


def refresh_login_captcha_widget(driver) -> bool:
    for query in (
        "app-cloudflare-captcha-container a[href='#refresh']",
        "#fr-fail-troubleshoot-link",
        "#expired-refresh-link",
        "#timeout-refresh-link",
        "a.cf-troubleshoot[href='#refresh']",
    ):
        node_id = _find_node_via_cdp(driver, (query,))
        if node_id is not None and _click_node_via_cdp(driver, node_id, "login-captcha-refresh"):
            logger.info("Виджет капчи на форме входа обновлён")
            return True
    return False


def click_login_captcha_checkbox(driver) -> bool:
    """Клик по чекbоксу «Подтвердите, что вы человек» в app-cloudflare-captcha-container."""
    node_id = _find_node_via_cdp(driver, _LOGIN_CAPTCHA_CDP_QUERIES)
    if node_id is not None:
        return _click_node_via_cdp(driver, node_id, "login-captcha")
    return click_turnstile_via_cdp(driver, "login-captcha")


def click_turnstile_via_cdp(driver, context: str = "") -> bool:
    node_id = _find_turnstile_node_via_cdp(driver)
    if node_id is None:
        return False
    return _click_node_via_cdp(driver, node_id, context or "main")


def _click_node_via_cdp(driver, node_id: int, context: str) -> bool:
    try:
        driver.execute_cdp_cmd(
            "DOM.scrollIntoViewIfNeeded", {"nodeId": node_id}
        )
    except Exception:
        pass

    try:
        box = driver.execute_cdp_cmd("DOM.getBoxModel", {"nodeId": node_id})
        content = box["model"]["content"]
        x = (content[0] + content[2]) / 2
        y = (content[1] + content[5]) / 2
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
        logger.info("Клик по Turnstile через CDP Shadow DOM (%s)", context)
        return True
    except Exception as exc:
        logger.debug("CDP клик по Shadow DOM не удался (%s): %s", context, exc)
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
