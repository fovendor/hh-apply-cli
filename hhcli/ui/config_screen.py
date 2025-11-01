from dataclasses import dataclass
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll, Horizontal, Center
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
    get_default_config,
)
from ..reference_data import ensure_reference_data
from ..constants import ConfigKeys, LogSource

COLUMN_WIDTH_MAX = 200


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(str(text).lower().split())


def _select_value(widget: Select) -> str | None:
    """Return a plain value for Select widgets, normalizing the blank sentinel."""
    value = widget.value
    if value is None or value is Select.BLANK:
        return None
    return value


def _set_select_value(widget: Select, value: str | None) -> None:
    """Safely restore saved values to Select widgets, clearing when empty."""
    if value:
        widget.value = value
    else:
        widget.clear()


def _theme_value(value: str | object | None) -> str | None:
    if value is None or value is Select.BLANK:
        return None
    return str(value)


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


@dataclass(frozen=True)
class LayoutField:
    label: str
    input_id: str
    config_key: str
    min_value: int = 1
    max_value: int = 100

    @property
    def selector(self) -> str:
        return f"#{self.input_id}"


@dataclass(frozen=True)
class LayoutSectionDef:
    title: str
    fields: tuple[LayoutField, ...]
    css_class: str


class AreaPickerDialog(ModalScreen[str | None]):
    """Диалог выбора региона/города."""

    BINDINGS = [
        Binding("escape", "cancel", "Отмена"),
        Binding("enter", "apply", "Применить"),
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
            yield Static("[dim]Пробел — выбрать, Enter — применить[/dim]", classes="picker__hint")
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

    def on_selection_list_option_selected(
        self, event: SelectionList.OptionSelected
    ) -> None:
        event.stop()
        if event.selection_list.id == "picker-list":
            value = str(event.option.value)
            self.selected_id = None if self.selected_id == value else value
            self._refresh(self._search.value)

    def on_selection_list_selection_toggled(
        self, event: SelectionList.SelectionToggled
    ) -> None:
        if event.selection_list.id != "picker-list":
            return
        value = str(event.selection.value)
        selected_values = {str(val) for val in event.selection_list.selected}
        self.selected_id = value if value in selected_values else None
        self._refresh(self._search.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "picker-apply":
            self.dismiss(self.selected_id)
        elif event.button.id == "picker-clear":
            self.selected_id = None
            self._refresh(self._search.value)
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
        self._list.deselect_all()
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


class ConfigUnsavedChangesDialog(ModalScreen[str | None]):
    """Подтверждение выхода при наличии несохранённых изменений."""

    BINDINGS = [
        Binding("escape", "cancel", "Отмена"),
    ]

    def compose(self) -> ComposeResult:
        with Center(id="config-confirm-center"):
            with Vertical(id="config-confirm-dialog", classes="config-confirm") as dialog:
                dialog.border_title = "Подтверждение"
                dialog.styles.border_title_align = "left"
                yield Static(
                    "Сохранить внесённые изменения перед выходом?",
                    classes="config-confirm__message",
                    expand=True,
                )
                with Horizontal(classes="config-confirm__buttons"):
                    yield Button("Да", id="confirm-save", variant="success")
                    yield Button("Нет", id="confirm-discard", classes="decline")
                    yield Button("Отмена", id="confirm-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "confirm-save": "save",
            "confirm-discard": "discard",
            "confirm-cancel": "cancel",
        }
        decision = mapping.get(event.button.id or "")
        if decision:
            self.dismiss(decision)

    def action_cancel(self) -> None:
        self.dismiss("cancel")


class ConfigScreen(Screen):
    """Экран для редактирования конфигурации профиля."""

    BINDINGS = [
        Binding("escape", "cancel", "Назад"),
        Binding("ctrl+s", "save_config", "Сохранить"),
    ]

    LAYOUT_SECTIONS: ClassVar[tuple[LayoutSectionDef, ...]] = (
        LayoutSectionDef(
            "Экран поиска вакансий",
            (
                LayoutField(
                    "Ширина панели \"Вакансии\" (%)",
                    "vacancy_pane_percent",
                    ConfigKeys.VACANCY_LEFT_PANE_PERCENT,
                    min_value=10,
                    max_value=90,
                ),
                LayoutField(
                    "Колонка \"№\" (симв.)",
                    "vacancy_col_index_width",
                    ConfigKeys.VACANCY_COL_INDEX_WIDTH,
                    max_value=COLUMN_WIDTH_MAX,
                ),
                LayoutField(
                    "Колонка \"Название\" (симв.)",
                    "vacancy_col_title_width",
                    ConfigKeys.VACANCY_COL_TITLE_WIDTH,
                    max_value=COLUMN_WIDTH_MAX,
                ),
                LayoutField(
                    "Колонка \"Компания\" (симв.)",
                    "vacancy_col_company_width",
                    ConfigKeys.VACANCY_COL_COMPANY_WIDTH,
                    max_value=COLUMN_WIDTH_MAX,
                ),
                LayoutField(
                    "Колонка \"Откликался\" (симв.)",
                    "vacancy_col_previous_width",
                    ConfigKeys.VACANCY_COL_PREVIOUS_WIDTH,
                    max_value=COLUMN_WIDTH_MAX,
                ),
            ),
            "display-settings-group--vacancy",
        ),
        LayoutSectionDef(
            "Экран истории откликов",
            (
                LayoutField(
                    "Ширина панели \"История\" (%)",
                    "history_pane_percent",
                    ConfigKeys.HISTORY_LEFT_PANE_PERCENT,
                    min_value=10,
                    max_value=90,
                ),
                LayoutField(
                    "Колонка \"№\" (симв.)",
                    "history_col_index_width",
                    ConfigKeys.HISTORY_COL_INDEX_WIDTH,
                    max_value=COLUMN_WIDTH_MAX,
                ),
                LayoutField(
                    "Колонка \"Название\" (симв.)",
                    "history_col_title_width",
                    ConfigKeys.HISTORY_COL_TITLE_WIDTH,
                    max_value=COLUMN_WIDTH_MAX,
                ),
                LayoutField(
                    "Колонка \"Компания\" (симв.)",
                    "history_col_company_width",
                    ConfigKeys.HISTORY_COL_COMPANY_WIDTH,
                    max_value=COLUMN_WIDTH_MAX,
                ),
                LayoutField(
                    "Колонка \"Статус\" (симв.)",
                    "history_col_status_width",
                    ConfigKeys.HISTORY_COL_STATUS_WIDTH,
                    max_value=COLUMN_WIDTH_MAX,
                ),
                LayoutField(
                    "Колонка \"✉\" (симв.)",
                    "history_col_sent_width",
                    ConfigKeys.HISTORY_COL_SENT_WIDTH,
                    max_value=COLUMN_WIDTH_MAX,
                ),
                LayoutField(
                    "Колонка \"Дата отклика\" (симв.)",
                    "history_col_date_width",
                    ConfigKeys.HISTORY_COL_DATE_WIDTH,
                    max_value=COLUMN_WIDTH_MAX,
                ),
            ),
            "display-settings-group--history",
        ),
    )

    LAYOUT_FIELDS: ClassVar[tuple[LayoutField, ...]] = tuple(
        field for section in LAYOUT_SECTIONS for field in section.fields
    )

    def __init__(self) -> None:
        super().__init__()
        self._quit_binding_q = None
        self._quit_binding_cyrillic = None
        self._areas: list[AreaOption] = []
        self._roles: list[RoleOption] = []
        self._selected_area_id: str | None = None
        self._selected_role_ids: list[str] = []
        self._initial_config: dict[str, Any] = {}
        self._form_loaded = False
        self._confirm_dialog_active = False
        self._initial_theme_name: str | None = None
        self._preview_theme_name: str | None = None
        self._theme_committed: bool = False

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

                with Horizontal(id="pickers-container"):
                    with Vertical(id="area-picker-container", classes="picker-group"):
                        yield Label("Регион / город поиска:", classes="summary-label")
                        yield Static("-", id="area_summary", classes="value-display")
                        yield Button("Выбрать регион", id="area_picker")

                    with Vertical(id="role-picker-container", classes="picker-group"):
                        yield Label("Профессиональные роли:", classes="summary-label")
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
                    Label("Пропускать компании, куда уже был отклик", classes="switch-label"),
                    classes="switch-container",
                )
                yield Horizontal(
                    Switch(id="deduplicate_by_name_and_company"),
                    Label("Убирать дубли по 'Название+Компания'", classes="switch-label"),
                    classes="switch-container",
                )
                yield Horizontal(
                    Switch(id="strikethrough_applied_vac"),
                    Label("Зачеркивать вакансии по точному ID", classes="switch-label"),
                    classes="switch-container",
                )
                yield Horizontal(
                    Switch(id="strikethrough_applied_vac_name"),
                    Label("Зачеркивать вакансии по 'Название+Компания'", classes="switch-label"),
                    classes="switch-container",
                )

                yield Static("Оформление", classes="header")
                yield Label("Тема интерфейса:")
                yield Select([], id="theme")

                yield Static("Настройки отображения экранов", classes="header")
                with Horizontal(id="display-settings-container", classes="display-settings-grid"):
                    for section in self.LAYOUT_SECTIONS:
                        with Vertical(classes=f"display-settings-group {section.css_class}") as group:
                            group.border_title = section.title
                            group.styles.border_title_align = "left"
                            for field in section.fields:
                                yield self._make_layout_row(field.label, field.input_id)

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
                log_to_db("ERROR", LogSource.CONFIG_SCREEN, f"Не удалось обновить справочники: {exc}")
                pass
            areas = list_areas()
            roles = list_professional_roles()

        if not areas:
            self.app.call_from_thread(
                self.app.notify,
                "Не удалось загрузить справочник городов.",
                severity="warning",
            )
            log_to_db("WARN", LogSource.CONFIG_SCREEN, "Справочник городов недоступен")
        if not roles:
            self.app.call_from_thread(
                self.app.notify,
                "Не удалось загрузить справочник профессиональных ролей.",
                severity="warning",
            )
            log_to_db("WARN", LogSource.CONFIG_SCREEN, "Справочник профессиональных ролей недоступен")

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
        defaults = get_default_config()
        self.query_one("#work_format", Select).set_options(
            [(item["name"], item["id"]) for item in work_formats]
        )

        self.query_one("#text_include", Input).value = ", ".join(config.get(ConfigKeys.TEXT_INCLUDE, []))
        self.query_one("#negative", Input).value = ", ".join(config.get(ConfigKeys.NEGATIVE, []))
        _set_select_value(self.query_one("#work_format", Select), config.get(ConfigKeys.WORK_FORMAT))
        _set_select_value(self.query_one("#search_field", Select), config.get(ConfigKeys.SEARCH_FIELD))
        self.query_one("#period", Input).value = config.get(ConfigKeys.PERIOD, "")
        self.query_one("#cover_letter", TextArea).load_text(config.get(ConfigKeys.COVER_LETTER, ""))
        self.query_one("#skip_applied_in_same_company", Switch).value = config.get(ConfigKeys.SKIP_APPLIED_IN_SAME_COMPANY, False)
        self.query_one("#deduplicate_by_name_and_company", Switch).value = config.get(ConfigKeys.DEDUPLICATE_BY_NAME_AND_COMPANY, True)
        self.query_one("#strikethrough_applied_vac", Switch).value = config.get(ConfigKeys.STRIKETHROUGH_APPLIED_VAC, True)
        self.query_one("#strikethrough_applied_vac_name", Switch).value = config.get(ConfigKeys.STRIKETHROUGH_APPLIED_VAC_NAME, True)

        manager = self.app.css_manager
        manager.reload_themes()

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

        self._selected_area_id = config.get(ConfigKeys.AREA_ID) or None
        raw_roles = config.get(ConfigKeys.ROLE_IDS_CONFIG, [])
        self._selected_role_ids = [str(rid) for rid in raw_roles if str(rid)]

        self._update_area_summary()
        self._update_roles_summary()

        theme_select = self.query_one("#theme", Select)
        themes = sorted(manager.themes.values(), key=lambda t: t._name)
        theme_select.set_options(
            [
                (self._beautify_theme_name(theme._name), theme._name)
                for theme in themes
            ]
        )
        configured_theme = config.get(ConfigKeys.THEME)
        if configured_theme not in manager.themes:
            configured_theme = manager.theme._name
        theme_select.value = configured_theme or manager.theme._name

        current_theme_name = manager.theme._name
        self._initial_theme_name = current_theme_name
        self._preview_theme_name = current_theme_name
        self._theme_committed = False
        self._populate_layout_settings(config, defaults)
        self._initial_config = self._current_form_config()
        self._form_loaded = True

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

    def _apply_theme_preview(self, theme_value: object | None) -> None:
        if not self.app or not self.app.css_manager:
            return
        resolved = _theme_value(theme_value)
        theme_key = (resolved or "hhcli-base").strip() or "hhcli-base"
        if self._preview_theme_name == theme_key:
            return
        try:
            self.app.css_manager.set_theme(theme_key)
            self._preview_theme_name = theme_key
        except ValueError:
            log_to_db(
                "WARN",
                LogSource.CONFIG_SCREEN,
                f"Предпросмотр темы '{theme_key}' недоступен.",
            )

    def _revert_theme_preview(self) -> None:
        if not self.app or not self.app.css_manager:
            return
        target = (self._initial_theme_name or "hhcli-base").strip() or "hhcli-base"
        if self._preview_theme_name == target:
            return
        try:
            self.app.css_manager.set_theme(target)
            self._preview_theme_name = target
        except ValueError:
            log_to_db(
                "WARN",
                LogSource.CONFIG_SCREEN,
                f"Возврат темы '{target}' невозможен, применяется базовая.",
            )
            self.app.css_manager.set_theme("hhcli-base")
            self._preview_theme_name = "hhcli-base"

    def _make_layout_row(self, label_text: str, input_id: str) -> Horizontal:
        return Horizontal(
            Label(label_text, classes="display-settings-label"),
            Input(id=input_id, classes="display-settings-input"),
            classes="display-settings-row",
        )

    def _parse_int_value(
        self,
        selector: str,
        fallback: int,
        *,
        min_value: int = 1,
        max_value: int = 100,
    ) -> int:
        raw = self.query_one(selector, Input).value.strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = fallback
        return max(min_value, min(max_value, value))

    def _current_form_config(self) -> dict[str, Any]:
        """Возвращает текущее состояние формы в формате конфигурации."""
        def parse_list(text: str) -> list[str]:
            return [item.strip() for item in text.split(",") if item.strip()]

        defaults = get_default_config()
        initial = getattr(self, "_initial_config", {})

        def int_setting(selector: str, key: str, *, min_value: int = 1, max_value: int = 100) -> int:
            fallback = initial.get(key, defaults[key])
            return self._parse_int_value(selector, fallback, min_value=min_value, max_value=max_value)

        config_snapshot = {
            ConfigKeys.TEXT_INCLUDE: parse_list(self.query_one("#text_include", Input).value),
            ConfigKeys.NEGATIVE: parse_list(self.query_one("#negative", Input).value),
            ConfigKeys.ROLE_IDS_CONFIG: list(self._selected_role_ids),
            ConfigKeys.WORK_FORMAT: _select_value(self.query_one("#work_format", Select)),
            ConfigKeys.AREA_ID: self._selected_area_id or "",
            ConfigKeys.SEARCH_FIELD: _select_value(self.query_one("#search_field", Select)),
            ConfigKeys.PERIOD: self.query_one("#period", Input).value,
            ConfigKeys.COVER_LETTER: self.query_one("#cover_letter", TextArea).text,
            ConfigKeys.SKIP_APPLIED_IN_SAME_COMPANY: self.query_one("#skip_applied_in_same_company", Switch).value,
            ConfigKeys.DEDUPLICATE_BY_NAME_AND_COMPANY: self.query_one("#deduplicate_by_name_and_company", Switch).value,
            ConfigKeys.STRIKETHROUGH_APPLIED_VAC: self.query_one("#strikethrough_applied_vac", Switch).value,
            ConfigKeys.STRIKETHROUGH_APPLIED_VAC_NAME: self.query_one("#strikethrough_applied_vac_name", Switch).value,
            ConfigKeys.THEME: _theme_value(self.query_one("#theme", Select).value) or "hhcli-base",
        }
        for field in self.LAYOUT_FIELDS:
            config_snapshot[field.config_key] = int_setting(
                field.selector,
                field.config_key,
                min_value=field.min_value,
                max_value=field.max_value,
            )
        return config_snapshot

    def _populate_layout_settings(self, config: dict[str, Any], defaults: dict[str, Any]) -> None:
        for field in self.LAYOUT_FIELDS:
            value = config.get(field.config_key, defaults[field.config_key])
            self.query_one(field.selector, Input).value = str(value)

    def _has_unsaved_changes(self) -> bool:
        if not self._form_loaded:
            return False
        return self._current_form_config() != self._initial_config

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-button":
            self.action_save_config()
        elif event.button.id == "area_picker":
            self._open_area_picker()
        elif event.button.id == "roles_picker":
            self._open_roles_picker()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "theme":
            return
        if not self._form_loaded:
            return
        self._apply_theme_preview(event.value)

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
        self._selected_area_id = area_id
        self._update_area_summary()

    def _on_roles_picker_closed(self, role_ids: list[str] | None) -> None:
        if role_ids is None:
            return
        self._selected_role_ids = role_ids
        self._update_roles_summary()

    def action_cancel(self) -> None:
        """Закрыть экран, при необходимости спросив подтверждение."""
        if not self._has_unsaved_changes():
            self.dismiss(False)
            return
        if self._confirm_dialog_active:
            return
        self._confirm_dialog_active = True
        self.app.push_screen(
            ConfigUnsavedChangesDialog(),
            self._on_unsaved_dialog_closed,
        )

    def action_save_config(self) -> None:
        """Собрать данные с формы и сохранить в БД."""
        profile_name = self.app.client.profile_name
        config = self._current_form_config()

        save_profile_config(profile_name, config)
        self.app.apply_theme_from_profile(profile_name)
        self._theme_committed = True
        self._initial_theme_name = self.app.css_manager.theme._name
        self._preview_theme_name = self._initial_theme_name
        self.app.notify("Настройки успешно сохранены.", title="Успех", severity="information")
        self.dismiss(True)

    def _on_unsaved_dialog_closed(self, decision: str | None) -> None:
        self._confirm_dialog_active = False
        if decision == "save":
            self.action_save_config()
        elif decision == "discard":
            self.dismiss(False)

    def dismiss(self, result=None) -> None:  # type: ignore[override]
        if not self._theme_committed:
            self._revert_theme_preview()
        super().dismiss(result)
