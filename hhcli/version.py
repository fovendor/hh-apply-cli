"""Вспомогательные функции для получения версии пакета hhcli."""

from __future__ import annotations

import pathlib
import re

from importlib.metadata import PackageNotFoundError, version as metadata_version

_VERSION_PATTERN = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def get_version() -> str:
    """Возвращает строку версии hhcli, предпочитая данные из pyproject.toml."""
    try:
        return _read_version_from_pyproject()
    except (FileNotFoundError, RuntimeError):
        try:
            return metadata_version("hhcli")
        except PackageNotFoundError as exc:
            raise RuntimeError("Не удалось определить версию hhcli.") from exc


def _read_version_from_pyproject() -> str:
    """Извлекает значение версии напрямую из pyproject.toml."""
    project_root = pathlib.Path(__file__).resolve().parents[1]
    pyproject_path = project_root / "pyproject.toml"
    content = pyproject_path.read_text(encoding="utf-8")
    match = _VERSION_PATTERN.search(content)
    if not match:
        raise RuntimeError("Не удалось определить версию hhcli из pyproject.toml")
    return match.group(1)
