import os
import json
from datetime import datetime
from platformdirs import user_data_dir
from sqlalchemy import (create_engine, MetaData, Table, Column, 
                        Integer, String, DateTime, JSON, Text,
                        insert, select, delete, update)
from sqlalchemy.sql import func

APP_NAME = "hhcli"
APP_AUTHOR = "fovendor"
DATA_DIR = user_data_dir(APP_NAME, APP_AUTHOR)
DB_FILENAME = "hhcli_v2.sqlite"
DB_PATH = os.path.join(DATA_DIR, DB_FILENAME)

engine = None
metadata = MetaData()

def get_default_config():
    """Возвращает стандартную конфигурацию поиска для нового профиля."""
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
    "profiles",
    metadata,
    Column("profile_name", String, primary_key=True),
    Column("hh_user_id", String, unique=True, nullable=False),
    Column("email", String),
    Column("access_token", String, nullable=False),
    Column("refresh_token", String, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("config_json", JSON, default=get_default_config),
)

app_state = Table(
    "app_state",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String),
)

# Таблица с логами
app_logs = Table(
    "app_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", DateTime, server_default=func.now()),
    Column("level", String(10), nullable=False), # INFO, WARN, ERROR
    Column("source", String(50)), # APIClient, TUI, Database
    Column("message", Text),
)

# История откликов
negotiation_history = Table(
    "negotiation_history",
    metadata,
    Column("vacancy_id", String, primary_key=True),
    Column("profile_name", String, nullable=False),
    Column("vacancy_title", String),
    Column("employer_name", String),
    Column("status", String), # e.g., 'applied', 'skipped', 'error'
    Column("reason", String), # e.g., 'questions_required'
    Column("applied_at", DateTime, nullable=False),
)

# Кэш деталей вакансий
vacancy_cache = Table(
    "vacancy_cache",
    metadata,
    Column("vacancy_id", String, primary_key=True),
    Column("json_data", JSON, nullable=False),
    Column("cached_at", DateTime, nullable=False),
)

# Кэш справочников
dictionaries_cache = Table(
    "dictionaries_cache",
    metadata,
    Column("name", String, primary_key=True), # e.g., 'areas', 'professional_roles'
    Column("json_data", JSON, nullable=False),
    Column("cached_at", DateTime, nullable=False),
)


# --- Функции для работы с БД ---

def init_db():
    """Инициализирует подключение к БД и создает все таблицы, если их нет."""
    global engine
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # Этот print можно будет убрать в релизной версии
    print(f"INFO: База данных используется по пути: {DB_PATH}")

    engine = create_engine(f"sqlite:///{DB_PATH}")
    metadata.create_all(engine)


# --- Существующие функции (без изменений в логике) ---

def save_or_update_profile(profile_name: str, user_info: dict, token_data: dict, expires_at: datetime):
    with engine.connect() as connection:
        stmt = select(profiles).where(profiles.c.hh_user_id == user_info['id'])
        existing = connection.execute(stmt).first()

        values = {
            "hh_user_id": user_info['id'],
            "email": user_info.get('email', ''),
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "expires_at": expires_at,
        }

        if existing:
            stmt_update = update(profiles).where(profiles.c.hh_user_id == user_info['id']).values(
                profile_name=profile_name, **values
            )
            connection.execute(stmt_update)
        else:
            # При создании нового профиля также добавляем дефолтный конфиг
            values["config_json"] = get_default_config()
            stmt_insert = insert(profiles).values(profile_name=profile_name, **values)
            connection.execute(stmt_insert)
        connection.commit()

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
        # Используем UPSERT (UPDATE or INSERT)
        stmt_select = select(app_state.c.key).where(app_state.c.key == "active_profile")
        if connection.execute(stmt_select).first():
            stmt = update(app_state).where(app_state.c.key == "active_profile").values(value=profile_name)
        else:
            stmt = insert(app_state).values(key="active_profile", value=profile_name)
        connection.execute(stmt)
        connection.commit()

def get_active_profile_name() -> str | None:
    with engine.connect() as connection:
        stmt = select(app_state.c.value).where(app_state.c.key == "active_profile")
        result = connection.execute(stmt).scalar_one_or_none()
        return result

def load_profile_config(profile_name: str) -> dict:
    """Загружает конфиг для указанного профиля."""
    with engine.connect() as connection:
        stmt = select(profiles.c.config_json).where(profiles.c.profile_name == profile_name)
        result = connection.execute(stmt).scalar_one_or_none()
        if result:
            # SQLAlchemy может возвращать JSON как строку, преобразуем в dict
            config = result if isinstance(result, dict) else json.loads(result)
            # Добавим значения по умолчанию, если их нет в сохраненном конфиге
            default_config = get_default_config()
            default_config.update(config)
            return default_config
        return get_default_config()

def save_profile_config(profile_name: str, config: dict):
    """Сохраняет конфиг для указанного профиля."""
    with engine.connect() as connection:
        stmt = update(profiles).where(profiles.c.profile_name == profile_name).values(
            config_json=config
        )
        connection.execute(stmt)
        connection.commit()