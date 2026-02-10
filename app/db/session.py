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
    """Initialize schema only for fresh PostgreSQL databases before migrations exist."""
    if not engine.url.drivername.startswith('postgresql'):
        return

    with engine.begin() as conn:
        result = conn.exec_driver_sql("select to_regclass('public.alembic_version')")
        alembic_version_exists = result.scalar() is not None
        if alembic_version_exists:
            return

        tables_result = conn.exec_driver_sql(
            """
            select count(*)
            from information_schema.tables
            where table_schema = 'public'
            """
        )
        public_table_count = int(tables_result.scalar() or 0)
        if public_table_count > 0:
            return

        sql_path = Path(__file__).resolve().parents[2] / 'sql' / '001_init.sql'
        if not sql_path.exists():
            raise FileNotFoundError(f'DB init SQL not found: {sql_path}')

        sql_text = sql_path.read_text(encoding='utf-8')
        statements = [stmt.strip() for stmt in sql_text.split(';') if stmt.strip()]
        for statement in statements:
            conn.exec_driver_sql(statement)
