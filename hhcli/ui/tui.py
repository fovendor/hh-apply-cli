import html
import random
from typing import Iterable, Optional

import html2text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.events import Key, MouseDown
from textual.screen import Screen
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
from textual.widgets._option_list import OptionList
from textual.widgets._selection_list import Selection
from rich.text import Text

from ..database import (
    get_full_negotiation_history_for_profile,
    load_profile_config,
    log_to_db,
    record_apply_action,
    save_vacancy_to_cache,
    set_active_profile,
    get_vacancy_from_cache,
    get_dictionary_from_cache,
    save_dictionary_to_cache,
    get_all_profiles,
)
from ..reference_data import ensure_reference_data
from ..constants import (
    ApiErrorReason,
    ConfigKeys,
    LogSource,
    SearchMode,
)

from .config_screen import ConfigScreen
from .css_manager import CssManager
from .widgets import Pagination

CSS_MANAGER = CssManager()

ERROR_REASON_MAP = {
    ApiErrorReason.APPLIED: "Отклик отправлен",
    ApiErrorReason.ALREADY_APPLIED: "Вы уже откликались",
    ApiErrorReason.TEST_REQUIRED: "Требуется пройти тест",
    ApiErrorReason.QUESTIONS_REQUIRED: "Требуются ответы на вопросы",
    ApiErrorReason.NEGOTIATIONS_FORBIDDEN: "Работодатель запретил отклики",
    ApiErrorReason.RESUME_NOT_PUBLISHED: "Резюме не опубликовано",
    ApiErrorReason.CONDITIONS_NOT_MET: "Не выполнены условия",
    ApiErrorReason.NOT_FOUND: "Вакансия в архиве",
    ApiErrorReason.BAD_ARGUMENT: "Некорректные параметры",
    ApiErrorReason.UNKNOWN_API_ERROR: "Неизвестная ошибка API",
    ApiErrorReason.NETWORK_ERROR: "Ошибка сети",
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


def _normalize(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(str(text).lower().split())


DELIVERED_MARKERS = ("отклик", "доставл", "прочитан", "applied")
FAILED_MARKERS = ("failed",)


def _is_delivered(status: Optional[str]) -> bool:
    s = _normalize(status)
    return any(m in s for m in DELIVERED_MARKERS)


def _is_failed(status: Optional[str]) -> bool:
    s = _normalize(status)
    return any(m in s for m in FAILED_MARKERS)


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


class ApplyConfirmationScreen(Screen):
    """Экран подтверждения отправки откликов."""

    BINDINGS = [
        Binding("escape", "cancel", "Назад", show=True, key_display="Esc"),
    ]

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count
        self.confirm_code = str(random.randint(1000, 9999))

    def compose(self) -> ComposeResult:
        yield Center(
            Static(
                f"Выбрано [b]{self.count}[/] вакансий для отклика.\n\n"
                f"Для подтверждения введите число [b green]"
                f"{self.confirm_code}[/]",
                id="confirm_label",
            ),
            Input(placeholder="Введите число здесь..."),
            Static("", id="error_label"),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value == self.confirm_code:
            self.dismiss(True)
            return
        self.query_one("#error_label", Static).update(
            "[b red]Неверное число. Попробуйте ещё раз.[/b red]"
        )
        event.input.value = ""

    def action_cancel(self) -> None:
        self.dismiss(False)


class VacancyListScreen(Screen):
    """Список вакансий + детали справа."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Назад"),
        Binding("_", "toggle_select", "Выбор", show=True, key_display="Space"),
        Binding("a", "apply_for_selected", "Отклик"),
        Binding("c", "edit_config", "Конфиг", show=True),
        Binding("с", "edit_config", "Конфиг (RU)", show=False),
        Binding("left", "prev_page", "Предыдущая страница", show=False),
        Binding("right", "next_page", "Следующая страница", show=False),
    ]
    _debounce_timer: Optional[Timer] = None

    ID_WIDTH = 5
    TITLE_WIDTH = 40
    COMPANY_WIDTH = 24
    PREVIOUS_WIDTH = 10
    PER_PAGE = 50

    def __init__(
        self, resume_id: str, search_mode: SearchMode,
        config_snapshot: Optional[dict] = None
    ) -> None:
        super().__init__()
        self.vacancies: list[dict] = []
        self.vacancies_by_id: dict[str, dict] = {}
        self.resume_id = resume_id
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

        index_segment = self._format_segment(
            index, self.ID_WIDTH, style=index_style
        )
        title_segment = self._format_segment(
            title,
            self.TITLE_WIDTH,
            style=strike_style or title_style,
            strike=strike,
        )
        company_segment = self._format_segment(
            company,
            self.COMPANY_WIDTH,
            style=strike_style or company_style,
            strike=strike,
        )
        previous_segment = self._format_segment(
            previous,
            self.PREVIOUS_WIDTH,
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
                        yield LoadingIndicator()
            yield Footer()

    def on_mount(self) -> None:
        header = self.query_one("#vacancy_list_header", Static)
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
        self._fetch_and_refresh_vacancies(page=0)

    def on_screen_resume(self) -> None:
        """При возврате фокусируем список вакансий без принудительного обновления."""
        self.query_one(VacancySelectionList).focus()

    def _fetch_and_refresh_vacancies(self, page: int) -> None:
        """Запускает воркер для загрузки вакансий и обновления UI."""
        self.current_page = page
        self.query_one(LoadingIndicator).display = True
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
        except Exception as e:
            log_to_db("ERROR", LogSource.VACANCY_LIST_FETCH, f"Ошибка загрузки: {e}")
            self.app.notify(f"Ошибка загрузки: {e}", severity="error")

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
        self.query_one(LoadingIndicator).display = False

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
            self.display_vacancy_details(cached, vacancy_id)
            return

        log_to_db("INFO", LogSource.CACHE,
                  f"Нет в кэше, тянем из API: {vacancy_id}")
        self.query_one(LoadingIndicator).display = True
        self.query_one("#vacancy_details").update("Загрузка...")
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
        except Exception as exc:
            log_to_db("ERROR", LogSource.VACANCY_LIST_SCREEN,
                      f"Ошибка деталей {vacancy_id}: {exc}")
            self.app.call_from_thread(
                self.query_one("#vacancy_details").update,
                f"Ошибка загрузки: {exc}"
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
        self.query_one(LoadingIndicator).display = False
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
            ApplyConfirmationScreen(len(self.selected_vacancies)),
            self.on_apply_confirmed
        )

    def action_edit_config(self) -> None:
        """Открыть экран редактирования конфигурации из списка вакансий."""
        self.app.push_screen(ConfigScreen(), self._on_config_screen_closed)

    def _on_config_screen_closed(self, saved: bool | None) -> None:
        """После закрытия настроек сохраняем выбор и при необходимости обновляем данные."""
        self.query_one(VacancySelectionList).focus()
        if not saved:
            return
        self.app.notify("Обновление списка вакансий...", timeout=1.5)
        self._fetch_and_refresh_vacancies(self.current_page)

    def on_apply_confirmed(self, ok: bool) -> None:
        if not ok:
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
            ok, reason_code = self.app.client.apply_to_vacancy(
                resume_id=self.resume_id,
                vacancy_id=vacancy_id, message=cover_letter
            )
            vac_title = v.get("name", vacancy_id)
            emp = (v.get("employer") or {}).get("name")

            human_readable_reason = ERROR_REASON_MAP.get(
                reason_code, reason_code
            )

            if ok:
                self.app.call_from_thread(
                    self.app.notify, f"[OK] {vac_title}",
                    title="Отклик отправлен"
                )
                record_apply_action(
                    vacancy_id, profile_name, vac_title, emp, ApiErrorReason.APPLIED, None
                )
            else:
                self.app.call_from_thread(
                    self.app.notify,
                    f"[Ошибка: {human_readable_reason}] {vac_title}",
                    title="Отклик не удался", severity="error", timeout=2
                )
                record_apply_action(
                    vacancy_id, profile_name, vac_title, emp, "failed",
                    human_readable_reason
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

    def action_run_search(self, mode: str) -> None:
        log_to_db("INFO", LogSource.SEARCH_MODE_SCREEN, f"Выбран режим '{mode}'")
        search_mode_enum = SearchMode(mode)
        
        if search_mode_enum == SearchMode.AUTO:
            self.app.push_screen(
                VacancyListScreen(
                    resume_id=self.resume_id, search_mode=SearchMode.AUTO
                )
            )
        else:
            cfg = load_profile_config(self.app.client.profile_name)
            self.app.push_screen(
                VacancyListScreen(
                    resume_id=self.resume_id, search_mode=SearchMode.MANUAL,
                    config_snapshot=cfg
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

    async def on_mount(self) -> None:
        log_to_db("INFO", LogSource.TUI, "Приложение смонтировано")
        all_profiles = get_all_profiles()

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
            profile_config = load_profile_config(profile_name)
            self.css_manager.set_theme(profile_config.get(ConfigKeys.THEME, "hhcli-base"))

            self.run_worker(
                self.cache_dictionaries, thread=True, name="DictCacheWorker"
            )

            self.app.notify(
                "Синхронизация истории откликов...",
                title="Синхронизация", timeout=2
            )
            self.run_worker(
                self.client.sync_negotiation_history,
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
        except Exception as exc:
            log_to_db("ERROR", LogSource.TUI,
                      f"Критическая ошибка профиля/резюме: {exc}")
            self.exit(result=exc)

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
        except Exception as exc:
            log_to_db(
                "ERROR", LogSource.TUI,
                f"Не удалось обновить справочники регионов/ролей: {exc}"
            )

    def action_quit(self) -> None:
        log_to_db("INFO", LogSource.TUI, "Пользователь запросил выход.")
        self.css_manager.cleanup()
        self.exit()
