from __future__ import annotations

import html2text
from datetime import datetime
from typing import Optional

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (
    Footer,
    Header,
    LoadingIndicator,
    Markdown,
    Button,
    Static,
    TabPane,
    TabbedContent,
    TextArea,
)
from textual.widgets._option_list import Option, OptionList
from textual.widgets.text_area import TextAreaTheme

from ...client import AuthorizationPending, CaptchaRequired
from ...constants import ConfigKeys, LogSource
from ...database import (
    get_default_config,
    get_vacancy_from_cache,
    load_profile_config,
    log_to_db,
    save_vacancy_to_cache,
)
from ..modules.history_service import fetch_resume_history
from ..utils.constants import MAX_COLUMN_WIDTH
from ..utils.formatting import (
    clamp,
    format_date,
    format_segment,
    normalize_width_map,
    set_loader_visible,
)
from ..widgets import HistoryOptionList
from ..widgets.history_panel import build_history_details_markdown
from .config import ConfigScreen


class NegotiationHistoryScreen(Screen):
    """Экран просмотра истории откликов"""

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
        self._current_chat_negotiation_id: Optional[str] = None
        self._current_chat_vacancy_id: Optional[str] = None
        self._chat_send_in_progress: bool = False
        self._negotiation_sync_in_progress: bool = False
        self._chat_sync_attempted: set[str] = set()

        self.html_converter = html2text.HTML2Text()
        self.html_converter.body_width = 0
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = True
        self.html_converter.mark_code = True

    def compose(self) -> ComposeResult:
        with Vertical(id="history_screen"):
            yield Header(show_clock=True, name="hhcli")
            if self.resume_title:
                yield Static(
                    f"Резюме: [b cyan]{self.resume_title}[/b cyan]\n",
                    id="history_resume_label",
                )
            with Horizontal(id="history_layout"):
                yield from self._compose_history_panel()
                yield from self._compose_details_panel()
            yield Footer()

    def _compose_history_panel(self) -> ComposeResult:
        with Vertical(id="history_panel", classes="pane") as history_panel:
            history_panel.border_title = "История откликов"
            history_panel.styles.border_title_align = "left"
            yield Static(id="history_list_header")
            yield HistoryOptionList(id="history_list")

    def _compose_details_panel(self) -> ComposeResult:
        with Vertical(id="history_details_panel", classes="pane") as details_panel:
            details_panel.border_title = "Детали и переписка"
            details_panel.styles.border_title_align = "left"
            with TabbedContent(initial="history_description_tab", id="history_details_tabs"):
                yield from self._compose_description_tab()
                yield from self._compose_chat_tab()

    def _compose_description_tab(self) -> ComposeResult:
        with TabPane("Описание вакансии", id="history_description_tab"):
            with VerticalScroll(id="history_details_pane"):
                yield Markdown(
                    "[dim]Выберите отклик слева, чтобы увидеть детали.[/dim]",
                    id="history_details",
                )
                yield LoadingIndicator(id="history_loader")

    def _compose_chat_tab(self) -> ComposeResult:
        with TabPane("Переписка", id="history_chat_tab"):
            with Vertical(id="history_chat_split"):
                with VerticalScroll(id="history_chat_upper"):
                    yield Markdown(
                        "[dim]Переписка загрузится после выбора отклика.[/dim]",
                        id="history_chat_markdown",
                    )
                with Vertical(id="history_chat_lower"):
                    yield from self._compose_chat_toolbar()
                    chat_input = TextArea(id="history_chat_input")
                    chat_input.placeholder = "Введите сообщение..."
                    chat_input.show_line_numbers = False
                    chat_input.action_undo = lambda: self._safe_chat_undo()
                    chat_input.action_redo = lambda: self._safe_chat_redo()
                    self._apply_history_chat_text_area_theme(chat_input)
                    yield chat_input
                    yield Button(
                        "Отправить работодателю",
                        id="history_chat_send",
                        variant="success",
                    )

    def _compose_chat_toolbar(self) -> ComposeResult:
        toolbar_items = [
            ("history_chat_bold", Text.from_markup("[b]B[/b]")),
            ("history_chat_italic", Text.from_markup("[i]I[/i]")),
            ("history_chat_strike", Text.from_markup("[strike]S[/strike]")),
            ("history_chat_ul", Text.from_markup("[b]⁝[/b]")),
            ("history_chat_ol", Text.from_markup("1.")),
        ]
        with Horizontal(id="history_chat_toolbar"):
            for btn_id, label in toolbar_items:
                yield Button(label, id=btn_id, classes="chat-toolbar-btn")

    def on_mount(self) -> None:
        self._reload_history_layout_preferences()
        self._apply_history_workspace_widths()
        self._update_history_header()
        self._refresh_history()
        self._apply_history_chat_text_area_theme()
        self._load_chat_for_negotiation(None)

    def on_screen_resume(self) -> None:
        self.app.apply_theme_from_profile(self.app.client.profile_name)
        self._reload_history_layout_preferences()
        self._apply_history_workspace_widths()
        self._update_history_header()
        self._apply_history_chat_text_area_theme()
        self.query_one(HistoryOptionList).focus()

    def on_key(self, event: Key) -> None:
        chat_input = self._get_history_chat_text_area()
        if chat_input and chat_input.has_focus:
            ctrl = bool(getattr(event, "ctrl", False) or getattr(event, "ctrl_key", False))
            shift = bool(getattr(event, "shift", False) or getattr(event, "shift_key", False))
            key_name = event.key or ""
            if ctrl and key_name == "z":
                event.stop()
                event.prevent_default()
                self._safe_chat_undo()
                return
            if ctrl and (
                key_name == "y"
                or (key_name == "z" and shift)
                or key_name == "shift+ctrl+z"
            ):
                event.stop()
                event.prevent_default()
                self._safe_chat_redo()
                return
        # У экрана нет базовой обработки клавиш, поэтому просто выходим, если ничего не сделали сами
        return None

    def _reload_history_layout_preferences(self) -> None:
        config = load_profile_config(self.app.client.profile_name)
        defaults = get_default_config()
        self._history_left_percent = clamp(
            int(
                config.get(
                    ConfigKeys.HISTORY_LEFT_PANE_PERCENT,
                    defaults[ConfigKeys.HISTORY_LEFT_PANE_PERCENT],
                )
            ),
            10,
            90,
        )
        history_width_values = {
            "index": clamp(
                int(
                    config.get(
                        ConfigKeys.HISTORY_COL_INDEX_WIDTH,
                        defaults[ConfigKeys.HISTORY_COL_INDEX_WIDTH],
                    )
                ),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "title": clamp(
                int(
                    config.get(
                        ConfigKeys.HISTORY_COL_TITLE_WIDTH,
                        defaults[ConfigKeys.HISTORY_COL_TITLE_WIDTH],
                    )
                ),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "company": clamp(
                int(
                    config.get(
                        ConfigKeys.HISTORY_COL_COMPANY_WIDTH,
                        defaults[ConfigKeys.HISTORY_COL_COMPANY_WIDTH],
                    )
                ),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "status": clamp(
                int(
                    config.get(
                        ConfigKeys.HISTORY_COL_STATUS_WIDTH,
                        defaults[ConfigKeys.HISTORY_COL_STATUS_WIDTH],
                    )
                ),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "sent": clamp(
                int(
                    config.get(
                        ConfigKeys.HISTORY_COL_SENT_WIDTH,
                        defaults[ConfigKeys.HISTORY_COL_SENT_WIDTH],
                    )
                ),
                1,
                MAX_COLUMN_WIDTH,
            ),
            "date": clamp(
                int(
                    config.get(
                        ConfigKeys.HISTORY_COL_DATE_WIDTH,
                        defaults[ConfigKeys.HISTORY_COL_DATE_WIDTH],
                    )
                ),
                1,
                MAX_COLUMN_WIDTH,
            ),
        }
        self._history_column_widths = normalize_width_map(
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
        entries = fetch_resume_history(profile_name, self.resume_id)

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
            set_loader_visible(self, "history_loader", False)
            return

        for idx, entry in enumerate(entries, start=1):
            vacancy_id = str(entry.get("vacancy_id") or "")
            title = entry.get("vacancy_title") or vacancy_id
            company = entry.get("employer_name") or "-"
            applied_label = format_date(entry.get("applied_at"))
            status_label = entry.get("status_display") or "-"
            sent_label = entry.get("sent_display") or (
                "да" if entry.get("was_delivered") else "нет"
            )

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
                vac_id_str = str(focused_option.id)
                self.load_vacancy_details(vac_id_str)
                record = self.history_by_vacancy.get(vac_id_str, {})
                self._load_chat_for_negotiation(
                    record.get("negotiation_id"), vacancy_id=vac_id_str
                )

    def _build_header_text(self) -> Text:
        widths = self._history_column_widths
        return Text.assemble(
            format_segment("№", widths["index"], style="bold"),
            Text("  "),
            format_segment("Название вакансии", widths["title"], style="bold"),
            Text("  "),
            format_segment("Компания", widths["company"], style="bold"),
            Text("  "),
            format_segment("Статус", widths["status"], style="bold"),
            Text("  "),
            format_segment("✉", widths["sent"], style="bold"),
            Text("  "),
            format_segment("Дата отклика", widths["date"], style="bold"),
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
            format_segment(index, widths["index"], style="bold"),
            Text("  "),
            format_segment(title, widths["title"]),
            Text("  "),
            format_segment(company, widths["company"]),
            Text("  "),
            format_segment(status, widths["status"]),
            Text("  "),
            format_segment(delivered, widths["sent"]),
            Text("  "),
            format_segment(applied, widths["date"]),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "history_chat_send":
            self.action_send_chat_message()
        elif event.button.id == "history_chat_bold":
            self._apply_chat_format("**", "**")
        elif event.button.id == "history_chat_italic":
            self._apply_chat_format("*", "*")
        elif event.button.id == "history_chat_strike":
            self._apply_chat_format("~~", "~~")
        elif event.button.id == "history_chat_ul":
            self._insert_chat_snippet("- ")
        elif event.button.id == "history_chat_ol":
            self._insert_chat_snippet("1. ")

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if self._debounce_timer:
            self._debounce_timer.stop()
        vacancy_id = event.option.id
        if not vacancy_id or vacancy_id == "__none__":
            return
        record = self.history_by_vacancy.get(str(vacancy_id), {})
        self._load_chat_for_negotiation(
            record.get("negotiation_id"),
            vacancy_id=str(vacancy_id),
        )
        self._debounce_timer = self.set_timer(
            0.2, lambda vid=str(vacancy_id): self.load_vacancy_details(vid)
        )

    def load_vacancy_details(self, vacancy_id: Optional[str]) -> None:
        if not vacancy_id:
            return
        self._pending_details_id = vacancy_id
        set_loader_visible(self, "history_loader", True)
        self.query_one("#history_details", Markdown).update("")

        cached = get_vacancy_from_cache(vacancy_id)
        if cached:
            self.display_history_details(cached, vacancy_id)
            set_loader_visible(self, "history_loader", False)
            return

        self.run_worker(
            self.fetch_history_details(vacancy_id),
            exclusive=True,
            thread=True,
        )

    def _build_history_chat_theme(self) -> TextAreaTheme:
        css_theme = getattr(self.app, "css_manager", None)
        theme = getattr(css_theme, "theme", None)
        colors = getattr(theme, "colors", {}) if theme else {}
        background = colors.get("background2", "#3B4252")
        text_color = colors.get("foreground3", "#ECEFF4")
        theme_name = getattr(theme, "_name", "default")
        return TextAreaTheme(
            name=f"history-chat-{theme_name}",
            base_style=Style(color=text_color, bgcolor=background),
            cursor_line_style=None,
            cursor_line_gutter_style=None,
        )

    def _apply_history_chat_text_area_theme(self, text_area: TextArea | None = None) -> None:
        target = text_area or self._get_history_chat_text_area()
        if target is None:
            return
        chat_theme = self._build_history_chat_theme()
        target.register_theme(chat_theme)
        target.theme = chat_theme.name

    def _get_history_chat_text_area(self) -> TextArea | None:
        try:
            return self.query_one("#history_chat_input", TextArea)
        except Exception:
            return None

    def _get_history_chat_send_button(self) -> Button | None:
        try:
            return self.query_one("#history_chat_send", Button)
        except Exception:
            return None

    def _insert_chat_snippet(self, snippet: str) -> None:
        chat_input = self._get_history_chat_text_area()
        if chat_input is None:
            return
        insert_method = getattr(chat_input, "insert", None)
        if callable(insert_method):
            try:
                insert_method(snippet)
            except Exception:
                chat_input.text = (chat_input.text or "") + snippet
        else:
            chat_input.text = (chat_input.text or "") + snippet
        chat_input.focus()

    def _apply_chat_format(self, prefix: str, suffix: str) -> None:
        chat_input = self._get_history_chat_text_area()
        if chat_input is None:
            return
        if self._wrap_chat_selection(chat_input, prefix, suffix):
            return
        insert_method = getattr(chat_input, "insert", None)
        move_method = getattr(chat_input, "move_cursor_relative", None)

        snippet = f"{prefix}{suffix}"
        if callable(insert_method):
            try:
                insert_method(snippet)
                if callable(move_method) and suffix:
                    move_method(columns=-len(suffix))
            except Exception:
                chat_input.text = (chat_input.text or "") + snippet
                if callable(move_method) and suffix:
                    try:
                        move_method(columns=-len(suffix))
                    except Exception:
                        pass
        else:
            chat_input.text = (chat_input.text or "") + snippet
        chat_input.focus()

    def _wrap_chat_selection(self, chat_input: TextArea, prefix: str, suffix: str) -> bool:
        """Оборачивает выделенный текст в TextArea указанными префиксом/суффиксом."""
        selection = getattr(chat_input, "selection", None)
        text = chat_input.text or ""
        if not selection or selection.start == selection.end:
            return False
        try:
            start_offset = self._location_to_offset(text, selection.start)
            end_offset = self._location_to_offset(text, selection.end)
            if start_offset > end_offset:
                start_offset, end_offset = end_offset, start_offset
            wrapped = text[:start_offset] + prefix + text[start_offset:end_offset] + suffix + text[end_offset:]
            chat_input.text = wrapped
            cursor_offset = start_offset + len(prefix) + (end_offset - start_offset) + len(suffix)
            target_location = self._offset_to_location(wrapped, cursor_offset)
            move_cursor = getattr(chat_input, "move_cursor", None)
            if callable(move_cursor):
                move_cursor(target_location)
            chat_input.focus()
            return True
        except Exception as exc:
            log_to_db(
                "WARN",
                LogSource.TUI,
                f"Не удалось обернуть выделение в чате: {exc}",
            )
            return False

    @staticmethod
    def _location_to_offset(text: str, location: tuple[int, int]) -> int:
        """Переводит пару (строка, колонка) в позицию символа в строке."""
        row, col = location
        lines = text.splitlines(True)
        if not lines:
            return 0
        if row >= len(lines):
            return len(text)
        offset = sum(len(lines[i]) for i in range(row))
        return min(len(text), offset + min(col, len(lines[row])))

    @staticmethod
    def _offset_to_location(text: str, offset: int) -> tuple[int, int]:
        """Переводит позицию в строке обратно в (строка, колонка)."""
        offset = max(0, min(len(text), offset))
        lines = text.splitlines(True)
        if not lines:
            return (0, 0)
        current = 0
        for idx, line in enumerate(lines):
            next_offset = current + len(line)
            if offset <= next_offset:
                return (idx, offset - current)
            current = next_offset
        return (len(lines) - 1, len(lines[-1]))

    def _safe_chat_undo(self) -> None:
        chat_input = self._get_history_chat_text_area()
        if chat_input is None:
            return
        try:
            chat_input.undo()
        except Exception as exc:
            log_to_db(
                "WARN",
                LogSource.TUI,
                f"Ошибка undo в чате: {exc}",
            )

    def _safe_chat_redo(self) -> None:
        chat_input = self._get_history_chat_text_area()
        if chat_input is None:
            return
        try:
            chat_input.redo()
        except Exception as exc:
            log_to_db(
                "WARN",
                LogSource.TUI,
                f"Ошибка redo в чате: {exc}",
            )

    def _message_time_label(self, value: str | None) -> str:
        if not value:
            return "-"
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return value

    def _load_chat_for_negotiation(
        self, negotiation_id: Optional[str], vacancy_id: Optional[str] = None
    ) -> None:
        """Загружает переписку для выбранного отклика"""
        self._current_chat_negotiation_id = negotiation_id or None
        self._current_chat_vacancy_id = vacancy_id or None
        try:
            md = self.query_one("#history_chat_markdown", Markdown)
        except Exception:
            return
        if not negotiation_id:
            sync_message = "[dim]Переписка недоступна для этого отклика.[/dim]"
            if vacancy_id:
                sync_message = "[dim]Переписка недоступна — обновляем историю, попробуйте подождать...[/dim]"
                self._maybe_sync_chat_for_vacancy(str(vacancy_id))
            md.update(sync_message)
            return
        md.update("[dim]Загрузка переписки...[/dim]")
        self.run_worker(
            self._fetch_chat_worker(str(negotiation_id)),
            thread=True,
            exclusive=True,
        )

    async def _fetch_chat_worker(self, negotiation_id: str) -> None:
        messages: list[dict] = []
        page = 0
        per_page = 50
        try:
            while True:
                data = self.app.client.get_negotiation_messages(
                    negotiation_id,
                    page=page,
                    per_page=per_page,
                    with_text_only=True,
                )
                items = data.get("items") or []
                messages.extend(items)
                pages_total = int(data.get("pages", 1) or 1)
                if page >= pages_total - 1:
                    break
                page += 1
            self.app.call_from_thread(
                self._render_chat_messages,
                negotiation_id,
                messages,
            )
        except CaptchaRequired as captcha_exc:
            log_to_db(
                "WARN",
                LogSource.SYNC_ENGINE,
                f"Переписка недоступна до завершения капчи: {captcha_exc}",
            )
            self.app.call_from_thread(
                self.app.notify,
                "hh.ru запросил капчу. Завершите проверку в окне webview, затем повторите загрузку переписки.",
                title="Капча",
                severity="warning",
                timeout=5,
            )
            self.app.call_from_thread(
                self._render_chat_messages,
                negotiation_id,
                [],
                "[dim]Требуется капча hh.ru. Завершите проверку в окне webview.[/dim]",
            )
        except AuthorizationPending as auth_exc:
            log_to_db(
                "WARN",
                LogSource.SYNC_ENGINE,
                f"Переписка недоступна до авторизации: {auth_exc}",
            )
            self.app.call_from_thread(
                self.app.notify,
                "Авторизуйтесь, чтобы загрузить переписку.",
                title="Авторизация",
                severity="warning",
                timeout=4,
            )
            self.app.call_from_thread(
                self._render_chat_messages,
                negotiation_id,
                [],
                "[dim]Требуется авторизация для просмотра переписки.[/dim]",
            )
        except Exception as exc:  # pragma: no cover - сетевые ошибки
            log_to_db(
                "ERROR",
                LogSource.SYNC_ENGINE,
                f"Не удалось загрузить переписку: {exc}",
            )
            self.app.call_from_thread(
                self.app.notify,
                "Ошибка загрузки переписки.",
                title="Переписка",
                severity="error",
                timeout=4,
            )
            self.app.call_from_thread(
                self._render_chat_messages,
                negotiation_id,
                [],
                "[dim]Не удалось загрузить переписку.[/dim]",
            )

    def _render_chat_messages(
        self,
        negotiation_id: str,
        messages: list[dict],
        error_message: str | None = None,
    ) -> None:
        if negotiation_id != self._current_chat_negotiation_id:
            return
        try:
            md = self.query_one("#history_chat_markdown", Markdown)
        except Exception:
            return
        if error_message:
            md.update(error_message)
            return
        if not messages:
            md.update("[dim]Сообщений нет.[/dim]")
            return

        def format_entry(entry: dict) -> str:
            author_type = (entry.get("author") or {}).get("participant_type")
            who = "Вы" if author_type == "applicant" else "Работодатель"
            ts = self._message_time_label(entry.get("created_at"))
            if author_type == "applicant":
                viewed = bool(entry.get("viewed_by_opponent"))
                status = "Прочитано работодателем" if viewed else "Не прочитано"
            else:
                viewed = bool(entry.get("viewed_by_me"))
                status = "Прочитано" if viewed else "Не прочитано"
            body = entry.get("text") or ""
            status_suffix = f" · {ts}" if viewed else ""
            status_line = f"> `Статус: {status}{status_suffix}`"
            return f"**{who}** ({ts})\n\n{body}\n\n{status_line}"

        sorted_messages = sorted(
            messages,
            key=lambda m: m.get("created_at") or "",
        )
        md.update("\n\n---\n\n".join(format_entry(e) for e in sorted_messages))

    def _maybe_sync_chat_for_vacancy(self, vacancy_id: str) -> None:
        """Пробует пересинхронизировать историю, если у записи нет negotiation_id."""
        if self._negotiation_sync_in_progress:
            return
        if vacancy_id in self._chat_sync_attempted:
            return
        self._chat_sync_attempted.add(vacancy_id)
        self._negotiation_sync_in_progress = True

        async def worker():
            try:
                self.app.client.sync_negotiation_history()
            except CaptchaRequired as captcha_exc:
                log_to_db(
                    "WARN",
                    LogSource.SYNC_ENGINE,
                    f"Синхронизация переписки прервана до завершения капчи: {captcha_exc}",
                )
                self.app.call_from_thread(
                    self.app.notify,
                    "hh.ru запросил капчу. Завершите проверку в окне webview, затем повторите обновление переписки.",
                    title="Капча",
                    severity="warning",
                    timeout=5,
                )
            except AuthorizationPending as auth_exc:
                log_to_db(
                    "WARN",
                    LogSource.SYNC_ENGINE,
                    f"Синхронизация переписки прервана: {auth_exc}",
                )
                self.app.call_from_thread(
                    self.app.notify,
                    "Авторизуйтесь, чтобы обновить переписку.",
                    title="Переписка",
                    severity="warning",
                    timeout=4,
                )
            except Exception as exc:  # pragma: no cover - сетевые ошибки
                log_to_db(
                    "ERROR",
                    LogSource.SYNC_ENGINE,
                    f"Ошибка синхронизации переписки: {exc}",
                )
                self.app.call_from_thread(
                    self.app.notify,
                    "Не удалось обновить переписку.",
                    title="Переписка",
                    severity="error",
                    timeout=4,
                )
            finally:
                self.app.call_from_thread(self._after_chat_sync, vacancy_id)

        self.run_worker(worker(), thread=True, exclusive=True)

    def _after_chat_sync(self, vacancy_id: str) -> None:
        self._negotiation_sync_in_progress = False
        self._refresh_history()
        option_list = self.query_one(HistoryOptionList)
        target_index = None
        for idx in range(option_list.option_count):
            opt = option_list.get_option_at_index(idx)
            if opt and str(opt.id) == str(vacancy_id):
                target_index = idx
                break
        if target_index is not None:
            option_list.highlighted = target_index
            record = self.history_by_vacancy.get(str(vacancy_id), {})
            self._load_chat_for_negotiation(
                record.get("negotiation_id"),
                vacancy_id=str(vacancy_id),
            )

    def action_send_chat_message(self) -> None:
        if self._chat_send_in_progress:
            return
        negotiation_id = self._current_chat_negotiation_id
        if not negotiation_id:
            self.app.notify(
                "Выберите отклик слева, чтобы отправить сообщение.",
                title="Переписка",
                severity="warning",
            )
            return
        chat_input = self._get_history_chat_text_area()
        if chat_input is None:
            return
        message = (chat_input.text or "").strip()
        if not message:
            self.app.notify(
                "Введите сообщение перед отправкой.",
                title="Переписка",
                severity="warning",
            )
            return
        self._chat_send_in_progress = True
        send_button = self._get_history_chat_send_button()
        if send_button:
            send_button.disabled = True
        self.run_worker(
            self._send_chat_message_worker(str(negotiation_id), message),
            thread=True,
        )

    async def _send_chat_message_worker(self, negotiation_id: str, message: str) -> None:
        success = False
        error_message = None
        severity = "error"
        try:
            success = self.app.client.send_negotiation_message(negotiation_id, message)
            if not success:
                error_message = "Не удалось отправить сообщение работодателю."
        except CaptchaRequired as captcha_exc:
            severity = "warning"
            error_message = f"hh.ru запросил капчу. Завершите проверку в окне webview: {captcha_exc}"
            log_to_db(
                "WARN",
                LogSource.SYNC_ENGINE,
                f"Отправка сообщения недоступна до завершения капчи: {captcha_exc}",
            )
        except AuthorizationPending as auth_exc:
            severity = "warning"
            error_message = f"Авторизуйтесь, чтобы отправить сообщение: {auth_exc}"
            log_to_db(
                "WARN",
                LogSource.SYNC_ENGINE,
                f"Отправка сообщения недоступна до авторизации: {auth_exc}",
            )
        except Exception as exc:  # pragma: no cover - сетевые ошибки
            error_message = f"Ошибка отправки сообщения: {exc}"
            log_to_db(
                "ERROR",
                LogSource.SYNC_ENGINE,
                f"Не удалось отправить сообщение в переписку {negotiation_id}: {exc}",
            )

        self.app.call_from_thread(
            self._finalize_chat_message_send,
            negotiation_id,
            success,
            error_message,
            severity,
        )

    def _finalize_chat_message_send(
        self,
        negotiation_id: str,
        success: bool,
        error_message: str | None = None,
        severity: str = "error",
    ) -> None:
        self._chat_send_in_progress = False
        send_button = self._get_history_chat_send_button()
        if send_button:
            send_button.disabled = False

        if not success:
            if error_message:
                self.app.notify(
                    error_message,
                    title="Переписка",
                    severity=severity,
                    timeout=4,
                )
            return

        if negotiation_id != self._current_chat_negotiation_id:
            return

        chat_input = self._get_history_chat_text_area()
        if chat_input:
            chat_input.text = ""
            chat_input.focus()
        self.app.notify(
            "Сообщение отправлено работодателю.",
            title="Переписка",
            severity="information",
            timeout=3,
        )
        self._load_chat_for_negotiation(negotiation_id)

    async def fetch_history_details(self, vacancy_id: str) -> None:
        try:
            details = self.app.client.get_vacancy_details(vacancy_id)
            save_vacancy_to_cache(vacancy_id, details)
            self.app.call_from_thread(
                self.display_history_details,
                details,
                vacancy_id,
            )
        except CaptchaRequired as captcha_exc:
            log_to_db(
                "WARN",
                LogSource.VACANCY_LIST_SCREEN,
                f"Загрузка деталей отклика остановлена до завершения капчи: {captcha_exc}",
            )
            self.app.call_from_thread(
                self.app.notify,
                "hh.ru запросил капчу. Завершите проверку в окне webview.",
                title="Капча",
                severity="warning",
                timeout=5,
            )
            self.app.call_from_thread(
                self._display_details_error,
                "Требуется капча hh.ru. Завершите проверку в окне webview.",
            )
        except AuthorizationPending as auth_exc:
            log_to_db(
                "WARN",
                LogSource.VACANCY_LIST_SCREEN,
                f"Загрузка деталей отклика приостановлена: {auth_exc}",
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
                "Требуется авторизация для просмотра деталей.",
            )
        except Exception as exc:  # pragma: no cover - сетевые ошибки
            log_to_db(
                "ERROR",
                LogSource.VACANCY_LIST_SCREEN,
                f"Ошибка деталей {vacancy_id}: {exc}",
            )
            self.app.call_from_thread(
                self._display_details_error, f"Ошибка загрузки: {exc}"
            )

    def display_history_details(self, details: dict, vacancy_id: str) -> None:
        if self._pending_details_id != vacancy_id:
            return

        record = self.history_by_vacancy.get(vacancy_id, {})
        doc = build_history_details_markdown(
            details,
            record,
            vacancy_id=vacancy_id,
            html_converter=self.html_converter,
        )
        self.query_one("#history_details").update(doc)
        set_loader_visible(self, "history_loader", False)
        self.query_one("#history_details_pane").scroll_home(animate=False)

    def action_edit_config(self) -> None:
        self.app.push_screen(ConfigScreen(), self._on_config_closed)

    def _on_config_closed(self, _: bool | None) -> None:
        self.query_one(HistoryOptionList).focus()

    def _display_details_error(self, message: str) -> None:
        self.query_one("#history_details", Markdown).update(message)
        set_loader_visible(self, "history_loader", False)


__all__ = ["NegotiationHistoryScreen"]
