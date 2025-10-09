import html
import re
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.events import Key
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, LoadingIndicator, Static

from hhcli.database import get_all_profiles, set_active_profile, load_profile_config, log_to_db


class VacancyListScreen(Screen):
    """Экран для отображения списка вакансий и их детального просмотра."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Назад", show=True),
    ]

    def __init__(self, vacancies: list[dict]):
        super().__init__()
        self.vacancies = vacancies
        self.vacancy_cache = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        with Horizontal():
            yield DataTable(id="vacancy_table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="details_pane"):
                yield Static("[dim]Выберите вакансию в списке слева для просмотра деталей.[/dim]", id="vacancy_details")
                yield LoadingIndicator()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("№", "Вакансия", "Компания", "З/П")

        if not self.vacancies:
            table.add_row("Вакансии не найдены.")
            self.query_one(LoadingIndicator).display = False
            return

        for i, vacancy in enumerate(self.vacancies):
            salary = vacancy.get('salary')
            salary_str = "[dim]не указана[/dim]"
            if salary:
                currency = salary.get('currency', '').upper()
                s_from = salary.get('from')
                s_to = salary.get('to')
                if s_from and s_to:
                    salary_str = f"{s_from:,} - {s_to:,} {currency}".replace(",", " ")
                elif s_from:
                    salary_str = f"от {s_from:,} {currency}".replace(",", " ")
                elif s_to:
                    salary_str = f"до {s_to:,} {currency}".replace(",", " ")

            table.add_row(
                str(i + 1),
                vacancy['name'],
                vacancy['employer']['name'],
                salary_str,
                key=vacancy['id']
            )
        self.query_one(LoadingIndicator).display = False

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        vacancy_id = event.row_key.value
        if vacancy_id:
            log_to_db("INFO", "VacancyListScreen", f"Просмотр деталей для вакансии ID: {vacancy_id}.")
            self.update_vacancy_details(vacancy_id)

    def update_vacancy_details(self, vacancy_id: str) -> None:
        if vacancy_id in self.vacancy_cache:
            self.display_vacancy_details(self.vacancy_cache[vacancy_id])
            return

        self.query_one(LoadingIndicator).display = True
        self.query_one("#vacancy_details").update("Загрузка...")
        self.run_worker(self.fetch_vacancy_details(vacancy_id), exclusive=True, thread=True)

    async def fetch_vacancy_details(self, vacancy_id: str) -> None:
        try:
            details = self.app.client.get_vacancy_details(vacancy_id)
            self.vacancy_cache[vacancy_id] = details
            self.app.call_from_thread(self.display_vacancy_details, details)
        except Exception as e:
            log_to_db("ERROR", "VacancyListScreen", f"Ошибка загрузки деталей для вакансии {vacancy_id}: {e}")
            self.app.call_from_thread(self.query_one("#vacancy_details").update, f"Ошибка загрузки: {e}")

    def display_vacancy_details(self, details: dict) -> None:
        description_text = details.get('description', '')
        clean_description = html.unescape(re.sub('<[^<]+?>', '', description_text))

        key_skills_list = details.get('key_skills', [])
        key_skills = ", ".join([skill['name'] for skill in key_skills_list])

        text_to_display = f"""[bold cyan]Вакансия:[/bold cyan] {details['name']}
[bold cyan]Компания:[/bold cyan] {details['employer']['name']}
[bold cyan]Ссылка:[/bold cyan] [u]{details['alternate_url']}[/u]
---
[bold]Ключевые навыки:[/bold]
{key_skills or '[dim]Не указаны[/dim]'}
---
[bold]Описание:[/bold]

{clean_description[:2000]}{'...' if len(clean_description) > 2000 else ''}
"""
        self.query_one("#vacancy_details").update(text_to_display)
        self.query_one(LoadingIndicator).display = False
        self.query_one("#details_pane").scroll_home(animate=False)


class SearchModeScreen(Screen):
    """Экран выбора режима поиска: автоматический или ручной."""

    BINDINGS = [
        Binding("1", "run_search('auto')", "Авто", show=False),
        Binding("2", "run_search('manual')", "Ручной", show=False),
        Binding("escape", "handle_escape", "Назад/Выход", show=True),
    ]

    def __init__(self, resume_id: str, resume_title: str, is_root_screen: bool = False):
        super().__init__()
        self.resume_id = resume_id
        self.resume_title = resume_title
        self.is_root_screen = is_root_screen

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        yield Static(f"Выбрано резюме: [b cyan]{self.resume_title}[/b cyan]\n\n")
        yield Static("[b]Выберите способ поиска вакансий:[/b]")
        yield Static("  [yellow]1)[/] Автоматический (рекомендации hh.ru)")
        yield Static("  [yellow]2)[/] Ручной (поиск по ключевым словам)")
        yield Footer()

    def action_handle_escape(self) -> None:
        """Обрабатывает нажатие Escape: выходит из приложения, если это корневой экран, иначе возвращается назад."""
        if self.is_root_screen:
            self.app.exit()
        else:
            self.app.pop_screen()

    async def action_run_search(self, mode: str) -> None:
        """Запускает поиск вакансий в выбранном режиме."""
        log_to_db("INFO", "SearchModeScreen", f"Выбран '{mode}' режим поиска.")
        self.app.notify("Идет поиск вакансий...", title="Загрузка", timeout=10)
        try:
            if mode == "auto":
                result = self.app.client.get_similar_vacancies(self.resume_id)
            else: # mode == "manual"
                config = load_profile_config(self.app.client.profile_name)
                result = self.app.client.search_vacancies(config)

            vacancies = result.get("items", [])
            log_to_db("INFO", "SearchModeScreen", f"Найдено {len(vacancies)} вакансий. Переход к списку.")
            self.app.push_screen(VacancyListScreen(vacancies=vacancies))
        except Exception as e:
            log_to_db("ERROR", "SearchModeScreen", f"Ошибка API при поиске вакансий: {e}")
            self.app.notify(f"Ошибка API: {e}", title="Ошибка", severity="error")


class ResumeSelectionScreen(Screen):
    """Экран выбора одного из резюме пользователя."""

    def __init__(self, resume_data: dict):
        super().__init__()
        self.resume_data = resume_data

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        yield DataTable(id="resume_table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("ID резюме", "Должность", "Ссылка")
        table.fixed_columns = 1

        resumes = self.resume_data.get("items", [])
        if not resumes:
            table.add_row("[b]У вас нет ни одного резюме.[/b]")
            return

        for resume in resumes:
            table.add_row(
                resume.get("id"),
                f"[bold green]{resume.get('title')}[/bold green]",
                resume.get("alternate_url"),
                key=resume.get("id")
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        resume_id = event.row_key.value
        resume_title = ""
        for resume in self.resume_data.get("items", []):
            if resume.get("id") == resume_id:
                resume_title = resume.get("title")
                break

        log_to_db("INFO", "ResumeScreen", f"Выбрано резюме ID: {resume_id}, Название: '{resume_title}'.")
        # is_root_screen=False, так как мы переходим с другого экрана
        self.app.push_screen(SearchModeScreen(resume_id=resume_id, resume_title=resume_title, is_root_screen=False))


class ProfileSelectionScreen(Screen):
    """Экран выбора профиля, если их в базе несколько."""

    def __init__(self, all_profiles: list[dict]):
        super().__init__()
        self.all_profiles = all_profiles

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        yield Static("[b]Выберите профиль для работы:[/b]\n")
        yield DataTable(id="profile_table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Имя профиля", "Email")
        for profile in self.all_profiles:
            table.add_row(
                f"[bold green]{profile['profile_name']}[/bold green]",
                profile['email'],
                key=profile['profile_name']
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        profile_name = event.row_key.value
        log_to_db("INFO", "ProfileScreen", f"Пользователь выбрал профиль '{profile_name}'.")
        set_active_profile(profile_name)
        self.dismiss(profile_name)

    def on_key(self, event: Key) -> None:
        # Предотвращаем закрытие экрана выбора профиля по Escape
        if event.key == "escape":
            event.prevent_default()


class HHCliApp(App):
    """Основное TUI-приложение."""

    CSS = """
    #vacancy_table {
        width: 105;
        min-width: 45;
    }
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

    def __init__(self, client):
        super().__init__()
        self.client = client

    async def on_mount(self) -> None:
        log_to_db("INFO", "TUI", "Основное TUI приложение смонтировано.")
        all_profiles = get_all_profiles()

        if not all_profiles:
            self.exit(result="В базе не найдено ни одного профиля. Пожалуйста, войдите в аккаунт, используя флаг --auth <имя_профиля>.")
            return

        if len(all_profiles) == 1:
            profile_name = all_profiles[0]['profile_name']
            log_to_db("INFO", "TUI", f"Найден один профиль '{profile_name}', используется автоматически.")
            set_active_profile(profile_name)
            await self.proceed_with_profile(profile_name)
        else:
            log_to_db("INFO", "TUI", "Найдено несколько профилей, отображается экран выбора.")
            self.push_screen(
                ProfileSelectionScreen(all_profiles),
                self.on_profile_selected
            )

    async def on_profile_selected(self, selected_profile: str) -> None:
        if selected_profile:
            log_to_db("INFO", "TUI", f"Выбран профиль '{selected_profile}' из списка.")
            await self.proceed_with_profile(selected_profile)
        else:
            log_to_db("INFO", "TUI", "Выбор профиля отменен, выход.")
            self.exit()

    async def proceed_with_profile(self, profile_name: str) -> None:
        try:
            self.client.load_profile_data(profile_name)
            self.sub_title = f"Профиль: {profile_name}"

            self.app.notify("Синхронизация истории откликов...", title="Синхронизация", timeout=20)
            self.run_worker(self.client.sync_negotiation_history, thread=True, name="SyncWorker")

            log_to_db("INFO", "TUI", f"Загрузка резюме для профиля '{profile_name}'.")
            resumes = self.client.get_my_resumes()
            
            items = resumes.get("items", [])
            if len(items) == 1:
                resume = items[0]
                log_to_db("INFO", "TUI", "Найдено одно резюме, переход к выбору режима поиска.")
                # is_root_screen=True, так как это первый интерактивный экран
                self.push_screen(SearchModeScreen(
                    resume_id=resume['id'], 
                    resume_title=resume['title'], 
                    is_root_screen=True
                ))
            else:
                log_to_db("INFO", "TUI", "Найдено несколько резюме, переход к экрану выбора.")
                self.push_screen(ResumeSelectionScreen(resume_data=resumes))
        except Exception as e:
            log_to_db("ERROR", "TUI", f"Критическая ошибка при загрузке профиля/резюме: {e}")
            self.exit(result=e)

    def action_quit(self) -> None:
        """Действие для выхода из приложения."""
        log_to_db("INFO", "TUI", "Пользователь запросил выход из приложения.")
        self.exit()