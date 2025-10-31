import webbrowser
import threading
from time import sleep
from datetime import datetime, timedelta

import requests
from flask import Flask, request, render_template_string

from hhcli.database import (
    save_or_update_profile, load_profile, delete_profile,
    log_to_db, get_last_sync_timestamp, set_last_sync_timestamp,
    upsert_negotiation_history
)
from .constants import ApiErrorReason, LogSource

API_BASE_URL = "https://api.hh.ru"
OAUTH_URL = "https://hh.ru/oauth"
REDIRECT_URI = "http://127.0.0.1:9037/oauth_callback"


class AuthorizationPending(RuntimeError):
    """Исключение о запуске повторной аутентификации."""


class HHApiClient:
    """
    Клиент для взаимодействия с API HeadHunter.
    Управляет аутентификацией и выполняет запросы.
    """
    def __init__(self):
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.profile_name = None
        self._auth_lock = threading.Lock()
        self._auth_thread: threading.Thread | None = None
        self._last_auth_url: str | None = None

    def load_profile_data(self, profile_name: str):
        profile_data = load_profile(profile_name)
        if not profile_data:
            raise ValueError(f"Профиль '{profile_name}' не найден.")
        self.profile_name = profile_data['profile_name']
        self.access_token = profile_data['access_token']
        self.refresh_token = profile_data['refresh_token']
        self.token_expires_at = profile_data['expires_at']

    def is_authenticated(self) -> bool:
        return (self.access_token is not None and
                self.token_expires_at > datetime.now())

    def start_authorization_flow(self, *, reason: str | None = None) -> None:
        """Запускает браузерную авторизацию в отдельном потоке."""
        if not self.profile_name:
            raise AuthorizationPending(
                "Профиль не загружен, авторизация невозможна."
            )

        with self._auth_lock:
            if self._auth_thread and self._auth_thread.is_alive():
                log_to_db(
                    "INFO",
                    LogSource.OAUTH,
                    f"Повторный запрос авторизации для профиля '{self.profile_name}'."
                )
                if self._last_auth_url:
                    webbrowser.open(self._last_auth_url)
                return

            log_details = f"Причина: {reason}" if reason else "Причина не указана."
            log_to_db(
                "INFO",
                LogSource.OAUTH,
                f"Запускаю авторизацию через браузер для '{self.profile_name}'. {log_details}"
            )

            def runner():
                try:
                    success = self.authorize(self.profile_name)
                    if success:
                        log_to_db(
                            "INFO",
                            LogSource.OAUTH,
                            f"Авторизация профиля '{self.profile_name}' завершена."
                        )
                    else:
                        log_to_db(
                            "ERROR",
                            LogSource.OAUTH,
                            f"Авторизация профиля '{self.profile_name}' завершилась с ошибкой."
                        )
                except Exception as exc:
                    log_to_db(
                        "ERROR",
                        LogSource.OAUTH,
                        f"Исключение внутри авторизации профиля '{self.profile_name}': {exc}"
                    )
                finally:
                    with self._auth_lock:
                        self._auth_thread = None

            self._auth_thread = threading.Thread(
                target=runner,
                name=f"OAuthFlow-{self.profile_name}",
                daemon=True,
            )
            self._auth_thread.start()

    def ensure_active_token(self) -> None:
        """Гарантирует наличие рабочего access_token или инициирует авторизацию."""
        if self.is_authenticated():
            return
        with self._auth_lock:
            if self._auth_thread and self._auth_thread.is_alive():
                raise AuthorizationPending(
                    "Авторизация уже идёт в браузере. Завершите её и повторите действие."
                )
        try:
            self._refresh_token()
        except AuthorizationPending:
            raise
        except Exception as exc:
            log_to_db(
                "ERROR",
                LogSource.API_CLIENT,
                f"Не удалось обновить токен для '{self.profile_name}': {exc}"
            )
            self.start_authorization_flow(reason="refresh_failed")
            raise AuthorizationPending(
                "Срок действия токена истёк. Открыта страница авторизации в браузере."
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
            msg = (f"Нет refresh_token для обновления "
                   f"профиля '{self.profile_name}'. Запускаю переавторизацию.")
            log_to_db("ERROR", LogSource.API_CLIENT, msg)
            self.start_authorization_flow(reason="missing_refresh_token")
            raise AuthorizationPending(
                "Не найден refresh_token. Открыта страница авторизации."
            )
        log_to_db("INFO", LogSource.API_CLIENT,
                  f"Токен для профиля '{self.profile_name}' истек, обновляю...")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token
        }
        response = requests.post(f"{OAUTH_URL}/token", data=payload)
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            log_to_db("ERROR", LogSource.API_CLIENT,
                      f"Ошибка обновления токена: {e.response.text}")
            error_details = {}
            try:
                error_details = e.response.json()
            except Exception:
                pass
            if (
                e.response.status_code in (400, 401)
                and error_details.get("error") == "invalid_grant"
            ):
                self.start_authorization_flow(reason="invalid_grant")
                raise AuthorizationPending(
                    "Не удалось обновить токен. "
                    "Откройте вкладку авторизации в браузере."
                ) from e
            raise
        new_token_data = response.json()
        user_info = load_profile(self.profile_name)
        self._save_token(new_token_data, user_info)
        log_to_db("INFO", LogSource.API_CLIENT, "Токен успешно обновлен.")

    def authorize(self, profile_name: str) -> bool:
        self.profile_name = profile_name

        PROXY_BASE_URL = "https://hh.ether-memory.com"
        PROXY_CONFIG_URL = f"{PROXY_BASE_URL}/api/get_config"
        PROXY_EXCHANGE_URL = f"{PROXY_BASE_URL}/api/exchange_code"

        try:
            print("Получение конфигурации с сервера...")
            config_resp = requests.get(PROXY_CONFIG_URL)
            config_resp.raise_for_status()
            public_client_id = config_resp.json()["client_id"]
        except requests.RequestException as e:
            print(f"Критическая ошибка: не удалось получить конфигурацию с сервера: {e}")
            log_to_db("ERROR", LogSource.OAUTH, f"Не удалось получить Client ID с прокси-сервера: {e}")
            return False

        auth_url = (f"{OAUTH_URL}/authorize?response_type=code&"
                    f"client_id={public_client_id}&redirect_uri={REDIRECT_URI}")
        self._last_auth_url = auth_url
        server_shutdown_event = threading.Event()
        app = Flask(__name__)

        @app.route("/oauth_callback")
        def oauth_callback():
            code = request.args.get("code")
            if not code:
                return "Ошибка: не удалось получить код авторизации.", 400
            try:
                proxy_payload = {"code": code}
                response = requests.post(PROXY_EXCHANGE_URL, json=proxy_payload)
                response.raise_for_status()
                token_data = response.json()

                headers = {
                    "Authorization": f"Bearer {token_data['access_token']}"
                }
                user_info_resp = requests.get(
                    f"{API_BASE_URL}/me", headers=headers
                )
                user_info_resp.raise_for_status()
                self._save_token(token_data, user_info_resp.json())
                server_shutdown_event.set()
                return render_template_string(
                    "<h1>Успешно!</h1><p>Можете закрыть эту вкладку "
                    "и вернуться в терминал.</p>"
                )
            except requests.RequestException as e:
                log_to_db("ERROR", LogSource.OAUTH, f"Ошибка при получении токена: {e}")
                return f"Произошла ошибка при получении токена: {e}", 500

        server_thread = threading.Thread(
            target=lambda: app.run(port=9037, debug=False)
        )
        server_thread.daemon = True
        server_thread.start()
        print("Сейчас в вашем браузере откроется страница "
              "для входа в аккаунт hh.ru...")
        sleep(1)
        webbrowser.open(auth_url)
        print("Ожидание успешной аутентификации...")
        server_shutdown_event.wait()
        return True

    def _request(self, method: str, endpoint: str, **kwargs):
        try:
            self.ensure_active_token()
        except AuthorizationPending as pending:
            log_to_db("ERROR", LogSource.API_CLIENT, str(pending))
            raise
        headers = kwargs.setdefault("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        url = f"{API_BASE_URL}{endpoint}"
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            if response.status_code in (201, 204):
                return None
            return response.json()
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                log_to_db(
                    "WARN", LogSource.API_CLIENT,
                    f"Получен 401 Unauthorized для {endpoint}. "
                    "Повторная попытка после обновления токена."
                )
                try:
                    self.ensure_active_token()
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    response = requests.request(method, url, **kwargs)
                    response.raise_for_status()
                    if response.status_code in (201, 204):
                        return None
                    return response.json()
                except AuthorizationPending:
                    raise
                except Exception as refresh_e:
                    msg = ("Повторная попытка обновления токена не удалась. "
                           f"Ошибка: {refresh_e}")
                    log_to_db("ERROR", LogSource.API_CLIENT, msg)
                    raise ConnectionError(
                        "Не удалось обновить токен. "
                        "Попробуйте пере-авторизоваться."
                    ) from refresh_e
            log_to_db(
                "ERROR", LogSource.API_CLIENT,
                f"HTTP ошибка для {method} {endpoint}: "
                f"{e.response.status_code} {e.response.text}"
            )
            raise e

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

    def search_vacancies(
            self, config: dict, page: int = 0, per_page: int = 50
    ):
        """
        Выполняет поиск вакансий по параметрам из конфигурации профиля.
        """
        positive_keywords = config.get('text_include', [])
        positive_str = " OR ".join(f'"{kw}"' for kw in positive_keywords)

        negative_keywords = config.get('negative', [])
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
            "area": config.get('area_id'),
            "professional_role": config.get('role_ids_config', []),
            "search_field": config.get('search_field'),
            "period": config.get('period'),
            "order_by": "publication_time",
            "page": page,
            "per_page": per_page
        }

        if config.get('work_format') and config['work_format'] != "ANY":
            params['work_format'] = config['work_format']

        params = {k: v for k, v in params.items() if v}

        return self._request("GET", "/vacancies", params=params)

    def get_vacancy_details(self, vacancy_id: str):
        return self._request("GET", f"/vacancies/{vacancy_id}")

    def get_dictionaries(self):
        """Загружает общие справочники hh.ru."""
        log_to_db("INFO", LogSource.API_CLIENT, "Запрос общих справочников...")
        return self._request("GET", "/dictionaries")

    def get_areas(self):
        """Возвращает полный список регионов hh.ru."""
        log_to_db("INFO", LogSource.API_CLIENT, "Запрос справочника регионов...")
        return self._request("GET", "/areas")

    def get_professional_roles(self):
        """Возвращает справочник профессиональных ролей hh.ru."""
        log_to_db(
            "INFO", LogSource.API_CLIENT, "Запрос справочника профессиональных ролей..."
        )
        return self._request("GET", "/professional_roles")

    def sync_negotiation_history(self):
        log_to_db(
            "INFO", LogSource.SYNC_ENGINE,
            f"Запуск синхронизации истории откликов "
            f"для профиля '{self.profile_name}'."
        )
        last_sync = get_last_sync_timestamp(self.profile_name)
        params = {"order_by": "updated_at", "per_page": 100}
        if last_sync:
            params["date_from"] = last_sync.isoformat()
            log_to_db(
                "INFO", LogSource.SYNC_ENGINE,
                f"Найдена последняя синхронизация: {last_sync}. "
                f"Загружаем обновления."
            )
        all_items = []
        page = 0
        while True:
            params["page"] = page
            try:
                log_to_db(
                    "INFO", LogSource.SYNC_ENGINE,
                    f"Запрос страницы {page} истории откликов..."
                )
                data = self._request("GET", "/negotiations", params=params)
                items = data.get("items", [])
                all_items.extend(items)
                if page >= data.get("pages", 0) - 1:
                    break
                page += 1
            except requests.HTTPError as e:
                log_to_db(
                    "ERROR", LogSource.SYNC_ENGINE,
                    f"Ошибка при загрузке истории откликов: {e}"
                )
                return
        if all_items:
            log_to_db(
                "INFO", LogSource.SYNC_ENGINE,
                f"Получено {len(all_items)} обновленных записей. "
                f"Сохранение в БД..."
            )
            upsert_negotiation_history(all_items, self.profile_name)
            log_to_db("INFO", LogSource.SYNC_ENGINE, "Сохранение завершено.")
        else:
            log_to_db(
                "INFO", LogSource.SYNC_ENGINE,
                "Новых обновлений в истории откликов не найдено."
            )
        set_last_sync_timestamp(self.profile_name, datetime.now())
        log_to_db("INFO", LogSource.SYNC_ENGINE, "Синхронизация успешно завершена.")

    def apply_to_vacancy(
            self, resume_id: str, vacancy_id: str,
            message: str = ""
    ) -> tuple[bool, str]:
        payload = {
            "resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "message": message
        }
        try:
            self._request("POST", "/negotiations", data=payload)
            log_to_db(
                "INFO", LogSource.API_CLIENT,
                f"Успешный отклик на вакансию {vacancy_id} "
                f"с резюме {resume_id}."
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
            except Exception:
                reason = f"http_{e.response.status_code}"

            log_to_db(
                "WARN", LogSource.API_CLIENT,
                f"API отклонил отклик на {vacancy_id}. "
                f"Причина: {reason}. Детали: {e.response.text}"
            )
            return False, reason
        except requests.RequestException as e:
            log_to_db("ERROR", LogSource.API_CLIENT,
                      f"Сетевая ошибка при отклике на {vacancy_id}: {e}")
            return False, ApiErrorReason.NETWORK_ERROR

    def logout(self, profile_name: str):
        delete_profile(profile_name)
        print(f"Профиль '{profile_name}' удален.")
        if self.profile_name == profile_name:
            self.access_token = None
