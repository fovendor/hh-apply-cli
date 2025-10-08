import os
import sys
import json
from hhcli.client import HHApiClient
from hhcli.database import init_db, set_active_profile, get_active_profile_name

CLIENT_ID = os.getenv("HH_CLIENT_ID")
CLIENT_SECRET = os.getenv("HH_CLIENT_SECRET")

def run():
    """Главная функция-запускатор и диспетчер команд."""
    init_db()

    if not CLIENT_ID or not CLIENT_SECRET:
        print("Ошибка: не установлены переменные окружения HH_CLIENT_ID и HH_CLIENT_SECRET.")
        return

    args = sys.argv[1:]

    # --- Обработка команды --auth ---
    if "--auth" in args:
        try:
            profile_index = args.index("--auth") + 1
            profile_name = args[profile_index]
            print(f"Запуск аутентификации для профиля: '{profile_name}'")
            
            client = HHApiClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
            client.authorize(profile_name)
            set_active_profile(profile_name) # Делаем новый профиль активным
            print(f"Профиль '{profile_name}' успешно создан и активирован.")
        except IndexError:
            print("Ошибка: после --auth необходимо указать имя профиля. Например: hhcli --auth my_account")
        return # Завершаем работу после аутентификации

    # --- Основная логика запуска ---
    active_profile = get_active_profile_name()
    if not active_profile:
        print("Активный профиль не выбран. Пожалуйста, сначала войдите в аккаунт:")
        print("  hhcli --auth <имя_профиля>")
        return

    print(f"--- hhcli v0.2.1 (Профиль: {active_profile}) ---")
    
    client = HHApiClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    try:
        client.load_profile_data(active_profile)
    except ValueError as e:
        print(f"Ошибка: {e}")
        # Это может случиться, если активный профиль был удален вручную
        print("Попробуйте войти заново с помощью --auth <имя_профиля>")
        return

    # Проверка на истекший токен будет выполнена автоматически при первом запросе
    print(f"Аутентифицированы как '{active_profile}'. Используется токен из БД.")

    print("\n--- Тестовый запрос: получение списка резюме ---")
    try:
        resumes = client.get_my_resumes()
        print(json.dumps(resumes, indent=2, ensure_ascii=False))
        print("\nТестовый запрос успешно выполнен!")
    except Exception as e:
        print(f"\nОшибка при выполнении запроса к API: {e}")

if __name__ == "__main__":
    run()