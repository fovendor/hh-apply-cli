import os
import json
from datetime import datetime
from platformdirs import user_data_dir
from sqlalchemy import (create_engine, MetaData, Table, Column, 
                        Integer, String, DateTime, JSON, # Добавляем тип JSON
                        insert, select, delete, update)

# --- Конфигурация БД (без изменений) ---
APP_NAME = "hhcli"
APP_AUTHOR = "fovendor"
DATA_DIR = user_data_dir(APP_NAME, APP_AUTHOR)
DB_FILENAME = "hhcli_v1.sqlite" 
DB_PATH = os.path.join(DATA_DIR, DB_FILENAME)

engine = None
metadata = MetaData()

# --- Новая схема таблиц ---

# Получаем дефолтный конфиг из первоисточника
def get_default_config():
    return {
        "text_include": "Python developer",
        "negative": " старший | senior | ведущий | Middle | ETL | BI | ML | Data Scientist | CV | NLP | Unity | Unreal | C# | C\\+\\+ | Golang | PHP | DevOps | AQA | QA | тестировщик | аналитик | analyst | маркетолог | менеджер | руководитель | стажер | intern | junior | джуниор",
        "work_format": "REMOTE",
        "area_id": "113",
        "search_field": "name",
        "period": "3",
        "role_ids_config": "96,104,107,112,113,114,116,121,124,125,126",
        "dedupe": "1",
        "skip_applied": "1",
        "strikethrough_applied_vac": "1",
        "strikethrough_applied_vac_name": "1",
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
    # НОВОЕ ПОЛЕ: Хранит настройки поиска в формате JSON
    Column("config_json", JSON, default=get_default_config),
)

app_state = Table(
    "app_state",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String),
)

# --- Функции для работы с БД ---

def init_db():
    global engine
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # Этот print можно будет убрать в релизной версии
    print(f"INFO: База данных используется по пути: {DB_PATH}")

    engine = create_engine(f"sqlite:///{DB_PATH}")
    
    # ВАЖНО: SQLAlchemy не умеет делать ALTER TABLE автоматически.
    # В реальном приложении здесь нужна была бы система миграций (Alembic).
    # Для нашего случая самый простой способ - удалить старую БД,
    # чтобы она пересоздалась с новой схемой.
    # При первом запуске после обновления это не вызовет проблем.
    metadata.create_all(engine)

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

# ... load_profile, delete_profile, set_active_profile, get_active_profile_name без изменений ...

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

def set_active_profile(profile_name: str):
    with engine.connect() as connection:
        connection.execute(delete(app_state).where(app_state.c.key == "active_profile"))
        connection.execute(insert(app_state).values(key="active_profile", value=profile_name))
        connection.commit()

def get_active_profile_name() -> str | None:
    with engine.connect() as connection:
        stmt = select(app_state.c.value).where(app_state.c.key == "active_profile")
        result = connection.execute(stmt).scalar_one_or_none()
        return result

# Работа с конфигом

def load_profile_config(profile_name: str) -> dict:
    """Загружает конфиг для указанного профиля."""
    with engine.connect() as connection:
        stmt = select(profiles.c.config_json).where(profiles.c.profile_name == profile_name)
        result = connection.execute(stmt).scalar_one_or_none()
        if result:
            # SQLAlchemy может возвращать JSON как строку, преобразуем в dict
            return result if isinstance(result, dict) else json.loads(result)
        return get_default_config()

def save_profile_config(profile_name: str, config: dict):
    """Сохраняет конфиг для указанного профиля."""
    with engine.connect() as connection:
        stmt = update(profiles).where(profiles.c.profile_name == profile_name).values(
            config_json=config
        )
        connection.execute(stmt)
        connection.commit()