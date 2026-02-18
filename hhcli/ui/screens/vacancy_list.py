from __future__ import annotations

import html
import html2text
from datetime import datetime
from typing import Iterable, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Header, LoadingIndicator, Markdown, SelectionList, Static
from textual.widgets._selection_list import Selection

from ...client import AuthorizationPending, CaptchaRequired
from ...constants import ConfigKeys, LogSource, SearchMode
from ...database import (
    get_default_config,
    get_vacancy_from_cache,
    load_profile_config,
    log_to_db,
    extract_stats_from_response,
    merge_vacancy_stats,
    should_refresh_stats,
    save_vacancy_to_cache,
)
from ..dialogs.apply_confirmation import ApplyConfirmationDialog
from ..modules.apply_service import apply_to_vacancies
from ..modules.history_service import load_delivery_summary
from ..modules.vacancy_service import deduplicate_vacancies, load_vacancies
from ..utils.constants import MAX_COLUMN_WIDTH
from ..utils.formatting import (
    clamp,
    format_segment,
    normalize,
    normalize_width_map,
    set_loader_visible,
)
from ..widgets import Pagination, VacancySelectionList
from .config import ConfigScreen
from .history import NegotiationHistoryScreen

class VacancyListScreen(Screen):
    """Экран со списком вакансий и панелью подробностей"""

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
        self._stats_timer: Optional[Timer] = None
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
        self._vacancy_column_widths = normalize_width_map(
            {
                "index": defaults[ConfigKeys.VACANCY_COL_INDEX_WIDTH],
                "title": defaults[ConfigKeys.VACANCY_COL_TITLE_WIDTH],
                "company": defaults[ConfigKeys.VACANCY_COL_COMPANY_WIDTH],
                "previous": defaults[ConfigKeys.VACANCY_COL_PREVIOUS_WIDTH],
            },
            self.COLUMN_KEYS,
            max_value=MAX_COLUMN_WIDTH,
        )

    def _reload_vacancy_layout_preferences(self) -> None:
        config = load_profile_config(self.app.client.profile_name)
        defaults = get_default_config()
        self._vacancy_left_percent = clamp(
            int(config.get(ConfigKeys.VACANCY_LEFT_PANE_PERCENT, defaults[ConfigKeys.VACANCY_LEFT_PANE_PERCENT])),
            10,
            90,
        )
        vacancy_width_values = {
            "index": clamp(
                int(config.get(ConfigKeys.VACANCY_COL_INDEX_WIDTH, defaults[ConfigKeys.VACANCY_COL_INDEX_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "title": clamp(
                int(config.get(ConfigKeys.VACANCY_COL_TITLE_WIDTH, defaults[ConfigKeys.VACANCY_COL_TITLE_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "company": clamp(
                int(config.get(ConfigKeys.VACANCY_COL_COMPANY_WIDTH, defaults[ConfigKeys.VACANCY_COL_COMPANY_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "previous": clamp(
                int(config.get(ConfigKeys.VACANCY_COL_PREVIOUS_WIDTH, defaults[ConfigKeys.VACANCY_COL_PREVIOUS_WIDTH])),
                1,
                MAX_COLUMN_WIDTH,
            ),
        }
        self._vacancy_column_widths = normalize_width_map(
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

        index_segment = format_segment(
            index, widths["index"], style=index_style
        )
        title_segment = format_segment(
            title,
            widths["title"],
            style=strike_style or title_style,
            strike=strike,
        )
        company_segment = format_segment(
            company,
            widths["company"],
            style=strike_style or company_style,
            strike=strike,
        )
        previous_segment = format_segment(
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
            yield Header(show_clock=True, name="hhcli")
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
        """Возвращает фокус списку вакансий без принудительной перезагрузки"""
        self.app.apply_theme_from_profile(self.app.client.profile_name)
        self.query_one(VacancySelectionList).focus()

    def _fetch_and_refresh_vacancies(self, page: int) -> None:
        """Запускает загрузку вакансий и обновляет интерфейс"""
        self.current_page = page
        self._reload_vacancy_layout_preferences()
        self._apply_vacancy_workspace_widths()
        self._update_vacancy_header()
        set_loader_visible(self, "vacancy_loader", True)
        self.query_one(VacancySelectionList).clear_options()
        self.query_one(VacancySelectionList).add_option(
            Selection("Загрузка вакансий...", "__none__", disabled=True)
        )
        self.run_worker(
            self._fetch_worker(page), exclusive=True, thread=True
        )

    async def _fetch_worker(self, page: int) -> None:
        """Фоновый воркер, который делает API-запрос за вакансиями"""
        try:
            items, pages, snapshot = load_vacancies(
                self.app.client,
                resume_id=self.resume_id,
                search_mode=self.search_mode,
                config_snapshot=self.config_snapshot,
                page=page,
                per_page=self.PER_PAGE,
            )
            self.config_snapshot = snapshot
            self.app.call_from_thread(self._on_vacancies_loaded, items, pages)
        except CaptchaRequired as captcha_exc:
            log_to_db(
                "WARN",
                LogSource.VACANCY_LIST_FETCH,
                f"Загрузка вакансий остановлена до завершения капчи: {captcha_exc}",
            )
            self.app.call_from_thread(
                self.app.notify,
                "hh.ru запросил капчу. Завершите проверку в окне webview и повторите загрузку.",
                title="Капча",
                severity="warning",
                timeout=5,
            )
            self.app.call_from_thread(self._show_captcha_required_message)
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
        """Показывает в списке вакансий инструкцию о необходимости авторизации"""
        vacancy_list = self.query_one(VacancySelectionList)
        vacancy_list.clear_options()
        vacancy_list.add_option(
            Selection("Завершите авторизацию в браузере, затем обновите список.", "__none__", disabled=True)
        )
        set_loader_visible(self, "vacancy_loader", False)

    def _show_captcha_required_message(self) -> None:
        """Показывает в списке вакансий инструкцию о необходимости прохождения капчи."""
        vacancy_list = self.query_one(VacancySelectionList)
        vacancy_list.clear_options()
        vacancy_list.add_option(
            Selection("hh.ru запросил капчу. Завершите проверку в окне webview и обновите список.", "__none__", disabled=True)
        )
        set_loader_visible(self, "vacancy_loader", False)

    def _on_vacancies_loaded(self, items: list, pages: int) -> None:
        """Обрабатывает успешную загрузку вакансий"""
        profile_name = self.app.client.profile_name
        config = load_profile_config(profile_name)
        
        filtered_items, removed = deduplicate_vacancies(
            items,
            enabled=config.get(ConfigKeys.DEDUPLICATE_BY_NAME_AND_COMPANY, True),
        )
        if removed > 0:
            self.app.notify(f"Удалено дублей: {removed}", title="Фильтрация")

        self.vacancies = filtered_items
        self.vacancies_by_id = {v["id"]: v for v in filtered_items}
        self.total_pages = pages

        pagination = self.query_one(Pagination)
        pagination.update_state(self.current_page, self.total_pages)

        self._refresh_vacancy_list()
        set_loader_visible(self, "vacancy_loader", False)

    def _refresh_vacancy_list(self) -> None:
        """Перерисовывает список вакансий и сохраняет фокус"""
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
        delivered_ids, delivered_keys, delivered_employers = load_delivery_summary(profile_name)

        start_offset = self.current_page * self.PER_PAGE

        for idx, vac in enumerate(self.vacancies):
            raw_name = vac["name"]
            strike = False

            if (config.get(ConfigKeys.STRIKETHROUGH_APPLIED_VAC) and
                    vac["id"] in delivered_ids):
                strike = True

            if not strike and config.get(ConfigKeys.STRIKETHROUGH_APPLIED_VAC_NAME):
                employer_data = vac.get("employer") or {}
                key = (f"{normalize(vac['name'])}|"
                       f"{normalize(employer_data.get('name'))}")
                if key in delivered_keys:
                    strike = True

            employer_name = (vac.get("employer") or {}).get("name") or "-"
            normalized_employer = normalize(employer_name)
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
        if self._stats_timer:
            self._stats_timer.stop()
            self._stats_timer = None
        self._pending_details_id = vacancy_id
        log_to_db("INFO", LogSource.VACANCY_LIST_SCREEN,
                  f"Просмотр деталей: {vacancy_id}")
        self.update_vacancy_details(vacancy_id)

    def update_vacancy_details(self, vacancy_id: str) -> None:
        cached = get_vacancy_from_cache(vacancy_id)
        if cached:
            log_to_db("INFO", LogSource.CACHE, f"Кэш попадание: {vacancy_id}")
            set_loader_visible(self, "vacancy_loader", False)
            self.display_vacancy_details(cached, vacancy_id)
            # Стартуем немедленный фоновой рефреш, чтобы показать актуальные цифры.
            self.run_worker(
                self._refresh_stats_worker(vacancy_id, force=True),
                exclusive=False,
                thread=True,
            )
            return

        log_to_db("INFO", LogSource.CACHE,
                  f"Нет в кэше, тянем из API: {vacancy_id}")
        set_loader_visible(self, "vacancy_loader", True)
        self.query_one("#vacancy_details", Markdown).update("")
        self.run_worker(
            self.fetch_vacancy_details(vacancy_id),
            exclusive=True, thread=True
        )

    async def fetch_vacancy_details(self, vacancy_id: str) -> None:
        try:
            details = self.app.client.get_vacancy_details(vacancy_id)
            responses, viewing = extract_stats_from_response(details)
            details = merge_vacancy_stats(details, responses, viewing)
            save_vacancy_to_cache(vacancy_id, details)
            self.app.call_from_thread(
                self.display_vacancy_details, details, vacancy_id
            )
            # Даже если API деталей не вернул counters, сразу дергаем stats-эндпоинт.
            self.run_worker(
                self._refresh_stats_worker(vacancy_id, force=True),
                exclusive=False,
                thread=True,
            )
        except CaptchaRequired as captcha_exc:
            log_to_db(
                "WARN",
                LogSource.VACANCY_LIST_SCREEN,
                f"Загрузка деталей вакансии остановлена до завершения капчи: {captcha_exc}",
            )
            self.app.call_from_thread(
                self.app.notify,
                "hh.ru запросил капчу. Завершите проверку в окне webview.",
                title="Капча",
                severity="warning",
                timeout=5,
            )
            self.app.call_from_thread(
                self.query_one("#vacancy_details").update,
                "Требуется капча hh.ru. Завершите проверку в окне webview."
            )
            self.app.call_from_thread(
                set_loader_visible,
                self,
                "vacancy_loader",
                False,
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
                set_loader_visible,
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
                set_loader_visible,
                self,
                "vacancy_loader",
                False,
            )

    def display_vacancy_details(self, details: dict, vacancy_id: str) -> None:
        if self._pending_details_id != vacancy_id:
            return

        meta = (details or {}).get("_hhcli_meta") or {}
        responses_count = meta.get("responses_count") or details.get("responses_count")
        viewing_count = (
            meta.get("viewing_count")
            or details.get("online_users_count")
            or (details.get("counters") or {}).get("views")
        )
        stats_block = ""
        if (responses_count is not None) or (viewing_count is not None):
            left = responses_count if responses_count is not None else "—"
            right = viewing_count if viewing_count is not None else "—"
            stats_block = (
                "| **Откликнулось** | **Смотрят сейчас** |\n"
                "| --- | --- |\n"
                f"| {left} | {right} |\n\n"
            )

        salary_line = "**Зарплата:** N/A\n\n"
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
            f"{stats_block}"
            f"{salary_line}"
            f"**Ключевые навыки:**\n{skills_text}\n\n"
            f"**Описание:**\n\n{desc_md}\n"
        )
        self.query_one("#vacancy_details").update(doc)
        set_loader_visible(self, "vacancy_loader", False)
        self.query_one("#details_pane").scroll_home(animate=False)
        self._schedule_stats_refresh(vacancy_id, details, meta)

    def _maybe_refresh_stats(self, vacancy_id: str, details: dict) -> None:
        if not details:
            return
        if not should_refresh_stats(details):
            return
        self.run_worker(
            self._refresh_stats_worker(vacancy_id),
            exclusive=False,
            thread=True,
        )

    async def _refresh_stats_worker(self, vacancy_id: str, force: bool = False) -> None:
        try:
            base = get_vacancy_from_cache(vacancy_id) or {}
            if (not force) and (not should_refresh_stats(base)):
                return
            stats = self.app.client.get_vacancy_stats(vacancy_id)
            responses, viewing = extract_stats_from_response(stats)
            merged = merge_vacancy_stats(base or stats, responses, viewing)
            save_vacancy_to_cache(vacancy_id, merged)
            if self._pending_details_id == vacancy_id:
                self.app.call_from_thread(
                    self.display_vacancy_details, merged, vacancy_id
                )
        except AuthorizationPending as auth_exc:
            log_to_db(
                "WARN",
                LogSource.VACANCY_LIST_SCREEN,
                f"Обновление статистики приостановлено: {auth_exc}",
            )
        except Exception as exc:
            log_to_db(
                "ERROR",
                LogSource.VACANCY_LIST_SCREEN,
                f"Ошибка обновления статистики {vacancy_id}: {exc}",
            )

    def _schedule_stats_refresh(self, vacancy_id: str, details: dict, meta: dict) -> None:
        if self._pending_details_id != vacancy_id:
            return
        if self._stats_timer:
            self._stats_timer.stop()
            self._stats_timer = None
        refresh_after = meta.get("stats_refresh_after")
        delay = 0.1
        if refresh_after:
            try:
                refresh_dt = datetime.fromisoformat(refresh_after)
                delta = (refresh_dt - datetime.now()).total_seconds()
                if delta > 0:
                    delay = delta
            except Exception:
                delay = 0.1
        self._stats_timer = self.set_timer(
            delay,
            lambda vid=vacancy_id, det=details: self._maybe_refresh_stats(vid, det),
        )

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
        """Открывает экран редактирования конфигурации из списка вакансий"""
        self.app.push_screen(
            ConfigScreen(resume_id=self.resume_id, resume_title=self.resume_title),
            self._on_config_screen_closed,
        )

    def action_open_history(self) -> None:
        """Открывает историю откликов для текущего резюме"""
        self.app.push_screen(
            NegotiationHistoryScreen(
                resume_id=self.resume_id,
                resume_title=self.resume_title,
            )
        )

    def _on_config_screen_closed(self, saved: bool | None) -> None:
        """После закрытия настроек восстанавливает фокус и обновляет список при необходимости"""
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

        try:
            results = apply_to_vacancies(
                client=self.app.client,
                profile_name=profile_name,
                resume_id=self.resume_id,
                resume_title=self.resume_title,
                vacancy_ids=list(self.selected_vacancies),
                vacancies_by_id=self.vacancies_by_id,
                cover_letter=cover_letter,
            )
        except CaptchaRequired as captcha_exc:
            log_to_db(
                "WARN",
                LogSource.VACANCY_LIST_SCREEN,
                f"Отправка откликов остановлена до завершения капчи: {captcha_exc}"
            )
            self.app.call_from_thread(
                self.app.notify,
                "hh.ru запросил капчу. Завершите проверку в окне webview и повторите отправку.",
                title="Капча",
                severity="warning",
                timeout=5,
            )
            return
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

        for result in results:
            if result.ok:
                self.app.call_from_thread(
                    self.app.notify,
                    f"[OK] {result.title}",
                    title="Отклик отправлен",
                )
            else:
                reason = result.human_reason or result.reason_code or "Ошибка"
                self.app.call_from_thread(
                    self.app.notify,
                    f"[Ошибка: {reason}] {result.title}",
                    title="Отклик не удался",
                    severity="error",
                    timeout=2,
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
        """Переключает список на предыдущую страницу"""
        if self.current_page > 0:
            self._fetch_and_refresh_vacancies(self.current_page - 1)

    def action_next_page(self) -> None:
        """Переключает список на следующую страницу"""
        if self.current_page < self.total_pages - 1:
            self._fetch_and_refresh_vacancies(self.current_page + 1)

    def on_pagination_page_changed(
        self, message: Pagination.PageChanged
    ) -> None:
        """Обрабатывает переключение страницы через виджет пагинации"""
        self._fetch_and_refresh_vacancies(message.page)
