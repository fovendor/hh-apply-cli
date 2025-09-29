#!/usr/bin/env bash
set -euo pipefail

# --- КОНСТАНТЫ И НАСТРОЙКИ ---
PACKAGE_NAME="hh-applicant-tool"
PACKAGE_NAME_WITH_EXTRA="hh-applicant-tool[qt]"
INSTALL_DIR="/usr/local/bin"
EXECUTABLE_NAME="hhcli"
CONFIG_DIR="$HOME/.config/hhcli"
CONFIG_FILE="$CONFIG_DIR/config.sh"
HHCLI_RAW_URL="https://raw.githubusercontent.com/fovendor/hhcli/install/hhcli"

# --- ЦВЕТА И ФУНКЦИИ ВЫВОДА ---
C_RESET='\033[0m'; C_RED='\033[0;31m'; C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'; C_CYAN='\033[0;36m'
msg() { echo -e "${C_CYAN}==>${C_RESET} ${1}"; }
msg_ok() { echo -e "${C_GREEN} ✓ ${C_RESET} ${1}"; }
msg_warn() { echo -e "${C_YELLOW} ! ${C_RESET} ${1}"; }
msg_err() { echo -e "${C_RED} ✗ ${C_RESET} ${1}" >&2; }

# --- ОСНОВНЫЕ ФУНКЦИИ ---

usage() {
    echo "Использование: bash <(curl...) [install|uninstall]"
    echo "  install    - Установить или обновить hhcli (действие по умолчанию)."
    echo "  uninstall  - Полностью удалить hhcli и все его данные."
    exit 1
}

# Функция полной очистки
uninstall_all() {
    msg "Запуск полного удаления hhcli..."

    msg "Удаление основного скрипта..."
    if [ -f "$INSTALL_DIR/$EXECUTABLE_NAME" ]; then
        sudo rm -f "$INSTALL_DIR/$EXECUTABLE_NAME"
        msg_ok "Скрипт $EXECUTABLE_NAME удален."
    else
        msg_warn "Скрипт $EXECUTABLE_NAME не найден."
    fi

    msg "Удаление бэкенда..."
    if command -v pipx &> /dev/null; then
        pipx uninstall "$PACKAGE_NAME" &>/dev/null || true
        msg_ok "Пакет $PACKAGE_NAME удален."
    fi

    msg "Удаление конфигурационных файлов..."
    rm -rf "$CONFIG_DIR"
    rm -rf "$HOME/.config/hh-applicant-tool"
    msg_ok "Конфигурации удалены."

    msg "Удаление кэша и отчетов..."
    rm -rf "$HOME/.cache/pm-search-cache"
    rm -rf "$HOME/hh-reports"
    msg_ok "Кэш и отчеты удалены."

    echo
    msg_ok "Полная очистка завершена."
}

install_all() {
    msg "Запуск установки hhcli"
    
    cleanup_old_versions() {
        msg "Поиск и удаление старых версий и конфигов..."
        local old_system_files=("/usr/local/bin/hh-applicant-tool" "/usr/local/bin/hh-apply-cli")
        local old_user_files=("$HOME/.local/bin/hhcli")
        if [ ! -f "$CONFIG_FILE" ] && [ -d "$HOME/.config/hh-applicant-tool" ]; then 
            msg_warn "Найден старый конфиг бэкенда. Удаляем для чистой установки..."; rm -rf "$HOME/.config/hh-applicant-tool"; msg_ok "Удалено.";
        fi
        for file in "${old_system_files[@]}"; do if [ -f "$file" ]; then msg_warn "Найден старый файл: $file. Удаляем..."; sudo rm -f "$file" && msg_ok "Удалено."; fi; done
        for file in "${old_user_files[@]}"; do if [ -f "$file" ]; then msg_warn "Найден старый файл: $file. Удаляем..."; rm -f "$file" && msg_ok "Удалено."; fi; done
    }

    check_and_install_deps() {
        msg "Проверка системных зависимостей..."
        local missing_packages=()

        if ! command -v apt-get &>/dev/null; then
            msg_err "Этот скрипт поддерживает только Debian/Ubuntu системы."; exit 1
        fi

        # Список утилит, которые должны быть в системе
        local required_cmds=("fzf" "jq" "w3m" "curl" "git" "python3" "pipx")
        for cmd in "${required_cmds[@]}"; do
            if ! command -v "$cmd" &>/dev/null; then
                missing_packages+=("$cmd")
            fi
        done

        # Отдельная проверка для python-pip, так как он не является системной утилитой
        if ! python3 -m pip --version &>/dev/null; then
            missing_packages+=("python3-pip")
        fi

        # Отдельная проверка для apt-пакета qt6
        if ! dpkg -s "qt6-qpa-plugins" &>/dev/null; then
            missing_packages+=("qt6-qpa-plugins")
        fi

        if [ ${#missing_packages[@]} -gt 0 ]; then
            local unique_missing_packages=($(printf "%s\n" "${missing_packages[@]}" | sort -u))
            msg_warn "Требуются следующие пакеты: ${unique_missing_packages[*]}"
            read -p "Попробовать установить их автоматически? (y/N): " choice
            if [[ "$choice" =~ ^[Yy]$ ]]; then
                msg "Для установки требуются права администратора (sudo)..."
                sudo apt-get update && sudo apt-get install -y "${unique_missing_packages[@]}"
            else
                msg_err "Установка прервана."; exit 1
            fi
        fi
        msg_ok "Все системные зависимости на месте."
    }

    install_backend() {
        msg "Установка/обновление бэкенда $PACKAGE_NAME_WITH_EXTRA..."
        pipx uninstall "$PACKAGE_NAME" &>/dev/null || true
        pipx install "$PACKAGE_NAME_WITH_EXTRA"
        msg_ok "Бэкенд успешно установлен с поддержкой GUI."
    }

    setup_config() {
        msg "Настройка пользовательской конфигурации..."
        mkdir -p "$CONFIG_DIR"
        if [ -f "$CONFIG_FILE" ]; then msg_ok "Файл конфигурации уже существует."; return; fi
        msg "Создаем новый файл конфигурации..."
        local temp_hhcli; temp_hhcli=$(mktemp)
        if ! curl -sSLf -o "$temp_hhcli" "$HHCLI_RAW_URL"; then msg_err "Не удалось скачать hhcli из $HHCLI_RAW_URL."; exit 1; fi
        local config_block; config_block=$(sed -n '/# ---------------------------- НАСТРОЙКИ ------------------------------------/,/# ------------------------ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------------/p' "$temp_hhcli" | sed '1d;$d')
        rm "$temp_hhcli"
        if [ -z "$config_block" ]; then msg_err "Не удалось извлечь блок настроек из hhcli."; exit 1; fi
        echo -e "#!/usr/bin/env bash\n#\n# Файл конфигурации для hhcli\n#\n${config_block}" > "$CONFIG_FILE"
        msg_ok "Файл конфигурации создан: $CONFIG_FILE"
    }

    install_cli() {
        msg "Установка основного скрипта hhcli..."
        local temp_script; temp_script=$(mktemp)
        if ! curl -sSLf -o "$temp_script" "$HHCLI_RAW_URL"; then msg_err "Не удалось скачать скрипт hhcli из $HHCLI_RAW_URL."; exit 1; fi
        local modified_script; modified_script=$(mktemp)
        local final_script; final_script=$(mktemp)
        awk -v config_file="$CONFIG_FILE" 'BEGIN{p=1} /# --- НАСТРОЙКИ ---/{print "\n# --- ЗАГРУЗКА КОНФИГУРАЦИИ ---\n[ -f \""config_file"\" ] && source \""config_file"\"\n"; p=0} /# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---/{p=1} p' "$temp_script" > "$modified_script"
        sed -e "/case \"\$1\" in/a\\
    --auth) hh-applicant-tool authorize; exit 0;;\\
    --list-resumes) hh-applicant-tool list-resumes; exit 0;;\\
    proxy) shift; hh-applicant-tool \"\$@\"; exit 0;;
    " "$modified_script" > "$final_script"
        msg "Установка hhcli в $INSTALL_DIR/$EXECUTABLE_NAME..."
        chmod +x "$final_script"
        if ! sudo mv "$final_script" "$INSTALL_DIR/$EXECUTABLE_NAME"; then msg_err "Не удалось переместить скрипт."; exit 1; fi
        rm "$temp_script" "$modified_script"
        msg_ok "Скрипт hhcli успешно установлен."
    }

    # Последовательность установки
    cleanup_old_versions
    check_and_install_deps
    install_backend
    setup_config
    install_cli
    
    echo
    msg_ok "Установка завершена!"
    echo -e "Теперь вы можете использовать команды:\n  ${C_YELLOW}hhcli${C_RESET}\n  ${C_YELLOW}hhcli --auth${C_RESET}\n  ${C_YELLOW}hhcli --list-resumes${C_RESET}\n\nДля настройки отредактируйте: ${C_YELLOW}${CONFIG_FILE}${C_RESET}"
}

# --- МАРШРУТИЗАТОР КОМАНД ---
main() {
    ACTION="${1:-install}"
    case "$ACTION" in
        install) install_all ;;
        uninstall) uninstall_all ;;
        *) usage ;;
    esac
}

main "$@"