from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


def _build_engine_and_session() -> tuple:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, session_local


engine, SessionLocal = _build_engine_and_session()


def get_db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_database_schema() -> None:
    if not engine.url.drivername.startswith('postgresql'):
        return

    sql_path = Path(__file__).resolve().parents[2] / 'sql' / '001_init.sql'
    if not sql_path.exists():
        raise FileNotFoundError(f'DB init SQL not found: {sql_path}')

    sql_text = sql_path.read_text(encoding='utf-8')
    statements = [stmt.strip() for stmt in sql_text.split(';') if stmt.strip()]

    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)
