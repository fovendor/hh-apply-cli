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

API_BASE_URL = "https://api.hh.ru"
OAUTH_URL = "https://hh.ru/oauth"
REDIRECT_URI = "http://127.0.0.1:9037/oauth_callback"

class HHApiClient:
    """
    Клиент для взаимодействия с API HeadHunter.
    Управляет аутентификацией и выполняет запросы.
    """
    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.profile_name = None

    def load_profile_data(self, profile_name: str):
        profile_data = load_profile(profile_name)
        if not profile_data:
            raise ValueError(f"Профиль '{profile_name}' не найден.")
        self.profile_name = profile_data['profile_name']
        self.access_token = profile_data['access_token']
        self.refresh_token = profile_data['refresh_token']
        self.token_expires_at = profile_data['expires_at']

    def is_authenticated(self) -> bool:
        return self.access_token is not None and self.token_expires_at > datetime.now()

    def _save_token(self, token_data: dict, user_info: dict):
        expires_in = token_data.get("expires_in", 3600)
        expires_at = datetime.now() + timedelta(seconds=expires_in)
        save_or_update_profile(self.profile_name, user_info, token_data, expires_at)
        self.access_token = token_data["access_token"]
        self.refresh_token = token_data["refresh_token"]
        self.token_expires_at = expires_at

    def _refresh_token(self):
        if not self.refresh_token:
            msg = f"Нет refresh_token для обновления профиля '{self.profile_name}'."
            log_to_db("ERROR", "APIClient", msg)
            raise Exception(msg)
        log_to_db("INFO", "APIClient", f"Токен для профиля '{self.profile_name}' истек, обновляю...")
        payload = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        response = requests.post(f"{OAUTH_URL}/token", data=payload)
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            log_to_db("ERROR", "APIClient", f"Ошибка обновления токена: {e.response.text}")
            raise e
        new_token_data = response.json()
        user_info = load_profile(self.profile_name)
        self._save_token(new_token_data, user_info)
        log_to_db("INFO", "APIClient", "Токен успешно обновлен.")

    def authorize(self, profile_name: str):
        self.profile_name = profile_name
        auth_url = (f"{OAUTH_URL}/authorize?response_type=code&"
                    f"client_id={self._client_id}&redirect_uri={REDIRECT_URI}")
        server_shutdown_event = threading.Event()
        app = Flask(__name__)
        @app.route("/oauth_callback")
        def oauth_callback():
            code = request.args.get("code")
            if not code: return "Ошибка: не удалось получить код авторизации.", 400
            try:
                payload = {"grant_type": "authorization_code", "client_id": self._client_id,
                           "client_secret": self._client_secret, "code": code, "redirect_uri": REDIRECT_URI}
                response = requests.post(f"{OAUTH_URL}/token", data=payload)
                response.raise_for_status()
                token_data = response.json()
                headers = {"Authorization": f"Bearer {token_data['access_token']}"}
                user_info_resp = requests.get(f"{API_BASE_URL}/me", headers=headers)
                user_info_resp.raise_for_status()
                self._save_token(token_data, user_info_resp.json())
                server_shutdown_event.set()
                return render_template_string("<h1>Успешно!</h1><p>Можете закрыть эту вкладку и вернуться в терминал.</p>")
            except requests.RequestException as e:
                log_to_db("ERROR", "OAuth", f"Ошибка при получении токена: {e}")
                return f"Произошла ошибка при получении токена: {e}", 500
        server_thread = threading.Thread(target=lambda: app.run(port=9037, debug=False))
        server_thread.daemon = True
        server_thread.start()
        print("Сейчас в вашем браузере откроется страница для входа в аккаунт hh.ru...")
        sleep(1); webbrowser.open(auth_url)
        print("Ожидание успешной аутентификации...")
        server_shutdown_event.wait()

    def _request(self, method: str, endpoint: str, **kwargs):
        if not self.is_authenticated():
            try: self._refresh_token()
            except Exception as e:
                log_to_db("ERROR", "APIClient", f"Не удалось обновить токен. Авторизация не удалась. Ошибка: {e}")
                raise ConnectionError("Не удалось обновить токен. Попробуйте пере-авторизоваться.") from e
        headers = kwargs.setdefault("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        url = f"{API_BASE_URL}{endpoint}"
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return None if response.status_code == 204 else response.json()
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                log_to_db("WARN", "APIClient", f"Получен 401 Unauthorized для {endpoint}. Попытка обновить токен.")
                try:
                    self._refresh_token()
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    response = requests.request(method, url, **kwargs)
                    response.raise_for_status()
                    return None if response.status_code == 204 else response.json()
                except Exception as refresh_e:
                     log_to_db("ERROR", "APIClient", f"Повторная попытка обновления токена не удалась. Ошибка: {refresh_e}")
                     raise ConnectionError("Не удалось обновить токен. Попробуйте пере-авторизоваться.") from refresh_e
            else:
                log_to_db("ERROR", "APIClient", f"HTTP ошибка для {method} {endpoint}: {e.response.status_code} {e.response.text}")
                raise e

    def get_my_resumes(self):
        return self._request("GET", "/resumes/mine")

    def get_similar_vacancies(self, resume_id: str, page: int = 0, per_page: int = 50):
        params = {"page": page, "per_page": per_page}
        return self._request("GET", f"/resumes/{resume_id}/similar_vacancies", params=params)

    def search_vacancies(self, config: dict, page: int = 0, per_page: int = 50):
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
        log_to_db("INFO", "APIClient", "Запрос общих справочников...")
        return self._request("GET", "/dictionaries")

    def get_areas(self):
        """Возвращает полный список регионов hh.ru."""
        log_to_db("INFO", "APIClient", "Запрос справочника регионов...")
        return self._request("GET", "/areas")

    def get_professional_roles(self):
        """Возвращает справочник профессиональных ролей hh.ru."""
        log_to_db("INFO", "APIClient", "Запрос справочника профессиональных ролей...")
        return self._request("GET", "/professional_roles")

    def sync_negotiation_history(self):
        log_to_db("INFO", "SyncEngine", f"Запуск синхронизации истории откликов для профиля '{self.profile_name}'.")
        last_sync = get_last_sync_timestamp(self.profile_name)
        params = {"order_by": "updated_at", "per_page": 100}
        if last_sync:
            params["date_from"] = last_sync.isoformat()
            log_to_db("INFO", "SyncEngine", f"Найдена последняя синхронизация: {last_sync}. Загружаем обновления.")
        all_items = []
        page = 0
        while True:
            params["page"] = page
            try:
                log_to_db("INFO", "SyncEngine", f"Запрос страницы {page} истории откликов...")
                data = self._request("GET", "/negotiations", params=params)
                items = data.get("items", [])
                all_items.extend(items)
                if page >= data.get("pages", 0) - 1: break
                page += 1
            except requests.HTTPError as e:
                log_to_db("ERROR", "SyncEngine", f"Ошибка при загрузке истории откликов: {e}")
                return
        if all_items:
            log_to_db("INFO", "SyncEngine", f"Получено {len(all_items)} обновленных записей. Сохранение в БД...")
            upsert_negotiation_history(all_items, self.profile_name)
            log_to_db("INFO", "SyncEngine", "Сохранение завершено.")
        else:
            log_to_db("INFO", "SyncEngine", "Новых обновлений в истории откликов не найдено.")
        set_last_sync_timestamp(self.profile_name, datetime.now())
        log_to_db("INFO", "SyncEngine", "Синхронизация успешно завершена.")

    def apply_to_vacancy(
            self, resume_id: str, vacancy_id: str,
            message: str = "") -> tuple[bool, str]:
        payload = {
            "resume_id": resume_id,
            "vacancy_id": vacancy_id,
            "message": message
        }
        try:
            self._request("POST", "/negotiations", json=payload)
            log_to_db("INFO", "APIClient", f"Успешный отклик на вакансию {vacancy_id} с резюме {resume_id}.")
            return (True, "applied")
        except requests.HTTPError as e:
            reason = "unknown_error"
            if e.response:
                try:
                    error_data = e.response.json()
                    first_error = error_data.get("errors", [{}])[0]
                    reason = first_error.get("type", "unknown_api_error")
                    log_to_db("WARN", "APIClient", f"API отклонил отклик на {vacancy_id}. Причина: {reason}. Детали: {error_data}")
                except Exception:
                    reason = f"http_{e.response.status_code}"
                    log_to_db("ERROR", "APIClient", f"API отклонил отклик на {vacancy_id} с не-JSON ответом. Код: {e.response.status_code}")
            return (False, reason)

    def logout(self, profile_name: str):
        delete_profile(profile_name)
        print(f"Профиль '{profile_name}' удален.")
        if self.profile_name == profile_name:
            self.access_token = None
