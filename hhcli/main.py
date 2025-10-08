import os
import sys
import json
from hhcli.client import HHApiClient
from hhcli.database import init_db # НОВЫЙ ИМПОРТ

# Эти переменные нужно будет получать из конфигурации (в будущем)
CLIENT_ID = os.getenv("HH_CLIENT_ID")
CLIENT_SECRET = os.getenv("HH_CLIENT_SECRET")

def run():
    """Главная функция-запускатор."""
    # Инициализируем БД при старте
    init_db()

    print("--- hhcli v0.2.0 (Этап 2) ---")
    
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Ошибка: не установлены переменные окружения HH_CLIENT_ID и HH_CLIENT_SECRET.")
        return
    
    force_auth = "--auth" in sys.argv
    
    client = HHApiClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)

    if force_auth:
        print("Запрошена принудительная аутентификация...")
        client.logout()

    if not client.is_authenticated():
        print("Токен не найден или истек. Запускаю аутентификацию...")
        try:
            client.authorize()
        except Exception as e:
            print(f"Не удалось завершить аутентификацию: {e}")
            return
    else:
        print("Вы уже аутентифицированы. Используется сохраненный токен из БД.")

    print("\n--- Тестовый запрос: получение списка резюме ---")
    try:
        resumes = client.get_my_resumes()
        print(json.dumps(resumes, indent=2, ensure_ascii=False))
        print("\nТестовый запрос успешно выполнен!")
    except Exception as e:
        print(f"\nОшибка при выполнении запроса к API: {e}")

if __name__ == "__main__":
    run()