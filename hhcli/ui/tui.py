import html
import random
from datetime import datetime, timedelta
from typing import Iterable, Optional

import html2text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.events import Key, MouseDown
from textual.screen import Screen, ModalScreen
from textual.timer import Timer
from textual.message import Message
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    Markdown,
    SelectionList,
    Static,
    Button,
)
from textual.widgets._option_list import OptionList, Option
from textual.widgets._selection_list import Selection
from rich.text import Text

from ..database import (
    get_full_negotiation_history_for_profile,
    get_negotiation_history_for_resume,
    get_default_config,
    load_profile_config,
    log_to_db,
    record_apply_action,
    save_vacancy_to_cache,
    set_active_profile,
    get_vacancy_from_cache,
    get_dictionary_from_cache,
    save_dictionary_to_cache,
    get_all_profiles,
    get_active_profile_name,
)
from ..reference_data import ensure_reference_data
from ..client import AuthorizationPending
from ..constants import (
    ApiErrorReason,
    ConfigKeys,
    DELIVERED_STATUS_CODES,
    ERROR_REASON_LABELS,
    FAILED_STATUS_CODES,
    LogSource,
    SearchMode,
)

from .config_screen import ConfigScreen
from .css_manager import CssManager
from .widgets import Pagination

CSS_MANAGER = CssManager()
MAX_COLUMN_WIDTH = 200
IGNORED_AFTER_DAYS = 4


def _clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))


def _normalize_width_map(
    width_map: dict[str, int],
    order: list[str],
    *,
    max_value: int | None = None,
) -> dict[str, int]:
    """Переводит сохранённые значения ширины в допустимые пределы."""
    normalized: dict[str, int] = {}
    for key in order:
        raw = width_map.get(key, 0)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        normalized_value = max(1, value)
        if max_value is not None:
            normalized_value = min(max_value, normalized_value)
        normalized[key] = normalized_value
    return normalized

FAILED_REASON_SHORT_LABELS: dict[str, str] = {
    ApiErrorReason.TEST_REQUIRED: "Тест",
    ApiErrorReason.QUESTIONS_REQUIRED: "Вопросы",
    ApiErrorReason.ALREADY_APPLIED: "Дубль",
    ApiErrorReason.NEGOTIATIONS_FORBIDDEN: "Запрет",
    ApiErrorReason.RESUME_NOT_PUBLISHED: "Резюме",
    ApiErrorReason.CONDITIONS_NOT_MET: "Не подходит",
    ApiErrorReason.NOT_FOUND: "Архив",
    ApiErrorReason.BAD_ARGUMENT: "Ошибка",
    ApiErrorReason.UNKNOWN_API_ERROR: "Ошибка",
    ApiErrorReason.NETWORK_ERROR: "Сеть",
}

STATUS_DISPLAY_MAP: dict[str, str] = {
    "applied": "Отклик",
    "invited": "Собес",
    "interview": "Собес",
    "interview_assigned": "Собес",
    "interview_scheduled": "Собес",
    "offer": "Оффер",
    "offer_made": "Оффер",
    "rejected": "Отказ",
    "declined": "Отказ",
    "canceled": "Отказ",
    "cancelled": "Отказ",
    "discard": "Отказ",
    "employer_viewed": "Просмотр",
    "viewed": "Просмотр",
    "seen": "Просмотр",
    "in_progress": "В работе",
    "considering": "В работе",
    "processing": "В работе",
    "responded": "Ответ",
    "response": "Отклик",
    "answered": "Ответ",
    "ignored": "Игнор",
    "hired": "Выход",
    "accepted": "Принят",
    "test_required": "Тест",
    "questions_required": "Вопросы",
}


class VacancySelectionList(SelectionList[str]):
    """Selection list that ignores pointer toggles."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._allow_toggle = False

    def toggle_current(self) -> None:
        """Toggle the highlighted option via code (used by hotkeys)."""
        if self.highlighted is None:
            return
        self._allow_toggle = True
        self.action_select()
        if self._allow_toggle:
            self._allow_toggle = False

    def action_select(self) -> None:
        if not self._allow_toggle:
            return
        super().action_select()

    def _on_option_list_option_selected(
            self, event: OptionList.OptionSelected
    ) -> None:
        if self._allow_toggle:
            self._allow_toggle = False
            super()._on_option_list_option_selected(event)
            return

        event.stop()
        self._allow_toggle = False
        if event.option_index != self.highlighted:
            self.highlighted = event.option_index
        else:
            self.post_message(
                self.SelectionHighlighted(self, event.option_index)
            )

    def on_mouse_down(self, event: MouseDown) -> None:
        if event.button != 1:
            event.stop()
            return
        self.focus()


class HistoryOptionList(OptionList):
    """Option list without toggle markers, used for read-only history."""

    def on_mouse_down(self, event: MouseDown) -> None:
        if event.button != 1:
            event.stop()
            return
        self.focus()


def _normalize(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(str(text).lower().split())

def _normalize_status_code(status: Optional[str]) -> str:
    return (status or "").strip().lower()


def _normalize_reason_code(reason: Optional[str]) -> str:
    return (reason or "").strip().lower()


def _now_for(dt: datetime) -> datetime:
    return datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()


def _set_loader_visible(container: Screen, loader_id: str, visible: bool) -> None:
    """Toggle a loading indicator within the given container."""
    container.query_one(f"#{loader_id}", LoadingIndicator).display = visible


def _is_ignored(applied_at: Optional[datetime]) -> bool:
    if not isinstance(applied_at, datetime):
        return False
    return (_now_for(applied_at) - applied_at) > timedelta(days=IGNORED_AFTER_DAYS)


def _is_delivered(status: Optional[str]) -> bool:
    code = _normalize_status_code(status)
    if not code:
        return False
    if code in FAILED_STATUS_CODES:
        return False
    if code in DELIVERED_STATUS_CODES:
        return True
    for prefix in ("applied", "response", "responded", "invited", "offer"):
        if code.startswith(prefix):
            return True
    return False


def _is_failed(status: Optional[str]) -> bool:
    code = _normalize_status_code(status)
    return code in FAILED_STATUS_CODES


def _format_history_status(
    status: Optional[str],
    reason: Optional[str],
    applied_at: Optional[datetime],
) -> str:
    code = _normalize_status_code(status)
    if not code:
        return "-"

    if code == "failed":
        reason_code = _normalize_reason_code(reason)
        if reason_code in FAILED_REASON_SHORT_LABELS:
            return FAILED_REASON_SHORT_LABELS[reason_code]
        if reason_code in ERROR_REASON_LABELS:
            return ERROR_REASON_LABELS[reason_code]
        return reason or "Ошибка"

    if code in {"applied", "response"}:
        if _is_ignored(applied_at):
            return STATUS_DISPLAY_MAP.get("ignored", "Игнор")
        return STATUS_DISPLAY_MAP.get(code, "Отклик")

    if code in STATUS_DISPLAY_MAP:
        return STATUS_DISPLAY_MAP[code]

    if status:
        return str(status).replace("_", " ").title()
    return "-"


def _collect_delivered(
    history: list[dict],
) -> tuple[set[str], set[str], set[str]]:
    """
    Возвращает:
      delivered_ids  — id вакансий, куда отклик действительно ушёл
      delivered_keys — ключи "title|employer" для delivered_ids
      delivered_employers — нормализованные названия компаний
    """
    processed_vacancies: dict[str, dict] = {}

    for h in history:
        vid = str(h.get("vacancy_id") or "")
        if not vid:
            continue

        status = h.get("status")
        updated_at = h.get("applied_at")

        if vid not in processed_vacancies:
            processed_vacancies[vid] = {
                "last_status": status,
                "last_updated_at": updated_at,
                "has_been_delivered": _is_delivered(status),
                "title": h.get("vacancy_title"),
                "employer": h.get("employer_name"),
            }
        else:
            if updated_at and updated_at > processed_vacancies[vid]["last_updated_at"]:
                processed_vacancies[vid]["last_status"] = status
                processed_vacancies[vid]["last_updated_at"] = updated_at

            if not processed_vacancies[vid]["has_been_delivered"] and _is_delivered(status):
                processed_vacancies[vid]["has_been_delivered"] = True

    delivered_ids: set[str] = set()
    delivered_keys: set[str] = set()
    delivered_employers: set[str] = set()

    for vid, data in processed_vacancies.items():
        is_successfully_delivered = (
            data["has_been_delivered"] and not _is_failed(data["last_status"])
        )
        if is_successfully_delivered:
            delivered_ids.add(vid)
            
            title = _normalize(data["title"])
            employer = _normalize(data["employer"])
            
            key = f"{title}|{employer}"
            if key.strip('|'):
                delivered_keys.add(key)
            
            if employer:
                delivered_employers.add(employer)

    return delivered_ids, delivered_keys, delivered_employers


class ApplyConfirmationDialog(ModalScreen[str | None]):
    """Модальное окно подтверждения отправки откликов."""

    BINDINGS = [
        Binding("escape", "cancel", "Отмена", show=True, key_display="Esc"),
    ]

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count
        self.confirm_code = str(random.randint(1000, 9999))

    def compose(self) -> ComposeResult:
        with Center(id="config-confirm-center"):
            with Vertical(id="config-confirm-dialog", classes="config-confirm") as dialog:
                dialog.border_title = "Подтверждение"
                dialog.styles.border_title_align = "left"
                yield Static(
                    "Если вы уверены, что хотите отправить отклики в выбранные компании, "
                    f"введите число: [b green]{self.confirm_code}[/]",
                    classes="config-confirm__message",
                    expand=True,
                )
                yield Static("", id="apply_confirm_error")
                yield Center(
                    Input(
                        placeholder="Введите число здесь...",
                        id="apply_confirm_input",
                    )
                )
                with Horizontal(classes="config-confirm__buttons"):
                    yield Button("Отправить", id="confirm-submit", variant="success")
                    yield Button("Сброс", id="confirm-reset", classes="decline")
                    yield Button("Отмена", id="confirm-cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._attempt_submit(event.value, event.input)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-submit":
            input_widget = self.query_one("#apply_confirm_input", Input)
            self._attempt_submit(input_widget.value, input_widget)
        elif event.button.id == "confirm-reset":
            self.dismiss("reset")
        elif event.button.id == "confirm-cancel":
            self.dismiss("cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")

    def _attempt_submit(self, value: str, input_widget: Input) -> None:
        if value.strip() == self.confirm_code:
            self.dismiss("submit")
            return
        self.query_one("#apply_confirm_error", Static).update(
            "[b red]Неверное число. Попробуйте ещё раз.[/b red]"
        )
        input_widget.value = ""
        input_widget.focus()


class VacancyListScreen(Screen):
    """Список вакансий + детали справа."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Назад"),
        Binding("_", "toggle_select", "Выбор", show=True, key_display="Space"),
        Binding("a", "apply_for_selected", "Откликнуться"),
        Binding("ф", "apply_for_selected", "Откликнуться (RU)", show=False),
        Binding("h", "open_history", "История", show=True),
        Binding("р", "open_history", "История (RU)", show=False),
        Binding("c", "edit_config", "Настройки", show=True),
        Binding("с", "edit_config", "Настройки (RU)", show=False),
        Binding("left", "prev_page", "Предыдущая страница", show=False),
        Binding("right", "next_page", "Следующая страница", show=False),
    ]
    _debounce_timer: Optional[Timer] = None

    PER_PAGE = 50
    COLUMN_KEYS = ["index", "title", "company", "previous"]

    def __init__(
        self,
        resume_id: str,
        search_mode: SearchMode,
        config_snapshot: Optional[dict] = None,
        *,
        resume_title: str | None = None,
    ) -> None:
        super().__init__()
        self.vacancies: list[dict] = []
        self.vacancies_by_id: dict[str, dict] = {}
        self.resume_id = resume_id
        self.resume_title = (resume_title or "").strip()
        self.selected_vacancies: set[str] = set()
        self._pending_details_id: Optional[str] = None
        self.current_page = 0
        self.total_pages = 1
        self.search_mode = search_mode
        self.config_snapshot = config_snapshot or {}

        self.html_converter = html2text.HTML2Text()
        self.html_converter.body_width = 0
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = True
        self.html_converter.mark_code = True

        defaults = get_default_config()
        self._vacancy_left_percent = defaults[ConfigKeys.VACANCY_LEFT_PANE_PERCENT]
        self._vacancy_column_widths = _normalize_width_map(
            {
                "index": defaults[ConfigKeys.VACANCY_COL_INDEX_WIDTH],
                "title": defaults[ConfigKeys.VACANCY_COL_TITLE_WIDTH],
                "company": defaults[ConfigKeys.VACANCY_COL_COMPANY_WIDTH],
                "previous": defaults[ConfigKeys.VACANCY_COL_PREVIOUS_WIDTH],
            },
            self.COLUMN_KEYS,
            max_value=MAX_COLUMN_WIDTH,
        )

    @staticmethod
    def _format_segment(
        content: str | None,
        width: int,
        *,
        style: str | None = None,
        strike: bool = False,
    ) -> Text:
        segment = Text(content or "", no_wrap=True, overflow="ellipsis")
        segment.truncate(width, overflow="ellipsis")
        if strike:
            segment.stylize("strike", 0, len(segment))
        if style:
            segment.stylize(style, 0, len(segment))
        padding = max(0, width - segment.cell_len)
        if padding:
            segment.append(" " * padding)
        return segment

    def _reload_vacancy_layout_preferences(self) -> None:
        config = load_profile_config(self.app.client.profile_name)
        defaults = get_default_config()
        self._vacancy_left_percent = _clamp(
            int(config.get(ConfigKeys.VACANCY_LEFT_PANE_PERCENT, defaults[ConfigKeys.VACANCY_LEFT_PANE_PERCENT])),
            10,
            90,
        )
        vacancy_width_values = {
            "index": _clamp(
                int(config.get(ConfigKeys.VACANCY_COL_INDEX_WIDTH, defaults[ConfigKeys.VACANCY_COL_INDEX_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "title": _clamp(
                int(config.get(ConfigKeys.VACANCY_COL_TITLE_WIDTH, defaults[ConfigKeys.VACANCY_COL_TITLE_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "company": _clamp(
                int(config.get(ConfigKeys.VACANCY_COL_COMPANY_WIDTH, defaults[ConfigKeys.VACANCY_COL_COMPANY_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "previous": _clamp(
                int(config.get(ConfigKeys.VACANCY_COL_PREVIOUS_WIDTH, defaults[ConfigKeys.VACANCY_COL_PREVIOUS_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
        }
        self._vacancy_column_widths = _normalize_width_map(
            vacancy_width_values, self.COLUMN_KEYS, max_value=MAX_COLUMN_WIDTH
        )

    def _apply_vacancy_workspace_widths(self) -> None:
        try:
            vacancy_panel = self.query_one("#vacancy_panel")
            details_panel = self.query_one("#details_panel")
        except Exception:
            return
        vacancy_panel.styles.width = f"{self._vacancy_left_percent}%"
        right_percent = max(5, 100 - self._vacancy_left_percent)
        details_panel.styles.width = f"{right_percent}%"

    def _update_vacancy_header(self) -> None:
        try:
            header = self.query_one("#vacancy_list_header", Static)
        except Exception:
            return
        header.update(
            self._build_row_text(
                index="№",
                title="Название вакансии",
                company="Компания",
                previous="Откликался",
                index_style="bold",
                title_style="bold",
                company_style="bold",
                previous_style="bold",
            )
        )

    @staticmethod
    def _selection_values(options: Iterable[Selection | str]) -> set[str]:
        values: set[str] = set()
        for option in options:
            value = getattr(option, "value", option)
            if value and value != "__none__":
                values.add(str(value))
        return values

    def _update_selected_from_list(self, selection_list: SelectionList) -> None:
        self.selected_vacancies = self._selection_values(
            selection_list.selected
        )

    def _build_row_text(
        self,
        *,
        index: str,
        title: str,
        company: str | None,
        previous: str,
        strike: bool = False,
        index_style: str | None = None,
        title_style: str | None = None,
        company_style: str | None = "dim",
        previous_style: str | None = None,
    ) -> Text:
        strike_style = "#8c8c8c" if strike else None
        widths = self._vacancy_column_widths

        index_segment = self._format_segment(
            index, widths["index"], style=index_style
        )
        title_segment = self._format_segment(
            title,
            widths["title"],
            style=strike_style or title_style,
            strike=strike,
        )
        company_segment = self._format_segment(
            company,
            widths["company"],
            style=strike_style or company_style,
            strike=strike,
        )
        previous_segment = self._format_segment(
            previous,
            widths["previous"],
            style=strike_style or previous_style,
            strike=strike,
        )

        return Text.assemble(
            index_segment,
            Text("  "),
            title_segment,
            Text("  "),
            company_segment,
            Text("  "),
            previous_segment,
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="vacancy_screen"):
            yield Header(show_clock=True, name="hh-cli")
            with Horizontal(id="vacancy_layout"):
                with Vertical(
                        id="vacancy_panel", classes="pane"
                ) as vacancy_panel:
                    vacancy_panel.border_title = "Вакансии"
                    vacancy_panel.styles.border_title_align = "left"
                    yield Static(id="vacancy_list_header")
                    yield VacancySelectionList(id="vacancy_list")
                    yield Pagination()
                with Vertical(
                        id="details_panel", classes="pane"
                ) as details_panel:
                    details_panel.border_title = "Детали"
                    details_panel.styles.border_title_align = "left"
                    with VerticalScroll(id="details_pane"):
                        yield Markdown(
                            "[dim]Выберите вакансию слева, "
                            "чтобы увидеть детали.[/dim]",
                            id="vacancy_details",
                        )
                        yield LoadingIndicator(id="vacancy_loader")
            yield Footer()

    def on_mount(self) -> None:
        self._reload_vacancy_layout_preferences()
        self._apply_vacancy_workspace_widths()
        self._update_vacancy_header()
        self._fetch_and_refresh_vacancies(page=0)

    def on_screen_resume(self) -> None:
        """При возврате фокусируем список вакансий без принудительного обновления."""
        self.app.apply_theme_from_profile(self.app.client.profile_name)
        self.query_one(VacancySelectionList).focus()

    def _fetch_and_refresh_vacancies(self, page: int) -> None:
        """Запускает воркер для загрузки вакансий и обновления UI."""
        self.current_page = page
        self._reload_vacancy_layout_preferences()
        self._apply_vacancy_workspace_widths()
        self._update_vacancy_header()
        _set_loader_visible(self, "vacancy_loader", True)
        self.query_one(VacancySelectionList).clear_options()
        self.query_one(VacancySelectionList).add_option(
            Selection("Загрузка вакансий...", "__none__", disabled=True)
        )
        self.run_worker(
            self._fetch_worker(page), exclusive=True, thread=True
        )

    async def _fetch_worker(self, page: int) -> None:
        """Воркер, выполняющий API-запрос."""
        try:
            if self.search_mode == SearchMode.MANUAL:
                self.config_snapshot = load_profile_config(self.app.client.profile_name)

            if self.search_mode == SearchMode.AUTO:
                result = self.app.client.get_similar_vacancies(
                    self.resume_id, page=page, per_page=self.PER_PAGE
                )
            else:
                result = self.app.client.search_vacancies(
                    self.config_snapshot, page=page, per_page=self.PER_PAGE
                )
            items = (result or {}).get("items", [])
            pages = (result or {}).get("pages", 1)
            self.app.call_from_thread(self._on_vacancies_loaded, items, pages)
        except AuthorizationPending as auth_exc:
            log_to_db("WARN", LogSource.VACANCY_LIST_FETCH,
                      f"Загрузка вакансий остановлена до завершения авторизации: {auth_exc}")
            self.app.call_from_thread(
                self.app.notify,
                "Завершите авторизацию в браузере и повторите загрузку.",
                title="Авторизация",
                severity="warning",
                timeout=4,
            )
            self.app.call_from_thread(
                self._show_authorization_required_message
            )
        except Exception as e:
            log_to_db("ERROR", LogSource.VACANCY_LIST_FETCH, f"Ошибка загрузки: {e}")
            self.app.notify(f"Ошибка загрузки: {e}", severity="error")

    def _show_authorization_required_message(self) -> None:
        """Отображает в списке вакансий сообщение о необходимости авторизации."""
        vacancy_list = self.query_one(VacancySelectionList)
        vacancy_list.clear_options()
        vacancy_list.add_option(
            Selection("Завершите авторизацию в браузере, затем обновите список.", "__none__", disabled=True)
        )
        _set_loader_visible(self, "vacancy_loader", False)

    def _on_vacancies_loaded(self, items: list, pages: int) -> None:
        """Обработчик успешной загрузки данных."""
        profile_name = self.app.client.profile_name
        config = load_profile_config(profile_name)
        
        filtered_items = items
        if config.get(ConfigKeys.DEDUPLICATE_BY_NAME_AND_COMPANY, True):
            seen_keys = set()
            unique_vacancies = []
            for vac in items:
                name = _normalize(vac.get("name"))
                employer = vac.get("employer") or {}
                emp_key = _normalize(employer.get("id") or employer.get("name"))
                key = f"{name}|{emp_key}"
                
                if key not in seen_keys:
                    seen_keys.add(key)
                    unique_vacancies.append(vac)
            
            num_removed = len(items) - len(unique_vacancies)
            if num_removed > 0:
                self.app.notify(f"Удалено дублей: {num_removed}", title="Фильтрация")

            filtered_items = unique_vacancies

        self.vacancies = filtered_items
        self.vacancies_by_id = {v["id"]: v for v in filtered_items}
        self.total_pages = pages

        pagination = self.query_one(Pagination)
        pagination.update_state(self.current_page, self.total_pages)

        self._refresh_vacancy_list()
        _set_loader_visible(self, "vacancy_loader", False)

    def _refresh_vacancy_list(self) -> None:
        """Перерисовывает список вакансий, сохраняя текущий фокус."""
        vacancy_list = self.query_one(VacancySelectionList)
        highlighted_pos = vacancy_list.highlighted

        vacancy_list.clear_options()

        if not self.vacancies:
            vacancy_list.add_option(
                Selection("Вакансии не найдены.", "__none__", disabled=True)
            )
            return

        profile_name = self.app.client.profile_name
        config = load_profile_config(profile_name)
        history = get_full_negotiation_history_for_profile(profile_name)

        delivered_ids, delivered_keys, delivered_employers = \
            _collect_delivered(history)

        start_offset = self.current_page * self.PER_PAGE

        for idx, vac in enumerate(self.vacancies):
            raw_name = vac["name"]
            strike = False

            if (config.get(ConfigKeys.STRIKETHROUGH_APPLIED_VAC) and
                    vac["id"] in delivered_ids):
                strike = True

            if not strike and config.get(ConfigKeys.STRIKETHROUGH_APPLIED_VAC_NAME):
                employer_data = vac.get("employer") or {}
                key = (f"{_normalize(vac['name'])}|"
                       f"{_normalize(employer_data.get('name'))}")
                if key in delivered_keys:
                    strike = True

            employer_name = (vac.get("employer") or {}).get("name") or "-"
            normalized_employer = _normalize(employer_name)
            previous_company = bool(
                normalized_employer and normalized_employer in delivered_employers
            )
            previous_label = "да" if previous_company else "нет"
            previous_style = "green" if previous_company else "dim"

            row_text = self._build_row_text(
                index=f"#{start_offset + idx + 1}",
                title=raw_name,
                company=employer_name,
                previous=previous_label,
                strike=strike,
                index_style="bold",
                previous_style=previous_style,
            )
            vacancy_list.add_option(Selection(row_text, vac["id"]))

        if highlighted_pos is not None and \
                highlighted_pos < vacancy_list.option_count:
            vacancy_list.highlighted = highlighted_pos
        else:
            vacancy_list.highlighted = 0 if vacancy_list.option_count else None

        vacancy_list.focus()
        self._update_selected_from_list(vacancy_list)

        if vacancy_list.option_count and vacancy_list.highlighted is not None:
            focused_option = vacancy_list.get_option_at_index(
                vacancy_list.highlighted
            )
            if focused_option.value not in (None, "__none__"):
                self.load_vacancy_details(str(focused_option.value))

    def on_selection_list_selection_highlighted(
        self, event: SelectionList.SelectionHighlighted
    ) -> None:
        if self._debounce_timer:
            self._debounce_timer.stop()
        vacancy_id = event.selection.value
        if not vacancy_id or vacancy_id == "__none__":
            return
        self._debounce_timer = self.set_timer(
            0.2, lambda vid=str(vacancy_id): self.load_vacancy_details(vid)
        )

    def on_selection_list_selection_toggled(
        self, event: SelectionList.SelectionToggled
    ) -> None:
        self._update_selected_from_list(event.selection_list)

    def load_vacancy_details(self, vacancy_id: Optional[str]) -> None:
        if not vacancy_id:
            return
        self._pending_details_id = vacancy_id
        log_to_db("INFO", LogSource.VACANCY_LIST_SCREEN,
                  f"Просмотр деталей: {vacancy_id}")
        self.update_vacancy_details(vacancy_id)

    def update_vacancy_details(self, vacancy_id: str) -> None:
        cached = get_vacancy_from_cache(vacancy_id)
        if cached:
            log_to_db("INFO", LogSource.CACHE, f"Кэш попадание: {vacancy_id}")
            _set_loader_visible(self, "vacancy_loader", False)
            self.display_vacancy_details(cached, vacancy_id)
            return

        log_to_db("INFO", LogSource.CACHE,
                  f"Нет в кэше, тянем из API: {vacancy_id}")
        _set_loader_visible(self, "vacancy_loader", True)
        self.query_one("#vacancy_details", Markdown).update("")
        self.run_worker(
            self.fetch_vacancy_details(vacancy_id),
            exclusive=True, thread=True
        )

    async def fetch_vacancy_details(self, vacancy_id: str) -> None:
        try:
            details = self.app.client.get_vacancy_details(vacancy_id)
            save_vacancy_to_cache(vacancy_id, details)
            self.app.call_from_thread(
                self.display_vacancy_details, details, vacancy_id
            )
        except AuthorizationPending as auth_exc:
            log_to_db(
                "WARN",
                LogSource.VACANCY_LIST_SCREEN,
                f"Загрузка деталей вакансии приостановлена: {auth_exc}"
            )
            self.app.call_from_thread(
                self.app.notify,
                "Авторизуйтесь повторно, чтобы посмотреть детали вакансии.",
                title="Авторизация",
                severity="warning",
                timeout=4,
            )
            self.app.call_from_thread(
                self.query_one("#vacancy_details").update,
                "Требуется авторизация для просмотра деталей."
            )
            self.app.call_from_thread(
                _set_loader_visible,
                self,
                "vacancy_loader",
                False,
            )
        except Exception as exc:
            log_to_db("ERROR", LogSource.VACANCY_LIST_SCREEN,
                      f"Ошибка деталей {vacancy_id}: {exc}")
            self.app.call_from_thread(
                self.query_one("#vacancy_details").update,
                f"Ошибка загрузки: {exc}"
            )
            self.app.call_from_thread(
                _set_loader_visible,
                self,
                "vacancy_loader",
                False,
            )

    def display_vacancy_details(self, details: dict, vacancy_id: str) -> None:
        if self._pending_details_id != vacancy_id:
            return

        salary_line = f"**Зарплата:** N/A\n\n"
        salary_data = details.get("salary")
        if salary_data:
            s_from = salary_data.get("from")
            s_to = salary_data.get("to")
            currency = (salary_data.get("currency") or "").upper()
            gross_str = " (до вычета налогов)" if salary_data.get("gross") else ""

            parts = []
            if s_from:
                parts.append(f"от {s_from:,}".replace(",", " "))
            if s_to:
                parts.append(f"до {s_to:,}".replace(",", " "))

            if parts:
                salary_str = " ".join(parts)
                salary_line = (f"**Зарплата:** {salary_str} {currency}{gross_str}\n\n")

        desc_html = details.get("description", "")
        desc_md = self.html_converter.handle(html.unescape(desc_html)).strip()
        skills = details.get("key_skills") or []
        skills_text = "* " + "\n* ".join(
            s["name"] for s in skills
        ) if skills else "Не указаны"

        doc = (
            f"## {details['name']}\n\n"
            f"**Компания:** {details['employer']['name']}\n\n"
            f"**Ссылка:** {details['alternate_url']}\n\n"
            f"{salary_line}"
            f"**Ключевые навыки:**\n{skills_text}\n\n"
            f"**Описание:**\n\n{desc_md}\n"
        )
        self.query_one("#vacancy_details").update(doc)
        _set_loader_visible(self, "vacancy_loader", False)
        self.query_one("#details_pane").scroll_home(animate=False)

    def action_toggle_select(self) -> None:
        self._toggle_current_selection()

    def on_key(self, event: Key) -> None:
        if event.key != "space":
            return
        event.prevent_default()
        event.stop()
        self._toggle_current_selection()

    def _toggle_current_selection(self) -> None:
        selection_list = self.query_one(VacancySelectionList)
        if selection_list.highlighted is None:
            return
        selection = selection_list.get_option_at_index(
            selection_list.highlighted
        )
        if selection.value in (None, "__none__"):
            return
        selection_list.toggle_current()
        log_to_db("INFO", LogSource.VACANCY_LIST_SCREEN,
                  f"Переключили выбор: {selection.value}")

    def action_apply_for_selected(self) -> None:
        if not self.selected_vacancies:
            selection_list = self.query_one(SelectionList)
            self._update_selected_from_list(selection_list)
            if not self.selected_vacancies:
                self.app.notify(
                    "Нет выбранных вакансий.",
                    title="Внимание", severity="warning"
                )
                return
        self.app.push_screen(
            ApplyConfirmationDialog(len(self.selected_vacancies)),
            self.on_apply_confirmed
        )

    def action_edit_config(self) -> None:
        """Открыть экран редактирования конфигурации из списка вакансий."""
        self.app.push_screen(ConfigScreen(), self._on_config_screen_closed)

    def action_open_history(self) -> None:
        """Показать историю откликов для текущего резюме."""
        self.app.push_screen(
            NegotiationHistoryScreen(
                resume_id=self.resume_id,
                resume_title=self.resume_title,
            )
        )

    def _on_config_screen_closed(self, saved: bool | None) -> None:
        """После закрытия настроек сохраняем выбор и при необходимости обновляем данные."""
        self.query_one(VacancySelectionList).focus()
        if not saved:
            return
        self.app.notify("Обновление списка вакансий...", timeout=1.5)
        self._fetch_and_refresh_vacancies(self.current_page)

    def on_apply_confirmed(self, decision: str | None) -> None:
        selection_list = self.query_one(VacancySelectionList)
        selection_list.focus()

        if decision == "reset":
            self.selected_vacancies.clear()
            selection_list.deselect_all()
            self._update_selected_from_list(selection_list)
            self.app.notify("Выбор вакансий сброшен.", title="Сброс", severity="information")
            self._fetch_and_refresh_vacancies(self.current_page)
            return

        if decision != "submit":
            return

        if not self.selected_vacancies:
            return

        self.app.notify(
            f"Отправка {len(self.selected_vacancies)} откликов...",
            title="В процессе", timeout=2
        )
        self.run_worker(self.run_apply_worker(), thread=True)

    async def run_apply_worker(self) -> None:
        profile_name = self.app.client.profile_name
        cover_letter = load_profile_config(profile_name).get(ConfigKeys.COVER_LETTER, "")

        for vacancy_id in list(self.selected_vacancies):
            v = self.vacancies_by_id.get(vacancy_id, {})
            try:
                ok, reason_code = self.app.client.apply_to_vacancy(
                    resume_id=self.resume_id,
                    vacancy_id=vacancy_id, message=cover_letter
                )
            except AuthorizationPending as auth_exc:
                log_to_db(
                    "WARN",
                    LogSource.VACANCY_LIST_SCREEN,
                    f"Отправка откликов остановлена до завершения авторизации: {auth_exc}"
                )
                self.app.call_from_thread(
                    self.app.notify,
                    "Авторизуйтесь повторно и повторите отправку откликов.",
                    title="Авторизация",
                    severity="warning",
                    timeout=4,
                )
                return
            vac_title = v.get("name", vacancy_id)
            emp = (v.get("employer") or {}).get("name")

            human_readable_reason = ERROR_REASON_LABELS.get(
                reason_code, reason_code
            )

            if ok:
                self.app.call_from_thread(
                    self.app.notify, f"[OK] {vac_title}",
                    title="Отклик отправлен"
                )
                record_apply_action(
                    vacancy_id,
                    profile_name,
                    self.resume_id,
                    self.resume_title,
                    vac_title,
                    emp,
                    ApiErrorReason.APPLIED,
                    None,
                )
            else:
                self.app.call_from_thread(
                    self.app.notify,
                    f"[Ошибка: {human_readable_reason}] {vac_title}",
                    title="Отклик не удался", severity="error", timeout=2
                )
                record_apply_action(
                    vacancy_id,
                    profile_name,
                    self.resume_id,
                    self.resume_title,
                    vac_title,
                    emp,
                    "failed",
                    reason_code,
                )

        def finalize() -> None:
            self.app.notify("Все отклики обработаны.", title="Готово")
            self.selected_vacancies.clear()
            selection_list = self.query_one(SelectionList)
            selection_list.deselect_all()
            self._update_selected_from_list(selection_list)
            self._refresh_vacancy_list()

        self.app.call_from_thread(finalize)

    def action_prev_page(self) -> None:
        """Переключиться на предыдущую страницу."""
        if self.current_page > 0:
            self._fetch_and_refresh_vacancies(self.current_page - 1)

    def action_next_page(self) -> None:
        """Переключиться на следующую страницу."""
        if self.current_page < self.total_pages - 1:
            self._fetch_and_refresh_vacancies(self.current_page + 1)

    def on_pagination_page_changed(
        self, message: Pagination.PageChanged
    ) -> None:
        """Обработчик нажатия на кнопку пагинации."""
        self._fetch_and_refresh_vacancies(message.page)


class NegotiationHistoryScreen(Screen):
    """Экран просмотра истории откликов."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Назад"),
        Binding("c", "edit_config", "Настройки", show=True),
        Binding("с", "edit_config", "Настройки (RU)", show=False),
    ]

    COLUMN_KEYS = ["index", "title", "company", "status", "sent", "date"]

    def __init__(self, resume_id: str, resume_title: str | None = None) -> None:
        super().__init__()
        self.resume_id = str(resume_id or "")
        self.resume_title = (resume_title or "").strip()
        self.history: list[dict] = []
        self.history_by_vacancy: dict[str, dict] = {}
        self._pending_details_id: Optional[str] = None
        self._debounce_timer: Optional[Timer] = None

        self.html_converter = html2text.HTML2Text()
        self.html_converter.body_width = 0
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = True
        self.html_converter.mark_code = True

    def compose(self) -> ComposeResult:
        with Vertical(id="history_screen"):
            yield Header(show_clock=True, name="hh-cli")
            if self.resume_title:
                yield Static(
                    f"Резюме: [b cyan]{self.resume_title}[/b cyan]\n",
                    id="history_resume_label",
                )
            with Horizontal(id="history_layout"):
                with Vertical(id="history_panel", classes="pane") as history_panel:
                    history_panel.border_title = "История откликов"
                    history_panel.styles.border_title_align = "left"
                    yield Static(id="history_list_header")
                    yield HistoryOptionList(id="history_list")
                with Vertical(id="history_details_panel", classes="pane") as details_panel:
                    details_panel.border_title = "Детали"
                    details_panel.styles.border_title_align = "left"
                    with VerticalScroll(id="history_details_pane"):
                        yield Markdown(
                            "[dim]Выберите отклик слева, чтобы увидеть детали.[/dim]",
                            id="history_details",
                        )
                        yield LoadingIndicator(id="history_loader")
            yield Footer()

    def on_mount(self) -> None:
        self._reload_history_layout_preferences()
        self._apply_history_workspace_widths()
        self._update_history_header()
        self._refresh_history()

    def on_screen_resume(self) -> None:
        self.app.apply_theme_from_profile(self.app.client.profile_name)
        self._reload_history_layout_preferences()
        self._apply_history_workspace_widths()
        self._update_history_header()
        self.query_one(HistoryOptionList).focus()

    def _reload_history_layout_preferences(self) -> None:
        config = load_profile_config(self.app.client.profile_name)
        defaults = get_default_config()
        self._history_left_percent = _clamp(
            int(config.get(ConfigKeys.HISTORY_LEFT_PANE_PERCENT, defaults[ConfigKeys.HISTORY_LEFT_PANE_PERCENT])),
            10,
            90,
        )
        history_width_values = {
            "index": _clamp(
                int(config.get(ConfigKeys.HISTORY_COL_INDEX_WIDTH, defaults[ConfigKeys.HISTORY_COL_INDEX_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "title": _clamp(
                int(config.get(ConfigKeys.HISTORY_COL_TITLE_WIDTH, defaults[ConfigKeys.HISTORY_COL_TITLE_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "company": _clamp(
                int(config.get(ConfigKeys.HISTORY_COL_COMPANY_WIDTH, defaults[ConfigKeys.HISTORY_COL_COMPANY_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "status": _clamp(
                int(config.get(ConfigKeys.HISTORY_COL_STATUS_WIDTH, defaults[ConfigKeys.HISTORY_COL_STATUS_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "sent": _clamp(
                int(config.get(ConfigKeys.HISTORY_COL_SENT_WIDTH, defaults[ConfigKeys.HISTORY_COL_SENT_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "date": _clamp(
                int(config.get(ConfigKeys.HISTORY_COL_DATE_WIDTH, defaults[ConfigKeys.HISTORY_COL_DATE_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
        }
        self._history_column_widths = _normalize_width_map(
            history_width_values, self.COLUMN_KEYS, max_value=MAX_COLUMN_WIDTH
        )

    def _apply_history_workspace_widths(self) -> None:
        try:
            history_panel = self.query_one("#history_panel")
            details_panel = self.query_one("#history_details_panel")
        except Exception:
            return
        history_panel.styles.width = f"{self._history_left_percent}%"
        details_panel.styles.width = f"{max(5, 100 - self._history_left_percent)}%"

    def _update_history_header(self) -> None:
        try:
            header = self.query_one("#history_list_header", Static)
        except Exception:
            return
        header.update(self._build_header_text())

    def _refresh_history(self) -> None:
        self._reload_history_layout_preferences()
        self._apply_history_workspace_widths()
        header = self.query_one("#history_list_header", Static)
        header.update(self._build_header_text())

        option_list = self.query_one(HistoryOptionList)
        option_list.clear_options()

        profile_name = self.app.client.profile_name
        raw_entries = get_negotiation_history_for_resume(profile_name, self.resume_id)

        entries: list[dict] = []
        for item in raw_entries:
            display_status = _format_history_status(
                item.get("status"),
                item.get("reason"),
                item.get("applied_at"),
            )
            enriched = dict(item)
            enriched["status_display"] = display_status
            enriched["sent_display"] = "да" if bool(item.get("was_delivered")) else "нет"
            entries.append(enriched)

        self.history = entries
        self.history_by_vacancy = {
            str(item.get("vacancy_id")): item for item in entries if item.get("vacancy_id")
        }

        if not entries:
            option_list.add_option(
                Option("История откликов пуста.", "__none__", disabled=True)
            )
            self.query_one("#history_details", Markdown).update(
                "[dim]Нет данных для отображения.[/dim]"
            )
            _set_loader_visible(self, "history_loader", False)
            return

        for idx, entry in enumerate(entries, start=1):
            vacancy_id = str(entry.get("vacancy_id") or "")
            title = entry.get("vacancy_title") or vacancy_id
            company = entry.get("employer_name") or "-"
            applied_label = self._format_date(entry.get("applied_at"))
            status_label = entry.get("status_display") or "-"
            sent_label = entry.get("sent_display") or ("да" if entry.get("was_delivered") else "нет")

            row_text = self._build_row_text(
                index=f"#{idx}",
                title=title,
                company=company,
                status=status_label,
                delivered=sent_label,
                applied=applied_label,
            )
            option_list.add_option(Option(row_text, vacancy_id))

        option_list.highlighted = 0 if option_list.option_count else None
        option_list.focus()

        if option_list.option_count and option_list.highlighted is not None:
            focused_option = option_list.get_option_at_index(option_list.highlighted)
            if focused_option and focused_option.id not in (None, "__none__"):
                self.load_vacancy_details(str(focused_option.id))

    def _build_header_text(self) -> Text:
        widths = self._history_column_widths
        return Text.assemble(
            VacancyListScreen._format_segment("№", widths["index"], style="bold"),
            Text("  "),
            VacancyListScreen._format_segment(
                "Название вакансии", widths["title"], style="bold"
            ),
            Text("  "),
            VacancyListScreen._format_segment("Компания", widths["company"], style="bold"),
            Text("  "),
            VacancyListScreen._format_segment("Статус", widths["status"], style="bold"),
            Text("  "),
            VacancyListScreen._format_segment("✉", widths["sent"], style="bold"),
            Text("  "),
            VacancyListScreen._format_segment(
                "Дата отклика", widths["date"], style="bold"
            ),
        )

    def _build_row_text(
        self,
        *,
        index: str,
        title: str,
        company: str,
        status: str,
        delivered: str,
        applied: str,
    ) -> Text:
        widths = self._history_column_widths
        return Text.assemble(
            VacancyListScreen._format_segment(index, widths["index"], style="bold"),
            Text("  "),
            VacancyListScreen._format_segment(title, widths["title"]),
            Text("  "),
            VacancyListScreen._format_segment(company, widths["company"]),
            Text("  "),
            VacancyListScreen._format_segment(status, widths["status"]),
            Text("  "),
            VacancyListScreen._format_segment(delivered, widths["sent"]),
            Text("  "),
            VacancyListScreen._format_segment(applied, widths["date"]),
        )

    @staticmethod
    def _format_datetime(value: datetime | str | None) -> str:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M")
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                return value
        return "-"

    @staticmethod
    def _format_date(value: datetime | str | None) -> str:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                return value.split(" ")[0]
        return "-"

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if self._debounce_timer:
            self._debounce_timer.stop()
        vacancy_id = event.option.id
        if not vacancy_id or vacancy_id == "__none__":
            return
        self._debounce_timer = self.set_timer(
            0.2, lambda vid=str(vacancy_id): self.load_vacancy_details(vid)
        )

    def load_vacancy_details(self, vacancy_id: Optional[str]) -> None:
        if not vacancy_id:
            return
        self._pending_details_id = vacancy_id
        _set_loader_visible(self, "history_loader", True)
        self.query_one("#history_details", Markdown).update("")

        cached = get_vacancy_from_cache(vacancy_id)
        if cached:
            self.display_history_details(cached, vacancy_id)
            _set_loader_visible(self, "history_loader", False)
            return

        self.run_worker(
            self.fetch_history_details(vacancy_id),
            exclusive=True,
            thread=True,
        )

    async def fetch_history_details(self, vacancy_id: str) -> None:
        try:
            details = self.app.client.get_vacancy_details(vacancy_id)
            save_vacancy_to_cache(vacancy_id, details)
            self.app.call_from_thread(
                self.display_history_details,
                details,
                vacancy_id,
            )
        except AuthorizationPending as auth_exc:
            log_to_db(
                "WARN",
                LogSource.VACANCY_LIST_SCREEN,
                f"Загрузка деталей отклика приостановлена: {auth_exc}"
            )
            self.app.call_from_thread(
                self.app.notify,
                "Авторизуйтесь повторно, чтобы просмотреть детали отклика.",
                title="Авторизация",
                severity="warning",
                timeout=4,
            )
            self.app.call_from_thread(
                self._display_details_error,
                "Требуется авторизация для просмотра деталей."
            )
        except Exception as exc:
            log_to_db("ERROR", LogSource.VACANCY_LIST_SCREEN, f"Ошибка деталей {vacancy_id}: {exc}")
            self.app.call_from_thread(self._display_details_error, f"Ошибка загрузки: {exc}")

    def display_history_details(self, details: dict, vacancy_id: str) -> None:
        if self._pending_details_id != vacancy_id:
            return

        record = self.history_by_vacancy.get(vacancy_id, {})

        salary_line = "N/A"
        salary_data = details.get("salary")
        if salary_data:
            s_from = salary_data.get("from")
            s_to = salary_data.get("to")
            currency = (salary_data.get("currency") or "").upper()
            gross_str = " (до вычета налогов)" if salary_data.get("gross") else ""

            parts = []
            if s_from:
                parts.append(f"от {s_from:,}".replace(",", " "))
            if s_to:
                parts.append(f"до {s_to:,}".replace(",", " "))
            if parts:
                salary_line = f"{' '.join(parts)} {currency}{gross_str}"

        desc_html = details.get("description", "")
        desc_md = self.html_converter.handle(html.unescape(desc_html)).strip()
        skills = details.get("key_skills") or []
        skills_text = "* " + "\n* ".join(
            s["name"] for s in skills
        ) if skills else "Не указаны"

        applied_label = self._format_datetime(record.get("applied_at"))
        status_label = record.get("status_display") or _format_history_status(
            record.get("status"),
            record.get("reason"),
            record.get("applied_at"),
        )
        reason_label = ""
        if _normalize_status_code(record.get("status")) == "failed":
            reason_code = _normalize_reason_code(record.get("reason"))
            if reason_code in ERROR_REASON_LABELS:
                reason_label = ERROR_REASON_LABELS[reason_code]
            elif record.get("reason"):
                reason_label = str(record.get("reason"))
        sent_label = "да" if bool(record.get("was_delivered")) else "нет"

        company_name = details.get("employer", {}).get("name") or record.get("employer_name") or "-"
        link = details.get("alternate_url") or "—"

        doc = (
            f"## {details.get('name', record.get('vacancy_title', vacancy_id))}\n\n"
            f"**Компания:** {company_name}\n\n"
            f"**Ссылка:** {link}\n\n"
            f"**Зарплата:** {salary_line}\n\n"
            f"**Ключевые навыки:**\n{skills_text}\n\n"
            f"**Дата и время отклика:** {applied_label}\n\n"
            f"**Статус:** {status_label}\n\n"
            f"**✉:** {sent_label}\n\n"
        )
        if reason_label:
            doc += f"**Причина:** {reason_label}\n\n"
        doc += "**Описание:**\n\n"
        if desc_md:
            doc += f"{desc_md}\n"
        else:
            doc += "[dim]Описание вакансии недоступно.[/dim]\n"
        self.query_one("#history_details").update(doc)
        _set_loader_visible(self, "history_loader", False)
        self.query_one("#history_details_pane").scroll_home(animate=False)

    def action_edit_config(self) -> None:
        self.app.push_screen(ConfigScreen(), self._on_config_closed)

    def _on_config_closed(self, _: bool | None) -> None:
        self.query_one(HistoryOptionList).focus()

    def _display_details_error(self, message: str) -> None:
        self.query_one("#history_details", Markdown).update(message)
        _set_loader_visible(self, "history_loader", False)

class ResumeSelectionScreen(Screen):
    """Выбор резюме."""

    def __init__(self, resume_data: dict) -> None:
        super().__init__()
        self.resume_data = resume_data
        self.index_to_resume_id: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        yield DataTable(id="resume_table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Должность", "Ссылка")
        self.index_to_resume_id.clear()

        items = self.resume_data.get("items", [])
        if not items:
            table.add_row("[b]У вас нет ни одного резюме.[/b]")
            return

        for r in items:
            table.add_row(f"[bold green]{r.get('title')}[/bold green]",
                          r.get("alternate_url"))
            self.index_to_resume_id.append(r.get("id"))

    def on_data_table_row_selected(self, _: DataTable.RowSelected) -> None:
        table = self.query_one(DataTable)
        idx = table.cursor_row
        if idx is None or idx < 0 or idx >= len(self.index_to_resume_id):
            return
        resume_id = self.index_to_resume_id[idx]
        resume_title = ""
        for r in self.resume_data.get("items", []):
            if r.get("id") == resume_id:
                resume_title = r.get("title") or ""
                break
        log_to_db("INFO", LogSource.RESUME_SCREEN,
                  f"Выбрано резюме: {resume_id} '{resume_title}'")
        self.app.push_screen(
            SearchModeScreen(
                resume_id=resume_id,
                resume_title=resume_title, is_root_screen=False
            )
        )

    def on_screen_resume(self) -> None:
        self.app.apply_theme_from_profile(self.app.client.profile_name)


class SearchModeScreen(Screen):
    """Выбор режима поиска: авто или ручной."""

    BINDINGS = [
        Binding("1", "run_search('auto')", "Авто", show=False),
        Binding("2", "run_search('manual')", "Ручной", show=False),
        Binding("c", "edit_config", "Настройки", show=True),
        Binding("с", "edit_config", "Настройки (RU)", show=False),
        Binding("escape", "handle_escape", "Назад/Выход", show=True),
    ]

    def __init__(
            self, resume_id: str, resume_title: str, is_root_screen: bool = False
    ) -> None:
        super().__init__()
        self.resume_id = resume_id
        self.resume_title = resume_title
        self.is_root_screen = is_root_screen

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        yield Static(
            f"Выбрано резюме: [b cyan]{self.resume_title}[/b cyan]\n"
        )
        yield Static("[b]Выберите способ поиска вакансий:[/b]")
        yield Static("  [yellow]1)[/] Автоматический (рекомендации hh.ru)")
        yield Static("  [yellow]2)[/] Ручной (поиск по ключевым словам)")
        yield Footer()

    def action_handle_escape(self) -> None:
        if self.is_root_screen:
            self.app.exit()
        else:
            self.app.pop_screen()

    def action_edit_config(self) -> None:
        """Открыть экран редактирования конфигурации."""
        self.app.push_screen(ConfigScreen())

    def on_screen_resume(self) -> None:
        self.app.apply_theme_from_profile(self.app.client.profile_name)

    def action_run_search(self, mode: str) -> None:
        log_to_db("INFO", LogSource.SEARCH_MODE_SCREEN, f"Выбран режим '{mode}'")
        search_mode_enum = SearchMode(mode)
        
        if search_mode_enum == SearchMode.AUTO:
            self.app.push_screen(
                VacancyListScreen(
                    resume_id=self.resume_id,
                    search_mode=SearchMode.AUTO,
                    resume_title=self.resume_title,
                )
            )
        else:
            cfg = load_profile_config(self.app.client.profile_name)
            self.app.push_screen(
                VacancyListScreen(
                    resume_id=self.resume_id,
                    search_mode=SearchMode.MANUAL,
                    config_snapshot=cfg,
                    resume_title=self.resume_title,
                )
            )


class ProfileSelectionScreen(Screen):
    """Выбор профиля, если их несколько."""

    def __init__(self, all_profiles: list[dict]) -> None:
        super().__init__()
        self.all_profiles = all_profiles
        self.index_to_profile: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        yield Static("[b]Выберите профиль:[/b]\n")
        yield DataTable(id="profile_table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Имя профиля", "Email")
        self.index_to_profile.clear()
        for p in self.all_profiles:
            table.add_row(
                f"[bold green]{p['profile_name']}[/bold green]", p["email"]
            )
            self.index_to_profile.append(p["profile_name"])

    def on_data_table_row_selected(self, _: DataTable.RowSelected) -> None:
        table = self.query_one(DataTable)
        idx = table.cursor_row
        if idx is None or idx < 0 or idx >= len(self.index_to_profile):
            return
        profile_name = self.index_to_profile[idx]
        log_to_db("INFO", LogSource.PROFILE_SCREEN, f"Выбран профиль '{profile_name}'")
        set_active_profile(profile_name)
        self.dismiss(profile_name)


class HHCliApp(App):
    """Основное TUI-приложение."""

    CSS_PATH = CSS_MANAGER.css_file
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("q", "quit", "Выход", show=True, priority=True),
        Binding("й", "quit", "Выход (RU)", show=False, priority=True),
    ]

    def __init__(self, client) -> None:
        super().__init__(watch_css=True)
        self.client = client
        self.dictionaries = {}
        self.css_manager = CSS_MANAGER
        self.title = "hh-cli"

    def apply_theme_from_profile(self, profile_name: Optional[str] = None) -> None:
        """Применяет тему, указанную в конфигурации профиля."""
        theme_name: Optional[str] = None
        if profile_name:
            try:
                profile_config = load_profile_config(profile_name)
                theme_name = profile_config.get(ConfigKeys.THEME)
            except Exception as exc:  # pragma: no cover - логируем и используем запасной вариант
                log_to_db(
                    "WARN",
                    LogSource.TUI,
                    f"Не удалось загрузить тему профиля '{profile_name}': {exc}",
                )
        if not theme_name:
            defaults = get_default_config()
            theme_name = defaults.get(ConfigKeys.THEME, "hhcli-base")
        try:
            self.css_manager.set_theme(theme_name or "hhcli-base")
        except ValueError:
            self.css_manager.set_theme("hhcli-base")

    async def on_mount(self) -> None:
        log_to_db("INFO", LogSource.TUI, "Приложение смонтировано")
        all_profiles = get_all_profiles()
        active_profile = get_active_profile_name()
        theme_profile = active_profile
        if not theme_profile and all_profiles:
            theme_profile = all_profiles[0]["profile_name"]
        self.apply_theme_from_profile(theme_profile)

        if not all_profiles:
            self.exit(
                "В базе не найдено ни одного профиля. "
                "Войдите через --auth <имя_профиля>."
            )
            return

        if len(all_profiles) == 1:
            profile_name = all_profiles[0]["profile_name"]
            log_to_db(
                "INFO", LogSource.TUI,
                f"Найден один профиль '{profile_name}', "
                f"используется автоматически."
            )
            set_active_profile(profile_name)
            await self.proceed_with_profile(profile_name)
        else:
            log_to_db("INFO", LogSource.TUI,
                      "Найдено несколько профилей — показ выбора.")
            self.push_screen(
                ProfileSelectionScreen(all_profiles), self.on_profile_selected
            )

    async def on_profile_selected(
            self, selected_profile: Optional[str]
    ) -> None:
        if not selected_profile:
            log_to_db("INFO", LogSource.TUI, "Выбор профиля отменён, выходим.")
            self.exit()
            return
        log_to_db("INFO", LogSource.TUI,
                  f"Выбран профиль '{selected_profile}' из списка.")
        await self.proceed_with_profile(selected_profile)

    async def proceed_with_profile(self, profile_name: str) -> None:
        try:
            self.client.load_profile_data(profile_name)
            self.sub_title = f"Профиль: {profile_name}"
            self.apply_theme_from_profile(profile_name)
            self.client.ensure_active_token()

            self.run_worker(
                self.cache_dictionaries, thread=True, name="DictCacheWorker"
            )

            self.app.notify(
                "Синхронизация истории откликов...",
                title="Синхронизация", timeout=2
            )
            self.run_worker(
                self._sync_history_worker,
                thread=True, name="SyncWorker"
            )

            log_to_db("INFO", LogSource.TUI, f"Загрузка резюме для '{profile_name}'")
            resumes = self.client.get_my_resumes()
            items = (resumes or {}).get("items") or []
            if len(items) == 1:
                r = items[0]
                self.push_screen(
                    SearchModeScreen(
                        resume_id=r["id"],
                        resume_title=r["title"], is_root_screen=True
                    )
                )
            else:
                self.push_screen(ResumeSelectionScreen(resume_data=resumes))
        except AuthorizationPending as auth_exc:
            log_to_db(
                "WARN",
                LogSource.TUI,
                f"Профиль '{profile_name}' требует повторной авторизации: {auth_exc}"
            )
            self.sub_title = f"Профиль: {profile_name} (ожидание авторизации)"
            self.app.notify(
                "Требуется повторная авторизация. "
                "Завершите вход в открывшемся браузере и повторите выбор профиля.",
                title="Авторизация", severity="warning", timeout=6
            )
            all_profiles = get_all_profiles()
            self.push_screen(
                ProfileSelectionScreen(all_profiles), self.on_profile_selected
            )
        except Exception as exc:
            log_to_db("ERROR", LogSource.TUI,
                      f"Критическая ошибка профиля/резюме: {exc}")
            self.exit(result=exc)

    def _sync_history_worker(self) -> None:
        """Синхронизирует историю откликов, учитывая необходимость авторизации."""
        try:
            self.client.sync_negotiation_history()
        except AuthorizationPending as auth_exc:
            log_to_db(
                "WARN",
                LogSource.SYNC_ENGINE,
                f"Синхронизация истории остановлена: {auth_exc}"
            )
            self.call_from_thread(
                self.notify,
                "Авторизация требуется для синхронизации истории откликов.",
                title="Авторизация",
                severity="warning",
                timeout=4,
            )

    async def cache_dictionaries(self) -> None:
        """Проверяет кэш справочников и обновляет его."""
        cached_dicts = get_dictionary_from_cache("main_dictionaries")
        if cached_dicts:
            log_to_db("INFO", LogSource.TUI, "Справочники загружены из кэша.")
            self.dictionaries = cached_dicts
        else:
            log_to_db(
                "INFO", LogSource.TUI,
                "Кэш справочников пуст/устарел. Запрос к API..."
            )
            try:
                live_dicts = self.client.get_dictionaries()
                save_dictionary_to_cache("main_dictionaries", live_dicts)
                self.dictionaries = live_dicts
                log_to_db("INFO", LogSource.TUI,
                          "Справочники успешно закэшированы.")
            except AuthorizationPending as auth_exc:
                log_to_db(
                    "WARN", LogSource.TUI,
                    f"Не удалось загрузить справочники: {auth_exc}"
                )
                self.app.notify(
                    "Завершите авторизацию, чтобы обновить справочники.",
                    title="Авторизация", severity="warning", timeout=4
                )
                return
            except Exception as e:
                log_to_db("ERROR", LogSource.TUI,
                          f"Не удалось загрузить справочники: {e}")
                self.app.notify(
                    "Ошибка загрузки справочников!", severity="error"
                )
                return

        try:
            updates = ensure_reference_data(self.client)
            if updates.get("areas"):
                log_to_db("INFO", LogSource.TUI, "Справочник регионов обновлён.")
            if updates.get("professional_roles"):
                log_to_db(
                    "INFO", LogSource.TUI,
                    "Справочник профессиональных ролей обновлён."
                )
        except AuthorizationPending as auth_exc:
            log_to_db(
                "WARN",
                LogSource.TUI,
                f"Обновление справочников остановлено до завершения авторизации: {auth_exc}"
            )
        except Exception as exc:
            log_to_db(
                "ERROR", LogSource.TUI,
                f"Не удалось обновить справочники регионов/ролей: {exc}"
            )

    def action_quit(self) -> None:
        log_to_db("INFO", LogSource.TUI, "Пользователь запросил выход.")
        self.css_manager.cleanup()
        self.exit()
