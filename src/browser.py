import logging

from src.cf_challenge import TURNSTILE_HOOK_SCRIPT
from src.config import Settings

logger = logging.getLogger(__name__)


def create_driver(settings: Settings):
    if settings.use_undetected_chrome:
        return _create_undetected_driver(settings)
    return _create_selenium_driver(settings)


def _create_undetected_driver(settings: Settings):
    import undetected_chromedriver as uc

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1400,900")
    options.add_argument("--lang=ru-RU,ru")
    options.set_capability(
        "goog:loggingPrefs",
        {"browser": "ALL", "performance": "ALL"},
    )

    if settings.chrome_user_data_dir:
        options.add_argument(f"--user-data-dir={settings.chrome_user_data_dir}")

    chrome_bin = __import__("os").environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    driver = uc.Chrome(
        options=options,
        headless=settings.headless,
    )
    logger.info("Браузер: undetected-chromedriver (обход детекции Cloudflare)")
    _apply_cdp_hooks(driver, settings)
    return driver


def _create_selenium_driver(settings: Settings):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1400,900")
    options.add_argument("--lang=ru-RU,ru")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.set_capability(
        "goog:loggingPrefs",
        {"browser": "ALL", "performance": "ALL"},
    )

    if settings.headless:
        options.add_argument("--headless=new")

    if settings.chrome_user_data_dir:
        options.add_argument(f"--user-data-dir={settings.chrome_user_data_dir}")

    chrome_bin = __import__("os").environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    chromedriver_path = __import__("os").environ.get("CHROMEDRIVER_PATH")
    if chromedriver_path:
        service = Service(executable_path=chromedriver_path)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    logger.info("Браузер: стандартный Selenium Chrome")
    _apply_cdp_hooks(driver, settings)
    return driver


def _apply_cdp_hooks(driver, settings: Settings) -> None:
    driver.execute_cdp_cmd("DOM.enable", {})
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """
        },
    )
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": TURNSTILE_HOOK_SCRIPT},
    )
    driver.set_page_load_timeout(max(settings.browser_timeout_sec * 3, 90))
