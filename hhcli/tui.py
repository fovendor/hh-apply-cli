from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, DataTable, Static
from textual.binding import Binding

# --- Экран №2: Выбор режима поиска ---

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


# --- Основной класс приложения ---

class HHCliApp(App):
    """Главное TUI-приложение."""

    SCREENS = {
        "resume_select": ResumeSelectionScreen,
        "search_mode": SearchModeScreen,
    }
    
    BINDINGS = [
        ("q", "quit", "Выход"),
    ]

    def __init__(self, client):
        super().__init__()
        self.client = client

    def on_mount(self) -> None:
        """При старте приложения получаем данные и показываем первый экран."""
        try:
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