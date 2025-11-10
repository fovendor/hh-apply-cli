import sys

from hhcli.client import HHApiClient
from hhcli.database import init_db, set_active_profile, get_active_profile_name, log_to_db
from hhcli.ui.tui import HHCliApp
from hhcli.version import get_version

def run():
    """Главная функция-запускатор и диспетчер команд."""
    args = sys.argv[1:]

    if any(flag in args for flag in ("-v", "--version")):
        print(get_version())
        return

    init_db()
    log_to_db("INFO", "Main", "Запуск приложения hhcli.")

    if "--auth" in args:
        try:
            profile_index = args.index("--auth") + 1
            profile_name = args[profile_index]
            log_to_db("INFO", "Main", f"Обнаружена команда --auth для профиля '{profile_name}'.")
            print(f"Запуск аутентификации для профиля: '{profile_name}'")

            client = HHApiClient()
            success = client.authorize(profile_name)

            if success:
                set_active_profile(profile_name)
                log_to_db("INFO", "Main", f"Профиль '{profile_name}' успешно создан и активирован.")
                print(f"Профиль '{profile_name}' успешно создан и активирован.")
            else:
                log_to_db("ERROR", "Main", f"Авторизация для профиля '{profile_name}' не удалась.")
                print(f"Авторизация для профиля '{profile_name}' не удалась. Пожалуйста, проверьте логи и попробуйте снова.")

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

    client = HHApiClient()
    try:
        log_to_db("INFO", "Main", f"Профиль '{active_profile}' активен. Загрузка данных профиля.")
        client.load_profile_data(active_profile)
    except ValueError as e:
        log_to_db("ERROR", "Main", f"Ошибка загрузки профиля '{active_profile}': {e}")
        print(f"Ошибка: {e}")
        return

    app = HHCliApp(client=client)
    app.apply_theme_from_profile(active_profile)

    log_to_db("INFO", "Main", "Запуск TUI.")
    result = app.run()

    if result:
        log_to_db("ERROR", "Main", f"TUI завершился с ошибкой: {result}")
        print(f"\n[ОШИБКА] {result}")

    log_to_db("INFO", "Main", "Приложение hhcli завершило работу.")

if __name__ == "__main__":
    run()
