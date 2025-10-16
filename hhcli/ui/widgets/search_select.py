from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, SelectionList, Static
from textual.widgets._selection_list import Selection


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(str(value).lower().split())


@dataclass
class SearchOption:
    """Описание варианта выбора для SearchSelect."""

    id: str
    prompt: Text | str
    label: str
    search_text: str


class SearchSelectBase(Vertical):
    """Базовый интерфейс для виджетов с поиском по вариантам."""

    allow_multiple: bool = False
    min_query_length: int = 2
    max_results: int = 40
    clear_button_label: str = "Очистить"

    def __init__(
        self,
        *,
        placeholder: str,
        empty_message: str,
        summary_prefix: str,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes or "search-select")
        base_id = id or self.__class__.__name__.lower()
        self._input_id = f"{base_id}-search-input"
        self._list_id = f"{base_id}-options"
        self._info_id = f"{base_id}-info"
        self._clear_id = f"{base_id}-clear"

        self._placeholder = placeholder
        self._empty_message = empty_message
        self._summary_prefix = summary_prefix

        self._options: list[SearchOption] = []
        self._option_map: dict[str, SearchOption] = {}
        self._selected_ids: list[str] = []
        self._current_query: str = ""

        self._input: Input | None = None
        self._list: SelectionList | None = None
        self._info: Static | None = None

    def compose(self):
        yield Input(
            placeholder=self._placeholder,
            id=self._input_id,
            classes="search-select__input",
        )
        yield SelectionList(id=self._list_id, classes="search-select__list")
        with Horizontal(classes="search-select__footer"):
            yield Static("", id=self._info_id, classes="search-select__info")
            yield Button(
                self.clear_button_label,
                id=self._clear_id,
                variant="default",
                classes="search-select__clear",
            )

    def on_mount(self) -> None:
        self._input = self.query_one(f"#{self._input_id}", Input)
        self._list = self.query_one(f"#{self._list_id}", SelectionList)
        self._info = self.query_one(f"#{self._info_id}", Static)
        self._render_options([])
        self._update_info()

    def clear(self) -> None:
        self._selected_ids = []
        if self._list:
            self._list.deselect_all()
            self._list.refresh()
        self._update_info()

    def set_options(self, options: Sequence[SearchOption]) -> None:
        self._options = list(options)
        self._option_map = {opt.id: opt for opt in self._options}
        self._apply_filter(self._current_query)
        self._update_info()

    def set_value(self, value: str | Sequence[str] | None) -> None:
        if value is None:
            self._selected_ids = []
        elif isinstance(value, (list, tuple, set)):
            normalized = [str(item) for item in value]
            if not self.allow_multiple and normalized:
                normalized = [normalized[0]]
            self._selected_ids = [vid for vid in normalized if vid in self._option_map]
        else:
            string_value = str(value)
            self._selected_ids = [string_value] if string_value in self._option_map else []
            if self.allow_multiple:
                # для множественного выбора сохраняем список, даже если одно значение
                self._selected_ids = self._selected_ids
        self._apply_filter(self._current_query)
        self._update_info()

    def get_selected_ids(self) -> list[str]:
        return list(self._selected_ids)

    def get_selected_labels(self) -> list[str]:
        return [
            self._option_map[sid].label
            for sid in self._selected_ids
            if sid in self._option_map
        ]

    # Event handlers ----------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != self._input_id:
            return
        self._apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != self._input_id or not self._list:
            return
        if self._list.option_count:
            self._list.focus()
            if self._list.highlighted is None:
                self._list.highlighted = 0

    def on_input_key(self, event: Input.Key) -> None:
        if event.input.id != self._input_id or not self._list:
            return
        if event.key in ("down", "tab"):
            if self._list.option_count:
                self._list.focus()
                if self._list.highlighted is None:
                    self._list.highlighted = 0
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == self._clear_id:
            self.clear()
            if self._input:
                self._input.value = ""
                self._input.focus()

    def on_selection_list_selection_toggled(
        self, event: SelectionList.SelectionToggled
    ) -> None:
        if event.selection_list.id != self._list_id:
            return

        if not self.allow_multiple:
            selected_values = event.selection_list.selected
            if str(event.selection.value) in selected_values:
                event.selection_list.deselect_all()
                event.selection_list.select(event.selection)
                self._selected_ids = [str(event.selection.value)]
            else:
                self._selected_ids = []
        else:
            self._selected_ids = [str(value) for value in event.selection_list.selected]
        self._update_info()

    # Internal helpers --------------------------------------------------------------

    def _apply_filter(self, raw_query: str) -> None:
        self._current_query = raw_query or ""
        normalized = _normalize(self._current_query)
        show_all_selected = bool(self._selected_ids) and len(normalized) < self.min_query_length

        if normalized and len(normalized.replace(" ", "")) >= self.min_query_length:
            filtered = [
                option
                for option in self._options
                if normalized in option.search_text
            ][: self.max_results]
        elif show_all_selected:
            filtered = [
                self._option_map[sid]
                for sid in self._selected_ids
                if sid in self._option_map
            ]
        else:
            filtered = []

        self._render_options(filtered)
        self._update_info(filtered, normalized)

    def _render_options(self, options: Iterable[SearchOption]) -> None:
        if not self._list:
            return
        selected = set(self._selected_ids)
        self._list.clear_options()
        for option in options:
            selection = Selection(
                option.prompt,
                option.id,
                option.id in selected,
            )
            self._list.add_option(selection)
        if self._list.option_count:
            if self._list.highlighted is None or self._list.highlighted >= self._list.option_count:
                self._list.highlighted = 0
        else:
            self._list.highlighted = None
        self._list.refresh()

    def _update_info(
        self,
        filtered: Sequence[SearchOption] | None = None,
        normalized_query: str | None = None,
    ) -> None:
        if not self._info:
            return

        labels = self.get_selected_labels()
        if not labels:
            summary = f"{self._summary_prefix} не выбрано"
        else:
            if self.allow_multiple and len(labels) > 3:
                primary = ", ".join(labels[:3])
                summary = f"{self._summary_prefix} {primary} (+ ещё {len(labels) - 3})"
            else:
                summary = f"{self._summary_prefix} {', '.join(labels)}"

        message_parts = [summary]

        if normalized_query is not None and len(normalized_query.replace(" ", "")) < self.min_query_length:
            message_parts.append(
                f"[dim]Введите минимум {self.min_query_length} символа для поиска[/dim]"
            )
        elif filtered is not None and not filtered:
            message_parts.append(self._empty_message)

        if self.allow_multiple:
            message_parts.append("[dim]Пробел — выбрать/снять, Enter — подтвердить[/dim]")
        else:
            message_parts.append("[dim]Пробел или Enter — выбрать значение[/dim]")

        self._info.update("\n".join(message_parts))


class AreaSelect(SearchSelectBase):
    allow_multiple = False
    min_query_length = 2
    clear_button_label = "Сбросить"

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(
            id=id,
            placeholder="Начните вводить название города или региона...",
            empty_message="[dim]По запросу ничего не найдено[/dim]",
            summary_prefix="Выбрано:",
            classes="search-select search-select--single",
        )

    @property
    def value(self) -> str | None:
        return self._selected_ids[0] if self._selected_ids else None

    def set_value(self, value: str | None) -> None:
        super().set_value(value)


class ProfessionalRoleMultiSelect(SearchSelectBase):
    allow_multiple = True
    min_query_length = 2
    clear_button_label = "Очистить"

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(
            id=id,
            placeholder="Поиск по ролям или категориям...",
            empty_message="[dim]Нет совпадений[/dim]",
            summary_prefix="Выбрано:",
            classes="search-select search-select--multi",
        )

    @property
    def values(self) -> list[str]:
        return self.get_selected_ids()

    def set_value(self, values: Sequence[str] | None) -> None:
        super().set_value(values)
