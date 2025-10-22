import os
import json
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from platformdirs import user_data_dir
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Integer,
    String,
    DateTime,
    Text,
    Boolean,
    ForeignKey,
    insert,
    select,
    delete,
    update,
    text as sa_text,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .constants import AppStateKeys, ConfigKeys, LogSource

APP_NAME = "hhcli"
APP_AUTHOR = "fovendor"
DATA_DIR = user_data_dir(APP_NAME, APP_AUTHOR)
DB_FILENAME = "hhcli_v2.sqlite"
DB_PATH = os.path.join(DATA_DIR, DB_FILENAME)

engine = None
metadata = MetaData()


def get_default_config() -> dict[str, Any]:
    """Возвращает стандартную конфигурацию поиска для нового профиля."""
    
    default_cover_letter = """Здравствуйте!

Описание вашей вакансии показалось мне интересным, хотелось бы подробнее узнать о требованиях к кандидату и о предстоящих задачах.

Коротко о себе:
...

Буду рад обсудить, как мой опыт может быть для вас полезен.

С уважением,
Имя Фамилия
+7 (000) 000-00-00 | Tg: @nickname | e-mail@gmail.com"""
    
    return {
        ConfigKeys.TEXT_INCLUDE: ["Python developer", "Backend developer"],
        ConfigKeys.NEGATIVE: [
            "старший", "senior", "ведущий", "Middle", "ETL", "BI", "ML",
            "Data Scientist", "CV", "NLP", "Unity", "Unreal", "C#", "C++"
        ],
        ConfigKeys.WORK_FORMAT: "REMOTE",
        ConfigKeys.AREA_ID: "113",
        ConfigKeys.SEARCH_FIELD: "name",
        ConfigKeys.PERIOD: "3",
        ConfigKeys.ROLE_IDS_CONFIG: [
            "96", "104", "107", "112", "113", "114", "116", "121", "124",
            "125", "126"
        ],
        ConfigKeys.COVER_LETTER: default_cover_letter,
        ConfigKeys.SKIP_APPLIED_IN_SAME_COMPANY: False,
        ConfigKeys.DEDUPLICATE_BY_NAME_AND_COMPANY: True,
        ConfigKeys.STRIKETHROUGH_APPLIED_VAC: True,
        ConfigKeys.STRIKETHROUGH_APPLIED_VAC_NAME: True,
        ConfigKeys.THEME: "hhcli-base",
    }


profiles = Table(
    "profiles", metadata,
    Column("profile_name", String, primary_key=True),
    Column("hh_user_id", String, unique=True, nullable=False),
    Column("email", String),
    Column("access_token", String, nullable=False),
    Column("refresh_token", String, nullable=False),
    Column("expires_at", DateTime, nullable=False),
)

profile_configs = Table(
    "profile_configs", metadata,
    Column("profile_name", String,
           ForeignKey('profiles.profile_name', ondelete='CASCADE'),
           primary_key=True),
    Column("work_format", String),
    Column("area_id", String),
    Column("search_field", String),
    Column("period", String),
    Column("cover_letter", Text),
    Column("theme", String, nullable=False, server_default="hhcli-base"),
    Column("skip_applied_in_same_company", Boolean, nullable=False,
           default=False),
    Column("deduplicate_by_name_and_company", Boolean, nullable=False,
           default=True),
    Column("strikethrough_applied_vac", Boolean, nullable=False, default=True),
    Column("strikethrough_applied_vac_name", Boolean, nullable=False,
           default=True),
)

config_negative_keywords = Table(
    "config_negative_keywords", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("profile_name", String,
           ForeignKey('profiles.profile_name', ondelete='CASCADE'),
           nullable=False, index=True),
    Column("keyword", String, nullable=False)
)

config_positive_keywords = Table(
    "config_positive_keywords", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("profile_name", String,
           ForeignKey('profiles.profile_name', ondelete='CASCADE'),
           nullable=False, index=True),
    Column("keyword", String, nullable=False)
)

config_professional_roles = Table(
    "config_professional_roles", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("profile_name", String,
           ForeignKey('profiles.profile_name', ondelete='CASCADE'),
           nullable=False, index=True),
    Column("role_id", String, nullable=False)
)

app_state = Table(
    "app_state", metadata,
    Column("key", String, primary_key=True),
    Column("value", String)
)

app_logs = Table(
    "app_logs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", DateTime, server_default=func.now()),
    Column("level", String(10), nullable=False),
    Column("source", String(50)),
    Column("message", Text)
)

negotiation_history = Table(
    "negotiation_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vacancy_id", String, nullable=False),
    Column("profile_name", String, nullable=False),
    Column("resume_id", String, nullable=False, default=""),
    Column("resume_title", String),
    Column("vacancy_title", String),
    Column("employer_name", String),
    Column("status", String),
    Column("reason", String),
    Column("applied_at", DateTime, nullable=False),
    UniqueConstraint("vacancy_id", "resume_id", name="uq_negotiation_vacancy_resume"),
)

vacancy_cache = Table(
    "vacancy_cache", metadata,
    Column("vacancy_id", String, primary_key=True),
    Column("json_data", Text, nullable=False),
    Column("cached_at", DateTime, nullable=False)
)

dictionaries_cache = Table(
    "dictionaries_cache", metadata,
    Column("name", String, primary_key=True),
    Column("json_data", Text, nullable=False),
    Column("cached_at", DateTime, nullable=False)
)

areas = Table(
    "areas", metadata,
    Column("id", String, primary_key=True),
    Column("parent_id", String, nullable=True, index=True),
    Column("name", String, nullable=False),
    Column("full_name", String, nullable=False),
    Column("search_name", String, nullable=False, index=True),
    Column("level", Integer, nullable=False, default=0),
    Column("sort_order", Integer, nullable=False, default=0),
)

professional_roles_catalog = Table(
    "professional_roles", metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("full_name", String, nullable=False),
    Column("search_name", String, nullable=False, index=True),
    Column("category_id", String, nullable=False, index=True),
    Column("category_name", String, nullable=False),
    Column("category_order", Integer, nullable=False, default=0),
    Column("role_order", Integer, nullable=False, default=0),
    Column("sort_order", Integer, nullable=False, default=0),
)

def save_vacancy_to_cache(vacancy_id: str, vacancy_data: dict):
    """Сохраняет JSON-данные вакансии в кэш в виде текста."""
    if not engine:
        return

    json_string = json.dumps(vacancy_data, ensure_ascii=False)

    values = {
        "vacancy_id": vacancy_id,
        "json_data": json_string,
        "cached_at": datetime.now()
    }
    stmt = sqlite_insert(vacancy_cache).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=['vacancy_id'],
        set_={
            "json_data": stmt.excluded.json_data,
            "cached_at": stmt.excluded.cached_at
        }
    )
    with engine.connect() as connection:
        connection.execute(stmt)
        connection.commit()

def save_dictionary_to_cache(name: str, data: dict):
    """Сохраняет справочник в кэш."""
    if not engine:
        return

    json_string = json.dumps(data, ensure_ascii=False)
    values = {
        "name": name,
        "json_data": json_string,
        "cached_at": datetime.now()
    }
    stmt = sqlite_insert(dictionaries_cache).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=['name'],
        set_={
            "json_data": stmt.excluded.json_data,
            "cached_at": stmt.excluded.cached_at
        }
    )
    with engine.connect() as connection:
        connection.execute(stmt)
        connection.commit()

def get_dictionary_from_cache(name: str, max_age_days: int = 7) -> dict | None:
    """Извлекает справочник из кэша, если он не устарел."""
    if not engine:
        return None

    age_limit = datetime.now() - timedelta(days=max_age_days)
    with engine.connect() as connection:
        stmt = select(dictionaries_cache.c.json_data).where(
            dictionaries_cache.c.name == name,
            dictionaries_cache.c.cached_at >= age_limit
        )
        result = connection.execute(stmt).scalar_one_or_none()
        if result:
            return json.loads(result)
        return None

def get_vacancy_from_cache(vacancy_id: str) -> dict | None:
    """
    Извлекает данные вакансии из кэша, если они не старше 7 дней.
    Возвращает dict или None.
    """
    if not engine:
        return None

    seven_days_ago = datetime.now() - timedelta(days=7)
    with engine.connect() as connection:
        stmt = select(vacancy_cache.c.json_data).where(
            vacancy_cache.c.vacancy_id == vacancy_id,
            vacancy_cache.c.cached_at >= seven_days_ago
        )
        result = connection.execute(stmt).scalar_one_or_none()

        if result:
            return json.loads(result)
        return None

def _upsert_app_state(connection, key: str, value: str) -> None:
    stmt = sqlite_insert(app_state).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(index_elements=["key"], set_=dict(value=value))
    connection.execute(stmt)


def get_app_state_value(key: str) -> str | None:
    """Возвращает значение из таблицы состояния приложения."""
    if not engine:
        return None
    with engine.connect() as connection:
        stmt = select(app_state.c.value).where(app_state.c.key == key)
        return connection.execute(stmt).scalar_one_or_none()

def set_app_state_value(key: str, value: str) -> None:
    """Сохраняет ключ-значение в таблицу состояния приложения."""
    if not engine:
        return
    with engine.begin() as connection:
        _upsert_app_state(connection, key, value)

def replace_areas(records: Sequence[dict[str, Any]], *, data_hash: str) -> None:
    """Полностью заменяет таблицу регионов на переданные данные."""
    if not engine:
        return
    prepared = [
        {
            "id": str(item["id"]),
            "parent_id": str(item["parent_id"]) if item.get("parent_id") else None,
            "name": item["name"],
            "full_name": item["full_name"],
            "search_name": item["search_name"],
            "level": int(item.get("level", 0)),
            "sort_order": int(item.get("sort_order", index)),
        }
        for index, item in enumerate(records)
    ]
    with engine.begin() as connection:
        connection.execute(areas.delete())
        if prepared:
            connection.execute(areas.insert(), prepared)
        timestamp = datetime.now().isoformat()
        _upsert_app_state(connection, AppStateKeys.AREAS_HASH, data_hash)
        _upsert_app_state(connection, AppStateKeys.AREAS_UPDATED_AT, timestamp)

def replace_professional_roles(records: Sequence[dict[str, Any]], *, data_hash: str) -> None:
    """Полностью заменяет таблицу профессиональных ролей на переданные данные."""
    if not engine:
        return
    prepared: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    duplicate_count = 0
    for item in records:
        rid = str(item["id"])
        if rid in seen_ids:
            duplicate_count += 1
            continue
        seen_ids.add(rid)
        prepared.append(
            {
                "id": rid,
                "name": item["name"],
                "full_name": item["full_name"],
                "search_name": item["search_name"],
                "category_id": str(item["category_id"]),
                "category_name": item["category_name"],
                "category_order": int(item.get("category_order", 0)),
                "role_order": int(item.get("role_order", 0)),
                "sort_order": len(prepared),
            }
        )
    with engine.begin() as connection:
        connection.execute(professional_roles_catalog.delete())
        if prepared:
            connection.execute(professional_roles_catalog.insert(), prepared)
        timestamp = datetime.now().isoformat()
        _upsert_app_state(connection, AppStateKeys.PROFESSIONAL_ROLES_HASH, data_hash)
        _upsert_app_state(connection, AppStateKeys.PROFESSIONAL_ROLES_UPDATED_AT, timestamp)
    if duplicate_count:
        log_to_db(
            "WARN",
            LogSource.REFERENCE_DATA,
            f"Получено дубликатов профессиональных ролей: {duplicate_count}",
        )

def list_areas() -> list[dict[str, Any]]:
    """Возвращает список всех регионов в порядке сортировки."""
    if not engine:
        return []
    with engine.connect() as connection:
        stmt = (
            select(
                areas.c.id,
                areas.c.parent_id,
                areas.c.name,
                areas.c.full_name,
                areas.c.search_name,
                areas.c.level,
                areas.c.sort_order,
            )
            .order_by(areas.c.sort_order)
        )
        rows = connection.execute(stmt).fetchall()
        return [dict(row._mapping) for row in rows]

def list_professional_roles() -> list[dict[str, Any]]:
    """Возвращает все профессиональные роли в порядке категорий и ролей."""
    if not engine:
        return []
    with engine.connect() as connection:
        stmt = (
            select(
                professional_roles_catalog.c.id,
                professional_roles_catalog.c.name,
                professional_roles_catalog.c.full_name,
                professional_roles_catalog.c.search_name,
                professional_roles_catalog.c.category_id,
                professional_roles_catalog.c.category_name,
                professional_roles_catalog.c.category_order,
                professional_roles_catalog.c.role_order,
                professional_roles_catalog.c.sort_order,
            )
            .order_by(
                professional_roles_catalog.c.category_order,
                professional_roles_catalog.c.role_order,
            )
        )
        rows = connection.execute(stmt).fetchall()
        return [dict(row._mapping) for row in rows]

def get_area_full_name(area_id: str) -> str | None:
    """Возвращает полное название региона по ID."""
    if not engine:
        return None
    with engine.connect() as connection:
        stmt = select(areas.c.full_name).where(areas.c.id == str(area_id))
        return connection.execute(stmt).scalar_one_or_none()

def get_professional_roles_by_ids(role_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Возвращает данные по списку ID профессиональных ролей, сохраняя порядок входных данных."""
    if not engine or not role_ids:
        return []
    normalized_ids = [str(rid) for rid in role_ids]
    with engine.connect() as connection:
        stmt = select(
            professional_roles_catalog.c.id,
            professional_roles_catalog.c.name,
            professional_roles_catalog.c.full_name,
            professional_roles_catalog.c.category_name,
        ).where(professional_roles_catalog.c.id.in_(normalized_ids))
        rows = connection.execute(stmt).fetchall()
        mapped = {row._mapping["id"]: dict(row._mapping) for row in rows}
    return [mapped[rid] for rid in normalized_ids if rid in mapped]

def init_db():
    global engine
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"INFO: База данных используется по пути: {DB_PATH}")
    engine = create_engine(f"sqlite:///{DB_PATH}")
    metadata.create_all(engine)
    ensure_schema_upgrades()

def ensure_schema_upgrades() -> None:
    """Гарантирует наличие новых колонок в существующей БД."""
    if not engine:
        return

    default_theme = get_default_config()[ConfigKeys.THEME]

    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.execute(
                sa_text("PRAGMA table_info(profile_configs)")
            )
        }
        if "theme" not in columns:
            connection.execute(sa_text("ALTER TABLE profile_configs ADD COLUMN theme TEXT"))
            connection.execute(
                sa_text("UPDATE profile_configs SET theme = :theme WHERE theme IS NULL"),
                {"theme": default_theme},
            )

        history_info = list(
            connection.execute(sa_text("PRAGMA table_info(negotiation_history)"))
        )
        history_columns = {row[1] for row in history_info}

        needs_rebuild = bool(history_info) and (
            "id" not in history_columns or "resume_id" not in history_columns
        )

        if needs_rebuild:
            connection.execute(
                sa_text(
                    """
CREATE TABLE negotiation_history_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    resume_id TEXT NOT NULL DEFAULT '',
    resume_title TEXT,
    vacancy_title TEXT,
    employer_name TEXT,
    status TEXT,
    reason TEXT,
    applied_at DATETIME NOT NULL,
    UNIQUE(vacancy_id, resume_id)
)
                    """
                )
            )

            if "resume_id" in history_columns:
                connection.execute(
                    sa_text(
                        """
INSERT INTO negotiation_history_new (
    vacancy_id, profile_name, resume_id, resume_title,
    vacancy_title, employer_name, status, reason, applied_at
)
SELECT
    vacancy_id,
    profile_name,
    COALESCE(resume_id, ''),
    COALESCE(resume_title, ''),
    vacancy_title,
    employer_name,
    status,
    reason,
    applied_at
FROM negotiation_history
                        """
                    )
                )
            else:
                connection.execute(
                    sa_text(
                        """
INSERT INTO negotiation_history_new (
    vacancy_id, profile_name, resume_id, resume_title,
    vacancy_title, employer_name, status, reason, applied_at
)
SELECT
    vacancy_id,
    profile_name,
    '',
    '',
    vacancy_title,
    employer_name,
    status,
    reason,
    applied_at
FROM negotiation_history
                        """
                    )
                )

            connection.execute(sa_text("DROP TABLE negotiation_history"))
            connection.execute(
                sa_text("ALTER TABLE negotiation_history_new RENAME TO negotiation_history")
            )

        connection.execute(
            sa_text(
                "CREATE INDEX IF NOT EXISTS idx_negotiation_profile_resume "
                "ON negotiation_history(profile_name, resume_id, applied_at DESC)"
            )
        )

def log_to_db(level: str, source: str, message: str):
    if not engine:
        return
    with engine.connect() as connection:
        stmt = insert(app_logs).values(
            level=level, source=source, message=message)
        connection.execute(stmt)
        connection.commit()

def record_apply_action(
        vacancy_id: str,
        profile_name: str,
        resume_id: str | None,
        resume_title: str | None,
        vacancy_title: str,
        employer_name: str,
        status: str,
        reason: str | None):
    values = {
        "vacancy_id": vacancy_id,
        "profile_name": profile_name,
        "resume_id": str(resume_id or "").strip(),
        "resume_title": (resume_title or "").strip(),
        "vacancy_title": vacancy_title,
        "employer_name": employer_name,
        "status": status,
        "reason": reason,
        "applied_at": datetime.now(),
    }
    stmt = sqlite_insert(negotiation_history).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=['vacancy_id', 'resume_id'],
        set_={
            "profile_name": values["profile_name"],
            "resume_title": values["resume_title"],
            "vacancy_title": values["vacancy_title"],
            "employer_name": values["employer_name"],
            "status": values["status"],
            "reason": values["reason"],
            "applied_at": values["applied_at"],
        }
    )
    with engine.connect() as connection:
        connection.execute(stmt)
        connection.commit()

def get_full_negotiation_history_for_profile(profile_name: str) -> list[dict]:
    with engine.connect() as connection:
        stmt = select(negotiation_history).where(
            negotiation_history.c.profile_name == profile_name
        ).order_by(negotiation_history.c.applied_at.desc())
        result = connection.execute(stmt).fetchall()
        return [dict(row._mapping) for row in result]


def get_negotiation_history_for_resume(
        profile_name: str, resume_id: str
) -> list[dict]:
    if not engine:
        return []
    resume_key = str(resume_id or "").strip()
    with engine.connect() as connection:
        stmt = (
            select(negotiation_history)
            .where(negotiation_history.c.profile_name == profile_name)
            .where(negotiation_history.c.resume_id == resume_key)
            .order_by(negotiation_history.c.applied_at.desc())
        )
        result = connection.execute(stmt).fetchall()
        return [dict(row._mapping) for row in result]

def get_last_sync_timestamp(profile_name: str) -> datetime | None:
    key = f"{AppStateKeys.LAST_NEGOTIATION_SYNC_PREFIX}{profile_name}"
    value = get_app_state_value(key)
    if value:
        return datetime.fromisoformat(value)
    return None

def set_last_sync_timestamp(profile_name: str, timestamp: datetime):
    key = f"{AppStateKeys.LAST_NEGOTIATION_SYNC_PREFIX}{profile_name}"
    set_app_state_value(key, timestamp.isoformat())

def upsert_negotiation_history(negotiations: list[dict], profile_name: str):
    if not negotiations:
        return
    with engine.connect() as connection:
        for item in negotiations:
            vacancy = item.get('vacancy', {})
            if not vacancy or not vacancy.get('id'):
                continue
            resume_info = item.get("resume") or {}
            values = {
                "vacancy_id": vacancy['id'],
                "profile_name": profile_name,
                "resume_id": str(resume_info.get("id") or "").strip(),
                "resume_title": (resume_info.get("title") or "").strip(),
                "vacancy_title": vacancy.get('name'),
                "employer_name": vacancy.get('employer', {}).get('name'),
                "status": item.get('state', {}).get('name', 'N/A'),
                "reason": None,
                "applied_at": datetime.fromisoformat(
                    item['updated_at'].replace("Z", "+00:00")),
            }
            stmt = sqlite_insert(negotiation_history).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=['vacancy_id', 'resume_id'],
                set_={
                    "profile_name": values["profile_name"],
                    "resume_title": values["resume_title"],
                    "vacancy_title": values["vacancy_title"],
                    "employer_name": values["employer_name"],
                    "status": values["status"],
                    "applied_at": values["applied_at"],
                }
            )
            connection.execute(stmt)
        connection.commit()

def save_or_update_profile(
        profile_name: str, user_info: dict,
        token_data: dict, expires_at: datetime):
    """
    Создает новый профиль с конфигурацией по умолчанию или обновляет
    токены для существующего.
    """
    with engine.connect() as connection, connection.begin():
        stmt = select(profiles).where(profiles.c.hh_user_id == user_info['id'])
        existing = connection.execute(stmt).first()

        profile_values = {
            "hh_user_id": user_info['id'], "email": user_info.get('email', ''),
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "expires_at": expires_at
        }

        if existing:
            connection.execute(update(profiles).where(
                profiles.c.hh_user_id == user_info['id']
            ).values(profile_name=profile_name, **profile_values))
        else:
            connection.execute(insert(profiles).values(
                profile_name=profile_name, **profile_values))

            defaults = get_default_config()

            config_main = {k: v for k, v in defaults.items() if k not in
                           [ConfigKeys.TEXT_INCLUDE, ConfigKeys.NEGATIVE, ConfigKeys.ROLE_IDS_CONFIG]}
            config_main["profile_name"] = profile_name
            connection.execute(insert(profile_configs).values(config_main))

            pos_keywords = [{"profile_name": profile_name, "keyword": kw}
                            for kw in defaults[ConfigKeys.TEXT_INCLUDE]]
            if pos_keywords:
                connection.execute(insert(config_positive_keywords), pos_keywords)

            neg_keywords = [{"profile_name": profile_name, "keyword": kw}
                            for kw in defaults[ConfigKeys.NEGATIVE]]
            if neg_keywords:
                connection.execute(insert(config_negative_keywords), neg_keywords)

            roles = [{"profile_name": profile_name, "role_id": r_id}
                     for r_id in defaults[ConfigKeys.ROLE_IDS_CONFIG]]
            if roles:
                connection.execute(insert(config_professional_roles), roles)

def load_profile(profile_name: str) -> dict | None:
    with engine.connect() as connection:
        stmt = select(profiles).where(profiles.c.profile_name == profile_name)
        result = connection.execute(stmt).first()
        if result:
            return dict(result._mapping)
        return None

def delete_profile(profile_name: str):
    with engine.connect() as connection:
        stmt = delete(profiles).where(profiles.c.profile_name == profile_name)
        connection.execute(stmt)
        connection.commit()

def get_all_profiles() -> list[dict]:
    with engine.connect() as connection:
        stmt = select(profiles)
        result = connection.execute(stmt).fetchall()
        return [dict(row._mapping) for row in result]

def set_active_profile(profile_name: str):
    with engine.connect() as connection:
        stmt = sqlite_insert(app_state).values(
            key=AppStateKeys.ACTIVE_PROFILE, value=profile_name)
        stmt = stmt.on_conflict_do_update(
            index_elements=['key'], set_=dict(value=profile_name))
        connection.execute(stmt)
        connection.commit()

def get_active_profile_name() -> str | None:
    with engine.connect() as connection:
        stmt = select(app_state.c.value).where(
            app_state.c.key == AppStateKeys.ACTIVE_PROFILE)
        return connection.execute(stmt).scalar_one_or_none()

def load_profile_config(profile_name: str) -> dict:
    """Загружает полную конфигурацию из всех связанных таблиц."""
    with engine.connect() as connection:
        stmt_main = select(profile_configs).where(
            profile_configs.c.profile_name == profile_name)
        result = connection.execute(stmt_main).first()
        if not result:
            return get_default_config()

        config = dict(result._mapping)
        defaults = get_default_config()
        config.setdefault(ConfigKeys.THEME, defaults[ConfigKeys.THEME])

        stmt_pos_keywords = select(config_positive_keywords.c.keyword).where(
            config_positive_keywords.c.profile_name == profile_name)
        config[ConfigKeys.TEXT_INCLUDE] = connection.execute(stmt_pos_keywords).scalars().all()

        stmt_keywords = select(config_negative_keywords.c.keyword).where(
            config_negative_keywords.c.profile_name == profile_name)
        config[ConfigKeys.NEGATIVE] = connection.execute(stmt_keywords).scalars().all()

        stmt_roles = select(config_professional_roles.c.role_id).where(
            config_professional_roles.c.profile_name == profile_name)
        config[ConfigKeys.ROLE_IDS_CONFIG] = connection.execute(stmt_roles).scalars().all()

        return config

def save_profile_config(profile_name: str, config: dict):
    """Сохраняет полную конфигурацию в связанные таблицы."""
    with engine.connect() as connection, connection.begin():
        positive_keywords = config.pop(ConfigKeys.TEXT_INCLUDE, [])
        negative_keywords = config.pop(ConfigKeys.NEGATIVE, [])
        role_ids = config.pop(ConfigKeys.ROLE_IDS_CONFIG, [])
        
        if config:
            connection.execute(update(profile_configs).where(
                profile_configs.c.profile_name == profile_name
            ).values(**config))

        connection.execute(delete(config_positive_keywords).where(
            config_positive_keywords.c.profile_name == profile_name))
        if positive_keywords:
            connection.execute(insert(config_positive_keywords),
                               [{"profile_name": profile_name, "keyword": kw}
                                for kw in positive_keywords])

        connection.execute(delete(config_negative_keywords).where(
            config_negative_keywords.c.profile_name == profile_name))
        if negative_keywords:
            connection.execute(insert(config_negative_keywords),
                               [{"profile_name": profile_name, "keyword": kw}
                                for kw in negative_keywords])

        connection.execute(delete(config_professional_roles).where(
            config_professional_roles.c.profile_name == profile_name))
        if role_ids:
            connection.execute(insert(config_professional_roles),
                               [{"profile_name": profile_name, "role_id": r_id}
                                for r_id in role_ids])
