"""Ресурсы тем для hhcli."""

from __future__ import annotations

from pathlib import Path
import sys


if getattr(sys, "frozen", False):
    THEMES_DIR = Path(sys._MEIPASS) / "hhcli" / "ui" / "themes"  # type: ignore[attr-defined]
else:
    THEMES_DIR = Path(__file__).parent

__all__ = ["THEMES_DIR"]
