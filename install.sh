#!/usr/bin/env bash
set -euo pipefail

# --- КОНСТАНТЫ И НАСТРОЙКИ ---
PACKAGE_NAME="hh-applicant-tool"
PACKAGE_NAME_WITH_EXTRA="hh-applicant-tool[qt]"
INSTALL_DIR="/usr/local/bin"
EXECUTABLE_NAME="hhcli"
CONFIG_DIR="$HOME/.config/hhcli"
CONFIG_FILE="$CONFIG_DIR/config.sh"
HHCLI_RAW_URL="https://raw.githubusercontent.com/fovendor/hhcli/main/hhcli"

# --- ЦВЕТА И ФУНКЦИИ ВЫВОДА ---
C_RESET='\033[0m'; C_RED='\033[0;31m'; C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'; C_CYAN='\033[0;36m'
msg() { echo -e "${C_CYAN}==>${C_RESET} ${1}"; }
msg_ok() { echo -e "${C_GREEN} ✓ ${C_RESET} ${1}"; }
msg_warn() { echo -e "${C_YELLOW} ! ${C_RESET} ${1}"; }
msg_err() { echo -e "${C_RED} ✗ ${C_RESET} ${1}" >&2; }

# --- ФУНКЦИЯ ОЧИСТКИ ---
cleanup_old_versions() {
    msg "Поиск и удаление старых версий..."
    local old_system_files=("/usr/local/bin/hh-applicant-tool" "/usr/local/bin/hh-apply-cli")
    local old_user_files=("$HOME/.local/bin/hhcli")
    
    for file_path in "${old_system_files[@]}"; do
        if [ -f "$file_path" ]; then msg_warn "Найден старый системный файл: $file_path. Удаляем..."; sudo rm -f "$file_path" && msg_ok "Удалено."; fi
    done
    
    for file_path in "${old_user_files[@]}"; do
        if [ -f "$file_path" ]; then msg_warn "Найден старый пользовательский файл: $file_path. Удаляем..."; rm -f "$file_path" && msg_ok "Удалено."; fi
    done
}

# 1. Проверка и установка зависимостей (ФИНАЛЬНОЕ ИСПРАВЛЕНИЕ)
check_and_install_deps() {
    msg "Проверка системных зависимостей..."
    local missing_deps=()
    local pkg_manager=""
    local deps_to_check=("fzf" "jq" "w3m" "curl" "git" "python3")

    if command -v apt-get &>/dev/null; then
        pkg_manager="apt"; deps_to_check+=("python3-pip" "pipx" "qt6-qpa-plugins")
    elif command -v dnf &>/dev/null; then
        pkg_manager="dnf"; deps_to_check+=("python3-pip" "pipx" "qt6-qtbase-gui")
    # ... другие менеджеры
    else
        msg_err "Не удалось определить пакетный менеджер."; exit 1
    fi

    for dep in "${deps_to_check[@]}"; do
        local is_missing=false
        case "$dep" in
            python3-pip)
                ! python3 -m pip --version &>/dev/null && is_missing=true
                ;;
            pipx)
                ! command -v pipx &>/dev/null && is_missing=true
                ;;
            qt6-qpa-plugins)
                # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
                # Используем более надежный метод dpkg -s
                if [[ "$pkg_manager" == "apt" ]] && ! dpkg -s "$dep" &>/dev/null; then
                    is_missing=true
                fi
                ;;
            *) # Проверка для остальных команд
                ! command -v "$dep" &>/dev/null && is_missing=true
                ;;
        esac
        
        if [[ "$is_missing" == true ]]; then
            missing_deps+=("$dep")
        fi
    done

    if [ ${#missing_deps[@]} -gt 0 ]; then
        msg_warn "Требуются следующие пакеты: ${missing_deps[*]}"
        read -p "Попробовать установить их автоматически? (y/N): " choice
        if [[ "$choice" =~ ^[Yy]$ ]]; then
            msg "Для установки требуются права администратора (sudo)..."
            sudo apt-get update && sudo apt-get install -y "${missing_deps[@]}"
        else
            msg_err "Установка прервана."; exit 1
        fi
    fi
    msg_ok "Все системные зависимости на месте."
}


# 2. Установка бэкенда
install_backend() {
    msg "Установка бэкенда $PACKAGE_NAME_WITH_EXTRA из PyPI..."
    if [[ ! ":$PATH:" == *":$HOME/.local/bin:"* ]]; then export PATH="$PATH:$HOME/.local/bin"; fi
    pipx ensurepath &>/dev/null
    
    msg "Переустановка бэкенда для включения поддержки GUI..."
    pipx uninstall "$PACKAGE_NAME" &>/dev/null || true
    pipx install "$PACKAGE_NAME_WITH_EXTRA"

    msg_ok "Бэкенд $PACKAGE_NAME успешно установлен с поддержкой GUI."
}

# 3. Создание файла конфигурации
setup_config() {
    msg "Настройка пользовательской конфигурации..."
    mkdir -p "$CONFIG_DIR"
    if [ -f "$CONFIG_FILE" ]; then msg_ok "Файл конфигурации уже существует."; return; fi

    msg "Создаем новый файл конфигурации..."
    local config_block
    config_block=$(curl -sSL "$HHCLI_RAW_URL" | sed -n '/# ---------------------------- НАСТРОЙКИ ------------------------------------/,/# ------------------------ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------------/p' | sed '1d;$d')
    if [ -z "$config_block" ]; then msg_err "Не удалось извлечь блок настроек из URL: $HHCLI_RAW_URL"; exit 1; fi
    echo -e "#!/usr/bin/env bash\n#\n# Файл конфигурации для hhcli\n#\n${config_block}" > "$CONFIG_FILE"
    msg_ok "Файл конфигурации создан: $CONFIG_FILE"
}

# 4. Установка основного скрипта
install_cli() {
    msg "Установка основного скрипта hhcli..."
    local temp_script; temp_script=$(mktemp)
    
    if ! curl -sSLf -o "$temp_script" "$HHCLI_RAW_URL"; then
        msg_err "Не удалось скачать скрипт hhcli из $HHCLI_RAW_URL."; exit 1
    fi

    local modified_script; modified_script=$(mktemp)
    local final_script; final_script=$(mktemp)

    awk -v config_file="$CONFIG_FILE" 'BEGIN{p=1} /# ---------------------------- НАСТРОЙКИ ------------------------------------/{print "\n# --- ЗАГРУЗКА КОНФИГУРАЦИИ ---\n[ -f \""config_file"\" ] && source \""config_file"\"\n"; p=0} /# ------------------------ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------------/{p=1} p' "$temp_script" > "$modified_script"
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

# --- ГЛАВНАЯ ФУНКЦИЯ ---
main() {
    msg "Запуск установки hhcli"
    cleanup_old_versions
    check_and_install_deps
    install_backend
    setup_config
    install_cli
    
    echo
    msg_ok "Установка завершена!"
    echo -e "Теперь вы можете использовать команды:\n  ${C_YELLOW}hhcli${C_RESET}\n  ${C_YELLOW}hhcli --auth${C_RESET}\n  ${C_YELLOW}hhcli --list-resumes${C_RESET}\n\nДля настройки отредактируйте: ${C_YELLOW}${CONFIG_FILE}${C_RESET}"
}

main