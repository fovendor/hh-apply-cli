from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ThemeDefinition:
    """Упрощённое представление темы для внешнего использования."""

    name: str
    colors: dict[str, str]


class HHCliThemeBase:
    """Базовая тема hhcli. Совместима с API dooit."""

    _name: str = "hhcli-base"

    # background colors
    background1: str = "#2E3440"  # Darkest
    background2: str = "#3B4252"  # Lighter
    background3: str = "#434C5E"  # Lightest

    # foreground colors
    foreground1: str = "#D8DEE9"  # Darkest
    foreground2: str = "#E5E9F0"  # Lighter
    foreground3: str = "#ECEFF4"  # Lightest

    # other colors
    red: str = "#BF616A"
    orange: str = "#D08770"
    yellow: str = "#EBCB8B"
    green: str = "#A3BE8C"
    blue: str = "#81A1C1"
    purple: str = "#B48EAD"
    magenta: str = "#B48EAD"
    cyan: str = "#8FBCBB"

    # accent colors
    primary: str = cyan
    secondary: str = blue

    @classmethod
    def to_css(cls) -> str:
        """Конвертирует тему в набор CSS-переменных."""
        return (
            f"$background1: {cls.background1};\n"
            f"$background2: {cls.background2};\n"
            f"$background3: {cls.background3};\n\n"
            f"$foreground1: {cls.foreground1};\n"
            f"$foreground2: {cls.foreground2};\n"
            f"$foreground3: {cls.foreground3};\n\n"
            f"$red: {cls.red};\n"
            f"$orange: {cls.orange};\n"
            f"$yellow: {cls.yellow};\n"
            f"$green: {cls.green};\n"
            f"$blue: {cls.blue};\n"
            f"$purple: {cls.purple};\n"
            f"$magenta: {cls.magenta};\n"
            f"$cyan: {cls.cyan};\n\n"
            f"$primary: {cls.primary};\n"
            f"$secondary: {cls.secondary};\n"
        )

    @classmethod
    def definition(cls) -> ThemeDefinition:
        """Возвращает сериализованное представление темы."""
        return ThemeDefinition(
            name=cls._name,
            colors={
                "background1": cls.background1,
                "background2": cls.background2,
                "background3": cls.background3,
                "foreground1": cls.foreground1,
                "foreground2": cls.foreground2,
                "foreground3": cls.foreground3,
                "red": cls.red,
                "orange": cls.orange,
                "yellow": cls.yellow,
                "green": cls.green,
                "blue": cls.blue,
                "purple": cls.purple,
                "magenta": cls.magenta,
                "cyan": cls.cyan,
                "primary": cls.primary,
                "secondary": cls.secondary,
            },
        )


class Nord(HHCliThemeBase):
    """Стандартная тема в стиле dooit."""

    _name = "hhcli-nord"


class SolarizedDark(HHCliThemeBase):
    """Альтернативная тема по мотивам solarized dark."""

    _name = "hhcli-solarized-dark"

    background1 = "#002b36"
    background2 = "#073642"
    background3 = "#0a3946"

    foreground1 = "#839496"
    foreground2 = "#93a1a1"
    foreground3 = "#eee8d5"

    red = "#dc322f"
    orange = "#cb4b16"
    yellow = "#b58900"
    green = "#859900"
    blue = "#268bd2"
    purple = "#6c71c4"
    magenta = "#d33682"
    cyan = "#2aa198"

    primary = cyan
    secondary = blue


AVAILABLE_THEMES: dict[str, type[HHCliThemeBase]] = {
    theme._name: theme
    for theme in (
        HHCliThemeBase,
        Nord,
        SolarizedDark,
    )
}


def list_themes() -> list[ThemeDefinition]:
    """Возвращает список доступных тем."""
    return [theme.definition() for theme in AVAILABLE_THEMES.values()]
