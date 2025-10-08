from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, DataTable, Static
from textual.binding import Binding
from textual.events import Key

from hhcli.database import get_all_profiles, set_active_profile

class ProfileSelectionScreen(Screen):
    """Экран для выбора одного из сохраненных профилей."""

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
                f"[bold green]{profile['profile_name']}[/]",
                profile['email'],
                key=profile['profile_name']
            )
    
    def on_data_table_row_selected(self, event) -> None:
        """Обработчик выбора профиля."""
        profile_name = event.row_key.value
        set_active_profile(profile_name)
        self.dismiss(profile_name)

    def on_key(self, event: Key) -> None:
        """Обрабатывает нажатие клавиш на этом экране."""
        if event.key == "escape":
            # Предотвращаем стандартное поведение (выход с экрана),
            # чтобы избежать пустого экрана.
            event.prevent_default()

class SearchModeScreen(Screen):
    """Экран для выбора режима поиска вакансий."""

    def __init__(self, resume_id: str, resume_title: str):
        super().__init__()
        self.resume_id = resume_id
        self.resume_title = resume_title

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, name="hh-cli")
        # ИСПРАВЛЕНО: Закрывающий тег теперь [b cyan]
        yield Static(f"Выбрано резюме: [b cyan]{self.resume_title}[/b cyan]\n\n")
        yield Static("[b]Выберите способ поиска вакансий:[/b]")
        yield Static("  [yellow]1)[/] Автоматический (рекомендации hh.ru)")
        yield Static("  [yellow]2)[/] Ручной (поиск по ключевым словам)")
        yield Footer()

    def on_key(self, event) -> None:
        """Обрабатывает нажатия клавиш."""
        if event.key == "1":
            self.app.notify("Выбран автоматический поиск (пока не реализовано)", title="Действие")
        elif event.key == "2":
            self.app.notify("Выбран ручной поиск (пока не реализовано)", title="Действие")
        elif event.key == "escape":
            self.app.pop_screen()


# --- Экран №1: Выбор резюме ---

class ResumeSelectionScreen(Screen):
    """Экран для выбора одного из резюме пользователя."""

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
        """Заполняет таблицу данными после ее отрисовки."""
        table = self.query_one(DataTable)
        table.add_columns("ID резюме", "Должность", "Ссылка")
        table.fixed_columns = 1

        resumes = self.resume_data.get("items", [])
        if not resumes:
            table.add_row("[b]У вас нет ни одного резюме.[/b]")
            return

        for resume in resumes:
            # ИСПРАВЛЕНО: Хотя это и работало благодаря shorthand `[/]`,
            # явное закрытие [bold green] надежнее.
            table.add_row(
                resume.get("id"),
                f"[bold green]{resume.get('title')}[/bold green]",
                resume.get("alternate_url"),
                key=resume.get("id")
            )
    
    def on_data_table_row_selected(self, event) -> None:
        """
        Обработчик события выбора строки в таблице (по нажатию Enter).
        """
        resume_id = event.row_key.value
        resume_title = ""
        for resume in self.resume_data.get("items", []):
            if resume.get("id") == resume_id:
                resume_title = resume.get("title")
                break
        
        self.app.push_screen(SearchModeScreen(resume_id=resume_id, resume_title=resume_title))

class HHCliApp(App):
    """Главное TUI-приложение."""

    SCREENS = {
        "profile_select": ProfileSelectionScreen,
        "resume_select": ResumeSelectionScreen,
        "search_mode": SearchModeScreen,
    }
    
    BINDINGS = [
        Binding("q", "quit", "Выход"),
        Binding("й", "quit", "Выход (RU)", show=False),
    ]

    def __init__(self, client):
        super().__init__()
        self.client = client

    async def on_mount(self) -> None:
        """При старте приложения решает, какой экран показать первым."""
        all_profiles = get_all_profiles()
        
        if not all_profiles:
            self.exit(result="В базе не найдено ни одного профиля. Пожалуйста, войдите в аккаунт, используя флаг --auth <имя_профиля>.")
            return
        
        if len(all_profiles) == 1:
            # Если профиль всего один, выбираем его автоматически
            profile_name = all_profiles[0]['profile_name']
            set_active_profile(profile_name)
            await self.proceed_with_profile(profile_name)
        else:
            # Если профилей несколько, показываем экран выбора
            # self.push_screen() вернет результат, когда экран закроется через self.dismiss()
            self.push_screen(
                ProfileSelectionScreen(all_profiles),
                self.on_profile_selected
            )
            
    async def on_profile_selected(self, selected_profile: str) -> None:
        """Колбэк, который вызывается после выбора профиля на экране."""
        if selected_profile:
            await self.proceed_with_profile(selected_profile)
        else:
            # Пользователь нажал Q на экране выбора профиля
            self.exit()

    async def proceed_with_profile(self, profile_name: str) -> None:
        """Загружает данные профиля и переходит к выбору резюме."""
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