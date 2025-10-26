#!/usr/bin/env bash
set -euo pipefail

# --- Скрипт для установки/обновления hhcli ---

echo "Запуск установщика hhcli..."

if ! command -v python3 &> /dev/null; then
    echo "Ошибка: Python 3 не найден. Пожалуйста, установите Python 3 и попробуйте снова."
    exit 1
fi

if ! command -v pipx &> /dev/null; then
    echo "pipx не найден. Устанавливаем pipx..."
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
    echo "pipx был установлен."
    echo "Пожалуйста, ПЕРЕЗАПУСТИТЕ ваш терминал, чтобы изменения вступили в силу, и запустите этот скрипт снова."
    exit 1
fi

echo "Установка/обновление пакета hhcli из PyPI..."
pipx install --force hhcli

echo ""
echo "Установка hhcli успешно завершена!"
echo ""
echo "--- Следующие шаги ---"
echo "1. Запустите процесс авторизации, придумав имя для вашего профиля:"
echo "   hhcli --auth my_profile"
echo ""
echo "2. Следуйте инструкциям в браузере."
echo "----------------------"