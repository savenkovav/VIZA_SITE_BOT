import logging
import sys

from src.browser import create_driver
from src.config import load_settings
from src.vfs_bot import VfsLoginBot


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> int:
    settings = load_settings()
    setup_logging(settings.log_level)
    logger = logging.getLogger(__name__)

    driver = None
    try:
        driver = create_driver(settings)
        bot = VfsLoginBot(driver, settings)
        success = bot.run_until_login_or_submit()
        if success:
            logger.info("Авторизация отправлена. Браузер остаётся открытым 120 с для проверки.")
            import time

            time.sleep(120)
            return 0
        logger.error("Не удалось завершить авторизацию")
        return 1
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
        return 130
    except Exception:
        logger.exception("Критическая ошибка")
        return 1
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
