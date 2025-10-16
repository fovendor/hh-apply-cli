from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Static,
    Switch,
    TextArea,
    Select,
)

from ..database import (
    list_areas,
    list_professional_roles,
    load_profile_config,
    log_to_db,
    save_profile_config,
)
from ..reference_data import ensure_reference_data
from .widgets.search_select import (
    AreaSelect,
    ProfessionalRoleMultiSelect,
    SearchOption,
)


class ConfigScreen(Screen):
    """Экран для редактирования конфигурации профиля."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Отмена"),
        Binding("ctrl+s", "save_config", "Сохранить"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._quit_binding_q = None
        self._quit_binding_cyrillic = None

    def compose(self) -> ComposeResult:
        with Vertical(id="config_screen"):
            yield Header(show_clock=True, name="hh-cli - Настройки")
            with VerticalScroll(id="config-form"):
                yield Static("Параметры поиска", classes="header")
                yield Label("Ключевые слова для поиска (через запятую):")
                yield Input(id="text_include", placeholder='Python developer, Backend developer')
                yield Label("Исключающие слова (через запятую):")
                yield Input(id="negative", placeholder="senior, C++, DevOps, аналитик")

                yield Label("Формат работы:")
                yield Select([], id="work_format")

                yield Label("Регион / город поиска:")
                yield AreaSelect(id="area_select")

                yield Label("Профессиональные роли (можно выбрать несколько):")
                yield ProfessionalRoleMultiSelect(id="role_select")

                yield Label("Область поиска:")
                yield Select(
                    [
                        ("В названии вакансии", "name"),
                        ("В названии компании", "company_name"),
                        ("В описании вакансии", "description"),
                    ],
                    id="search_field",
                )

                yield Label("Период публикации (дней, 1-30):")
                yield Input(id="period", placeholder="3")

                yield Static("Отображение и отклики", classes="header")
                yield Horizontal(
                    Switch(id="skip_applied_in_same_company"),
                    Label("Пропускать компании, куда уже был отклик"),
                    classes="switch-container",
                )
                yield Horizontal(
                    Switch(id="deduplicate_by_name_and_company"),
                    Label("Убирать дубли по 'Название+Компания'"),
                    classes="switch-container",
                )
                yield Horizontal(
                    Switch(id="strikethrough_applied_vac"),
                    Label("Зачеркивать вакансии по точному ID"),
                    classes="switch-container",
                )
                yield Horizontal(
                    Switch(id="strikethrough_applied_vac_name"),
                    Label("Зачеркивать вакансии по 'Название+Компания'"),
                    classes="switch-container",
                )

                yield Static("Оформление", classes="header")
                yield Label("Тема интерфейса:")
                yield Select([], id="theme")

                yield Static("Сопроводительное письмо", classes="header")
                yield TextArea(id="cover_letter", language="markdown")

                yield Button("Сохранить и выйти", variant="success", id="save-button")

            yield Footer()

    def on_mount(self) -> None:
        """При монтировании экрана удаляем глобальные биндинги выхода."""
        self._quit_binding_q = self.app._bindings.keys.pop("q", None)
        self._quit_binding_cyrillic = self.app._bindings.keys.pop("й", None)

        self.run_worker(self._load_data_worker, thread=True)

    def on_unmount(self) -> None:
        """При размонтировании экрана восстанавливаем глобальные биндинги."""
        if self._quit_binding_q:
            self.app._bindings.keys['q'] = self._quit_binding_q
        if self._quit_binding_cyrillic:
            self.app._bindings.keys['й'] = self._quit_binding_cyrillic

    def _load_data_worker(self) -> None:
        """
        Эта часть выполняется в фоновом потоке, загружает данные, не трогая виджеты.
        """
        profile_name = self.app.client.profile_name
        config = load_profile_config(profile_name)
        work_formats = self.app.dictionaries.get("work_format", [])
        areas = list_areas()
        roles = list_professional_roles()
        if not areas or not roles:
            try:
                ensure_reference_data(self.app.client)
            except Exception as exc:
                log_to_db("ERROR", "ConfigScreen", f"Не удалось обновить справочники: {exc}")
                # Не критично, просто оставим списки пустыми и покажем пользователю
                pass
            areas = list_areas()
            roles = list_professional_roles()

        if not areas:
            self.app.call_from_thread(
                self.app.notify,
                "Не удалось загрузить справочник городов.",
                severity="warning",
            )
            log_to_db("WARN", "ConfigScreen", "Справочник городов недоступен")
        if not roles:
            self.app.call_from_thread(
                self.app.notify,
                "Не удалось загрузить справочник профессиональных ролей.",
                severity="warning",
            )
            log_to_db("WARN", "ConfigScreen", "Справочник профессиональных ролей недоступен")

        self.app.call_from_thread(self._populate_form, config, work_formats, areas, roles)

    def _populate_form(
        self,
        config: dict,
        work_formats: list,
        areas: list[dict],
        roles: list[dict],
    ) -> None:
        """
        Эта часть выполняется в основном потоке, обновляет виджеты.
        """
        self.query_one("#work_format", Select).set_options(
            [(item["name"], item["id"]) for item in work_formats]
        )

        self.query_one("#text_include", Input).value = ", ".join(config.get("text_include", []))
        self.query_one("#negative", Input).value = ", ".join(config.get("negative", []))
        self.query_one("#work_format", Select).value = config.get("work_format")
        self.query_one("#search_field", Select).value = config.get("search_field")
        self.query_one("#period", Input).value = config.get("period", "")
        self.query_one("#cover_letter", TextArea).load_text(config.get("cover_letter", ""))
        self.query_one("#skip_applied_in_same_company", Switch).value = config.get("skip_applied_in_same_company", False)
        self.query_one("#deduplicate_by_name_and_company", Switch).value = config.get("deduplicate_by_name_and_company", True)
        self.query_one("#strikethrough_applied_vac", Switch).value = config.get("strikethrough_applied_vac", True)
        self.query_one("#strikethrough_applied_vac_name", Switch).value = config.get("strikethrough_applied_vac_name", True)

        area_widget = self.query_one("#area_select", AreaSelect)
        area_widget.set_options(
            [
                SearchOption(
                    id=area["id"],
                    prompt=f"{area['full_name']} [dim]({area['id']})[/]",
                    label=area["full_name"],
                    search_text=area["search_name"],
                )
                for area in areas
            ]
        )
        area_widget.set_value(config.get("area_id"))

        roles_widget = self.query_one("#role_select", ProfessionalRoleMultiSelect)
        roles_widget.set_options(
            [
                SearchOption(
                    id=role["id"],
                    prompt=f"{role['category_name']} — {role['name']} [dim]({role['id']})[/]",
                    label=f"{role['category_name']} — {role['name']}",
                    search_text=role["search_name"],
                )
                for role in roles
            ]
        )
        roles_widget.set_value(config.get("role_ids_config", []))

        theme_select = self.query_one("#theme", Select)
        themes = sorted(self.app.css_manager.themes.values(), key=lambda t: t._name)
        theme_select.set_options([
            (self._beautify_theme_name(theme._name), theme._name)
            for theme in themes
        ])
        theme_select.value = config.get("theme", "hhcli-base")

    @staticmethod
    def _beautify_theme_name(theme_name: str) -> str:
        name = theme_name.removeprefix("hhcli-").replace("-", " ")
        return name.title() or theme_name

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-button":
            self.action_save_config()

    def action_save_config(self) -> None:
        """Собрать данные с формы и сохранить в БД."""
        profile_name = self.app.client.profile_name

        def parse_list(text: str) -> list[str]:
            """Безопасно парсит строку с запятыми в список строк."""
            return [item.strip() for item in text.split(",") if item.strip()]

        area_widget = self.query_one("#area_select", AreaSelect)
        roles_widget = self.query_one("#role_select", ProfessionalRoleMultiSelect)

        config = {
            "text_include": parse_list(self.query_one("#text_include", Input).value),
            "negative": parse_list(self.query_one("#negative", Input).value),
            "role_ids_config": roles_widget.values,
            "work_format": self.query_one("#work_format", Select).value,
            "area_id": area_widget.value or "",
            "search_field": self.query_one("#search_field", Select).value,
            "period": self.query_one("#period", Input).value,
            "cover_letter": self.query_one("#cover_letter", TextArea).text,
            "skip_applied_in_same_company": self.query_one("#skip_applied_in_same_company", Switch).value,
            "deduplicate_by_name_and_company": self.query_one("#deduplicate_by_name_and_company", Switch).value,
            "strikethrough_applied_vac": self.query_one("#strikethrough_applied_vac", Switch).value,
            "strikethrough_applied_vac_name": self.query_one("#strikethrough_applied_vac_name", Switch).value,
            "theme": self.query_one("#theme", Select).value or "hhcli-base",
        }

        save_profile_config(profile_name, config)
        self.app.css_manager.set_theme(config["theme"])
        self.app.notify("Настройки успешно сохранены.", title="Успех", severity="information")
        self.app.pop_screen()
