import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    email: str
    password: str
    login_url: str
    rucaptcha_api_key: str | None
    check_interval_sec: int
    post_403_nav_wait_sec: int
    browser_timeout_sec: int
    headless: bool
    use_undetected_chrome: bool
    chrome_user_data_dir: str | None
    log_level: str


def load_settings() -> Settings:
    email = os.getenv("VFS_EMAIL", "").strip()
    password = os.getenv("VFS_PASSWORD", "").strip()
    if not email or not password:
        raise ValueError(
            "Задайте VFS_EMAIL и VFS_PASSWORD в файле .env (см. env.example)"
        )

    user_data = os.getenv("CHROME_USER_DATA_DIR", "").strip() or None
    rucaptcha_key = os.getenv("RU_CAPCHA_API", "").strip() or None

    return Settings(
        email=email,
        password=password,
        login_url=os.getenv(
            "VFS_LOGIN_URL", "https://visa.vfsglobal.com/blr/ru/pol/login"
        ).strip(),
        rucaptcha_api_key=rucaptcha_key,
        check_interval_sec=int(os.getenv("CHECK_INTERVAL_SEC", "60")),
        post_403_nav_wait_sec=int(os.getenv("POST_403_NAV_WAIT_SEC", "5")),
        browser_timeout_sec=int(os.getenv("BROWSER_TIMEOUT_SEC", "30")),
        headless=_env_bool("HEADLESS", False),
        use_undetected_chrome=_env_bool("USE_UNDETECTED_CHROME", True),
        chrome_user_data_dir=user_data,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
