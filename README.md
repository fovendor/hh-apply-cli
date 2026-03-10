# hhcli

[![PyPI version](https://img.shields.io/pypi/v/hhcli.svg?cacheSeconds=360)](https://pypi.org/project/hhcli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Follow on Telegram](https://img.shields.io/badge/Telegram-Join-blue?logo=telegram)](https://t.me/hhcli)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/hhcli?period=total&units=NONE&left_color=GRAY&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/hhcli)


hhcli — это неофициальный CLI-клиент для поиска работы и откликов на hh.ru, позволяющий искать вакансии, просматривать их, отмечать понравившиеся и откликаться на них в интерфейсе терминала. 

> У приложения есть [канал в Telegram](https://t.me/hhcli), где публикуются основные новости проекта и [сайт](https://hhcli.ru/?utm_source=github&utm_medium=social&utm_campaign=readme_github), где можно запустить тестовую версию утилиты для ознакомления с интерфейсом и фичами.

![gif-of-hhcli](img/review.gif "A short demo CLI TUI interface").

## Ключевые возможности

- Локальная база данных SQLite, хранящая профили, историю, кэш вакансий, справочники и т.д.
- Кроссплатформенный TUI-интерфейс (Linux, Windows).
- Профили для разных аккаунтов с поддержкой нескольких резюме внутри одного аккаунта.
- Два режима поиска: автоматический по рекомендациям hh.ru и ручной с настраиваемыми фильтрами.
- Отклик на несколько выбранных вакансий с отправкой сопроводительного письма.
- Хранение истории всех откликов и переписок с работодателями.
- Переписка с работодателями и форматирование текста сообщений прямо внутри приложения.
- Фильтры и отсев дубликатов вакансий (спама по городам). 
- Подсветка компаний, в которые ранее был отклик.
- Подсветка вакансий, на которые был отклик (по названию+компания или по id вакансии).
- Приложение чистит базу данных от устаревшего кэша вакансий (старше 5 дней) и логов (старше 20 дней).
- Выбор и возможность создания собственных тем оформления.

## Установка

<details markdown="1" style="margin-bottom: 1.5rem;">
<summary><h3 style="display:inline">Linux</h3></summary>

### Ubuntu / Debian / Mint (apt)

```bash
sudo apt update && sudo apt install -y
python3 python3-pip pipx git
python3-gi gir1.2-webkit2-4.1 gir1.2-gtk-3.0 libwebkit2gtk-4.1-0
pipx install hhcli --system-site-packages
python3 -m pipx ensurepath
# Перезапустите терминал прежде, чем запускать программу
```

### Arch / Manjaro (pacman)

```bash
sudo pacman -Syu python python-pip pipx git webkit2gtk python-gobject gtk3
pipx install hhcli --system-site-packages
python3 -m pipx ensurepath
# Перезапустите терминал прежде, чем запускать программу
```

### Fedora / RHEL / Rocky (dnf / yum)

```bash
sudo dnf install python3 python3-pip pipx git  # либо sudo yum install ...
# Пакеты WebKit2GTK могут называться webkit2gtk4.1 / webkit2gtk3 / pywebkitgtk
sudo dnf install webkit2gtk4.1 gtk3 gobject-introspection
pipx install hhcli --system-site-packages
python3 -m pipx ensurepath
# Перезапустите терминал прежде, чем запускать программу
```

### Другие дистрибутивы

- Установите Python ≥3.9 и `pipx` из стандартного репозитория.
- Установите WebKit2GTK+ и Python GObject bindings (названия пакетов зависят от дистрибутива).
- Выполните `pipx install hhcli --system-site-packages`.
- Если `pipx` отсутствует, можно поставить локально: `pip install --user pipx && pipx ensurepath`.
</details>

<details markdown="1">
<summary><h3 style="display:inline">Windows</h3></summary>

### Windows 10/11

1. Скачайте Python 3.9+ с [python.org](https://www.python.org/downloads/windows/) и поставьте галочку “Add Python to PATH”.
2. Установите `pipx` (PowerShell или CMD, права администратора не нужны):

```powershell
python -m pip install --upgrade pip
python -m pip install pipx
python -m pipx ensurepath
```
Перезапустите PowerShell (или CMD) и выполните установку hhcli:

```powershell
pipx install hhcli
```

**После установки** откройте новое окно PowerShell/CMD и введите hhcli.

**Для окна авторизации** нужен WebView2 Runtime. Обычно он уже предустановлен в Windows 10/11. Если окно авторизации не появляется, откройте [страницу Microsoft](https://developer.microsoft.com/nl-nl/microsoft-edge/webview2?form=MA13LH) и скачайте **Evergreen Bootstrapper** (x64 для большинства ПК). Fixed Version не требуется.

</details>

## Запуск и авторизация

После установки запустите программу. 

```bash
hhcli
```

Будет предложено создать новый профиль. Придумать короткое имя для вашего профиля (go, python, pm, analyst и т.д). В открывшемся мини-браузере загрузится страница hh.ru для аутентификации на сайте. После успешной аутентификации программа предложит выбрать способ поиска вакансий. Если в аккаунте несколько резюме, сначала будет предложено выбрать, какое из них использовать для поиска.

### Настройка

Настройка приложения (ключевые слова для поиска, шаблон сопроводительного письма, внешний вид) производится внутри приложения. Нажмите клавишу `c` на любом из основных экранов, чтобы перейти в меню настроек.

### Горячие клавиши

| Клавиша | Действие |
| :--- | :--- |
| `Пробел` | Выбрать/снять выбор с текущей вакансии. |
| `A` | Откликнуться на все выбранные вакансии. |
| `H` | Открыть экран с историей откликов для текущего резюме. |
| `C` | Открыть экран настроек профиля. |
| `Q` / `Esc` | Вернуться на предыдущий экран или выйти из приложения. |
| `←` / `→` | Переключение между страницами в списке поиска вакансий. |

### Темы оформления

У приложения есть своя дизайн-система для переключения тем оформления. Чтобы создать новую тему, скопируйте содержимое любого существующего файла `.tcss` из каталога `hhcli/ui/themes` в новый файл и настройте палитру. Новая тема будет доступна на экране настроек.

**Переменные стилей:** базовые переменные отвечают за основные цвета темы (остальные значения собираются автоматически в `hhcli/ui/themes/design_system.tcss`):

- `background1` — основной фон приложения.
- `background2` — фон панелей, карточек, списков.
- `background3` — фон шапок, рамок и выделений.
- `foreground1` — вторичный текст (подписи, подсказки).
- `foreground2` — основной текст.
- `foreground3` — акцентный текст/заголовки.
- `primary` — главный акцент (кнопки, ссылки, выделения).
- `secondary` — дополнительный акцент и ховеры.
- `red`, `orange`, `yellow`, `green`, `blue`, `purple`, `magenta`, `cyan` — цвета статусов и вспомогательных подсветок.
- `scrim` — полупрозрачная подложка для модальных окон.

### Основные команды

| Команда | Описание |
| :--- | :--- |
| `hhcli` | Запускает основной интерфейс. |
| `hhcli -v` / `hhcli --version` | Показывает текущую версию. |
| `hhcli -i` / `hhcli --info` | Выводит информацию о версии, пути к локальной базе и доступных профилях. |

## Обновление / удаление

**Обновить**:

Windows (PowerShell/CMD):

```powershell
pipx upgrade hhcli
```

Linux:
```bash
pipx install hhcli --force --system-site-packages
```

**Удалить**: 
```
pipx uninstall hhcli
```

Если ставили из исходников, удалите виртуальное окружение и данные по пути:

- Linux: `~/.local/share/hhcli`
- Windows: `%LOCALAPPDATA%\hhcli`

## TO DO

Дальнейшие планы:

- Поддержка macOS.
- Расширение возможностей фильтрации и аналитики по истории откликов.
- Нотификация и уведомление о непрочитанных сообщениях работодателей.
- Возможность изменения отправленного ранее сообщения работодателю.

## Предыстория

Изначально hhcli не планировался как большой и долго поддерживаемый проект. Но текущая ситуация на рынке труда: глупые алгоритмы отбора, фейковые вакансии, некомпетентные HR'ы и в целом низкая эффективность ручного поиска и откликов через сайт мотивируют меня развивать этот инструмент дальше.

Прежняя версия hhcli делегировала практически всю работу с API утилите [hh-applicant-tool](https://github.com/s3rgeym/hh-applicant-tool), отчасти поэтому была полностью переписана в текущее исполнение. Подробнее можно ознакомиться в ветке [legacy](https://github.com/fovendor/hhcli/tree/legacy). 

Legacy-версия перестала поддерживаться 26.10.2025, её дальнейшая работоспособность не гарантирована и полностью зависит от `hh-applicant-tool`.

## Лицензия

Проект распространяется под лицензией MIT. Смотрите файл `LICENSE` для подробностей.
