import os
from datetime import datetime
from platformdirs import user_data_dir
from sqlalchemy import (create_engine, MetaData, Table, Column, 
                        Integer, String, DateTime,
                        insert, select, delete, update)

# --- Конфигурация БД ---
APP_NAME = "hhcli"
APP_AUTHOR = "fovendor"
DATA_DIR = user_data_dir(APP_NAME, APP_AUTHOR)

DB_FILENAME = "hhcli_v1.sqlite" 
DB_PATH = os.path.join(DATA_DIR, DB_FILENAME)

engine = None
metadata = MetaData()

# --- Новая схема таблиц ---

# Таблица для хранения профилей/аккаунтов
profiles = Table(
    "profiles",
    metadata,
    # Имя профиля, которое задает пользователь, например, "fovendor"
    Column("profile_name", String, primary_key=True),
    # Уникальный ID пользователя с hh.ru, чтобы избежать дублей
    Column("hh_user_id", String, unique=True, nullable=False),
    Column("email", String), # Email для удобства отображения
    Column("access_token", String, nullable=False),
    Column("refresh_token", String, nullable=False),
    Column("expires_at", DateTime, nullable=False),
)

# Простая таблица для хранения состояния приложения, например, какой профиль активен
app_state = Table(
    "app_state",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String),
)

# --- Функции для работы с БД ---

def init_db():
    """Инициализирует соединение с БД и создает таблицы, если они не существуют."""
    global engine
    os.makedirs(DATA_DIR, exist_ok=True)
    engine = create_engine(f"sqlite:///{DB_PATH}")
    metadata.create_all(engine)

def save_or_update_profile(profile_name: str, user_info: dict, token_data: dict, expires_at: datetime):
    """Сохраняет или обновляет данные профиля (логика UPSERT)."""
    with engine.connect() as connection:
        # Проверяем, существует ли уже профиль с таким hh_user_id
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
            # Если пользователь уже есть (возможно, под другим именем профиля), обновляем его
            stmt_update = update(profiles).where(profiles.c.hh_user_id == user_info['id']).values(
                profile_name=profile_name, **values
            )
            connection.execute(stmt_update)
        else:
            # Если нет, вставляем новую запись
            stmt_insert = insert(profiles).values(profile_name=profile_name, **values)
            connection.execute(stmt_insert)
        connection.commit()

def load_profile(profile_name: str) -> dict | None:
    """Загружает данные конкретного профиля."""
    with engine.connect() as connection:
        stmt = select(profiles).where(profiles.c.profile_name == profile_name)
        result = connection.execute(stmt).first()
        if result:
            return dict(result._mapping)
        return None

def delete_profile(profile_name: str):
    """Удаляет профиль."""
    with engine.connect() as connection:
        stmt = delete(profiles).where(profiles.c.profile_name == profile_name)
        connection.execute(stmt)
        connection.commit()

def set_active_profile(profile_name: str):
    """Устанавливает активный профиль."""
    with engine.connect() as connection:
        # Удаляем старую запись, если она есть (логика UPSERT)
        connection.execute(delete(app_state).where(app_state.c.key == "active_profile"))
        # Вставляем новую
        connection.execute(insert(app_state).values(key="active_profile", value=profile_name))
        connection.commit()

def get_active_profile_name() -> str | None:
    """Получает имя активного профиля."""
    with engine.connect() as connection:
        stmt = select(app_state.c.value).where(app_state.c.key == "active_profile")
        result = connection.execute(stmt).scalar_one_or_none()
        return result