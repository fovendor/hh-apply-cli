import os
import json
import webbrowser
import threading
from time import sleep
from datetime import datetime, timedelta

import requests
from flask import Flask, request, render_template_string
from platformdirs import user_data_dir

# --- Константы ---
API_BASE_URL = "https://api.hh.ru"
OAUTH_URL = "https://hh.ru/oauth"
APP_NAME = "hhcli"
APP_AUTHOR = "fovendor"
TOKEN_FILE = "token.json"
REDIRECT_URI = "http://127.0.0.1:9037/oauth_callback"

class HHApiClient:
    """
    Клиент для взаимодействия с API HeadHunter.
    Управляет аутентификацией и выполняет запросы.
    """
    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._data_dir = user_data_dir(APP_NAME, APP_AUTHOR)
        self._token_path = os.path.join(self._data_dir, TOKEN_FILE)

        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None

        self._load_token()

    def is_authenticated(self) -> bool:
        """Проверяет, есть ли у нас валидный access_token."""
        return self.access_token is not None and self.token_expires_at > datetime.now()

    def _load_token(self):
        """Загружает токен из файла, если он существует."""
        if not os.path.exists(self._token_path):
            return
        try:
            with open(self._token_path, "r") as f:
                token_data = json.load(f)
            self.access_token = token_data.get("access_token")
            self.refresh_token = token_data.get("refresh_token")
            # Храним время истечения токена для будущих проверок
            self.token_expires_at = datetime.fromisoformat(token_data.get("expires_at"))
        except (json.JSONDecodeError, KeyError):
            print("Ошибка чтения файла токена. Потребуется новая аутентификация.")
            self.access_token = None
            self.refresh_token = None

    def _save_token(self, token_data: dict):
        """Сохраняет данные токена в файл."""
        os.makedirs(self._data_dir, exist_ok=True)
        # Рассчитываем и сохраняем точное время истечения токена
        expires_in = token_data.get("expires_in", 3600) # По умолчанию 1 час
        self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
        
        token_data_to_save = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "expires_at": self.token_expires_at.isoformat()
        }
        
        with open(self._token_path, "w") as f:
            json.dump(token_data_to_save, f)
        
        # Обновляем состояние объекта
        self.access_token = token_data["access_token"]
        self.refresh_token = token_data["refresh_token"]

    def _refresh_token(self):
        """Обновляет access_token, используя refresh_token."""
        if not self.refresh_token:
            raise Exception("Нет refresh_token для обновления.")
            
        print("Токен истек, обновляю...")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        # При обновлении токена авторизация не нужна
        response = requests.post(f"{OAUTH_URL}/token", data=payload)
        response.raise_for_status()
        new_token_data = response.json()
        self._save_token(new_token_data)
        print("Токен успешно обновлен.")

    def authorize(self):
        """Запускает процесс OAuth2 аутентификации."""
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
                # Обмениваем код на токен
                payload = {
                    "grant_type": "authorization_code",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": REDIRECT_URI,
                }
                response = requests.post(f"{OAUTH_URL}/token", data=payload)
                response.raise_for_status()
                token_data = response.json()
                self._save_token(token_data)
                
                # Сигнализируем об остановке сервера
                server_shutdown_event.set()
                return render_template_string("<h1>Успешно!</h1><p>Можете закрыть эту вкладку и вернуться в терминал.</p>")
            except requests.RequestException as e:
                return f"Произошла ошибка при получении токена: {e}", 500

        # Запускаем Flask в отдельном потоке
        server_thread = threading.Thread(target=lambda: app.run(port=9037, debug=False))
        server_thread.daemon = True
        server_thread.start()

        print("Сейчас в вашем браузере откроется страница для входа в аккаунт hh.ru...")
        sleep(1)
        webbrowser.open(auth_url)
        print("Ожидание успешной аутентификации...")

        # Ждем, пока колбэк не установит событие
        server_shutdown_event.wait()
        print("Аутентификация прошла успешно!")

    def _request(self, method: str, endpoint: str, **kwargs):
        """Обертка для выполнения запросов к API с автоматическим обновлением токена."""
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
            if e.response.status_code == 401: # Unauthorized
                print("Попытка обновить токен и повторить запрос...")
                self._refresh_token()
                headers["Authorization"] = f"Bearer {self.access_token}" # Обновляем заголовок
                
                # Повторяем запрос ОДИН раз
                response = requests.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json()
            else:
                raise e # другие HTTP ошибки

    # --- Публичные методы API ---
    
    def get_my_resumes(self):
        """Получает список резюме текущего пользователя."""
        return self._request("GET", "/resumes/mine")