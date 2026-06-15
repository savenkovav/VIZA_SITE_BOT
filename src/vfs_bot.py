import logging
import random
import time
from enum import Enum, auto

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.cf_challenge import TURNSTILE_HOOK_SCRIPT
from src.cf_intercept import (
    parse_intercepted_params_from_logs,
    parse_turnstile_params_from_network,
)
from src.config import Settings
from src.rucaptcha import RuCaptchaClient, RuCaptchaError, extract_turnstile_params, merge_turnstile_params
from src.human_click import human_pause
from src.shadow_dom import (
    CHALLENGE_PRE_CLICK_WAIT_SEC,
    click_turnstile_widget_block,
    click_turnstile_shadow,
    get_login_turnstile_iframe_src,
    login_captcha_block_present,
    login_captcha_checkbox_present,
    login_captcha_checkbox_ready,
    login_captcha_failed,
    login_captcha_needs_refresh,
    login_captcha_success_visible,
    login_captcha_succeeded,
    login_captcha_verifying,
    parse_sitekey_from_iframe_src,
    refresh_login_captcha_widget,
    shadow_checkbox_present,
)

logger = logging.getLogger(__name__)

# Cloudflare Turnstile: input скрыт, кликабелен label.cb-lb
TURNSTILE_LABEL_SELECTORS = [
    "label.cb-lb",
    "div.cb-c label",
    "div.main-wrapper label.cb-lb",
]
TURNSTILE_CHECKBOX_SELECTORS = [
    "label.cb-lb input[type='checkbox']",
    "div.cb-c input[type='checkbox']",
    "input[type='checkbox']",
]
TURNSTILE_LABEL_XPATH = (
    "//label[contains(@class,'cb-lb')]"
    " | //label[.//span[contains(@class,'cb-lb-t')]]"
    " | //label[.//span[contains(text(),'Подтвердите, что вы человек')]]"
)

TURNSTILE_IFRAME_SELECTORS = [
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='challenge-platform']",
    "iframe[src*='cdn-cgi']",
    "iframe[src*='turnstile']",
    "#turnstile-challenge iframe",
    ".cf-turnstile iframe",
    "iframe",
]

CHALLENGE_WIDGET_WAIT_SEC = 90
CHALLENGE_PASS_TIMEOUT_SEC = 90
CHALLENGE_POST_CLICK_CHECK_SEC = 15
LOGIN_TURNSTILE_WAIT_SEC = 20

BACK_HOME_CSS = "a.c-brand-orange.text-decoration-underline.cursor-pointer[href='blr/ru/pol/login']"
BACK_HOME_XPATH = "/html/body/app-root/div/main/div/app-not-found/div/a"

ONETRUST_ACCEPT_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#accept-recommended-btn-handler",
]
ONETRUST_CLOSE_SELECTORS = [
    "#close-pc-btn-handler",
    ".onetrust-close-btn-handler",
]

LOGIN_EMAIL_CSS = "#email"
LOGIN_PASSWORD_CSS = "#password"
LOGIN_BUTTON_CSS = "button.btn-brand-orange"
LOGIN_CAPTCHA_CONTAINER_CSS = "div[appcloudflarerecaptcha], app-cloudflare-captcha-container"
LOGIN_TURNSTILE_SITEKEY_FALLBACK = "0x4AAAAAABhlz7Ei4byodYjs"


class PageState(Enum):
    CLOUDFLARE_CHALLENGE = auto()
    WAITING_ROOM = auto()
    NOT_FOUND_403 = auto()
    LOGIN_FORM = auto()
    OTHER = auto()


class VfsLoginBot:
    def __init__(self, driver: WebDriver, settings: Settings) -> None:
        self.driver = driver
        self.settings = settings
        self._wait = WebDriverWait(driver, settings.browser_timeout_sec)
        self._rucaptcha: RuCaptchaClient | None = None
        self._cached_turnstile_params: dict[str, str | None] = {}
        self._last_turnstile_token: str | None = None
        self._fresh_intercept_received = False
        self._callback_frame_path: list[int] | None = None
        self._login_intercept_at_load = False
        if settings.rucaptcha_api_key:
            self._rucaptcha = RuCaptchaClient(settings.rucaptcha_api_key)
            logger.info("RuCaptcha: сервис обхода капчи включён")

    def open_login_page(self) -> None:
        logger.info("Открываю страницу: %s", self.settings.login_url)
        self.driver.get(self.settings.login_url)
        self._wait_for_challenge_bootstrap()
        human_pause(0.8, 1.6)
        if self.detect_page_state() == PageState.CLOUDFLARE_CHALLENGE:
            logger.info("Первый экран Challenge — пробую естественный клик по капче")
            if self._attempt_natural_captcha_pass(
                "первый экран", timeout=CHALLENGE_PASS_TIMEOUT_SEC
            ):
                time.sleep(self.settings.post_403_nav_wait_sec)
            else:
                logger.info("Естественный проход не удался — продолжу через RuCaptcha")
        self._inject_turnstile_intercept()

    def _wait_for_challenge_bootstrap(self) -> None:
        for _ in range(40):
            source = self.driver.page_source
            if "_cf_chl_opt" in source or "orchestrate/chl_page" in source:
                time.sleep(1)
                return
            time.sleep(0.5)

    def detect_page_state(self) -> PageState:
        source = self.driver.page_source.lower()
        title = self.driver.title.lower()

        if self._login_form_visible():
            return PageState.LOGIN_FORM

        if "app-not-found" in source or self._element_exists(By.CSS_SELECTOR, BACK_HOME_CSS):
            return PageState.NOT_FOUND_403

        if self._is_waiting_room_page():
            return PageState.WAITING_ROOM

        if (
            "just a moment" in title
            or "один момент" in title
            or ("момент" in title and "_cf_chl_opt" in source)
            or "challenge-platform" in source
            or "_cf_chl_opt" in source
        ):
            return PageState.CLOUDFLARE_CHALLENGE

        return PageState.OTHER

    def _is_waiting_room_page(self) -> bool:
        if self._login_form_visible():
            return False
        title = self.driver.title.lower()
        source = self.driver.page_source.lower()
        if "waiting room" in title or "зал ожидания" in title:
            return True
        if "turnstile-challenge" in source and (
            "waiting room" in source or "waitingrooms-text" in source or "message-4" in source
        ):
            return True
        return self._element_exists(By.CSS_SELECTOR, "#turnstile-challenge[data-sitekey]")

    def run_until_login_or_submit(self) -> bool:
        self.open_login_page()

        while True:
            state = self.detect_page_state()
            logger.info("Текущее состояние страницы: %s", state.name)

            if state == PageState.LOGIN_FORM:
                logger.info("Форма «Войти» обнаружена — жду виджет Turnstile")
                self._prepare_login_form_on_load()
                time.sleep(self.settings.post_403_nav_wait_sec)
                if self._login_form_visible():
                    return self._fill_and_submit_login()
                continue

            if state == PageState.CLOUDFLARE_CHALLENGE:
                logger.info("Страница Cloudflare Challenge — обход капчи")
                self._try_click_challenge_checkbox()
                self._handle_post_captcha_transition()
            elif state == PageState.WAITING_ROOM:
                logger.info("Waiting Room — обход Turnstile через RuCaptcha")
                self._try_solve_standalone_turnstile("waiting-room")
                self._handle_post_captcha_transition()
            elif state == PageState.NOT_FOUND_403:
                logger.info("Страница 403 — перехожу по ссылке «Вернуться на главную»")
                self._click_back_to_home()
                time.sleep(self.settings.post_403_nav_wait_sec)
            else:
                logger.info(
                    "Неизвестное состояние, повторная проверка через %s с",
                    self.settings.check_interval_sec,
                )

            if self._login_form_visible():
                logger.info("Форма «Войти» появилась при проверке — жду виджет Turnstile")
                self._prepare_login_form_on_load()
                time.sleep(self.settings.post_403_nav_wait_sec)
                if self._login_form_visible():
                    return self._fill_and_submit_login()

            logger.info(
                "Ожидание %s с перед следующей проверкой страницы",
                self.settings.check_interval_sec,
            )
            time.sleep(self.settings.check_interval_sec)

    def _login_form_visible(self) -> bool:
        return self._element_exists(By.CSS_SELECTOR, LOGIN_EMAIL_CSS) and self._element_exists(
            By.CSS_SELECTOR, LOGIN_PASSWORD_CSS
        )

    def _challenge_page_left(self) -> bool:
        return self.detect_page_state() != PageState.CLOUDFLARE_CHALLENGE

    def _attempt_challenge_captcha_pass(
        self, context: str, timeout: float = CHALLENGE_PASS_TIMEOUT_SEC
    ) -> bool:
        """Challenge: клик → проверка перехода → повторный клик через 5 с."""
        logger.info(
            "Challenge (%s): клик с проверкой перехода (таймаут %s с)",
            context,
            int(timeout),
        )
        deadline = time.monotonic() + timeout
        attempt = 0

        while time.monotonic() < deadline:
            attempt += 1
            if attempt > 1:
                logger.info(
                    "Переход не произошёл — повторный клик #%s (ожидание %s с)",
                    attempt,
                    CHALLENGE_PRE_CLICK_WAIT_SEC,
                )

            clicked = click_turnstile_widget_block(
                self.driver, context="challenge"
            ) or self._click_turnstile_in_all_frames()

            if not clicked:
                logger.warning("Клик #%s не удался (%s)", attempt, context)
            else:
                logger.info(
                    "Клик #%s выполнен, жду переход (до %s с)...",
                    attempt,
                    CHALLENGE_POST_CLICK_CHECK_SEC,
                )
                check_deadline = min(
                    time.monotonic() + CHALLENGE_POST_CLICK_CHECK_SEC, deadline
                )
                while time.monotonic() < check_deadline:
                    if login_captcha_failed(self.driver):
                        logger.warning("Сбой проверки — повторный клик")
                        break

                    if self._challenge_page_left():
                        logger.info(
                            "Переход выполнен (%s), состояние: %s",
                            context,
                            self.detect_page_state().name,
                        )
                        return True

                    if login_captcha_success_visible(
                        self.driver
                    ) or login_captcha_succeeded(self.driver):
                        time.sleep(1.5)
                        if self._challenge_page_left():
                            logger.info(
                                "Переход после «Успешно» (%s)", context
                            )
                            return True

                    if login_captcha_verifying(self.driver):
                        logger.debug("Turnstile: идёт проверка...")

                    time.sleep(0.5)
                else:
                    if self._challenge_page_left():
                        return True

                logger.info(
                    "Переход не произошёл после клика #%s — повтор через %s с",
                    attempt,
                    CHALLENGE_PRE_CLICK_WAIT_SEC,
                )

        logger.info("Challenge не пройден кликом (%s)", context)
        return False

    def _attempt_natural_captcha_pass(
        self, context: str, timeout: float = 30, *, login_form: bool = False
    ) -> bool:
        """ЛКМ по Turnstile и ожидание прохода (без RuCaptcha)."""
        if not login_form:
            return self._attempt_challenge_captcha_pass(context, timeout)

        logger.info("Естественный проход капчи (%s): пауза и ЛКМ как у человека", context)
        human_pause(0.5, 1.5)

        clicked = click_turnstile_widget_block(
            self.driver, context="login"
        ) or self._click_turnstile_in_all_frames()
        if not clicked:
            logger.debug("Виджет капчи для клика не найден (%s)", context)
            return False

        logger.info("Клик по капче выполнен (%s), жду проверку Turnstile...", context)
        deadline = time.monotonic() + timeout
        last_log_at = 0.0

        while time.monotonic() < deadline:
            if login_captcha_failed(self.driver):
                logger.warning("Turnstile: «Сбой проверки» после клика (%s)", context)
                return False

            if self._is_login_captcha_activated():
                logger.info("Капча на форме входа активирована (%s)", context)
                return True

            if login_captcha_success_visible(self.driver) or login_captcha_succeeded(
                self.driver
            ):
                logger.info("Turnstile: «Успешно» (%s)", context)
                time.sleep(1.5)
                if self._is_login_captcha_activated():
                    return True

            if login_captcha_verifying(self.driver):
                logger.debug("Turnstile: идёт проверка (%s)...", context)

            now = time.monotonic()
            if now - last_log_at >= 8:
                logger.info(
                    "Ожидание естественного прохода (%s)... success=%s, verifying=%s, button=%s",
                    context,
                    login_captcha_success_visible(self.driver),
                    login_captcha_verifying(self.driver),
                    self._is_login_button_enabled(),
                )
                last_log_at = now

            time.sleep(0.5)

        logger.info("Таймаут естественного прохода (%s)", context)
        return False

    def _prepare_login_form_on_load(self, timeout: float = 30) -> bool:
        """Ждём iframe Turnstile и извлекаем sitekey из URL (standalone, не Challenge)."""
        logger.info("Ожидаю виджет Turnstile на форме входа")
        self._inject_turnstile_intercept()
        deadline = time.monotonic() + timeout
        last_log_at = 0.0

        while time.monotonic() < deadline:
            params = self._extract_login_form_turnstile_params()
            if params.get("sitekey"):
                self._cached_turnstile_params = params
                self._login_intercept_at_load = True
                logger.info(
                    "Виджет Turnstile готов: sitekey=%s..., iframe=%s",
                    params["sitekey"][:12],
                    bool(params.get("iframe_src")),
                )
                return True

            now = time.monotonic()
            if now - last_log_at >= 8:
                iframe_src = get_login_turnstile_iframe_src(self.driver)
                logger.info(
                    "Ожидание виджета... block=%s, iframe=%s, checkbox=%s",
                    login_captcha_block_present(self.driver),
                    bool(iframe_src),
                    login_captcha_checkbox_ready(self.driver),
                )
                last_log_at = now

            time.sleep(0.25)

        logger.warning("Виджет Turnstile не появился за %s с", timeout)
        return False

    def _extract_login_form_turnstile_params(self) -> dict[str, str | None]:
        iframe_src = get_login_turnstile_iframe_src(self.driver)
        sitekey = parse_sitekey_from_iframe_src(iframe_src or "")
        if not sitekey:
            sitekey = parse_sitekey_from_iframe_src(
                self._collect_page_source_for_turnstile()
            )
        if not sitekey:
            sitekey = LOGIN_TURNSTILE_SITEKEY_FALLBACK

        hooked = self._read_hooked_turnstile_params()
        intercept = parse_intercepted_params_from_logs(self.driver) or {}

        return merge_turnstile_params(
            {
                "sitekey": sitekey,
                "pageurl": self.driver.current_url,
                "useragent": self.driver.execute_script("return navigator.userAgent;"),
                "iframe_src": iframe_src,
            },
            hooked,
            intercept,
        )

    def _is_login_captcha_activated(self) -> bool:
        if login_captcha_success_visible(self.driver) or login_captcha_succeeded(
            self.driver
        ):
            return True
        return self._is_login_button_enabled()

    def _wait_for_login_captcha_activated(self, timeout: float = 30) -> bool:
        logger.info("Ожидаю активацию капчи (#success / токен / кнопка «Войти»)...")
        deadline = time.monotonic() + timeout
        last_log_at = 0.0

        while time.monotonic() < deadline:
            if login_captcha_failed(self.driver):
                logger.warning("Виджет капчи: «Сбой проверки»")
                return False

            if self._is_login_captcha_activated():
                if login_captcha_success_visible(self.driver):
                    logger.info("Капча активирована: #success «Успешно»")
                elif self._is_login_button_enabled():
                    logger.info("Капча активирована: кнопка «Войти» доступна")
                else:
                    logger.info("Капча активирована: токен в hidden input")
                return True

            if login_captcha_verifying(self.driver):
                logger.debug("Turnstile: идёт проверка...")

            now = time.monotonic()
            if now - last_log_at >= 8:
                logger.info(
                    "Ожидание активации... success=%s, token=%s, button=%s, verifying=%s",
                    login_captcha_success_visible(self.driver),
                    self._login_captcha_token_present(),
                    self._is_login_button_enabled(),
                    login_captcha_verifying(self.driver),
                )
                last_log_at = now

            time.sleep(0.5)

        return self._is_login_captcha_activated()

    def _fill_and_submit_login(self) -> bool:
        logger.info("Заполняю форму авторизации")
        self._dismiss_cookie_banner()
        self._inject_turnstile_intercept()

        email_el = self._wait_for_clickable(By.CSS_SELECTOR, LOGIN_EMAIL_CSS)
        password_el = self._wait_for_clickable(By.CSS_SELECTOR, LOGIN_PASSWORD_CSS)

        email_el.clear()
        email_el.send_keys(self.settings.email)
        password_el.clear()
        password_el.send_keys(self.settings.password)

        if not self._solve_login_form_captcha(max_attempts=3):
            logger.warning("Не удалось решить капчу на форме входа")
            return False

        if not self._wait_for_login_button_enabled(timeout=30):
            logger.warning("Кнопка «Войти» не активировалась после решения капчи")
            return False

        if not self._click_login_button():
            return False

        logger.info("Нажата кнопка «Войти», ожидаю результат")
        time.sleep(5)
        return True

    def _solve_login_form_captcha(self, max_attempts: int = 3) -> bool:
        """ЛКМ по блоку Turnstile на форме входа (как на первом экране Challenge)."""
        if self._is_login_captcha_activated():
            logger.info("Капча на форме входа уже активирована")
            return True

        for attempt in range(1, max_attempts + 1):
            if login_captcha_needs_refresh(self.driver):
                logger.warning(
                    "Turnstile на форме входа требует обновления (попытка %s)", attempt
                )
                refresh_login_captcha_widget(self.driver)
                time.sleep(2)

            logger.info(
                "Клик по капче на форме входа (попытка %s/%s)", attempt, max_attempts
            )
            if self._attempt_natural_captcha_pass(
                f"форма входа #{attempt}", timeout=35, login_form=True
            ):
                return True

            if login_captcha_failed(self.driver):
                logger.warning(
                    "Сбой проверки после клика — обновляю виджет (попытка %s)",
                    attempt,
                )
                refresh_login_captcha_widget(self.driver)
                time.sleep(2)
                continue

            logger.warning(
                "Клик не активировал капчу (попытка %s/%s)", attempt, max_attempts
            )
            time.sleep(2)

        return False

    def _drain_browser_logs(self) -> None:
        try:
            self.driver.get_log("browser")
        except Exception:
            pass

    def _drain_performance_logs(self) -> None:
        try:
            self.driver.get_log("performance")
        except Exception:
            pass

    def _reset_turnstile_hook_state(self) -> None:
        self._fresh_intercept_received = False
        self._callback_frame_path = None
        self._cached_turnstile_params = {}
        clear_script = """
        delete window.__cfTurnstileParams;
        delete window.cfCallback;
        """
        try:
            self.driver.switch_to.default_content()
            self.driver.execute_script(clear_script)
        except Exception:
            pass
        for path in self._enumerate_frame_paths():
            if not path:
                continue
            try:
                self._switch_to_frame_path(path)
                self.driver.execute_script(clear_script)
            except Exception:
                pass
        self.driver.switch_to.default_content()

    def _remember_callback_frame(self) -> None:
        for path in self._enumerate_frame_paths():
            try:
                self._switch_to_frame_path(path)
                has_cb = self.driver.execute_script(
                    "return typeof window.cfCallback === 'function';"
                )
                if has_cb:
                    self._callback_frame_path = path
                    logger.info("cfCallback найден во frame %s", path)
                    self.driver.switch_to.default_content()
                    return
            except Exception:
                pass
        self.driver.switch_to.default_content()

    def _finalize_login_captcha_token(self) -> None:
        token = self._last_turnstile_token
        if not token:
            return
        self.driver.execute_script(
            """
            const token = arguments[0];
            const inputs = document.querySelectorAll(
                'div[appcloudflarerecaptcha] input[name="cf-turnstile-response"], '
                + 'div[appcloudflarerecaptcha] input[id$="_response"], '
                + 'app-cloudflare-captcha-container input[name="cf-turnstile-response"], '
                + 'app-cloudflare-captcha-container input[id$="_response"], '
                + 'input[name="cf-turnstile-response"]'
            );
            inputs.forEach((el) => {
                el.value = token;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            });

            const form = document.querySelector('app-login form');
            if (form) {
                form.dispatchEvent(new Event('input', { bubbles: true }));
                form.dispatchEvent(new Event('change', { bubbles: true }));
            }

            const container = document.querySelector('div[appcloudflarerecaptcha]')
                || document.querySelector('app-cloudflare-captcha-container');
            if (container) {
                container.dispatchEvent(new CustomEvent('turnstile-token', {
                    detail: { token },
                    bubbles: true,
                }));
            }
            """,
            token,
        )

    def _login_captcha_token_present(self) -> bool:
        try:
            return bool(
                self.driver.execute_script(
                    """
                    const selectors = [
                        'div[appcloudflarerecaptcha] input[name="cf-turnstile-response"]',
                        'div[appcloudflarerecaptcha] input[id$="_response"]',
                        'app-cloudflare-captcha-container input[name="cf-turnstile-response"]',
                        'app-cloudflare-captcha-container input[id$="_response"]',
                        'input[id^="cf-chl-widget"][id$="_response"]',
                        'input[name="cf-turnstile-response"]',
                    ];
                    for (const sel of selectors) {
                        for (const el of document.querySelectorAll(sel)) {
                            if (el.value && el.value.length > 20) return true;
                        }
                    }
                    return false;
                    """
                )
            )
        except Exception:
            return False

    def _wait_for_login_button_enabled(self, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if login_captcha_failed(self.driver):
                logger.debug("Виджет капчи в состоянии «Сбой проверки»")
                return False
            if self._is_login_button_enabled():
                logger.info("Кнопка «Войти» активна")
                return True
            time.sleep(0.5)
        return self._is_login_button_enabled()

    def _is_login_button_enabled(self) -> bool:
        try:
            buttons = self.driver.find_elements(By.CSS_SELECTOR, LOGIN_BUTTON_CSS)
            for btn in buttons:
                if "Войти" not in btn.text:
                    continue
                classes = btn.get_attribute("class") or ""
                if "mat-mdc-button-disabled" in classes:
                    continue
                disabled = btn.get_attribute("disabled")
                aria_disabled = btn.get_attribute("aria-disabled")
                if btn.is_enabled() and disabled is None and aria_disabled not in {
                    "true",
                    "True",
                }:
                    return True
        except Exception:
            pass
        return False

    def _click_login_button(self) -> bool:
        if not self._is_login_button_enabled():
            logger.debug("Кнопка «Войти» ещё неактивна")
            return False
        try:
            buttons = self.driver.find_elements(By.CSS_SELECTOR, LOGIN_BUTTON_CSS)
            for btn in buttons:
                if "Войти" in btn.text and self._is_login_button_enabled():
                    self._safe_click(btn)
                    return True
            btn = self._wait_for_clickable(By.XPATH, "//button[contains(., 'Войти')]")
            self._safe_click(btn)
            return True
        except (TimeoutException, NoSuchElementException, ElementNotInteractableException) as exc:
            logger.debug("Не удалось нажать «Войти»: %s", exc)
            return False

    def _click_back_to_home(self) -> None:
        self._dismiss_cookie_banner()
        for locator in (
            (By.CSS_SELECTOR, BACK_HOME_CSS),
            (By.XPATH, BACK_HOME_XPATH),
            (By.PARTIAL_LINK_TEXT, "Вернуться на главную"),
        ):
            el = None
            try:
                el = self._wait_for_clickable(*locator)
                self._safe_click(el)
                logger.info("Переход по ссылке «Вернуться на главную» выполнен")
                return
            except (TimeoutException, NoSuchElementException):
                continue
            except ElementClickInterceptedException:
                href = el.get_attribute("href") if el else None
                if href:
                    logger.info(
                        "Клик перехвачен оверлеем — переход по URL: %s", href
                    )
                    self.driver.get(href)
                    return

        logger.warning(
            "Ссылка «Вернуться на главную» не найдена — открываю %s",
            self.settings.login_url,
        )
        self.driver.get(self.settings.login_url)

    def _dismiss_cookie_banner(self) -> None:
        for selector in ONETRUST_ACCEPT_SELECTORS + ONETRUST_CLOSE_SELECTORS:
            try:
                for btn in self.driver.find_elements(By.CSS_SELECTOR, selector):
                    if not btn.is_displayed():
                        continue
                    try:
                        btn.click()
                    except ElementClickInterceptedException:
                        self.driver.execute_script("arguments[0].click();", btn)
                    logger.info("OneTrust: закрыт баннер cookies (%s)", selector)
                    time.sleep(0.5)
                    return
            except Exception:
                continue

        removed = self.driver.execute_script(
            """
            let n = 0;
            document.querySelectorAll(
                '#onetrust-banner-sdk, #onetrust-pc-sdk, .onetrust-pc-dark-filter'
            ).forEach((el) => { el.remove(); n++; });
            document.body.style.overflow = '';
            document.body.classList.remove('ot-ftr-stacked');
            return n;
            """
        )
        if removed:
            logger.info("OneTrust: удалён оверлей cookies (%s элементов)", removed)

    def _handle_post_captcha_transition(self) -> None:
        """После обхода капчи: проверка Waiting Room и страницы 403."""
        time.sleep(self.settings.post_403_nav_wait_sec)
        state = self.detect_page_state()
        if state == PageState.WAITING_ROOM:
            logger.info("После Challenge открылся Waiting Room — обход Turnstile")
            self._try_solve_standalone_turnstile("waiting-room")
            time.sleep(self.settings.post_403_nav_wait_sec)
            state = self.detect_page_state()
        if state == PageState.NOT_FOUND_403:
            logger.info("После капчи открылась страница 403 — «Вернуться на главную»")
            self._click_back_to_home()
            time.sleep(self.settings.post_403_nav_wait_sec)

    def _try_solve_standalone_turnstile(self, context: str) -> None:
        """Waiting Room или форма входа: sitekey из data-sitekey + RuCaptcha."""
        self._cached_turnstile_params = {}
        self._inject_turnstile_intercept()
        self._wait_for_turnstile_ready(challenge=False, label=context)

        if self._rucaptcha and self._solve_turnstile_via_rucaptcha(challenge=False):
            return

        if self._click_turnstile_in_all_frames():
            return
        click_turnstile_shadow(self.driver, context)

    def _try_click_challenge_checkbox(self) -> None:
        self._try_solve_turnstile_challenge("Cloudflare Challenge")

    def _try_solve_turnstile_challenge(self, label: str) -> None:
        """Сначала естественный клик, затем RuCaptcha (Challenge)."""
        self._cached_turnstile_params = {}

        if self._attempt_natural_captcha_pass(
            label, timeout=CHALLENGE_PASS_TIMEOUT_SEC
        ):
            return

        self._inject_turnstile_intercept()
        self._wait_for_turnstile_ready(
            challenge=True, label=label, allow_checkbox_click=False
        )

        if self._rucaptcha and self._solve_turnstile_via_rucaptcha(challenge=True):
            return

        logger.warning("Чекбокс капчи не найден — возможно, требуется ручное действие")

    def _wait_for_turnstile_ready(
        self,
        *,
        challenge: bool,
        label: str = "Turnstile",
        allow_checkbox_click: bool = True,
    ) -> None:
        kind = "Cloudflare Challenge" if challenge and label == "Turnstile" else label
        wait_sec = CHALLENGE_WIDGET_WAIT_SEC if challenge else LOGIN_TURNSTILE_WAIT_SEC
        logger.info("Ожидаю параметры %s...", kind)
        deadline = time.monotonic() + wait_sec
        last_log_at = 0.0
        while time.monotonic() < deadline:
            self._inject_turnstile_intercept()
            params = self._collect_turnstile_params(include_standalone=not challenge)
            if self._turnstile_params_complete(params, challenge=challenge):
                logger.info(
                    "Turnstile готов для RuCaptcha (%s): sitekey=%s..., action=%s, data=%s, pagedata=%s",
                    kind,
                    params["sitekey"][:12],
                    bool(params.get("action")),
                    bool(params.get("data")),
                    bool(params.get("pagedata")),
                )
                return

            if not challenge and params.get("sitekey"):
                return

            if challenge and params.get("sitekey"):
                logger.debug(
                    "Частичные параметры Challenge (жду data/pagedata): sitekey=%s...",
                    params["sitekey"][:12],
                )

            now = time.monotonic()
            if now - last_log_at >= 10:
                iframe_count = len(self.driver.find_elements(By.TAG_NAME, "iframe"))
                has_turnstile = self.driver.execute_script(
                    "return typeof window.turnstile !== 'undefined';"
                )
                has_shadow_cb = shadow_checkbox_present(self.driver)
                has_sitekey = bool(self._extract_standalone_sitekey().get("sitekey"))
                logger.info(
                    "Ожидание %s... iframe=%s, turnstile=%s, shadow_cb=%s, data-sitekey=%s",
                    kind,
                    iframe_count,
                    has_turnstile,
                    has_shadow_cb,
                    has_sitekey,
                )
                last_log_at = now

            if challenge and allow_checkbox_click and self._click_turnstile_in_all_frames():
                logger.info("Чекбокс Turnstile найден и нажат")
                return
            time.sleep(1)

        if challenge:
            logger.warning("turnstile.render не перехвачен за %s с", wait_sec)

    def _turnstile_params_complete(
        self, params: dict[str, str | None], *, challenge: bool
    ) -> bool:
        if not params.get("sitekey"):
            return False
        if challenge:
            return bool(params.get("data") and params.get("pagedata"))
        return True

    def _extract_standalone_sitekey(self) -> dict[str, str | None]:
        try:
            self.driver.switch_to.default_content()
            raw = self.driver.execute_script(
                """
                const el = document.querySelector(
                    '#turnstile-challenge, .cf-turnstile[data-sitekey], [data-sitekey]'
                );
                if (!el) return null;
                return {
                    sitekey: el.getAttribute('data-sitekey'),
                    action: el.getAttribute('data-action'),
                    callback: el.getAttribute('data-callback'),
                };
                """
            )
            if raw and raw.get("sitekey"):
                return {
                    "sitekey": raw.get("sitekey"),
                    "action": raw.get("action"),
                    "pageurl": self.driver.current_url,
                    "useragent": self.driver.execute_script(
                        "return navigator.userAgent;"
                    ),
                }
        except Exception:
            pass
        return {}

    def _inject_turnstile_intercept(self) -> None:
        try:
            self.driver.switch_to.default_content()
            self.driver.execute_script(TURNSTILE_HOOK_SCRIPT)
        except Exception:
            pass
        for path in self._enumerate_frame_paths():
            if not path:
                continue
            try:
                self._switch_to_frame_path(path)
                self.driver.execute_script(TURNSTILE_HOOK_SCRIPT)
            except Exception:
                pass
        self.driver.switch_to.default_content()

    def _collect_turnstile_params(
        self,
        *,
        include_standalone: bool = False,
        fresh_intercept_only: bool = False,
    ) -> dict[str, str | None]:
        if fresh_intercept_only:
            fresh = parse_intercepted_params_from_logs(self.driver) or {}
            if fresh.get("sitekey"):
                self._fresh_intercept_received = True
                self._cached_turnstile_params = merge_turnstile_params({}, fresh)
            return dict(self._cached_turnstile_params)

        sources = [
            parse_intercepted_params_from_logs(self.driver) or {},
            parse_turnstile_params_from_network(self.driver) or {},
            self._read_hooked_turnstile_params(),
        ]
        if include_standalone:
            sources.append(self._extract_standalone_sitekey())
            sources.append(
                extract_turnstile_params(self.driver.page_source)
            )
        fresh = merge_turnstile_params(*sources)
        self._cached_turnstile_params = merge_turnstile_params(
            self._cached_turnstile_params,
            fresh,
        )
        return dict(self._cached_turnstile_params)

    def _read_hooked_turnstile_params(self) -> dict[str, str | None]:
        empty = {"sitekey": None, "action": None, "data": None, "pagedata": None}
        for path in self._enumerate_frame_paths():
            try:
                self._switch_to_frame_path(path)
                raw = self.driver.execute_script(
                    """
                    const p = window.__cfTurnstileParams;
                    if (!p || !p.sitekey) return null;
                    return {
                        sitekey: p.sitekey,
                        action: p.action || null,
                        data: p.cData || null,
                        pagedata: p.chlPageData || null,
                    };
                    """
                )
                if raw and raw.get("sitekey"):
                    self.driver.switch_to.default_content()
                    return {
                        "sitekey": raw.get("sitekey"),
                        "action": raw.get("action"),
                        "data": raw.get("data"),
                        "pagedata": raw.get("pagedata"),
                    }
            except Exception:
                pass
        self.driver.switch_to.default_content()
        return empty

    def _inject_turnstile_token(self, token: str) -> None:
        callback_called = False
        frame_paths: list[list[int]] = []
        if self._callback_frame_path is not None:
            frame_paths.append(self._callback_frame_path)
        for path in self._enumerate_frame_paths():
            if path not in frame_paths:
                frame_paths.append(path)

        for path in frame_paths:
            try:
                self._switch_to_frame_path(path)
                called = self.driver.execute_script(
                    """
                    const token = arguments[0];
                    if (typeof window.cfCallback === 'function') {
                        window.cfCallback(token);
                        return true;
                    }
                    const hooked = window.__cfTurnstileParams;
                    if (hooked && typeof hooked.callback === 'function') {
                        hooked.callback(token);
                        return true;
                    }
                    return false;
                    """,
                    token,
                )
                if called:
                    callback_called = True
            except Exception:
                pass

        self.driver.switch_to.default_content()
        self.driver.execute_script(
            """
            const token = arguments[0];
            if (typeof window.wrtsc === 'function') {
                window.wrtsc(token);
                return;
            }
            if (typeof window.cfCallback === 'function') {
                window.cfCallback(token);
                return;
            }
            document.querySelectorAll(
                'app-cloudflare-captcha-container input[name="cf-turnstile-response"], '
                + 'app-cloudflare-captcha-container input[id$="_response"], '
                + '#turnstile-challenge, .cf-turnstile[data-sitekey], [data-sitekey]'
            ).forEach((el) => {
                if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                    el.value = token;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
                const cbName = el.getAttribute && el.getAttribute('data-callback');
                if (cbName && typeof window[cbName] === 'function') {
                    window[cbName](token);
                }
            });
            const selectors = [
                'input[name="cf-turnstile-response"]',
                'textarea[name="cf-turnstile-response"]',
            ];
            for (const sel of selectors) {
                document.querySelectorAll(sel).forEach((el) => {
                    el.value = token;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                });
            }
            """,
            token,
        )

    def _solve_turnstile_via_rucaptcha(
        self, *, challenge: bool = True, login_form: bool = False
    ) -> bool:
        assert self._rucaptcha is not None
        if login_form:
            self._cached_turnstile_params = merge_turnstile_params(
                self._cached_turnstile_params,
                self._extract_login_form_turnstile_params(),
            )
        elif not challenge:
            self._cached_turnstile_params = merge_turnstile_params(
                self._cached_turnstile_params,
                self._collect_turnstile_params(include_standalone=True),
            )
        params = dict(self._cached_turnstile_params)
        sitekey = params.get("sitekey")
        if not sitekey:
            logger.warning("RuCaptcha: sitekey не найден")
            return False
        if challenge and (not params.get("data") or not params.get("pagedata")):
            logger.warning(
                "RuCaptcha: неполные параметры Challenge (data=%s, pagedata=%s)",
                bool(params.get("data")),
                bool(params.get("pagedata")),
            )
            return False

        pageurl = params.get("pageurl") or self.driver.current_url
        useragent = params.get("useragent") or self.driver.execute_script(
            "return navigator.userAgent;"
        )
        kind = "Cloudflare Challenge" if challenge else "standalone Turnstile"
        logger.info(
            "RuCaptcha: отправляю %s (sitekey=%s..., action=%s, data=%s, pagedata=%s)",
            kind,
            sitekey[:12],
            bool(params.get("action")),
            bool(params.get("data")),
            bool(params.get("pagedata")),
        )
        try:
            token, response_ua = self._rucaptcha.solve_turnstile(
                sitekey=sitekey,
                pageurl=pageurl,
                action=params.get("action"),
                data=params.get("data") if challenge else None,
                pagedata=params.get("pagedata") if challenge else None,
                useragent=useragent,
            )
        except RuCaptchaError as exc:
            logger.error("RuCaptcha: не удалось решить капчу: %s", exc)
            return False

        if response_ua:
            self.driver.execute_cdp_cmd(
                "Network.setUserAgentOverride",
                {"userAgent": response_ua},
            )

        self._inject_turnstile_token(token)
        self._last_turnstile_token = token
        if login_form:
            self._finalize_login_captcha_token()
        time.sleep(3 if challenge else 2)
        logger.info("RuCaptcha: токен применён (%s)", kind)
        return True

    def _click_turnstile_in_all_frames(self) -> bool:
        self.driver.switch_to.default_content()
        for path in self._enumerate_frame_paths():
            try:
                self._switch_to_frame_path(path)
                if self._click_turnstile_in_current_context(f"frame{path}"):
                    self.driver.switch_to.default_content()
                    return True
            except Exception as exc:
                logger.debug("Ошибка в frame %s: %s", path, exc)
            finally:
                self.driver.switch_to.default_content()
        return False

    def _enumerate_frame_paths(self, max_depth: int = 6) -> list[list[int]]:
        paths: list[list[int]] = [[]]

        def walk(path: list[int], depth: int) -> None:
            if depth >= max_depth:
                return
            self.driver.switch_to.default_content()
            self._switch_to_frame_path(path)
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            for index in range(len(iframes)):
                child = path + [index]
                paths.append(child)
                walk(child, depth + 1)

        walk([], 0)
        return paths

    def _switch_to_frame_path(self, path: list[int]) -> None:
        self.driver.switch_to.default_content()
        for index in path:
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            if index >= len(iframes):
                raise IndexError(f"iframe index {index} not found")
            self.driver.switch_to.frame(iframes[index])

    def _collect_page_source_for_turnstile(self) -> str:
        parts = [self.driver.page_source]
        for iframe in self.driver.find_elements(By.CSS_SELECTOR, "iframe"):
            try:
                src = iframe.get_attribute("src") or ""
                outer = iframe.get_attribute("outerHTML") or ""
                parts.append(src)
                parts.append(outer)
                self.driver.switch_to.default_content()
                self.driver.switch_to.frame(iframe)
                parts.append(self.driver.page_source)
            except Exception:
                pass
            finally:
                self.driver.switch_to.default_content()
        return "\n".join(parts)

    def _click_turnstile_in_frames(self) -> bool:
        return self._click_turnstile_in_all_frames()

    def _click_turnstile_in_current_context(self, context: str) -> bool:
        if click_turnstile_shadow(self.driver, context):
            time.sleep(1)
            return True

        for css in TURNSTILE_LABEL_SELECTORS:
            labels = self.driver.find_elements(By.CSS_SELECTOR, css)
            for label in labels:
                if self._click_turnstile_label(label, context, css):
                    return True

        try:
            labels = self.driver.find_elements(By.XPATH, TURNSTILE_LABEL_XPATH)
            for label in labels:
                if self._click_turnstile_label(label, context, "xpath"):
                    return True
        except (NoSuchElementException, StaleElementReferenceException):
            pass

        for css in TURNSTILE_CHECKBOX_SELECTORS:
            inputs = self.driver.find_elements(By.CSS_SELECTOR, css)
            for checkbox in inputs:
                if self._click_turnstile_checkbox(checkbox, context, css):
                    return True
        return False

    def _click_turnstile_label(self, label, context: str, selector: str) -> bool:
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", label
            )
            human_pause(0.25, 0.7)
            try:
                (
                    ActionChains(self.driver)
                    .move_to_element(label)
                    .pause(random.uniform(0.15, 0.4))
                    .click()
                    .perform()
                )
            except (ElementNotInteractableException, ElementClickInterceptedException):
                self.driver.execute_script("arguments[0].click();", label)
            logger.info("Клик по label Turnstile (%s, %s)", context, selector)
            human_pause(0.4, 0.9)
            return True
        except StaleElementReferenceException:
            return False

    def _click_turnstile_checkbox(self, checkbox, context: str, selector: str) -> bool:
        try:
            label = checkbox.find_element(By.XPATH, "./ancestor::label[1]")
            return self._click_turnstile_label(label, context, f"{selector} -> label")
        except NoSuchElementException:
            pass
        try:
            if not checkbox.is_enabled():
                return False
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", checkbox
            )
            self.driver.execute_script("arguments[0].click();", checkbox)
            logger.info("Клик по input Turnstile через JS (%s, %s)", context, selector)
            time.sleep(0.5)
            return True
        except (StaleElementReferenceException, ElementNotInteractableException):
            return False

    def _safe_click(self, element) -> None:
        self._dismiss_cookie_banner()
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                element,
            )
            element.click()
        except ElementClickInterceptedException:
            self._dismiss_cookie_banner()
            try:
                element.click()
            except ElementClickInterceptedException:
                self.driver.execute_script("arguments[0].click();", element)

    def _element_exists(self, by: By, value: str) -> bool:
        try:
            elements = self.driver.find_elements(by, value)
            return any(el.is_displayed() for el in elements)
        except Exception:
            return False

    def _wait_for_clickable(self, by: By, value: str):
        return self._wait.until(EC.element_to_be_clickable((by, value)))
