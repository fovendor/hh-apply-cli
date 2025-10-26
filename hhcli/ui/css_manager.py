from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional, Type, Union
from uuid import uuid4

from platformdirs import user_cache_dir

from .theme import (
    AVAILABLE_THEMES,
    HHCliThemeBase,
)

_cache_root = Path(user_cache_dir("hhcli"))


if getattr(sys, "frozen", False):
    BASE_PATH = Path(sys._MEIPASS) / "hhcli" / "ui"  # type: ignore[attr-defined]
else:
    BASE_PATH = Path(__file__).parent


def _generate_random_id() -> str:
    return uuid4().hex


class CssManager:
    """Отвечает за генерацию и кэширование итогового CSS."""

    base_css: Path = BASE_PATH / "styles.tcss"
    themes: dict[str, HHCliThemeBase] = {
        name: theme_cls()
        for name, theme_cls in AVAILABLE_THEMES.items()
    }

    def __init__(
        self,
        theme: Optional[HHCliThemeBase] = None,
        cache_path: Path = _cache_root,
    ) -> None:
        self.theme: HHCliThemeBase = theme or self.themes["hhcli-base"]
        self.cache_path = cache_path
        self.stylesheets: Path = self.cache_path / "stylesheets"
        self.css_file: Path = self.cache_path / "hhcli.tcss"

        self.cache_path.mkdir(parents=True, exist_ok=True)
        self.stylesheets.mkdir(parents=True, exist_ok=True)

        if not self.css_file.exists():
            self.write("")

        self.refresh_css()

    def read_css(self) -> str:
        return self.css_file.read_text()

    def refresh_css(self) -> None:
        css = self.theme.to_css()
        with open(self.base_css, "r", encoding="utf8") as base:
            css = css + "\n" + base.read()

        for sheet in sorted(self.stylesheets.glob("*.tcss")):
            with open(sheet, "r", encoding="utf8") as extra:
                css = css + "\n" + extra.read()

        self.write(css)

    def add_theme(self, theme: Type[HHCliThemeBase]) -> None:
        instance = theme()
        self.themes[instance._name] = instance
        self.refresh_css()

    def set_theme(self, theme: Union[str, Type[HHCliThemeBase]]) -> None:
        if isinstance(theme, str):
            selected = self.themes.get(theme)
            if selected is None:
                raise ValueError(f"Тема '{theme}' не зарегистрирована.")
            self.theme = selected
        else:
            self.theme = theme()
            self.themes[self.theme._name] = self.theme

        self.refresh_css()

    def inject_css(self, css: str, *, _id: Optional[str] = None) -> str:
        uuid = _id or _generate_random_id()
        css_file = self.stylesheets / f"{uuid}.tcss"
        with open(css_file, "w", encoding="utf8") as handle:
            handle.write(css)
        self.refresh_css()
        return uuid

    def unject_css(self, _id: str) -> bool:
        css_file = self.stylesheets / f"{_id}.tcss"
        if not css_file.exists():
            return False
        css_file.unlink()
        self.refresh_css()
        return True

    def is_active(self, _id: str) -> bool:
        return (self.stylesheets / f"{_id}.tcss").exists()

    def write(self, css: str) -> None:
        with open(self.css_file, "w", encoding="utf8") as handle:
            handle.write(css)

    def cleanup(self) -> None:
        for sheet in self.stylesheets.glob("*.tcss"):
            sheet.unlink(missing_ok=True)
        self.refresh_css()
