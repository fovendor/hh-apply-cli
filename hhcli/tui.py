# hhcli/tui.py
import html
import random
from typing import Optional

import html2text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, VerticalScroll
from textual.events import Key
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    Markdown,
    Static,
)

from hhcli.database import (
    get_active_profile_name,
    get_all_profiles,
    get_full_negotiation_history_for_profile,
    load_profile_config,
    log_to_db,
    record_apply_action,
    save_vacancy_to_cache,
    set_active_profile,
    get_vacancy_from_cache,
)


def _normalize(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(str(text).lower().split())


class ApplyConfirmationScreen(Screen):
    """Экран подтверждения отправки откликов."""

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count
        self.confirm_code = str(random.randint(1000, 9999))

    def compose(self) -> ComposeResult:
        yield Center(
            Static(
                f"Выбрано [b]{self.count}[/] вакансий для отклика.\n\n"
                f"Для подтверждения введите число [b green]{self.confirm_code}[/]",
                id="confirm_label",
            ),
            Input(placeholder="Введите число здесь..."),
            Static("", id="error_label"),
        )

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


class VacancyListScreen(Screen):
    """Список вакансий + детали справа."""

    BINDINGS = [
        Binding("x", "toggle_select", "Выбрать", show=True),   # видно в футере
        Binding("a", "apply_for_selected", "Откликнуться"),
        Binding("escape", "app.pop_screen", "Назад"),
    ]

    _debounce_timer: Optional[Timer] = None

    def __init__(self, vacancies: list[dict], resume_id: str) -> None:
        super().__init__()
        self.vacancies = vacancies
        self.resume_id = resume_id
        self.selected_vacancies: set[str] = set()
        self.vacancies_by_id = {v["id"]: v for v in vacancies}
        self.index_to_id: list[str] = []
        self._pending_details_id: Optional[str] = None  # для отмены устаревших ответов

        self.html_converter = html2text.HTML2Text()
        self.html_converter.body_width = 0
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = True
        self.html_converter.mark_code = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        with Horizontal():
            yield DataTable(id="vacancy_table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="details_pane"):
                yield Markdown(
                    "[dim]Выберите вакансию слева, чтобы увидеть детали.[/dim]",
                    id="vacancy_details",
                )
                yield LoadingIndicator()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("✓", "№", "Вакансия", "Компания", "З/П")
        self.index_to_id.clear()

        if not self.vacancies:
            table.add_row("", "", "Вакансии не найдены.")
            self.query_one(LoadingIndicator).display = False
            return

        profile_name = self.app.client.profile_name
        config = load_profile_config(profile_name)
        history = get_full_negotiation_history_for_profile(profile_name)

        applied_ids = {h["vacancy_id"] for h in history}
        applied_keys = {
            f"{_normalize(h['vacancy_title'])}|{_normalize(h['employer_name'])}"
            for h in history
        }

        for i, vac in enumerate(self.vacancies):
            salary = vac.get("salary")
            salary_str = "[dim]не указана[/dim]"
            if salary:
                cur = (salary.get("currency") or "").upper()
                s_from, s_to = salary.get("from"), salary.get("to")
                if s_from and s_to:
                    salary_str = f"{s_from:,}-{s_to:,} {cur}".replace(",", " ")
                elif s_from:
                    salary_str = f"от {s_from:,} {cur}".replace(",", " ")
                elif s_to:
                    salary_str = f"до {s_to:,} {cur}".replace(",", " ")

            vac_name = vac["name"]
            strike = False
            if config.get("strikethrough_applied_vac") and vac["id"] in applied_ids:
                strike = True
            if not strike and config.get("strikethrough_applied_vac_name"):
                key = f"{_normalize(vac['name'])}|{_normalize(vac['employer']['name'])}"
                if key in applied_keys:
                    strike = True
            if strike:
                vac_name = f"[s]{vac_name}[/s]"

            table.add_row(
                "[ ]",
                str(i + 1),
                vac_name,
                vac["employer"]["name"],
                salary_str,
            )
            self.index_to_id.append(vac["id"])

        self.query_one(LoadingIndicator).display = False

    def _current_vacancy_id(self) -> Optional[str]:
        table = self.query_one(DataTable)
        idx = table.cursor_row
        if idx is None or idx < 0 or idx >= len(self.index_to_id):
            return None
        return self.index_to_id[idx]

    # ===== Лёгкая прокрутка: debounce 300 мс и защита от устаревших ответов ====
    def on_data_table_row_highlighted(self, _: DataTable.RowHighlighted) -> None:
        if self._debounce_timer:
            self._debounce_timer.stop()
        vacancy_id = self._current_vacancy_id()
        if not vacancy_id:
            return
        self._debounce_timer = self.set_timer(
            0.3, lambda vid=vacancy_id: self.load_vacancy_details(vid)
        )

    def load_vacancy_details(self, vacancy_id: Optional[str]) -> None:
        if not vacancy_id:
            return
        self._pending_details_id = vacancy_id
        log_to_db("INFO", "VacancyListScreen", f"Просмотр деталей: {vacancy_id}")
        self.update_vacancy_details(vacancy_id)

    def update_vacancy_details(self, vacancy_id: str) -> None:
        cached = get_vacancy_from_cache(vacancy_id)
        if cached:
            log_to_db("INFO", "Cache", f"Кэш попадание: {vacancy_id}")
            self.display_vacancy_details(cached, vacancy_id)
            return

        log_to_db("INFO", "Cache", f"Нет в кэше, тянем из API: {vacancy_id}")
        self.query_one(LoadingIndicator).display = True
        self.query_one("#vacancy_details").update("Загрузка...")
        self.run_worker(
            self.fetch_vacancy_details(vacancy_id),
            exclusive=True,
            thread=True,
            name="VacancyDetails",
        )

    async def fetch_vacancy_details(self, vacancy_id: str) -> None:
        try:
            details = self.app.client.get_vacancy_details(vacancy_id)
            save_vacancy_to_cache(vacancy_id, details)
            self.app.call_from_thread(self.display_vacancy_details, details, vacancy_id)
        except Exception as exc:  # noqa: BLE001
            log_to_db("ERROR", "VacancyListScreen", f"Ошибка деталей {vacancy_id}: {exc}")
            self.app.call_from_thread(
                self.query_one("#vacancy_details").update,
                f"Ошибка загрузки: {exc}",
            )

    def display_vacancy_details(self, details: dict, vacancy_id: str) -> None:
        # Если за время загрузки пользователь ушёл на другую строку — игнорируем ответ
        if self._pending_details_id != vacancy_id:
            return

        desc_html = details.get("description", "")
        desc_md = self.html_converter.handle(html.unescape(desc_html)).strip()

        skills = details.get("key_skills") or []
        skills_text = "* " + "\n* ".join(s["name"] for s in skills) if skills else "Не указаны"

        doc = (
            f"# {details['name']}\n\n"
            f"**Компания:** {details['employer']['name']}\n"
            f"**Ссылка:** {details['alternate_url']}\n\n"
            f"**Ключевые навыки:**\n{skills_text}\n\n"
            f"**Описание:**\n\n{desc_md}\n"
        )
        self.query_one("#vacancy_details").update(doc)
        self.query_one(LoadingIndicator).display = False
        self.query_one("#details_pane").scroll_home(animate=False)

    # ===== Выбор вакансии: hotkey в футере + поддержка 'ч' =====
    def action_toggle_select(self) -> None:
        self._toggle_current_selection()

    def on_key(self, event: Key) -> None:
        if event.key not in ("x", "ч"):
            return
        event.prevent_default()
        event.stop()
        self._toggle_current_selection()

    def _toggle_current_selection(self) -> None:
        vacancy_id = self._current_vacancy_id()
        if not vacancy_id:
            return
        table = self.query_one(DataTable)
        mark_cell = (table.cursor_row, 0)
        if vacancy_id in self.selected_vacancies:
            self.selected_vacancies.remove(vacancy_id)
            table.update_cell_at(mark_cell, "[ ]")
            log_to_db("INFO", "VacancyListScreen", f"Сняли выбор: {vacancy_id}")
        else:
            self.selected_vacancies.add(vacancy_id)
            table.update_cell_at(mark_cell, "[green]x[/]")
            log_to_db("INFO", "VacancyListScreen", f"Выбрали: {vacancy_id}")
        table.refresh()

    # ===== Массовый отклик =====
    def action_apply_for_selected(self) -> None:
        if not self.selected_vacancies:
            self.app.notify("Нет выбранных вакансий.", title="Внимание", severity="warning")
            return
        self.app.push_screen(
            ApplyConfirmationScreen(len(self.selected_vacancies)),
            self.on_apply_confirmed,
        )

    def on_apply_confirmed(self, ok: bool) -> None:
        if not ok:
            return
        self.app.notify(
            f"Отправка {len(self.selected_vacancies)} откликов...",
            title="В процессе",
            timeout=5,
        )
        self.run_worker(self.run_apply_worker(), thread=True)

    async def run_apply_worker(self) -> None:
        profile_name = self.app.client.profile_name
        config = load_profile_config(profile_name)
        cover_letter = config.get("cover_letter", "")
        table = self.query_one(DataTable)

        for vacancy_id in list(self.selected_vacancies):
            v = self.vacancies_by_id.get(vacancy_id, {})
            ok, reason = self.app.client.apply_to_vacancy(
                resume_id=self.resume_id,
                vacancy_id=vacancy_id,
                message=cover_letter,
            )
            vac_title = v.get("name", vacancy_id)
            emp = (v.get("employer") or {}).get("name")

            if ok:
                self.app.call_from_thread(
                    self.app.notify, f"[OK] {vac_title}", title="Отклик отправлен"
                )
                record_apply_action(vacancy_id, profile_name, vac_title, emp, "applied", None)
            else:
                self.app.call_from_thread(
                    self.app.notify,
                    f"[Ошибка: {reason}] {vac_title}",
                    title="Отклик не удался",
                    severity="error",
                    timeout=8,
                )
                record_apply_action(vacancy_id, profile_name, vac_title, emp, "failed", reason)

        self.app.call_from_thread(self.app.notify, "Все отклики обработаны.", title="Готово")
        self.selected_vacancies.clear()
        for row in range(table.row_count):
            table.update_cell_at((row, 0), "[ ]", update_width=True)
        table.refresh()


class ResumeSelectionScreen(Screen):
    """Выбор одного из резюме пользователя."""

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
        table.add_columns("ID резюме", "Должность", "Ссылка")
        table.fixed_columns = 1
        self.index_to_resume_id.clear()

        items = self.resume_data.get("items", [])
        if not items:
            table.add_row("[b]У вас нет ни одного резюме.[/b]")
            return

        for r in items:
            table.add_row(
                r.get("id"),
                f"[bold green]{r.get('title')}[/bold green]",
                r.get("alternate_url"),
            )
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

        log_to_db("INFO", "ResumeScreen", f"Выбрано резюме: {resume_id} '{resume_title}'")
        self.app.push_screen(
            SearchModeScreen(resume_id=resume_id, resume_title=resume_title, is_root_screen=False)
        )


class SearchModeScreen(Screen):
    """Выбор режима поиска: авто или ручной."""

    BINDINGS = [
        Binding("1", "run_search('auto')", "Авто", show=False),
        Binding("2", "run_search('manual')", "Ручной", show=False),
        Binding("escape", "handle_escape", "Назад/Выход", show=True),
    ]

    def __init__(self, resume_id: str, resume_title: str, is_root_screen: bool = False) -> None:
        super().__init__()
        self.resume_id = resume_id
        self.resume_title = resume_title
        self.is_root_screen = is_root_screen

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        yield Static(f"Выбрано резюме: [b cyan]{self.resume_title}[/b cyan]\n")
        yield Static("[b]Выберите способ поиска вакансий:[/b]")
        yield Static("  [yellow]1)[/] Автоматический (рекомендации hh.ru)")
        yield Static("  [yellow]2)[/] Ручной (поиск по ключевым словам)")
        yield Footer()

    def action_handle_escape(self) -> None:
        if self.is_root_screen:
            self.app.exit()
        else:
            self.app.pop_screen()

    async def action_run_search(self, mode: str) -> None:
        log_to_db("INFO", "SearchModeScreen", f"Выбран режим '{mode}'")
        self.app.notify("Идёт поиск вакансий...", title="Загрузка", timeout=10)

        try:
            if mode == "auto":
                result = self.app.client.get_similar_vacancies(self.resume_id)
            else:
                cfg = load_profile_config(self.app.client.profile_name)
                result = self.app.client.search_vacancies(cfg)
        except Exception as exc:  # noqa: BLE001
            log_to_db("ERROR", "SearchModeScreen", f"Ошибка API: {exc}")
            self.app.notify(f"Ошибка API: {exc}", title="Ошибка", severity="error", timeout=10)
            return

        items = (result or {}).get("items") or []
        if items:
            log_to_db("INFO", "SearchModeScreen", f"Найдено {len(items)} вакансий")
            self.app.push_screen(VacancyListScreen(vacancies=items, resume_id=self.resume_id))
        else:
            log_to_db("WARN", "SearchModeScreen", "Пустой результат")
            self.app.notify(
                "По вашему запросу ничего не найдено.",
                title="Результат поиска",
                severity="warning",
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
            table.add_row(f"[bold green]{p['profile_name']}[/bold green]", p["email"])
            self.index_to_profile.append(p["profile_name"])

    def on_data_table_row_selected(self, _: DataTable.RowSelected) -> None:
        table = self.query_one(DataTable)
        idx = table.cursor_row
        if idx is None or idx < 0 or idx >= len(self.index_to_profile):
            return
        profile_name = self.index_to_profile[idx]
        log_to_db("INFO", "ProfileScreen", f"Выбран профиль '{profile_name}'")
        set_active_profile(profile_name)
        self.dismiss(profile_name)


class HHCliApp(App):
    """Основное TUI-приложение."""

    CSS = """
    #vacancy_table { width: 105; min-width: 45; }
    #details_pane { padding: 0 1; }
    """

    SCREENS = {
        "profile_select": ProfileSelectionScreen,
        "resume_select": ResumeSelectionScreen,
        "search_mode": SearchModeScreen,
        "vacancy_list": VacancyListScreen,
    }

    BINDINGS = [
        Binding("q", "quit", "Выход", show=True, priority=True),
        Binding("й", "quit", "Выход (RU)", show=False, priority=True),
    ]

    def __init__(self, client) -> None:
        super().__init__()
        self.client = client

    async def on_mount(self) -> None:
        log_to_db("INFO", "TUI", "Приложение смонтировано")
        all_profiles = get_all_profiles()

        if not all_profiles:
            self.exit(
                result=(
                    "В базе не найдено ни одного профиля. "
                    "Пожалуйста, войдите в аккаунт через --auth <имя_профиля>."
                )
            )
            return

        active_profile = get_active_profile_name()
        if not active_profile:
            log_to_db("INFO", "TUI", "Активный профиль не найден — показ выбора.")
            self.push_screen(ProfileSelectionScreen(all_profiles), self.on_profile_selected)
            return

        await self.proceed_with_profile(active_profile)

    async def on_profile_selected(self, selected_profile: Optional[str]) -> None:
        if not selected_profile:
            log_to_db("INFO", "TUI", "Выбор профиля отменён, выходим.")
            self.exit()
            return
        log_to_db("INFO", "TUI", f"Выбран профиль '{selected_profile}' из списка.")
        await self.proceed_with_profile(selected_profile)

    async def proceed_with_profile(self, profile_name: str) -> None:
        try:
            self.client.load_profile_data(profile_name)
            self.sub_title = f"Профиль: {profile_name}"

            self.app.notify("Синхронизация истории откликов...", title="Синхронизация", timeout=20)
            self.run_worker(self.client.sync_negotiation_history, thread=True, name="SyncWorker")

            log_to_db("INFO", "TUI", f"Загрузка резюме для '{profile_name}'")
            resumes = self.client.get_my_resumes()
            items = (resumes or {}).get("items") or []

            if len(items) == 1:
                r = items[0]
                log_to_db("INFO", "TUI", "Одно резюме — сразу к поиску.")
                self.push_screen(
                    SearchModeScreen(resume_id=r["id"], resume_title=r["title"], is_root_screen=True)
                )
            else:
                log_to_db("INFO", "TUI", "Несколько резюме — экран выбора.")
                self.push_screen(ResumeSelectionScreen(resume_data=resumes))
        except Exception as exc:  # noqa: BLE001
            log_to_db("ERROR", "TUI", f"Критическая ошибка профиля/резюме: {exc}")
            self.exit(result=exc)

    def action_quit(self) -> None:
        log_to_db("INFO", "TUI", "Пользователь запросил выход.")
        self.exit()
