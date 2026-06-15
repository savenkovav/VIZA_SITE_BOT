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


def shadow_checkbox_present(driver) -> bool:
    try:
        return _find_turnstile_node_via_cdp(driver) is not None
    except Exception as exc:
        logger.debug("Проверка Shadow DOM не удалась: %s", exc)
        return False


def click_turnstile_shadow(driver, context: str = "") -> bool:
    return click_turnstile_via_cdp(driver, context)


def click_turnstile_via_cdp(driver, context: str = "") -> bool:
    node_id = _find_turnstile_node_via_cdp(driver)
    if node_id is None:
        return False

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
        logger.info("Клик по Turnstile через CDP Shadow DOM (%s)", context or "main")
        return True
    except Exception as exc:
        logger.debug("CDP клик по Shadow DOM не удался: %s", exc)
        return False


def _find_turnstile_node_via_cdp(driver) -> int | None:
    for query in _TURNSTILE_CDP_QUERIES:
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
