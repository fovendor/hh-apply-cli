from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from .themes import THEMES_DIR

_VARIABLE_RE = re.compile(r"^\s*\$(?P<name>[A-Za-z0-9_-]+)\s*:\s*(?P<value>[^;]+);$")
_SAFE_NAME_RE = re.compile(r"[^a-z0-9]+")

_CSS_CACHE: dict[type["HHCliThemeBase"], str] = {}
_COLORS_CACHE: dict[type["HHCliThemeBase"], dict[str, str]] = {}
_THEMES_CACHE: dict[str, type["HHCliThemeBase"]] | None = None

DEFAULT_BASE_THEME_CSS = """$background1: #2E3440;
$background2: #3B4252;
$background3: #434C5E;

$foreground1: #D8DEE9;
$foreground2: #E5E9F0;
$foreground3: #ECEFF4;

$red: #BF616A;
$orange: #D08770;
$yellow: #EBCB8B;
$green: #A3BE8C;
$blue: #81A1C1;
$purple: #B48EAD;
$magenta: #B48EAD;
$cyan: #8FBCBB;

$primary: #8FBCBB;
$secondary: #81A1C1;

$scrim: rgba(0, 0, 0, 0.65);
"""


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


def _iter_theme_files() -> list[Path]:
    return sorted(
        path
        for path in THEMES_DIR.glob("*.tcss")
        if path.is_file()
    )


def _slugify(stem: str) -> str:
    slug = _SAFE_NAME_RE.sub("-", stem.lower()).strip("-")
    return slug or "theme"


def _class_name_from_slug(slug: str) -> str:
    parts = slug.replace("-", "_").split("_")
    return "HHCliTheme" + "".join(part.capitalize() for part in parts if part)


def _build_theme_classes() -> dict[str, type["HHCliThemeBase"]]:
    mapping: dict[str, type[HHCliThemeBase]] = {}
    for path in _iter_theme_files():
        slug = _slugify(path.stem)
        candidate_slug = slug
        suffix = 2
        theme_name = f"hhcli-{candidate_slug}"
        while theme_name in mapping:
            candidate_slug = f"{slug}-{suffix}"
            suffix += 1
            theme_name = f"hhcli-{candidate_slug}"

        class_name = _class_name_from_slug(candidate_slug)
        attrs = {
            "_name": theme_name,
            "css_filename": path.name,
            "theme_path": path,
        }
        theme_class = type(class_name, (HHCliThemeBase,), attrs)
        mapping[theme_name] = theme_class

    if "hhcli-base" not in mapping:
        class_name = _class_name_from_slug("base")
        attrs = {
            "_name": "hhcli-base",
            "css_filename": "base.tcss",
            "theme_path": THEMES_DIR / "base.tcss",
            "inline_css": DEFAULT_BASE_THEME_CSS,
        }
        mapping["hhcli-base"] = type(class_name, (HHCliThemeBase,), attrs)

    if not mapping:
        raise RuntimeError(  # pragma: no cover - защитная ветка
            f"Не удалось создать ни одной темы в каталоге '{THEMES_DIR}'."
        )

    # Всегда хотим видеть базовую тему первой.
    sorted_items = sorted(mapping.items(), key=lambda item: item[0])
    sorted_items.sort(key=lambda item: item[0] != "hhcli-base")
    return dict(sorted_items)


def get_available_themes() -> dict[str, type["HHCliThemeBase"]]:
    global _THEMES_CACHE
    if _THEMES_CACHE is None:
        _THEMES_CACHE = _build_theme_classes()
    available = dict(_THEMES_CACHE)
    globals()["AVAILABLE_THEMES"] = available
    return available


def refresh_available_themes() -> dict[str, type["HHCliThemeBase"]]:
    global _THEMES_CACHE
    _CSS_CACHE.clear()
    _COLORS_CACHE.clear()
    _THEMES_CACHE = _build_theme_classes()
    available = dict(_THEMES_CACHE)
    globals()["AVAILABLE_THEMES"] = available
    return available


@dataclass(slots=True)
class ThemeDefinition:
    """Упрощённое представление темы для внешнего использования."""

    name: str
    colors: dict[str, str]


class HHCliThemeBase:
    """Базовый класс определения темы оформления."""

    _name: ClassVar[str] = "hhcli-base"
    css_filename: ClassVar[str] = "base.tcss"
    theme_path: ClassVar[Path | None] = None
    inline_css: ClassVar[str | None] = None

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
        cached = _CSS_CACHE.get(cls)
        if cached is not None:
            return cached

        if cls.inline_css is not None:
            _CSS_CACHE[cls] = cls.inline_css
            return cls.inline_css

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


AVAILABLE_THEMES: dict[str, type[HHCliThemeBase]] = get_available_themes()


def list_themes() -> list[ThemeDefinition]:
    """Возвращает список доступных тем."""
    themes = get_available_themes()
    return [theme.definition() for theme in themes.values()]
