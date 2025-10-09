import os
import json
from datetime import datetime
from platformdirs import user_data_dir
from sqlalchemy import (create_engine, MetaData, Table, Column, 
                        Integer, String, DateTime, JSON, Text,
                        insert, select, delete, update)
from sqlalchemy.sql import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

APP_NAME = "hhcli"
APP_AUTHOR = "fovendor"
DATA_DIR = user_data_dir(APP_NAME, APP_AUTHOR)
DB_FILENAME = "hhcli_v2.sqlite"
DB_PATH = os.path.join(DATA_DIR, DB_FILENAME)

engine = None
metadata = MetaData()

def get_default_config():
    return {
        "text_include": "Python developer",
        "negative": " старший | senior | ведущий | Middle | ETL | BI | ML | Data Scientist | CV | NLP | Unity | Unreal | C# | C\\+\\+ | Golang | PHP | DevOps | AQA | QA | тестировщик | аналитик | analyst | маркетолог | менеджер | руководитель | стажер | intern | junior | джуниор",
        "work_format": "REMOTE",
        "area_id": "113",
        "search_field": "name",
        "period": "3",
        "role_ids_config": "96,104,107,112,113,114,116,121,124,125,126",
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
    Column("config_json", JSON, default=get_default_config),
)
app_state = Table("app_state", metadata, Column("key", String, primary_key=True), Column("value", String))
app_logs = Table(
    "app_logs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", DateTime, server_default=func.now()),
    Column("level", String(10), nullable=False),
    Column("source", String(50)),
    Column("message", Text),
)
negotiation_history = Table(
    "negotiation_history", metadata,
    Column("vacancy_id", String, primary_key=True),
    Column("profile_name", String, nullable=False),
    Column("vacancy_title", String),
    Column("employer_name", String),
    Column("status", String),
    Column("reason", String),
    Column("applied_at", DateTime, nullable=False),
)
vacancy_cache = Table(
    "vacancy_cache", metadata,
    Column("vacancy_id", String, primary_key=True),
    Column("json_data", JSON, nullable=False),
    Column("cached_at", DateTime, nullable=False),
)
dictionaries_cache = Table(
    "dictionaries_cache", metadata,
    Column("name", String, primary_key=True),
    Column("json_data", JSON, nullable=False),
    Column("cached_at", DateTime, nullable=False),
)

def init_db():
    global engine
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"INFO: База данных используется по пути: {DB_PATH}")
    engine = create_engine(f"sqlite:///{DB_PATH}")
    metadata.create_all(engine)

# Функции для логирования и синхронизации
def log_to_db(level: str, source: str, message: str):
    """Записывает лог в базу данных."""
    if not engine:
        print(f"DB NOT READY [LOG]: {level}/{source}: {message}")
        return
    with engine.connect() as connection:
        stmt = insert(app_logs).values(level=level, source=source, message=message)
        connection.execute(stmt)
        connection.commit()

def get_last_sync_timestamp(profile_name: str) -> datetime | None:
    """Получает время последней синхронизации откликов для профиля."""
    with engine.connect() as connection:
        key = f"last_negotiation_sync_{profile_name}"
        stmt = select(app_state.c.value).where(app_state.c.key == key)
        result = connection.execute(stmt).scalar_one_or_none()
        if result:
            return datetime.fromisoformat(result)
        return None

def set_last_sync_timestamp(profile_name: str, timestamp: datetime):
    """Устанавливает время последней синхронизации откликов для профиля."""
    with engine.connect() as connection:
        key = f"last_negotiation_sync_{profile_name}"
        value = timestamp.isoformat()
        stmt = sqlite_insert(app_state).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(index_elements=['key'], set_=dict(value=value))
        connection.execute(stmt)
        connection.commit()

def upsert_negotiation_history(negotiations: list[dict], profile_name: str):
    """Добавляет или обновляет записи в истории откликов."""
    if not negotiations:
        return
    with engine.connect() as connection:
        for item in negotiations:
            vacancy = item.get('vacancy', {})
            if not vacancy or not vacancy.get('id'):
                continue
            values = {
                "vacancy_id": vacancy['id'], "profile_name": profile_name,
                "vacancy_title": vacancy.get('name'),
                "employer_name": vacancy.get('employer', {}).get('name'),
                "status": item.get('state', {}).get('name', 'N/A'),
                "reason": None,
                "applied_at": datetime.fromisoformat(item['updated_at'].replace("Z", "+00:00")),
            }
            stmt = sqlite_insert(negotiation_history).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=['vacancy_id'],
                set_={"status": values["status"], "applied_at": values["applied_at"]}
            )
            connection.execute(stmt)
        connection.commit()

def save_or_update_profile(profile_name: str, user_info: dict, token_data: dict, expires_at: datetime):
    with engine.connect() as connection:
        stmt = select(profiles).where(profiles.c.hh_user_id == user_info['id'])
        existing = connection.execute(stmt).first()
        values = {
            "hh_user_id": user_info['id'], "email": user_info.get('email', ''),
            "access_token": token_data["access_token"], "refresh_token": token_data["refresh_token"],
            "expires_at": expires_at,
        }
        if existing:
            stmt_update = update(profiles).where(profiles.c.hh_user_id == user_info['id']).values(profile_name=profile_name, **values)
            connection.execute(stmt_update)
        else:
            values["config_json"] = get_default_config()
            stmt_insert = insert(profiles).values(profile_name=profile_name, **values)
            connection.execute(stmt_insert)
        connection.commit()

def load_profile(profile_name: str) -> dict | None:
    with engine.connect() as connection:
        stmt = select(profiles).where(profiles.c.profile_name == profile_name)
        result = connection.execute(stmt).first()
        return dict(result._mapping) if result else None

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
        stmt = sqlite_insert(app_state).values(key="active_profile", value=profile_name)
        stmt = stmt.on_conflict_do_update(index_elements=['key'], set_=dict(value=profile_name))
        connection.execute(stmt)
        connection.commit()

def get_active_profile_name() -> str | None:
    with engine.connect() as connection:
        stmt = select(app_state.c.value).where(app_state.c.key == "active_profile")
        return connection.execute(stmt).scalar_one_or_none()

def load_profile_config(profile_name: str) -> dict:
    with engine.connect() as connection:
        stmt = select(profiles.c.config_json).where(profiles.c.profile_name == profile_name)
        result = connection.execute(stmt).scalar_one_or_none()
        if result:
            config = result if isinstance(result, dict) else json.loads(result)
            default_config = get_default_config()
            default_config.update(config)
            return default_config
        return get_default_config()

def save_profile_config(profile_name: str, config: dict):
    with engine.connect() as connection:
        stmt = update(profiles).where(profiles.c.profile_name == profile_name).values(config_json=config)
        connection.execute(stmt)
        connection.commit()