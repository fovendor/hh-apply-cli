#!/usr/bin/env bash
set -euo pipefail

PACKAGE_NAME="hh-applicant-tool"
PACKAGE_NAME_WITH_EXTRA="hh-applicant-tool[qt]"
INSTALL_DIR="/usr/local/bin"
EXECUTABLE_NAME="hhcli"
CONFIG_DIR="$HOME/.config/hhcli"
CONFIG_FILE="$CONFIG_DIR/config.sh"
CACHE_DIR="$HOME/.cache/hhcli-cache"
LOG_DIR="$CACHE_DIR"

HHCLI_RAW_URL="https://raw.githubusercontent.com/fovendor/hhcli/legacy/hhcli"
CONFIG_RAW_URL="https://raw.githubusercontent.com/fovendor/hhcli/legay/config.sh"

C_RESET=$'\033[0m'; C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'; C_CYAN=$'\033[0;36m'

msg() { echo "==> ${1}"; }
msg_ok() { echo " ✓  ${1}"; }
msg_warn() { echo " !  ${1}"; }
msg_err() { echo " ✗  ${1}" >&2; }

cleanup_old_versions() {
    msg "Поиск и удаление старых версий и конфигов."
    local old_system_files=("/usr/local/bin/hh-applicant-tool" "/usr/local/bin/hh-apply-cli")
    local old_user_files=("$HOME/.local/bin/hhcli")
    if [ ! -f "$CONFIG_FILE" ] && [ -d "$HOME/.config/hh-applicant-tool" ]; then
        msg_warn "Найден старый конфиг бэкенда. Удаляем..."; rm -rf "$HOME/.config/hh-applicant-tool"; msg_ok "Удалено.";
    fi
    for file in "${old_system_files[@]}"; do if [ -f "$file" ]; then msg_warn "Найден старый файл: $file. Удаляем..."; sudo rm -f "$file" && msg_ok "Удалено."; fi; done
    for file in "${old_user_files[@]}"; do if [ -f "$file" ]; then msg_warn "Найден старый файл: $file. Удаляем..."; rm -f "$file" && msg_ok "Удалено."; fi; done
}

check_and_install_deps() {
    msg "Проверка системных зависимостей."
    if ! command -v apt-get >/dev/null 2>&1; then msg_err "Этот скрипт поддерживает только Debian/Ubuntu."; exit 1; fi
    declare -A CMD2PKG=([fzf]="fzf" [jq]="jq" [w3m]="w3m" [curl]="curl" [git]="git" [python3]="python3" [pipx]="pipx")
    local missing_packages=()
    for cmd in "${!CMD2PKG[@]}"; do if ! type -P "$cmd" >/dev/null 2>&1; then missing_packages+=("${CMD2PKG[$cmd]}"); fi; done
    if ! python3 -m pip --version >/dev/null 2>&1; then missing_packages+=("python3-pip"); fi
    if ! dpkg -s "qt6-qpa-plugins" >/dev/null 2>&1; then missing_packages+=("qt6-qpa-plugins"); fi
    mapfile -t unique_missing < <(printf "%s\n" "${missing_packages[@]}" | awk 'NF' | sort -u)
    if ((${#unique_missing[@]} > 0)); then
        msg_warn "Требуются следующие пакеты: ${unique_missing[*]}"
        msg "Установка зависимостей..."
        sudo -v && sudo apt-get update && sudo apt-get install -y "${unique_missing[@]}"
    fi
    msg_ok "Все системные зависимости на месте."
}

install_backend() {
    msg "Установка/обновление бэкенда $PACKAGE_NAME_WITH_EXTRA."
    pipx uninstall "$PACKAGE_NAME" &>/dev/null || true
    pipx install "$PACKAGE_NAME_WITH_EXTRA"
    msg_ok "Бэкенд успешно установлен."
}

setup_config() {
    msg "Настройка пользовательской конфигурации."
    mkdir -p "$CONFIG_DIR"
    if [ ! -f "$CONFIG_FILE" ]; then
        msg "Файл конфигурации не найден. Создаем новый."
        if ! curl -sSLf -o "$CONFIG_FILE" "$CONFIG_RAW_URL"; then
            msg_err "Не удалось скачать шаблон конфигурации из $CONFIG_RAW_URL."
            exit 1
        fi
        msg_ok "Файл конфигурации создан: $CONFIG_FILE"
    else
        msg_ok "Файл конфигурации уже существует, оставляем без изменений."
    fi
}

install_cli() {
    msg "Установка основной программы."
    local temp_script; temp_script=$(mktemp)
    if ! curl -sSLf -o "$temp_script" "$HHCLI_RAW_URL"; then msg_err "Не удалось скачать скрипт hhcli из $HHCLI_RAW_URL."; exit 1; fi
    local final_script; final_script=$(mktemp)
    sed -e "/case \"\$1\" in/a\\
--auth) hh-applicant-tool authorize; exit 0;;\\
--list-resumes) hh-applicant-tool list-resumes; exit 0;;\\
proxy) shift; hh-applicant-tool \"\$@\"; exit 0;;
" "$temp_script" > "$final_script"
    msg "Установка hhcli в $INSTALL_DIR/$EXECUTABLE_NAME."
    chmod +x "$final_script"
    if ! sudo mv "$final_script" "$INSTALL_DIR/$EXECUTABLE_NAME"; then msg_err "Не удалось переместить скрипт в $INSTALL_DIR."; exit 1; fi
    rm "$temp_script"
    msg_ok "Скрипт hhcli успешно установлен."
}

_uninstall_logic() {
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
    rm -rf "$CACHE_DIR"
    rm -rf "$HOME/hh-reports"
    msg_ok "Кэш и отчеты удалены."
    msg "Полная очистка завершена."
}

_install_logic() {
    msg "Запуск установки hhcli."
    cleanup_old_versions
    check_and_install_deps
    install_backend
    setup_config
    install_cli
    msg "Установка завершена!"
}

usage() {
    echo "Использование: bash <(curl...) [install|uninstall]"
    echo "  install    - Установить или обновить hhcli (действие по умолчанию)."
    echo "  uninstall  - Полностью удалить hhcli и все его данные."
    exit 1
}

uninstall_all() {
    sudo -v || { printf "\n%sОшибка: не удалось получить права администратора.%s\n" "${C_RED}" "${C_RESET}"; exit 1; }

    mkdir -p "$LOG_DIR"
    local log_file="$LOG_DIR/uninstall-$(date +'%Y%m%d-%H%M%S').log"

    printf " %s Удаление hhcli...%s\n" "${C_CYAN}" "${C_RESET}"

    {
        _uninstall_logic
    } > "$log_file" 2>&1

    printf " %s✔ Удаление hhcli завершено. %s\n" "${C_GREEN}" "${C_RESET}"
    printf " %s Подробности в лог-файле: %s%s%s\n" "${C_CYAN}" "${C_YELLOW}" "$log_file" "${C_RESET}"
}

install_all() {
    sudo -v || { printf "\n%sОшибка: не удалось получить права администратора.%s\n" "${C_RED}" "${C_RESET}"; exit 1; }

    mkdir -p "$LOG_DIR"
    local log_file="$LOG_DIR/install-$(date +'%Y%m%d-%H%M%S').log"

    printf " %s Установка hhcli...%s\n" "${C_CYAN}" "${C_RESET}"

    {
        _install_logic
    } > "$log_file" 2>&1

    printf "Краткая справка:\n"
    {
        printf "  Аутентификация:\t%s\n" "${C_YELLOW}hhcli --auth${C_RESET}"
        printf "  Список резюме:\t%s\n" "${C_YELLOW}hhcli --list-resumes${C_RESET}"
        printf "  Настройка:\t%s\n" "${C_YELLOW}nano ${CONFIG_FILE}${C_RESET}"
        printf "  Запуск:\t%s\n" "${C_YELLOW}hhcli${C_RESET}"
    } | column -t -s $'\t'

    printf "\n %s Подробности в лог-файле: %s%s%s\n" "${C_CYAN}" "${C_YELLOW}" "$log_file" "${C_RESET}"
}

main() {
    local ACTION="${1:-install}"
    case "$ACTION" in
        install) install_all ;;
        uninstall) uninstall_all ;;
        *) usage ;;
    esac
}

main "$@"