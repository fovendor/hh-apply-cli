import os
import sys
from hhcli.client import HHApiClient
from hhcli.database import init_db, set_active_profile, get_active_profile_name
from hhcli.tui import HHCliApp

CLIENT_ID = os.getenv("HH_CLIENT_ID")
CLIENT_SECRET = os.getenv("HH_CLIENT_SECRET")

def run():
    """Главная функция-запускатор и диспетчер команд."""
    init_db()

    if not CLIENT_ID or not CLIENT_SECRET:
        print("Ошибка: не установлены переменные окружения HH_CLIENT_ID и HH_CLIENT_SECRET.")
        return

    args = sys.argv[1:]

    # Обработка команды --auth
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
        return

    # Основная логика запуска
    active_profile = get_active_profile_name()
    if not active_profile:
        print("Активный профиль не выбран. Пожалуйста, сначала войдите в аккаунт:")
        print("  hhcli --auth <имя_профиля>")
        return
    
    client = HHApiClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    try:
        client.load_profile_data(active_profile)
    except ValueError as e:
        print(f"Ошибка: {e}")
        return

    # Создаем и запускаем TUI-приложение, передавая ему весь API-клиент
    app = HHCliApp(client=client)
    
    # app.run() может вернуть результат (например, ошибку), который мы можем обработать
    result = app.run()
    
    if result:
        # Если TUI завершился с ошибкой, выводим ее в консоль
        print(f"\n[ОШИБКА] {result}")

if __name__ == "__main__":
    run()