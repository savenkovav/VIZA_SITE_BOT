import base64
import json
import logging
import re

from src.rucaptcha import extract_turnstile_params

logger = logging.getLogger(__name__)


def parse_intercepted_params_from_logs(driver) -> dict[str, str | None] | None:
    try:
        logs = driver.get_log("browser")
    except Exception as exc:
        logger.debug("Не удалось прочитать browser log: %s", exc)
        return None

    for entry in reversed(logs):
        message = entry.get("message", "")
        if "intercepted-params:" not in message:
            continue
        for text in (message, _decode_log_message(message)):
            parsed = _extract_json_payload(text)
            if parsed and parsed.get("sitekey"):
                logger.info("Параметры Turnstile перехвачены из console.log")
                return {
                    "sitekey": parsed.get("sitekey"),
                    "action": parsed.get("action"),
                    "data": parsed.get("data"),
                    "pagedata": parsed.get("pagedata"),
                    "useragent": parsed.get("userAgent"),
                    "pageurl": parsed.get("pageurl"),
                }
    return None


def parse_turnstile_params_from_network(driver) -> dict[str, str | None] | None:
    try:
        logs = driver.get_log("performance")
    except Exception as exc:
        logger.debug("Не удалось прочитать performance log: %s", exc)
        return None

    seen_request_ids: set[str] = set()
    combined_body: list[str] = []

    for entry in logs:
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, json.JSONDecodeError):
            continue
        if message.get("method") != "Network.responseReceived":
            continue

        params = message.get("params", {})
        response = params.get("response", {})
        url = response.get("url", "")
        if not _is_challenge_url(url):
            continue

        request_id = params.get("requestId")
        if not request_id or request_id in seen_request_ids:
            continue
        seen_request_ids.add(request_id)

        body = _read_response_body(driver, request_id)
        if body:
            combined_body.append(body)

    if not combined_body:
        return None

    extracted = extract_turnstile_params("\n".join(combined_body))
    if not extracted.get("sitekey"):
        return None

    result = {
        "sitekey": extracted.get("sitekey"),
        "action": extracted.get("action"),
        "data": extracted.get("data"),
        "pagedata": extracted.get("pagedata"),
        "useragent": None,
        "pageurl": driver.current_url,
    }
    if result.get("data") and result.get("pagedata"):
        logger.info("Полные параметры Turnstile из network (sitekey=%s...)", result["sitekey"][:12])
    else:
        logger.info("Частичные параметры из network (sitekey=%s...)", result["sitekey"][:12])
    return result


def _is_challenge_url(url: str) -> bool:
    markers = (
        "challenge-platform",
        "challenges.cloudflare.com",
        "turnstile",
        "cdn-cgi/challenge",
    )
    return any(marker in url for marker in markers)


def _read_response_body(driver, request_id: str) -> str:
    try:
        payload = driver.execute_cdp_cmd(
            "Network.getResponseBody",
            {"requestId": request_id},
        )
    except Exception:
        return ""
    body = payload.get("body", "")
    if payload.get("base64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8", errors="ignore")
        except Exception:
            return ""
    return body


def _decode_log_message(message: str) -> str:
    try:
        return message.encode("utf-8").decode("unicode_escape")
    except Exception:
        return message


def _extract_json_payload(text: str) -> dict | None:
    for pattern in (
        r"intercepted-params:(\{.*\})",
        r"intercepted-params:({.*?})",
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    return None
