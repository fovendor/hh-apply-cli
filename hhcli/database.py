import os
from datetime import datetime
from platformdirs import user_data_dir
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, DateTime, insert, select, delete

# --- Определение пути к БД и ее структуры ---

# Кроссплатформенное определение пути к директории с данными
APP_NAME = "hhcli"
APP_AUTHOR = "fovendor"
DATA_DIR = user_data_dir(APP_NAME, APP_AUTHOR)
DB_PATH = os.path.join(DATA_DIR, "hhcli.sqlite")

# SQLAlchemy будет управлять соединением с БД
engine = None
# Метаданные - это реестр, который знает обо всех таблицах.
metadata = MetaData()

# Таблица для хранения токенов аутентификации
# Пока у нас один профиль, id будет всегда равен 1
auth_tokens = Table(
    "auth_tokens",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("access_token", String, nullable=False),
    Column("refresh_token", String, nullable=False),
    Column("expires_at", DateTime, nullable=False),
)

# --- Функции для работы с БД ---

def init_db():
    """
    Инициализирует соединение с БД и создает таблицы, если они не существуют.
    """
    global engine
    
    # Создаем директорию для данных, если ее нет
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # Создаем движок, который будет работать с нашим файлом БД
    engine = create_engine(f"sqlite:///{DB_PATH}")
    
    # Эта команда создает все таблицы, если они еще не созданы.
    # Безопасна для повторного вызова.
    metadata.create_all(engine)

def save_token(token_data: dict, expires_at: datetime):
    """Сохраняет или обновляет токен в БД."""
    with engine.connect() as connection:
        # Сначала удаляем старый токен, если он есть (простейшая логика upsert)
        stmt_delete = delete(auth_tokens).where(auth_tokens.c.id == 1)
        connection.execute(stmt_delete)
        
        # Вставляем новый
        stmt_insert = insert(auth_tokens).values(
            id=1,
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            expires_at=expires_at
        )
        connection.execute(stmt_insert)
        connection.commit()

def load_token() -> dict | None:
    """Загружает токен из БД."""
    with engine.connect() as connection:
        stmt = select(auth_tokens).where(auth_tokens.c.id == 1)
        result = connection.execute(stmt).first()
        
        if result:
            return {
                "access_token": result.access_token,
                "refresh_token": result.refresh_token,
                "expires_at": result.expires_at.isoformat() # Возвращаем в том же формате, что и было в JSON
            }
        return None

def delete_token():
    """Удаляет токен из БД."""
    with engine.connect() as connection:
        stmt = delete(auth_tokens).where(auth_tokens.c.id == 1)
        connection.execute(stmt)
        connection.commit()