import os
import json
from datetime import datetime, timedelta
from typing import Any

from platformdirs import user_data_dir
from sqlalchemy import (create_engine, MetaData, Table, Column,
                        Integer, String, DateTime, Text, Boolean,
                        ForeignKey, insert, select, delete, update)
from sqlalchemy.sql import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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
        "text_include": ["Python developer", "Backend developer"],
        "negative": [
            "старший", "senior", "ведущий", "Middle", "ETL", "BI", "ML",
            "Data Scientist", "CV", "NLP", "Unity", "Unreal", "C#", "C++"
        ],
        "work_format": "REMOTE",
        "area_id": "113",
        "search_field": "name",
        "period": "3",
        "role_ids_config": [
            "96", "104", "107", "112", "113", "114", "116", "121", "124",
            "125", "126"
        ],
        "cover_letter": default_cover_letter,
        "skip_applied_in_same_company": False,
        "deduplicate_by_name_and_company": True,
        "strikethrough_applied_vac": True,
        "strikethrough_applied_vac_name": True,
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
    "negotiation_history", metadata,
    Column("vacancy_id", String, primary_key=True),
    Column("profile_name", String, nullable=False),
    Column("vacancy_title", String),
    Column("employer_name", String),
    Column("status", String),
    Column("reason", String),
    Column("applied_at", DateTime, nullable=False)
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

def init_db():
    global engine
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"INFO: База данных используется по пути: {DB_PATH}")
    engine = create_engine(f"sqlite:///{DB_PATH}")
    metadata.create_all(engine)


def log_to_db(level: str, source: str, message: str):
    if not engine:
        return
    with engine.connect() as connection:
        stmt = insert(app_logs).values(
            level=level, source=source, message=message)
        connection.execute(stmt)
        connection.commit()

def record_apply_action(
        vacancy_id: str, profile_name: str, vacancy_title: str,
        employer_name: str, status: str, reason: str | None):
    values = {
        "vacancy_id": vacancy_id, "profile_name": profile_name,
        "vacancy_title": vacancy_title, "employer_name": employer_name,
        "status": status, "reason": reason, "applied_at": datetime.now(),
    }
    stmt = sqlite_insert(negotiation_history).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=['vacancy_id'], set_=values)
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


def get_last_sync_timestamp(profile_name: str) -> datetime | None:
    with engine.connect() as connection:
        key = f"last_negotiation_sync_{profile_name}"
        stmt = select(app_state.c.value).where(app_state.c.key == key)
        result = connection.execute(stmt).scalar_one_or_none()
        if result:
            return datetime.fromisoformat(result)
        return None


def set_last_sync_timestamp(profile_name: str, timestamp: datetime):
    with engine.connect() as connection:
        key = f"last_negotiation_sync_{profile_name}"
        value = timestamp.isoformat()
        stmt = sqlite_insert(app_state).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(
            index_elements=['key'], set_=dict(value=value))
        connection.execute(stmt)
        connection.commit()


def upsert_negotiation_history(negotiations: list[dict], profile_name: str):
    if not negotiations:
        return
    with engine.connect() as connection:
        for item in negotiations:
            vacancy = item.get('vacancy', {})
            if not vacancy or not vacancy.get('id'):
                continue
            values = {
                "vacancy_id": vacancy['id'],
                "profile_name": profile_name,
                "vacancy_title": vacancy.get('name'),
                "employer_name": vacancy.get('employer', {}).get('name'),
                "status": item.get('state', {}).get('name', 'N/A'),
                "reason": None,
                "applied_at": datetime.fromisoformat(
                    item['updated_at'].replace("Z", "+00:00")),
            }
            stmt = sqlite_insert(negotiation_history).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=['vacancy_id'],
                set_={
                    "status": values["status"],
                    "applied_at": values["applied_at"]
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
                           ["text_include", "negative", "role_ids_config"]}
            config_main["profile_name"] = profile_name
            connection.execute(insert(profile_configs).values(config_main))

            pos_keywords = [{"profile_name": profile_name, "keyword": kw}
                            for kw in defaults["text_include"]]
            if pos_keywords:
                connection.execute(insert(config_positive_keywords), pos_keywords)

            neg_keywords = [{"profile_name": profile_name, "keyword": kw}
                            for kw in defaults["negative"]]
            if neg_keywords:
                connection.execute(insert(config_negative_keywords), neg_keywords)

            roles = [{"profile_name": profile_name, "role_id": r_id}
                     for r_id in defaults["role_ids_config"]]
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
            key="active_profile", value=profile_name)
        stmt = stmt.on_conflict_do_update(
            index_elements=['key'], set_=dict(value=profile_name))
        connection.execute(stmt)
        connection.commit()


def get_active_profile_name() -> str | None:
    with engine.connect() as connection:
        stmt = select(app_state.c.value).where(
            app_state.c.key == "active_profile")
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

        stmt_pos_keywords = select(config_positive_keywords.c.keyword).where(
            config_positive_keywords.c.profile_name == profile_name)
        config["text_include"] = connection.execute(stmt_pos_keywords).scalars().all()

        stmt_keywords = select(config_negative_keywords.c.keyword).where(
            config_negative_keywords.c.profile_name == profile_name)
        config["negative"] = connection.execute(stmt_keywords).scalars().all()

        return config

def save_profile_config(profile_name: str, config: dict):
    """Сохраняет полную конфигурацию в связанные таблицы."""
    with engine.connect() as connection, connection.begin():
        positive_keywords = config.pop("text_include", [])
        negative_keywords = config.pop("negative", [])
        role_ids = config.pop("role_ids_config", [])
        
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