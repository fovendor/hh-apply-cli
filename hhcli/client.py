import webbrowser
import threading
from time import sleep
from datetime import datetime, timedelta

import requests
from flask import Flask, request, render_template_string

from hhcli.database import save_or_update_profile, load_profile, delete_profile

# --- Константы ---
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
        self.profile_name = None # Имя текущего профиля

    def load_profile_data(self, profile_name: str):
        """Загружает данные профиля и настраивает клиент."""
        profile_data = load_profile(profile_name)
        if not profile_data:
            raise ValueError(f"Профиль '{profile_name}' не найден.")
        
        self.profile_name = profile_data['profile_name']
        self.access_token = profile_data['access_token']
        self.refresh_token = profile_data['refresh_token']
        self.token_expires_at = profile_data['expires_at']

    def is_authenticated(self) -> bool:
        """Проверяет, есть ли у нас валидный access_token."""
        return self.access_token is not None and self.token_expires_at > datetime.now()

    def _save_token(self, token_data: dict, user_info: dict):
        """Сохраняет токен, привязывая его к профилю."""
        expires_in = token_data.get("expires_in", 3600)
        expires_at = datetime.now() + timedelta(seconds=expires_in)
        
        save_or_update_profile(self.profile_name, user_info, token_data, expires_at)
        
        # Обновляем состояние объекта
        self.access_token = token_data["access_token"]
        self.refresh_token = token_data["refresh_token"]
        self.token_expires_at = expires_at

    def _refresh_token(self):
        """Обновляет access_token, используя refresh_token."""
        if not self.refresh_token:
            raise Exception(f"Нет refresh_token для обновления профиля '{self.profile_name}'.")
            
        print(f"Токен для профиля '{self.profile_name}' истек, обновляю...")
        payload = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        response = requests.post(f"{OAUTH_URL}/token", data=payload)
        response.raise_for_status()
        new_token_data = response.json()
        
        # Для обновления токена нам не нужна новая информация о пользователе
        # Мы можем передавать старые данные в _save_token, ничего криминального
        # Правильнее было бы иметь отдельную функцию update_token_only, но для простоты сойдет
        user_info = {'id': 'dummy', 'email': 'dummy'} # Эти данные не будут использованы при обновлении
        self._save_token(new_token_data, user_info)
        print("Токен успешно обновлен.")

    def authorize(self, profile_name: str):
        """Запускает процесс OAuth2 аутентификации для указанного профиля."""
        self.profile_name = profile_name # Сохраняем имя профиля для колбэка
        auth_url = (
            f"{OAUTH_URL}/authorize?response_type=code&"
            f"client_id={self._client_id}&redirect_uri={REDIRECT_URI}"
        )
        
        server_shutdown_event = threading.Event()
        app = Flask(__name__)

        @app.route("/oauth_callback")
        def oauth_callback():
            code = request.args.get("code")
            if not code:
                return "Ошибка: не удалось получить код авторизации.", 400
            
            try:
                payload = {
                    "grant_type": "authorization_code", "client_id": self._client_id,
                    "client_secret": self._client_secret, "code": code,
                    "redirect_uri": REDIRECT_URI,
                }
                response = requests.post(f"{OAUTH_URL}/token", data=payload)
                response.raise_for_status()
                token_data = response.json()

                # Получаем информацию о пользователе
                temp_access_token = token_data['access_token']
                headers = {"Authorization": f"Bearer {temp_access_token}"}
                user_info_resp = requests.get(f"{API_BASE_URL}/me", headers=headers)
                user_info_resp.raise_for_status()
                user_info = user_info_resp.json()
                
                # Сохраняем токен вместе с информацией о пользователе
                self._save_token(token_data, user_info)
                
                server_shutdown_event.set()
                return render_template_string("<h1>Успешно!</h1><p>Можете закрыть эту вкладку и вернуться в терминал.</p>")
            except requests.RequestException as e:
                return f"Произошла ошибка при получении токена: {e}", 500

        server_thread = threading.Thread(target=lambda: app.run(port=9037, debug=False))
        server_thread.daemon = True
        server_thread.start()

        print("Сейчас в вашем браузере откроется страница для входа в аккаунт hh.ru...")
        sleep(1)
        webbrowser.open(auth_url)
        print("Ожидание успешной аутентификации...")
        server_shutdown_event.wait()
        
    def _request(self, method: str, endpoint: str, **kwargs):
        """Обертка для выполнения запросов к API."""
        if not self.is_authenticated():
            self._refresh_token()

        headers = kwargs.setdefault("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        
        url = f"{API_BASE_URL}{endpoint}"
        
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                self._refresh_token()
                headers["Authorization"] = f"Bearer {self.access_token}"
                response = requests.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json()
            else:
                raise e

    def get_my_resumes(self):
        """Получает список резюме."""
        return self._request("GET", "/resumes/mine")

    def logout(self, profile_name: str):
        """Удаляет конкретный профиль."""
        delete_profile(profile_name)
        print(f"Профиль '{profile_name}' удален.")
        if self.profile_name == profile_name:
            self.access_token = None