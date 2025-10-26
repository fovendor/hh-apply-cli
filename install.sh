#!/usr/bin/env bash
set -euo pipefail

PACKAGE_NAME="hhcli"
DATA_DIR="$HOME/.local/share/hhcli"
CACHE_DIR="$HOME/.cache/hhcli"

do_install() {
    echo "Запуск установщика hhcli..."

    # 1. Проверяем наличие Python
    if ! command -v python3 &> /dev/null; then
        echo "Ошибка: Python 3 не найден. Пожалуйста, установите Python 3 и попробуйте снова."
        exit 1
    fi

    # 2. Проверяем и при необходимости устанавливаем pipx
    if ! command -v pipx &> /dev/null; then
        echo "pipx не найден. Устанавливаем pipx..."
        python3 -m pip install --user pipx
        python3 -m pipx ensurepath
        echo "pipx был установлен."
        echo "Пожалуйста, ПЕРЕЗАПУСТИТЕ ваш терминал, чтобы изменения вступили в силу, и запустите этот скрипт снова."
        exit 1
    fi

    # 3. Устанавливаем или обновляем hhcli с помощью pipx, игнорируя локальный кеш
    echo "Установка/обновление пакета ${PACKAGE_NAME} из PyPI..."
    pipx install --force --pip-args="--no-cache-dir" "${PACKAGE_NAME}"

    echo ""
    echo "Установка hhcli успешно завершена!"
    echo ""
    echo "--- Следующие шаги ---"
    echo "1. Запустите процесс авторизации, придумав имя для вашего профиля:"
    echo "   hhcli --auth my_profile"
    echo ""
    echo "2. Следуйте инструкциям в браузере."
    echo "----------------------"
}

do_uninstall() {
    echo "Запуск деинсталлятора hhcli..."
    echo ""

    # 1. Удаляем пакет через pipx
    if command -v pipx &> /dev/null && pipx list | grep -q "${PACKAGE_NAME}"; then
        echo "Удаление пакета ${PACKAGE_NAME} через pipx..."
        pipx uninstall "${PACKAGE_NAME}"
        echo "Пакет успешно удален."
    else
        echo "Пакет ${PACKAGE_NAME} не найден в pipx. Пропускаем."
    fi

    echo ""
    echo "--- Удаление пользовательских данных (опционально) ---"
    echo "Приложение было удалено, но ваши данные (профили, история, кэш) остались."
    echo "Если вы хотите их удалить, выполните следующие команды вручную:"
    echo ""
    echo "  # ВНИМАНИЕ: Эта команда безвозвратно удалит все ваши профили и историю откликов!"
    echo "  rm -rf ${DATA_DIR}"
    echo ""
    echo "  # Эта команда удалит кэш приложения"
    echo "  rm -rf ${CACHE_DIR}"
    echo "--------------------------------------------------------"
    echo ""
    echo "Удаление hhcli завершено!"
}

usage() {
    echo "Использование: bash <(curl...) [команда]"
    echo ""
    echo "Команды:"
    echo "  install    (по умолчанию) Установить или обновить hhcli."
    echo "  uninstall  Удалить приложение hhcli (данные пользователя не затрагиваются)."
    exit 1
}

main() {
    ACTION="${1:-install}" # Если аргумент не передан, считаем, что это 'install'

    case "$ACTION" in
        install)
            do_install
            ;;
        uninstall)
            do_uninstall
            ;;
        *)
            echo "Ошибка: неизвестная команда '$ACTION'"
            usage
            ;;
    esac
}

main "$@"