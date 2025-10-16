from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.screen import Screen, ModalScreen
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
    SelectionList,
)
from textual.widgets._selection_list import Selection

from ..database import (
    list_areas,
    list_professional_roles,
    load_profile_config,
    log_to_db,
    save_profile_config,
)
from ..reference_data import ensure_reference_data


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(str(text).lower().split())


@dataclass
class AreaOption:
    id: str
    label: str
    search_text: str


@dataclass
class RoleOption:
    id: str
    label: str
    search_text: str


class AreaPickerDialog(ModalScreen[str | None]):
    """Диалог выбора региона/города."""

    BINDINGS = [
        Binding("escape", "cancel", "Отмена"),
        Binding("enter", "apply", "Выбрать"),
    ]

    def __init__(self, options: list[AreaOption], selected: str | None) -> None:
        super().__init__()
        self.options = options
        self.selected_id = selected
        self._filtered: list[AreaOption] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="picker"):
            yield Static("Выберите регион", classes="picker__title")
            yield Input(placeholder="Начните вводить название...", id="picker-search")
            yield SelectionList(id="picker-list")
            with Horizontal(classes="picker__buttons"):
                yield Button("Выбрать", id="picker-apply", variant="primary")
                yield Button("Отмена", id="picker-cancel")

    def on_mount(self) -> None:
        self._search = self.query_one("#picker-search", Input)
        self._list = self.query_one("#picker-list", SelectionList)
        self._refresh("")
        self.set_timer(0, lambda: self._search.focus())

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-search":
            self._refresh(event.value)

    def on_selection_list_option_selected(
        self, event: SelectionList.OptionSelected
    ) -> None:
        event.stop()
        if event.selection_list.id == "picker-list":
            self.selected_id = str(event.option.value)
            self.dismiss(self.selected_id)

    def on_selection_list_selection_toggled(
        self, event: SelectionList.SelectionToggled
    ) -> None:
        if event.selection_list.id == "picker-list":
            self.selected_id = next(
                (str(value) for value in event.selection_list.selected), None
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "picker-apply":
            self.dismiss(self.selected_id)
        elif event.button.id == "picker-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_apply(self) -> None:
        self.dismiss(self.selected_id)

    def _refresh(self, query: str) -> None:
        normalized = _normalize(query)
        if normalized:
            candidates = [
                option
                for option in self.options
                if normalized in option.search_text
            ][:200]
        else:
            candidates = self.options[:200]
        self._filtered = candidates
        self._list.clear_options()
        for option in candidates:
            self._list.add_option(
                Selection(
                    f"{option.label} [dim]({option.id})[/]",
                    option.id,
                    initial_state=(option.id == self.selected_id),
                )
            )
        if self._list.option_count:
            self._list.highlighted = 0


class RolePickerDialog(ModalScreen[list[str] | None]):
    """Диалог выбора профессиональных ролей."""

    BINDINGS = [
        Binding("escape", "cancel", "Отмена"),
        Binding("enter", "apply", "Применить"),
    ]

    def __init__(self, options: list[RoleOption], selected: list[str]) -> None:
        super().__init__()
        self.options = options
        self.selected_ids = set(selected)
        self._filtered: list[RoleOption] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="picker"):
            yield Static("Выберите профессиональные роли", classes="picker__title")
            yield Input(placeholder="Поиск по названию или категории...", id="picker-search")
            yield SelectionList(id="picker-list")
            yield Static("[dim]Пробел — выбрать/снять, Enter — подтвердить[/dim]", classes="picker__hint")
            with Horizontal(classes="picker__buttons"):
                yield Button("Применить", id="picker-apply", variant="primary")
                yield Button("Очистить", id="picker-clear", variant="warning")
                yield Button("Отмена", id="picker-cancel")

    def on_mount(self) -> None:
        self._search = self.query_one("#picker-search", Input)
        self._list = self.query_one("#picker-list", SelectionList)
        self._refresh("")
        self.set_timer(0, lambda: self._search.focus())

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-search":
            self._refresh(event.value)

    def on_selection_list_selection_toggled(
        self, event: SelectionList.SelectionToggled
    ) -> None:
        if event.selection_list.id != "picker-list":
            return
        self._toggle_value(str(event.selection.value))

    def on_selection_list_option_selected(
        self, event: SelectionList.OptionSelected
    ) -> None:
        event.stop()
        if event.selection_list.id == "picker-list":
            self._toggle_value(str(event.option.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "picker-apply":
            self.dismiss(sorted(self.selected_ids))
        elif event.button.id == "picker-clear":
            self.selected_ids.clear()
            self._refresh(self._search.value)
        elif event.button.id == "picker-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_apply(self) -> None:
        self.dismiss(sorted(self.selected_ids))

    def _refresh(self, query: str) -> None:
        normalized = _normalize(query)
        if normalized:
            candidates = [
                option
                for option in self.options
                if normalized in option.search_text
            ][:400]
        else:
            candidates = self.options[:400]
        self._filtered = candidates
        self._list.clear_options()
        for option in candidates:
            self._list.add_option(
                Selection(
                    option.label,
                    option.id,
                    initial_state=(option.id in self.selected_ids),
                )
            )
        if self._list.option_count:
            self._list.highlighted = 0

    def _toggle_value(self, value: str) -> None:
        if value in self.selected_ids:
            self.selected_ids.remove(value)
        else:
            self.selected_ids.add(value)


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
        self._areas: list[AreaOption] = []
        self._roles: list[RoleOption] = []
        self._selected_area_id: str | None = None
        self._selected_role_ids: list[str] = []

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
                yield Static("-", id="area_summary", classes="value-display")
                yield Button("Выбрать регион", id="area_picker")

                yield Label("Профессиональные роли (можно выбрать несколько):")
                yield Static("-", id="roles_summary", classes="value-display")
                yield Button("Выбрать роли", id="roles_picker")

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

        self._areas = [
            AreaOption(
                id=str(area["id"]),
                label=area["full_name"],
                search_text=_normalize(f"{area['full_name']} {area['name']} {area['id']}"),
            )
            for area in areas
        ]
        self._roles = [
            RoleOption(
                id=str(role["id"]),
                label=f"{role['category_name']} — {role['name']}",
                search_text=_normalize(f"{role['category_name']} {role['name']} {role['id']}"),
            )
            for role in roles
        ]

        self._selected_area_id = config.get("area_id") or None
        raw_roles = config.get("role_ids_config", [])
        self._selected_role_ids = [str(rid) for rid in raw_roles if str(rid)]

        self._update_area_summary()
        self._update_roles_summary()

        theme_select = self.query_one("#theme", Select)
        themes = sorted(self.app.css_manager.themes.values(), key=lambda t: t._name)
        theme_select.set_options(
            [
                (self._beautify_theme_name(theme._name), theme._name)
                for theme in themes
            ]
        )
        theme_select.value = config.get("theme", "hhcli-base")

    def _update_area_summary(self) -> None:
        summary_widget = self.query_one("#area_summary", Static)
        if not self._selected_area_id:
            summary_widget.update("[dim]Не выбрано[/dim]")
            return
        label = self._find_area_label(self._selected_area_id)
        summary_widget.update(label or "[dim]Не выбрано[/dim]")

    def _update_roles_summary(self) -> None:
        summary_widget = self.query_one("#roles_summary", Static)
        if not self._selected_role_ids:
            summary_widget.update("[dim]Не выбрано[/dim]")
            return
        labels = self._find_role_labels(self._selected_role_ids)
        if not labels:
            summary_widget.update("[dim]Не выбрано[/dim]")
            return
        if len(labels) > 3:
            summary_widget.update(", ".join(labels[:3]) + f" [+ ещё {len(labels) - 3}]")
        else:
            summary_widget.update(", ".join(labels))

    def _find_area_label(self, area_id: str) -> str | None:
        for option in self._areas:
            if option.id == area_id:
                return option.label
        return None

    def _find_role_labels(self, role_ids: list[str]) -> list[str]:
        cache = {option.id: option.label for option in self._roles}
        return [cache[rid] for rid in role_ids if rid in cache]

    @staticmethod
    def _beautify_theme_name(theme_name: str) -> str:
        name = theme_name.removeprefix("hhcli-").replace("-", " ")
        return name.title() or theme_name

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-button":
            self.action_save_config()
        elif event.button.id == "area_picker":
            self._open_area_picker()
        elif event.button.id == "roles_picker":
            self._open_roles_picker()

    def _open_area_picker(self) -> None:
        if not self._areas:
            self.app.notify("Справочник городов пуст.", severity="warning")
            return
        self.app.push_screen(
            AreaPickerDialog(self._areas, self._selected_area_id),
            self._on_area_picker_closed,
        )

    def _open_roles_picker(self) -> None:
        if not self._roles:
            self.app.notify("Справочник ролей пуст.", severity="warning")
            return
        self.app.push_screen(
            RolePickerDialog(self._roles, self._selected_role_ids),
            self._on_roles_picker_closed,
        )

    def _on_area_picker_closed(self, area_id: str | None) -> None:
        if area_id is None:
            return
        self._selected_area_id = area_id
        self._update_area_summary()

    def _on_roles_picker_closed(self, role_ids: list[str] | None) -> None:
        if role_ids is None:
            return
        self._selected_role_ids = role_ids
        self._update_roles_summary()

    def action_save_config(self) -> None:
        """Собрать данные с формы и сохранить в БД."""
        profile_name = self.app.client.profile_name

        def parse_list(text: str) -> list[str]:
            """Безопасно парсит строку с запятыми в список строк."""
            return [item.strip() for item in text.split(",") if item.strip()]

        config = {
            "text_include": parse_list(self.query_one("#text_include", Input).value),
            "negative": parse_list(self.query_one("#negative", Input).value),
            "role_ids_config": list(self._selected_role_ids),
            "work_format": self.query_one("#work_format", Select).value,
            "area_id": self._selected_area_id or "",
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
