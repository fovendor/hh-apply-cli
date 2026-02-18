import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests
import webview
from webview import WebViewException

from hhcli.database import (
    delete_profile,
    get_last_sync_timestamp,
    load_profile,
    log_http_metric,
    log_oauth_event,
    log_to_db,
    save_or_update_profile,
    set_last_sync_timestamp,
    upsert_negotiation_history,
)
from .constants import ApiErrorReason, LogSource
from .mimicry import (
    ANDROID_CLIENT_ID,
    ANDROID_CLIENT_SECRET,
    REDIRECT_URI,
    android_user_agent,
    sleep_human_delay,
)

API_BASE_URL = "https://api.hh.ru"
OAUTH_URL = "https://hh.ru/oauth"


class AuthorizationPending(RuntimeError):
    """Сигнализирует о необходимости повторной аутентификации."""


class CaptchaRequired(AuthorizationPending):
    """Сигнализирует, что hh.ru требует пройти капчу в webview."""


class HHApiClient:
    """Клиент для API hh.ru, мимикрирующий под Android-приложение."""

    RETRY_ATTEMPTS = 3
    RETRY_BACKOFF_SECONDS = 1.0
    MIN_DELAY = 0.3
    MAX_DELAY = 1.0
    CAPTCHA_WINDOW_BOOTSTRAP_URL = "about:blank"
    CAPTCHA_MEDIA_GUARD_JS = """
(() => {
  const denied = () => Promise.reject(new Error('Media access disabled by hhcli'));
  const mediaStub = {
    getUserMedia: denied,
    getDisplayMedia: denied,
    enumerateDevices: () => Promise.resolve([]),
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false
  };
  try {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      enumerable: true,
      value: mediaStub
    });
  } catch (_err) {
    try { navigator.mediaDevices = mediaStub; } catch (_inner) {}
    if (navigator.mediaDevices) {
      try { navigator.mediaDevices.getUserMedia = denied; } catch (_inner) {}
      try { navigator.mediaDevices.getDisplayMedia = denied; } catch (_inner) {}
      try { navigator.mediaDevices.enumerateDevices = () => Promise.resolve([]); } catch (_inner) {}
    }
  }
})();
"""

    def __init__(self):
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.token_expires_at: datetime | None = None
        self.profile_name: str | None = None
        self._auth_lock = threading.Lock()
        self._auth_in_progress = False
        self._last_auth_url: str | None = None
        self._last_request_ts = time.monotonic()
        self._preferred_gui = self._detect_preferred_gui()
        self._captcha_flow_runner: Callable[[str], bool] | None = None

        self.session = requests.Session()
        self.session.headers.update(
            {
                "user-agent": android_user_agent(),
                "x-hh-app-active": "true",
            }
        )

    def set_captcha_flow_runner(self, runner: Callable[[str], bool] | None) -> None:
        """Регистрирует внешний раннер капчи (например, запуск в main-thread UI)."""
        self._captcha_flow_runner = runner

    def load_profile_data(self, profile_name: str):
        profile_data = load_profile(profile_name)
        if not profile_data:
            raise ValueError(f"Профиль '{profile_name}' не найден.")
        self.profile_name = profile_data["profile_name"]
        self.access_token = profile_data["access_token"]
        self.refresh_token = profile_data["refresh_token"]
        self.token_expires_at = profile_data["expires_at"]

    def is_authenticated(self) -> bool:
        return bool(self.access_token) and bool(self.token_expires_at) and (
            self.token_expires_at > datetime.now()
        )

    def start_authorization_flow(self, *, reason: str | None = None) -> None:
        """Запускает авторизацию через pywebview (только из главного потока)."""
        if not self.profile_name:
            raise AuthorizationPending(
                "Профиль не загружен, авторизация невозможна."
            )

        with self._auth_lock:
            if self._auth_in_progress:
                log_to_db(
                    "INFO",
                    LogSource.OAUTH,
                    f"Повторный запрос авторизации для профиля '{self.profile_name}'.",
                )
                return
            self._auth_in_progress = True

        log_details = f"Причина: {reason}" if reason else "Причина не указана."
        log_to_db(
            "INFO",
            LogSource.OAUTH,
            f"Запускаю авторизацию через pywebview для '{self.profile_name}'. {log_details}",
        )
        try:
            success = self.authorize(self.profile_name)
            if success:
                log_to_db(
                    "INFO",
                    LogSource.OAUTH,
                    f"Авторизация профиля '{self.profile_name}' завершена.",
                )
            else:
                log_to_db(
                    "ERROR",
                    LogSource.OAUTH,
                    f"Авторизация профиля '{self.profile_name}' завершилась с ошибкой.",
                )
        except WebViewException as exc:
            msg = self._format_webview_dependency_message(exc)
            log_to_db("ERROR", LogSource.OAUTH, f"{msg} Детали: {exc}")
            raise RuntimeError(msg) from exc
        except Exception as exc:  # noqa: BLE001
            log_to_db(
                "ERROR",
                LogSource.OAUTH,
                f"Исключение внутри авторизации профиля '{self.profile_name}': {exc}",
            )
            raise
        finally:
            with self._auth_lock:
                self._auth_in_progress = False

    def ensure_active_token(self) -> None:
        """Проверяет токен и при необходимости инициирует повторную авторизацию."""
        if self.is_authenticated():
            return
        try:
            self._refresh_token()
        except AuthorizationPending:
            raise
        except Exception as exc:  # noqa: BLE001
            log_to_db(
                "ERROR",
                LogSource.API_CLIENT,
                f"Не удалось обновить токен для '{self.profile_name}': {exc}",
            )
            self.start_authorization_flow(reason="refresh_failed")
            raise AuthorizationPending(
                "Срок действия токена истёк. Открыто окно авторизации."
            ) from exc

    def _save_token(self, token_data: dict, user_info: dict):
        expires_in = token_data.get("expires_in", 3600)
        expires_at = datetime.now() + timedelta(seconds=expires_in)
        save_or_update_profile(
            self.profile_name, user_info, token_data, expires_at
        )
        self.access_token = token_data["access_token"]
        self.refresh_token = token_data["refresh_token"]
        self.token_expires_at = expires_at

    def _refresh_token(self):
        if not self.refresh_token:
            msg = (
                f"Нет refresh_token для обновления профиля '{self.profile_name}'. "
                "Запускаю переавторизацию."
            )
            log_to_db("ERROR", LogSource.API_CLIENT, msg)
            self.start_authorization_flow(reason="missing_refresh_token")
            raise AuthorizationPending(
                "Не найден refresh_token. Открыта страница авторизации."
            )
        log_to_db(
            "INFO",
            LogSource.API_CLIENT,
            f"Токен для профиля '{self.profile_name}' истек, обновляю...",
        )
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": ANDROID_CLIENT_ID,
            "client_secret": ANDROID_CLIENT_SECRET,
        }
        self._last_request_ts = sleep_human_delay(self._last_request_ts)
        started = time.monotonic()
        response = self.session.post(f"{OAUTH_URL}/token", data=payload)
        log_http_metric(
            "POST",
            "/oauth/token",
            getattr(response, "status_code", None),
            int((time.monotonic() - started) * 1000),
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as e:  # noqa: PERF203
            log_to_db(
                "ERROR",
                LogSource.API_CLIENT,
                f"Ошибка обновления токена: {e.response.text if e.response is not None else e}",
            )
            error_details: dict[str, Any] = {}
            try:
                error_details = e.response.json() if e.response is not None else {}
            except Exception:  # noqa: BLE001
                pass
            if (
                e.response is not None
                and e.response.status_code in (400, 401)
                and error_details.get("error") == "invalid_grant"
            ):
                log_oauth_event(self.profile_name, "refresh_failed", "invalid_grant")
                self.start_authorization_flow(reason="invalid_grant")
                raise AuthorizationPending(
                    "Не удалось обновить токен. "
                    "Откройте окно авторизации."
                ) from e
            raise
        new_token_data = response.json()
        user_info = load_profile(self.profile_name)
        self._save_token(new_token_data, user_info)
        log_to_db("INFO", LogSource.API_CLIENT, "Токен успешно обновлен.")
        log_oauth_event(self.profile_name, "refresh_success")

    def authorize(self, profile_name: str) -> bool:
        """Полный OAuth-поток через pywebview с мобильными кредами."""
        self.profile_name = profile_name

        auth_url = (
            f"{OAUTH_URL}/authorize?response_type=code"
            f"&client_id={ANDROID_CLIENT_ID}"
            f"&redirect_uri={REDIRECT_URI}"
        )
        self._last_auth_url = auth_url
        log_oauth_event(self.profile_name, "start_auth", auth_url)
        code_holder: dict[str, str] = {}
        event = threading.Event()
        nav_hook_installed = False

        def _try_capture(url: str) -> bool:
            """Читает code из redirect_uri; True если удалось поймать и обработать."""
            if event.is_set() or not url:
                return False
            if not url.startswith(REDIRECT_URI):
                return False
            parsed = urlsplit(url)
            code = parse_qs(parsed.query).get("code", [None])[0]
            if not code:
                return False
            code_holder["code"] = code
            event.set()
            log_oauth_event(self.profile_name, "code_received")
            try:
                window.destroy()
            except Exception:
                pass
            return True

        def handle_loaded(*_args):
            if event.is_set():
                return
            try:
                current_url = window.get_current_url() or ""
            except Exception:
                return
            _try_capture(current_url)
            _ensure_navigation_hook()

        def _ensure_navigation_hook():
            nonlocal nav_hook_installed
            if nav_hook_installed:
                return
            try:
                native = getattr(window, "native", None)
                webview_obj = None
                if native and hasattr(native, "webview"):
                    webview_obj = native.webview
                elif native and hasattr(native, "browser") and hasattr(native.browser, "webview"):
                    webview_obj = native.browser.webview
                if not webview_obj:
                    return

                def _nav_handler(sender, args):
                    url = str(getattr(args, "Uri", "") or getattr(args, "uri", "") or "")
                    if _try_capture(url):
                        try:
                            args.Cancel = True
                        except Exception:
                            pass

                if hasattr(webview_obj, "NavigationStarting"):
                    webview_obj.NavigationStarting += _nav_handler
                    nav_hook_installed = True
                    log_oauth_event(self.profile_name, "nav_hook_installed", "NavigationStarting")
                    return

                core = getattr(webview_obj, "CoreWebView2", None)
                if core and hasattr(core, "NavigationStarting"):
                    core.NavigationStarting += _nav_handler
                    nav_hook_installed = True
                    log_oauth_event(self.profile_name, "nav_hook_installed", "CoreWebView2.NavigationStarting")
            except Exception:
                pass

        webview_kwargs: dict[str, str] = {}
        if self._preferred_gui:
            webview_kwargs["gui"] = self._preferred_gui

        window = webview.create_window(
            "Авторизация hh.ru",
            url=auth_url,
            width=480,
            height=800,
            resizable=True,
        )
        window.events.loaded += handle_loaded

        # WebView2 (Windows) блокирует переход на кастомные схемы и не грузит страницу,
        # поэтому пытаемся зацепиться за системное событие NavigationStarting (Edge).
        _ensure_navigation_hook()

        def watch_redirect():
            while not event.is_set():
                _ensure_navigation_hook()
                try:
                    current_url = window.get_current_url() or ""
                except Exception:
                    break
                if current_url.startswith(REDIRECT_URI):
                    handle_loaded()
                    break
                time.sleep(0.2)

        webview.start(func=watch_redirect, debug=False, **webview_kwargs)

        if not event.is_set():
            log_to_db(
                "ERROR",
                LogSource.OAUTH,
                f"Код авторизации не получен для профиля '{profile_name}'.",
            )
            log_oauth_event(self.profile_name, "auth_failed", "no_code")
            return False

        payload = {
            "grant_type": "authorization_code",
            "code": code_holder["code"],
            "client_id": ANDROID_CLIENT_ID,
            "client_secret": ANDROID_CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
        }

        self._last_request_ts = sleep_human_delay(self._last_request_ts)
        started = time.monotonic()
        response = self.session.post(f"{OAUTH_URL}/token", data=payload)
        log_http_metric(
            "POST",
            "/oauth/token",
            getattr(response, "status_code", None),
            int((time.monotonic() - started) * 1000),
        )
        response.raise_for_status()
        token_data = response.json()

        headers = {"Authorization": f"Bearer {token_data['access_token']}"}
        self._last_request_ts = sleep_human_delay(self._last_request_ts)
        started = time.monotonic()
        user_info_resp = self.session.get(f"{API_BASE_URL}/me", headers=headers)
        log_http_metric(
            "GET",
            "/me",
            getattr(user_info_resp, "status_code", None),
            int((time.monotonic() - started) * 1000),
        )
        user_info_resp.raise_for_status()

        self._save_token(token_data, user_info_resp.json())
        log_oauth_event(self.profile_name, "auth_success")
        return True

    @staticmethod
    def _detect_preferred_gui() -> str | None:
        if sys.platform.startswith("linux"):
            # Принудительно используем WebKit2GTK и не даём откатиться на Qt/PyQt.
            return "gtk"
        if sys.platform.startswith("win"):
            # Принудительно используем WebView2 и не даём откатиться на IE (mshtml),
            # иначе страница hh.ru может не отрабатывать.
            return "edgechromium"
        return None

    @staticmethod
    def _format_webview_dependency_message(exc: Exception) -> str:
        if sys.platform.startswith("win"):
            return (
                "pywebview не смог инициализировать WebView2. "
                "Установите Microsoft Edge WebView2 Runtime: "
                "https://developer.microsoft.com/microsoft-edge/webview2/ "
                "и перезапустите приложение."
            )
        if sys.platform.startswith("linux"):
            return (
                "pywebview не смог инициализировать GUI. "
                "Установите WebKit2GTK и разрешите pipx использовать системные пакеты: "
                "`sudo apt install python3-gi gir1.2-webkit2-4.1 gir1.2-gtk-3.0 libwebkit2gtk-4.1-0` "
                "затем `pipx install hhcli --force --system-site-packages`."
            )
        return (
            "pywebview не смог инициализировать GUI. "
            "Убедитесь, что установлены зависимости веб-движка для вашей ОС."
        )

    @staticmethod
    def _extract_captcha_url(response: requests.Response | None) -> str | None:
        """Извлекает captcha_url из ответа hh API с ошибкой captcha_required."""
        if response is None or response.status_code != 403:
            return None
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return None
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if not isinstance(errors, list):
            return None
        for item in errors:
            if not isinstance(item, dict):
                continue
            reason = str(item.get("value") or item.get("type") or "").strip().lower()
            if reason != "captcha_required":
                continue
            captcha_url = str(item.get("captcha_url") or "").strip()
            return captcha_url or None
        return None

    def start_captcha_flow(self, captcha_url: str) -> bool:
        """Открывает капчу hh.ru в pywebview (тот же механизм, что OAuth-форма)."""
        target_url = str(captcha_url or "").strip()
        if not target_url:
            return False

        solved = {"ok": False}
        done = threading.Event()
        state = {"captcha_seen": False, "navigation_started": False}
        webview_kwargs: dict[str, str] = {}
        if self._preferred_gui:
            webview_kwargs["gui"] = self._preferred_gui

        window = webview.create_window(
            "Проверка hh.ru (капча)",
            url=self.CAPTCHA_WINDOW_BOOTSTRAP_URL,
            width=480,
            height=800,
            resizable=True,
        )

        def _check_captcha_state() -> None:
            if done.is_set():
                return
            try:
                current_url = str(window.get_current_url() or "")
            except Exception:
                return
            if not current_url:
                return
            normalized_url = current_url.lower()
            if "/account/captcha" in normalized_url:
                state["captcha_seen"] = True
                return
            if not state["captcha_seen"]:
                return
            # После посещения /account/captcha переход на любую другую страницу hh.ru
            # считаем успешным прохождением проверки.
            if "hh.ru" not in normalized_url:
                return
            if normalized_url.startswith(self.CAPTCHA_WINDOW_BOOTSTRAP_URL):
                return
            if normalized_url.startswith("about:"):
                return
            if "/account/captcha" not in normalized_url:
                solved["ok"] = True
                done.set()
                try:
                    window.destroy()
                except Exception:
                    pass

        def _on_loaded(*_args) -> None:
            self._inject_captcha_media_guards_js(window)
            if not state["navigation_started"]:
                return
            _check_captcha_state()

        def _on_closed(*_args) -> None:
            done.set()

        window.events.loaded += _on_loaded
        window.events.closed += _on_closed

        def _watch_captcha() -> None:
            # Даем backend pywebview время создать native webview-объект, чтобы
            # включить запрет media-пермиссий до загрузки страницы капчи.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if self._apply_captcha_media_guards(window):
                    break
                time.sleep(0.05)
            try:
                window.load_url(target_url)
                state["navigation_started"] = True
            except Exception:
                done.set()
                return
            while not done.is_set():
                _check_captcha_state()
                time.sleep(0.2)

        webview.start(func=_watch_captcha, debug=False, **webview_kwargs)
        return bool(solved["ok"])

    def _run_captcha_flow(self, captcha_url: str) -> bool:
        """Запускает капчу через внешний раннер (если задан), иначе локально."""
        runner = self._captcha_flow_runner
        if runner is not None:
            return bool(runner(captcha_url))
        return bool(self.start_captcha_flow(captcha_url))

    def _apply_captcha_media_guards(self, window) -> bool:
        """Пытается запретить media permissions в нативном webview окна капчи."""
        # Linux / GTK (актуально для webkit2): отключаем media stream и
        # отклоняем любые permission-request.
        if sys.platform.startswith("linux"):
            try:
                from webview.platforms import gtk as gtk_platform

                browser_view = gtk_platform.BrowserView.instances.get(window.uid)
                if not browser_view:
                    return False
                native_webview = getattr(browser_view, "webview", None)
                if native_webview is None:
                    return False

                settings_props = native_webview.get_settings().props
                if hasattr(settings_props, "enable_media_stream"):
                    settings_props.enable_media_stream = False
                if hasattr(settings_props, "enable_webaudio"):
                    settings_props.enable_webaudio = False

                if not getattr(window, "_hhcli_media_permission_hooked", False):
                    def _deny_permission(_wv, request):
                        try:
                            request.deny()
                        except Exception:
                            pass
                        return True

                    native_webview.connect("permission-request", _deny_permission)
                    setattr(window, "_hhcli_media_permission_hooked", True)
                return True
            except Exception:
                return False
        # Windows / WebView2: отклоняем camera/microphone/geolocation запросы.
        if sys.platform.startswith("win"):
            try:
                from webview.platforms import winforms as winforms_platform

                browser_form = winforms_platform.BrowserView.instances.get(window.uid)
                if not browser_form:
                    return False
                edge_browser = getattr(browser_form, "browser", None)
                web_view = getattr(edge_browser, "web_view", None)
                if web_view is None:
                    return False

                def _deny_permission(_sender, args):
                    kind = str(getattr(args, "PermissionKind", "")).lower()
                    if not any(token in kind for token in ("camera", "microphone", "geo")):
                        return
                    try:
                        state_obj = getattr(args, "State", None)
                        deny_state = getattr(type(state_obj), "Deny", None)
                        if deny_state is not None:
                            args.State = deny_state
                    except Exception:
                        pass
                    try:
                        args.Handled = True
                    except Exception:
                        pass

                if not getattr(window, "_hhcli_media_permission_hooked", False):
                    core = getattr(web_view, "CoreWebView2", None)
                    if core is not None and hasattr(core, "PermissionRequested"):
                        core.PermissionRequested += _deny_permission
                    if hasattr(web_view, "CoreWebView2InitializationCompleted"):
                        def _on_init(sender, init_args):
                            try:
                                if getattr(init_args, "IsSuccess", True):
                                    sender.CoreWebView2.PermissionRequested += _deny_permission
                            except Exception:
                                pass

                        web_view.CoreWebView2InitializationCompleted += _on_init
                    setattr(window, "_hhcli_media_permission_hooked", True)
                return True
            except Exception:
                return False
        return False

    def _inject_captcha_media_guards_js(self, window) -> None:
        """JS fallback: блокируем media API внутри страницы капчи."""
        try:
            window.evaluate_js(self.CAPTCHA_MEDIA_GUARD_JS)
        except Exception:
            pass

    def _request(self, method: str, endpoint: str, **kwargs):
        try:
            self.ensure_active_token()
        except AuthorizationPending as pending:
            log_to_db("ERROR", LogSource.API_CLIENT, str(pending))
            raise

        headers = kwargs.setdefault("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        url = f"{API_BASE_URL}{endpoint}"
        attempt = 0
        captcha_attempted = False
        while True:
            self._last_request_ts = sleep_human_delay(self._last_request_ts)
            try:
                started = time.monotonic()
                response = self.session.request(method, url, **kwargs)
                log_http_metric(
                    method,
                    endpoint,
                    getattr(response, "status_code", None),
                    int((time.monotonic() - started) * 1000),
                )
                response.raise_for_status()
                if response.status_code in (201, 204):
                    return None
                return response.json()
            except requests.HTTPError as e:
                captcha_url = self._extract_captcha_url(e.response)
                if captcha_url:
                    if captcha_attempted:
                        log_to_db(
                            "ERROR",
                            LogSource.API_CLIENT,
                            f"Капча hh.ru не снята после повтора {method} {endpoint}.",
                        )
                        raise CaptchaRequired(
                            "hh.ru требует капчу. Пройдите проверку и повторите запрос."
                        ) from e
                    captcha_attempted = True
                    log_to_db(
                        "WARN",
                        LogSource.API_CLIENT,
                        f"HH API запросил капчу для {method} {endpoint}. Открываю окно проверки.",
                    )
                    try:
                        solved = self._run_captcha_flow(captcha_url)
                    except WebViewException as exc:
                        msg = self._format_webview_dependency_message(exc)
                        log_to_db(
                            "ERROR",
                            LogSource.API_CLIENT,
                            f"Не удалось открыть окно капчи: {msg} Детали: {exc}",
                        )
                        raise CaptchaRequired(
                            "HH API требует капчу, но окно проверки не удалось открыть."
                        ) from exc
                    except Exception as exc:  # noqa: BLE001
                        log_to_db(
                            "ERROR",
                            LogSource.API_CLIENT,
                            f"Не удалось завершить капчу для {method} {endpoint}: {exc}",
                        )
                        raise CaptchaRequired(
                            "HH API требует капчу, но окно проверки завершилось ошибкой."
                        ) from exc
                    if not solved:
                        raise CaptchaRequired(
                            "Проверка hh.ru не завершена. Пройдите капчу и повторите запрос."
                        ) from e
                    # Повторяем исходный запрос после успешной капчи.
                    continue
                if e.response is not None and e.response.status_code == 401:
                    log_to_db(
                        "WARN",
                        LogSource.API_CLIENT,
                        f"Получен 401 Unauthorized для {endpoint}. "
                        "Повторная попытка после обновления токена.",
                    )
                    try:
                        self.ensure_active_token()
                        headers["Authorization"] = f"Bearer {self.access_token}"
                        self._last_request_ts = sleep_human_delay(
                            self._last_request_ts
                        )
                        started = time.monotonic()
                        response = self.session.request(method, url, **kwargs)
                        log_http_metric(
                            method,
                            endpoint,
                            getattr(response, "status_code", None),
                            int((time.monotonic() - started) * 1000),
                        )
                        response.raise_for_status()
                        if response.status_code in (201, 204):
                            return None
                        return response.json()
                    except AuthorizationPending:
                        raise
                    except Exception as refresh_e:  # noqa: BLE001
                        msg = (
                            "Повторная попытка обновления токена не удалась. "
                            f"Ошибка: {refresh_e}"
                        )
                        log_to_db("ERROR", LogSource.API_CLIENT, msg)
                        raise ConnectionError(
                            "Не удалось обновить токен. "
                            "Попробуйте пере-авторизоваться."
                        ) from refresh_e
                log_to_db(
                    "ERROR",
                    LogSource.API_CLIENT,
                    f"HTTP ошибка для {method} {endpoint}: "
                    f"{e.response.status_code if e.response is not None else 'n/a'} "
                    f"{e.response.text if e.response is not None else ''}",
                )
                raise e
            except requests.RequestException as e:
                log_http_metric(method, endpoint, None, 0)
                attempt += 1
                if attempt > self.RETRY_ATTEMPTS:
                    msg = (
                        "Ошибка соединения с hh.ru. "
                        "Проверьте подключение или повторите попытку позже."
                    )
                    log_to_db("ERROR", LogSource.API_CLIENT, f"{msg} Детали: {e}")
                    raise ConnectionError(msg) from e
                delay = self.RETRY_BACKOFF_SECONDS * attempt
                log_to_db(
                    "WARN",
                    LogSource.API_CLIENT,
                    f"Сетевая ошибка для {method} {endpoint} (попытка "
                    f"{attempt}/{self.RETRY_ATTEMPTS}). Повтор через {delay:.1f}с. "
                    f"Детали: {e}",
                )
                time.sleep(delay)

    def get_my_resumes(self):
        return self._request("GET", "/resumes/mine")

    def get_similar_vacancies(
        self, resume_id: str, page: int = 0, per_page: int = 50
    ):
        params = {"page": page, "per_page": per_page}
        data = self._request(
            "GET", f"/resumes/{resume_id}/similar_vacancies", params=params
        )
        data.setdefault("pages", data.get("found", 0) // per_page + 1)
        return data

    def search_vacancies(self, config: dict, page: int = 0, per_page: int = 50):
        """Выполняет поиск вакансий по конфигурации профиля и возвращает ответ API."""
        positive_keywords = config.get("text_include", [])
        positive_str = " OR ".join(f'"{kw}"' for kw in positive_keywords)

        negative_keywords = config.get("negative", [])
        negative_str = " OR ".join(f'"{kw}"' for kw in negative_keywords)

        text_query = ""
        if positive_str:
            text_query = f"({positive_str})"

        if negative_str:
            if text_query:
                text_query += f" NOT ({negative_str})"
            else:
                text_query = f"NOT ({negative_str})"

        params = {
            "text": text_query,
            "area": config.get("area_id"),
            "professional_role": config.get("role_ids_config", []),
            "search_field": config.get("search_field"),
            "period": config.get("period"),
            "order_by": "publication_time",
            "page": page,
            "per_page": per_page,
        }

        if config.get("work_format") and config["work_format"] != "ANY":
            params["work_format"] = config["work_format"]

        params = {k: v for k, v in params.items() if v}

        return self._request("GET", "/vacancies", params=params)

    def get_vacancy_details(self, vacancy_id: str):
        return self._request("GET", f"/vacancies/{vacancy_id}")

    def get_vacancy_stats(self, vacancy_id: str):
        params = {
            "with_responses_count": "true",
            "with_online_users_count": "true",
            "increment_views_counter": "true",
            "with_chat_info": "true",
        }
        return self._request("GET", f"/vacancies/{vacancy_id}", params=params)

    def get_resume_details(self, resume_id: str):
        params = {"with_professional_roles": "true", "with_creds": "true"}
        return self._request("GET", f"/resumes/{resume_id}", params=params)

    def publish_resume(self, resume_id: str, *, hhtm_source: str | None = "resume_renewal") -> None:
        params = {"with_professional_roles": "true"}
        if hhtm_source:
            params["hhtmSource"] = hhtm_source
        self._request("POST", f"/resumes/{resume_id}/publish", params=params)

    def get_dictionaries(self):
        """Запрашивает общие справочники hh.ru."""
        log_to_db("INFO", LogSource.API_CLIENT, "Запрос общих справочников...")
        return self._request("GET", "/dictionaries")

    def get_areas(self):
        """Запрашивает полный список регионов hh.ru."""
        log_to_db("INFO", LogSource.API_CLIENT, "Запрос справочника регионов...")
        return self._request("GET", "/areas")

    def get_professional_roles(self):
        """Запрашивает справочник профессиональных ролей hh.ru."""
        log_to_db(
            "INFO", LogSource.API_CLIENT, "Запрос справочника профессиональных ролей..."
        )
        return self._request("GET", "/professional_roles")

    def sync_negotiation_history(self):
        log_to_db(
            "INFO",
            LogSource.SYNC_ENGINE,
            f"Запуск синхронизации истории откликов "
            f"для профиля '{self.profile_name}'.",
        )
        last_sync = get_last_sync_timestamp(self.profile_name)
        params = {"order_by": "updated_at", "per_page": 100}
        if last_sync:
            params["date_from"] = last_sync.isoformat()
            log_to_db(
                "INFO",
                LogSource.SYNC_ENGINE,
                f"Найдена последняя синхронизация: {last_sync}. "
                f"Загружаем обновления.",
            )
        all_items = []
        page = 0
        while True:
            params["page"] = page
            try:
                log_to_db(
                    "INFO",
                    LogSource.SYNC_ENGINE,
                    f"Запрос страницы {page} истории откликов...",
                )
                data = self._request("GET", "/negotiations", params=params)
                items = data.get("items", [])
                all_items.extend(items)
                if page >= data.get("pages", 0) - 1:
                    break
                page += 1
            except requests.HTTPError as e:
                log_to_db(
                    "ERROR",
                    LogSource.SYNC_ENGINE,
                    f"Ошибка при загрузке истории откликов: {e}",
                )
                return
        if all_items:
            log_to_db(
                "INFO",
                LogSource.SYNC_ENGINE,
                f"Получено {len(all_items)} обновленных записей. "
                f"Сохранение в БД...",
            )
            upsert_negotiation_history(all_items, self.profile_name)
            log_to_db("INFO", LogSource.SYNC_ENGINE, "Сохранение завершено.")
        else:
            log_to_db(
                "INFO",
                LogSource.SYNC_ENGINE,
                "Новых обновлений в истории откликов не найдено.",
            )
        set_last_sync_timestamp(self.profile_name, datetime.now())
        log_to_db("INFO", LogSource.SYNC_ENGINE, "Синхронизация успешно завершена.")

    def apply_to_vacancy(
        self, resume_id: str, vacancy_id: str, message: str = ""
    ) -> tuple[bool, str]:
        payload = {
            "resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "message": message,
        }
        try:
            self._request("POST", "/negotiations", data=payload)
            log_to_db(
                "INFO",
                LogSource.API_CLIENT,
                f"Успешный отклик на вакансию {vacancy_id} "
                f"с резюме {resume_id}.",
            )
            return True, ApiErrorReason.APPLIED
        except requests.HTTPError as e:
            reason = ApiErrorReason.UNKNOWN_API_ERROR
            try:
                error_data = e.response.json()
                if "errors" in error_data and error_data["errors"]:
                    first_error = error_data["errors"][0]
                    reason = first_error.get("value", first_error.get("type"))
                elif "description" in error_data:
                    reason = error_data["description"]
            except Exception:  # noqa: BLE001
                reason = f"http_{e.response.status_code}"

            log_to_db(
                "WARN",
                LogSource.API_CLIENT,
                f"API отклонил отклик на {vacancy_id}. "
                f"Причина: {reason}. Детали: {e.response.text if e.response is not None else e}",
            )
            return False, reason
        except requests.RequestException as e:
            log_to_db(
                "ERROR",
                LogSource.API_CLIENT,
                f"Сетевая ошибка при отклике на {vacancy_id}: {e}",
            )
            return False, ApiErrorReason.NETWORK_ERROR

    def get_negotiation(self, negotiation_id: str):
        """Возвращает подробную информацию об отклике/приглашении."""
        return self._request("GET", f"/negotiations/{negotiation_id}")

    def get_negotiation_messages(
        self,
        negotiation_id: str,
        *,
        page: int = 0,
        per_page: int = 50,
        with_text_only: bool = False,
    ) -> dict:
        """Получает сообщения по отклику (для переписки в TUI)."""
        params = {
            "page": page,
            "per_page": per_page,
            "with_text_only": str(bool(with_text_only)).lower(),
        }
        return self._request(
            "GET",
            f"/negotiations/{negotiation_id}/messages",
            params=params,
        )

    def get_messages(self, negotiation_id: str):
        """Возвращает переписку по отклику."""
        return self._request("GET", f"/negotiations/{negotiation_id}/messages")

    def send_message(
        self, negotiation_id: str, message: str, save_to_db: bool = True
    ) -> tuple[bool, str]:
        payload = {"message": message}
        try:
            self._request(
                "POST",
                f"/negotiations/{negotiation_id}/messages",
                json=payload,
            )
            if save_to_db:
                upsert_negotiation_history([], self.profile_name)
            log_to_db(
                "INFO",
                LogSource.API_CLIENT,
                f"Сообщение по отклику {negotiation_id} отправлено.",
            )
            return True, "sent"
        except requests.HTTPError as e:
            try:
                error_data = e.response.json()
                reason = error_data.get("description") or error_data.get("errors")
            except Exception:  # noqa: BLE001
                reason = f"http_{e.response.status_code}"
            log_to_db(
                "WARN",
                LogSource.API_CLIENT,
                f"Не удалось отправить сообщение по отклику {negotiation_id}. "
                f"Причина: {reason}.",
            )
            return False, str(reason)
        except requests.RequestException as e:
            log_to_db(
                "ERROR",
                LogSource.API_CLIENT,
                f"Сетевая ошибка при отправке сообщения по {negotiation_id}: {e}",
            )
            return False, ApiErrorReason.NETWORK_ERROR

    def delete_profile(self, profile_name: str):
        delete_profile(profile_name)
