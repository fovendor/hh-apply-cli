from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Iterable

from .database import (
    get_app_state_value,
    log_to_db,
    replace_areas,
    replace_professional_roles,
)
from .constants import AppStateKeys, LogSource

if TYPE_CHECKING:
    from .client import HHApiClient


def _normalize(text: str | None) -> str:
    return " ".join(str(text or "").lower().split())


def _clean(text: str | None) -> str:
    return str(text or "").strip()


def _hash_payload(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _flatten_areas(
    nodes: list[dict[str, Any]],
    *,
    parent_id: str | None = None,
    path: Iterable[str] | None = None,
    level: int = 0,
    counter: list[int] | None = None,
) -> list[dict[str, Any]]:
    path = list(path or [])
    counter = counter or [0]
    flattened: list[dict[str, Any]] = []

    for node in nodes:
        counter[0] += 1
        area_id = str(node.get("id"))
        name = _clean(node.get("name"))
        current_path = path + [name]
        full_name = " / ".join(current_path)
        flattened.append(
            {
                "id": area_id,
                "parent_id": parent_id,
                "name": name,
                "full_name": full_name,
                "search_name": _normalize(f"{full_name} {area_id}"),
                "level": level,
                "sort_order": counter[0],
            }
        )
        children = node.get("areas") or []
        if children:
            flattened.extend(
                _flatten_areas(
                    children,
                    parent_id=area_id,
                    path=current_path,
                    level=level + 1,
                    counter=counter,
                )
            )
    return flattened


def _flatten_professional_roles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        categories = (
            payload.get("categories")
            or payload.get("items")
            or payload.get("data")
            or []
        )
    elif isinstance(payload, list):
        categories = payload
    else:
        categories = []

    if not categories:
        keys = []
        if isinstance(payload, dict):
            keys = list(payload.keys())
        log_to_db(
            "ERROR",
            LogSource.REFERENCE_DATA,
            f"Справочник профессиональных ролей пуст или формат неизвестен: тип {type(payload).__name__}, ключи={keys}",
        )
        return []

    flattened: list[dict[str, Any]] = []
    sort_counter = 0

    for category_order, category in enumerate(categories):
        category_id = str(category.get("id"))
        category_name = _clean(category.get("name"))
        roles = category.get("roles") or []
        for role_order, role in enumerate(roles):
            sort_counter += 1
            role_id = str(role.get("id"))
            role_name = _clean(role.get("name"))
            full_name = f"{category_name} — {role_name}" if category_name else role_name
            flattened.append(
                {
                    "id": role_id,
                    "name": role_name,
                    "full_name": full_name,
                    "search_name": _normalize(f"{category_name} {role_name} {role_id}"),
                    "category_id": category_id,
                    "category_name": category_name,
                    "category_order": category_order,
                    "role_order": role_order,
                    "sort_order": sort_counter,
                }
            )
    return flattened


def sync_areas(client: "HHApiClient") -> bool:
    """Синхронизирует справочник регионов с API hh.ru. Возвращает True, если произошли изменения."""
    data = client.get_areas()
    payload_hash = _hash_payload(data)
    if payload_hash == get_app_state_value(AppStateKeys.AREAS_HASH):
        return False

    flattened = _flatten_areas(data)
    replace_areas(flattened, data_hash=payload_hash)
    log_to_db("INFO", LogSource.REFERENCE_DATA, f"Загружено {len(flattened)} записей регионов.")
    return True


def sync_professional_roles(client: "HHApiClient") -> bool:
    """Синхронизирует справочник профессиональных ролей. Возвращает True, если были изменения."""
    data = client.get_professional_roles()
    payload_hash = _hash_payload(data)
    if payload_hash == get_app_state_value(AppStateKeys.PROFESSIONAL_ROLES_HASH):
        return False

    flattened = _flatten_professional_roles(data)
    if not flattened:
        return False
    replace_professional_roles(flattened, data_hash=payload_hash)
    log_to_db("INFO", LogSource.REFERENCE_DATA, f"Загружено {len(flattened)} профессиональных ролей.")
    return True


def ensure_reference_data(client: "HHApiClient") -> dict[str, bool]:
    """
    Гарантирует наличие актуальных данных по регионам и профессиональным ролям.
    Возвращает словарь с флагами обновления.
    """
    updated_areas = sync_areas(client)
    updated_roles = sync_professional_roles(client)
    return {"areas": updated_areas, "professional_roles": updated_roles}