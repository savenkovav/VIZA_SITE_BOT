import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

API_IN_URL = "https://rucaptcha.com/in.php"
API_RES_URL = "https://rucaptcha.com/res.php"


class RuCaptchaError(Exception):
    pass


class RuCaptchaClient:
    def __init__(
        self,
        api_key: str,
        poll_interval_sec: float = 5,
        timeout_sec: float = 180,
    ) -> None:
        self.api_key = api_key
        self.poll_interval_sec = poll_interval_sec
        self.timeout_sec = timeout_sec

    def solve_turnstile(
        self,
        sitekey: str,
        pageurl: str,
        *,
        action: str | None = None,
        data: str | None = None,
        pagedata: str | None = None,
        useragent: str | None = None,
    ) -> tuple[str, str | None]:
        params: dict[str, str] = {
            "key": self.api_key,
            "method": "turnstile",
            "sitekey": sitekey,
            "pageurl": pageurl,
            "json": "1",
        }
        if action:
            params["action"] = action
        if data:
            params["data"] = data
        if pagedata:
            params["pagedata"] = pagedata
        if useragent:
            params["useragent"] = useragent

        task_id = self._submit(params)
        logger.info("RuCaptcha: задача создана, id=%s", task_id)
        return self._wait_result(task_id)

    def _submit(self, params: dict[str, str]) -> str:
        response = self._request(API_IN_URL, params)
        if response.get("status") != 1:
            raise RuCaptchaError(response.get("request", "Неизвестная ошибка RuCaptcha"))
        return str(response["request"])

    def _wait_result(self, task_id: str) -> tuple[str, str | None]:
        deadline = time.monotonic() + self.timeout_sec
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval_sec)
            response = self._request(
                API_RES_URL,
                {
                    "key": self.api_key,
                    "action": "get",
                    "id": task_id,
                    "json": "1",
                },
            )
            if response.get("status") == 1:
                token = str(response["request"])
                ua = response.get("useragent")
                logger.info("RuCaptcha: токен Turnstile получен")
                return token, str(ua) if ua else None
            error = str(response.get("request", ""))
            if error == "CAPCHA_NOT_READY":
                logger.debug("RuCaptcha: капча ещё решается...")
                continue
            raise RuCaptchaError(error)
        raise RuCaptchaError("Превышено время ожидания решения капчи")

    def _request(self, url: str, params: dict[str, str]) -> dict:
        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{query}")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuCaptchaError(f"Ошибка сети RuCaptcha: {exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuCaptchaError(f"Некорректный ответ RuCaptcha: {body[:200]}") from exc


def extract_turnstile_params(page_source: str) -> dict[str, str | None]:
    sitekey = _first_match(
        page_source,
        [
            r'data-sitekey=["\']([^"\']+)',
            r"sitekey['\"]?\s*[:=]\s*['\"](0x[^'\"]+)",
            r'"sitekey"\s*:\s*"(0x[^"]+)"',
            r'[?&]k=(0x[a-fA-F0-9]+)',
            r'(0x4[A-Za-z0-9_-]{10,})',
        ],
    )
    action = _first_match(
        page_source,
        [
            r'data-action=["\']([^"\']+)',
            r'turnstile\.render\([^)]*action\s*:\s*["\']([^"\']+)',
            r'action\s*:\s*["\'](managed|[^"\']+)["\']',
            r'"action"\s*:\s*"([^"]+)"',
        ],
    )
    data = _first_match(
        page_source,
        [
            r'cData\s*:\s*["\']([^"\']+)',
            r'"cData"\s*:\s*"([^"]+)"',
            r'data\s*:\s*["\']([0-9a-fA-F]{8,})["\']',
        ],
    )
    pagedata = _first_match(
        page_source,
        [
            r'chlPageData\s*:\s*["\']([^"\']+)',
            r'"chlPageData"\s*:\s*"([^"]+)"',
            r'pagedata\s*:\s*["\']([^"\']+)',
        ],
    )
    return {
        "sitekey": sitekey,
        "action": action,
        "data": data,
        "pagedata": pagedata,
    }


def merge_turnstile_params(*sources: dict[str, str | None]) -> dict[str, str | None]:
    merged: dict[str, str | None] = {
        "sitekey": None,
        "action": None,
        "data": None,
        "pagedata": None,
        "useragent": None,
        "pageurl": None,
    }
    for source in sources:
        for key in merged:
            if source.get(key):
                merged[key] = source[key]
    return merged


def _first_match(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None
