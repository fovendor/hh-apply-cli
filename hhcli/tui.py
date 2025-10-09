import html
import re
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.events import Key
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, LoadingIndicator, Static

from hhcli.database import get_all_profiles, set_active_profile, load_profile_config

class VacancyListScreen(Screen):
    """Экран для отображения списка вакансий и их детального просмотра."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Назад", show=True),
        Binding("q", "quit", "Выход", priority=True),
        Binding("й", "quit", "Выход (RU)", show=False, priority=True),
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
                if salary.get('from') and salary.get('to'):
                    salary_str = f"{salary['from']:,} - {salary['to']:,} {currency}".replace(",", " ")
                elif salary.get('from'):
                    salary_str = f"от {salary['from']:,} {currency}".replace(",", " ")
                elif salary.get('to'):
                    salary_str = f"до {salary['to']:,} {currency}".replace(",", " ")

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
            self.app.call_from_thread(self.query_one("#vacancy_details").update, f"Ошибка загрузки: {e}")

    def display_vacancy_details(self, details: dict) -> None:
        description_text = details.get('description', '')
        clean_description = html.unescape(re.sub('<[^<]+?>', '', description_text))

        key_skills = ", ".join([skill['name'] for skill in details.get('key_skills', [])])
        
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
    def __init__(self, resume_id: str, resume_title: str):
        super().__init__()
        self.resume_id = resume_id
        self.resume_title = resume_title
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        yield Static(f"Выбрано резюме: [b cyan]{self.resume_title}[/b cyan]\n\n")
        yield Static("[b]Выберите способ поиска вакансий:[/b]")
        yield Static("  [yellow]1)[/] Автоматический (рекомендации hh.ru)")
        yield Static("  [yellow]2)[/] Ручной (поиск по ключевым словам)")
        yield Footer()
    async def on_key(self, event: Key) -> None:
        if event.key == "1":
            await self.run_search(mode="auto")
        elif event.key == "2":
            await self.run_search(mode="manual")
        elif event.key == "escape":
            self.app.pop_screen()
    async def run_search(self, mode: str):
        self.app.notify("Идет поиск вакансий...", title="Загрузка", timeout=10)
        try:
            if mode == "auto":
                result = self.app.client.get_similar_vacancies(self.resume_id)
            else:
                config = load_profile_config(self.app.client.profile_name)
                result = self.app.client.search_vacancies(config)
            vacancies = result.get("items", [])
            self.app.push_screen(VacancyListScreen(vacancies=vacancies))
        except Exception as e:
            self.app.notify(f"Ошибка API: {e}", title="Ошибка", severity="error")

class ResumeSelectionScreen(Screen):
    BINDINGS = [
        Binding("q", "quit", "Выход", priority=True),
        Binding("й", "quit", "Выход (RU)", show=False, priority=True),
    ]
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
        self.app.push_screen(SearchModeScreen(resume_id=resume_id, resume_title=resume_title))

class ProfileSelectionScreen(Screen):
    BINDINGS = [
        Binding("q", "quit", "Выход", priority=True),
        Binding("й", "quit", "Выход (RU)", show=False, priority=True),
    ]
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
        set_active_profile(profile_name)
        self.dismiss(profile_name)
    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            event.prevent_default()

class HHCliApp(App):
    CSS = """
    #vacancy_table {
        width: 105; /* Ширина левой панели в символах, как в fzf */
        min-width: 45; /* Минимальная ширина */
        # overflow-x: hidden; /* Отключаем горизонтальный скролл */
    }

    """
    SCREENS = {
        "profile_select": ProfileSelectionScreen,
        "resume_select": ResumeSelectionScreen,
        "search_mode": SearchModeScreen,
        "vacancy_list": VacancyListScreen,
    }
    BINDINGS = [
        Binding("q", "quit", "Выход"),
        Binding("й", "quit", "Выход (RU)", show=False),
    ]
    def __init__(self, client):
        super().__init__()
        self.client = client
    async def on_mount(self) -> None:
        all_profiles = get_all_profiles()
        if not all_profiles:
            self.exit(result="В базе не найдено ни одного профиля. Пожалуйста, войдите в аккаунт, используя флаг --auth <имя_профиля>.")
            return
        if len(all_profiles) == 1:
            profile_name = all_profiles[0]['profile_name']
            set_active_profile(profile_name)
            await self.proceed_with_profile(profile_name)
        else:
            self.push_screen(
                ProfileSelectionScreen(all_profiles),
                self.on_profile_selected
            )
    async def on_profile_selected(self, selected_profile: str) -> None:
        if selected_profile:
            await self.proceed_with_profile(selected_profile)
        else:
            self.exit()
    async def proceed_with_profile(self, profile_name: str) -> None:
        try:
            self.client.load_profile_data(profile_name)
            self.sub_title = f"Профиль: {profile_name}"
            resumes = self.client.get_my_resumes()
            if len(resumes.get("items", [])) == 1:
                resume = resumes["items"][0]
                self.push_screen(
                    SearchModeScreen(resume_id=resume['id'], resume_title=resume['title'])
                )
            else:
                self.push_screen(ResumeSelectionScreen(resume_data=resumes))
        except Exception as e:
            self.exit(result=e)