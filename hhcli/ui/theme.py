from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import ClassVar

from .themes import THEMES_DIR

_VARIABLE_RE = re.compile(r"^\s*\$(?P<name>[A-Za-z0-9_-]+)\s*:\s*(?P<value>[^;]+);$")

_CSS_CACHE: dict[type["HHCliThemeBase"], str] = {}
_COLORS_CACHE: dict[type["HHCliThemeBase"], dict[str, str]] = {}


def _parse_variables(css: str) -> dict[str, str]:
    variables: dict[str, str] = {}
    for raw_line in css.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("/*") or line.startswith("//"):
            continue
        match = _VARIABLE_RE.match(line)
        if match:
            variables[match.group("name")] = match.group("value").strip()
    return variables


@dataclass(slots=True)
class ThemeDefinition:
    """Упрощённое представление темы для внешнего использования."""

    name: str
    colors: dict[str, str]


class HHCliThemeBase:
    """Базовый класс определения темы оформления."""

    _name: ClassVar[str] = "hhcli-base"
    css_filename: ClassVar[str] = "base.tcss"

    def __init__(self) -> None:
        self.css_path: Path = self._get_css_path()
        self.css: str = self._load_css()
        self.colors: dict[str, str] = self._load_colors()

    @classmethod
    def _get_css_path(cls) -> Path:
        path = Path(cls.css_filename)
        if not path.is_absolute():
            path = THEMES_DIR / path
        return path

    @classmethod
    def _load_css(cls) -> str:
        try:
            return _CSS_CACHE[cls]
        except KeyError:
            css = cls._get_css_path().read_text(encoding="utf8")
            _CSS_CACHE[cls] = css
            return css

    @classmethod
    def _load_colors(cls) -> dict[str, str]:
        try:
            return dict(_COLORS_CACHE[cls])
        except KeyError:
            css = cls._load_css()
            colors = _parse_variables(css)
            _COLORS_CACHE[cls] = colors
            return dict(colors)

    def to_css(self) -> str:
        """Возвращает CSS-переменные темы."""
        return self.css

    @classmethod
    def definition(cls) -> ThemeDefinition:
        """Возвращает сериализованное представление темы."""
        return ThemeDefinition(name=cls._name, colors=cls._load_colors())


class Nord(HHCliThemeBase):
    """Стандартная тема по мотивам Nord."""

    _name = "hhcli-nord"
    css_filename = "nord.tcss"


class SolarizedDark(HHCliThemeBase):
    """Альтернативная тема по мотивам Solarized Dark."""

    _name = "hhcli-solarized-dark"
    css_filename = "solarized_dark.tcss"


class Dracula(HHCliThemeBase):
    """Популярная тёмная тема Dracula."""

    _name = "hhcli-dracula"
    css_filename = "dracula.tcss"


class Monokai(HHCliThemeBase):
    """Классическая тема Monokai."""

    _name = "hhcli-monokai"
    css_filename = "monokai.tcss"


class GruvboxDark(HHCliThemeBase):
    """Тёмная тема Gruvbox."""

    _name = "hhcli-gruvbox-dark"
    css_filename = "gruvbox_dark.tcss"


class OneDark(HHCliThemeBase):
    """Тема Atom One Dark."""

    _name = "hhcli-one-dark"
    css_filename = "one_dark.tcss"


class GithubLight(HHCliThemeBase):
    """Светлая тема в стиле GitHub Light."""

    _name = "hhcli-github-light"
    css_filename = "github_light.tcss"


class TokyoNight(HHCliThemeBase):
    """Тёмная тема Tokyo Night."""

    _name = "hhcli-tokyo-night"
    css_filename = "tokyo_night.tcss"


_THEME_CLASSES: tuple[type[HHCliThemeBase], ...] = (
    HHCliThemeBase,
    Nord,
    SolarizedDark,
    Dracula,
    Monokai,
    GruvboxDark,
    OneDark,
    GithubLight,
    TokyoNight,
)


AVAILABLE_THEMES: dict[str, type[HHCliThemeBase]] = {
    theme._name: theme for theme in _THEME_CLASSES
}


def list_themes() -> list[ThemeDefinition]:
    """Возвращает список доступных тем."""
    return [theme.definition() for theme in AVAILABLE_THEMES.values()]
