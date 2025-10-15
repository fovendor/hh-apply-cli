import os
import sys
from hhcli.client import HHApiClient
from hhcli.database import init_db, set_active_profile, get_active_profile_name, log_to_db
from hhcli.ui.tui import HHCliApp

CLIENT_ID = os.getenv("HH_CLIENT_ID")
CLIENT_SECRET = os.getenv("HH_CLIENT_SECRET")

def run():
    """Главная функция-запускатор и диспетчер команд."""
    init_db()
    log_to_db("INFO", "Main", "Запуск приложения hhcli.")

    if not CLIENT_ID or not CLIENT_SECRET:
        log_to_db("ERROR", "Main", "Переменные окружения HH_CLIENT_ID и HH_CLIENT_SECRET не установлены.")
        print("Ошибка: не установлены переменные окружения HH_CLIENT_ID и HH_CLIENT_SECRET.")
        return

    args = sys.argv[1:]

    if "--auth" in args:
        try:
            profile_index = args.index("--auth") + 1
            profile_name = args[profile_index]
            log_to_db("INFO", "Main", f"Обнаружена команда --auth для профиля '{profile_name}'.")
            print(f"Запуск аутентификации для профиля: '{profile_name}'")

            client = HHApiClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
            client.authorize(profile_name)
            set_active_profile(profile_name)

            log_to_db("INFO", "Main", f"Профиль '{profile_name}' успешно создан и активирован.")
            print(f"Профиль '{profile_name}' успешно создан и активирован.")
        except IndexError:
            log_to_db("ERROR", "Main", "Команда --auth вызвана без имени профиля.")
            print("Ошибка: после --auth необходимо указать имя профиля. Например: hhcli --auth my_account")

        log_to_db("INFO", "Main", "Приложение hhcli завершило работу после аутентификации.")
        return

    active_profile = get_active_profile_name()
    if not active_profile:
        log_to_db("WARN", "Main", "Активный профиль не найден. Вывод подсказки и завершение.")
        print("Активный профиль не выбран. Пожалуйста, сначала войдите в аккаунт:")
        print("  hhcli --auth <имя_профиля>")
        return

    client = HHApiClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
    try:
        log_to_db("INFO", "Main", f"Профиль '{active_profile}' активен. Загрузка данных профиля.")
        client.load_profile_data(active_profile)
    except ValueError as e:
        log_to_db("ERROR", "Main", f"Ошибка загрузки профиля '{active_profile}': {e}")
        print(f"Ошибка: {e}")
        return

    app = HHCliApp(client=client)

    log_to_db("INFO", "Main", "Запуск TUI.")
    result = app.run()

    if result:
        log_to_db("ERROR", "Main", f"TUI завершился с ошибкой: {result}")
        print(f"\n[ОШИБКА] {result}")

    log_to_db("INFO", "Main", "Приложение hhcli завершило работу.")

if __name__ == "__main__":
    run()
