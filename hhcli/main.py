import os
import sys  # ИМПОРТИРУЕМ sys для доступа к аргументам командной строки
import json
from hhcli.client import HHApiClient

# Эти переменные нужно будет получать из конфигурации (в будущем)
# Сейчас для теста можно вставить их сюда или использовать переменные окружения
CLIENT_ID = os.getenv("HH_CLIENT_ID")
CLIENT_SECRET = os.getenv("HH_CLIENT_SECRET")

def run():
    """Главная функция-запускатор."""
    print("--- hhcli v0.1.0 (Этап 1) ---")
    
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Ошибка: не установлены переменные окружения HH_CLIENT_ID и HH_CLIENT_SECRET.")
        print("Пожалуйста, установите их перед запуском.")
        return
    
    # Определяем, нужно ли принудительно запускать аутентификацию
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
        print("Вы уже аутентифицированы. Используется сохраненный токен.")

    print("\n--- Тестовый запрос: получение списка резюме ---")
    try:
        resumes = client.get_my_resumes()
        print(json.dumps(resumes, indent=2, ensure_ascii=False))
        print("\nТестовый запрос успешно выполнен!")
    except Exception as e:
        print(f"\nОшибка при выполнении запроса к API: {e}")

if __name__ == "__main__":
    run()