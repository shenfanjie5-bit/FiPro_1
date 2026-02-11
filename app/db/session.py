from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


BASELINE_ALEMBIC_REVISION = '20260210_0001'
BASELINE_CORE_TABLES = (
    'strategies',
    'strategy_versions',
    'tickers',
    'daily_snapshots',
    'reports',
    'decision_logs',
    'watchlist',
    'memory_notes',
    'memory_embeddings',
    'tool_traces',
)


def _normalize_database_url(database_url: str) -> str:
    raw = (database_url or '').strip()
    if raw.startswith('postgresql+'):
        return raw
    if raw.startswith('postgresql://'):
        return raw.replace('postgresql://', 'postgresql+psycopg://', 1)
    if raw.startswith('postgres://'):
        return raw.replace('postgres://', 'postgresql+psycopg://', 1)
    return raw


def _build_engine_and_session() -> tuple:
    settings = get_settings()
    engine = create_engine(_normalize_database_url(settings.database_url), pool_pre_ping=True)
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
            placeholders = ','.join(f"'{name}'" for name in BASELINE_CORE_TABLES)
            baseline_tables_result = conn.exec_driver_sql(
                f"""
                select count(*)
                from information_schema.tables
                where table_schema = 'public'
                  and table_name in ({placeholders})
                """
            )
            baseline_table_count = int(baseline_tables_result.scalar() or 0)
            if baseline_table_count == len(BASELINE_CORE_TABLES):
                conn.exec_driver_sql('create table if not exists alembic_version (version_num varchar(32) not null)')
                conn.exec_driver_sql('delete from alembic_version')
                conn.exec_driver_sql(f"insert into alembic_version (version_num) values ('{BASELINE_ALEMBIC_REVISION}')")
            return

        sql_path = Path(__file__).resolve().parents[2] / 'sql' / '001_init.sql'
        if not sql_path.exists():
            raise FileNotFoundError(f'DB init SQL not found: {sql_path}')

        sql_text = sql_path.read_text(encoding='utf-8')
        statements = [stmt.strip() for stmt in sql_text.split(';') if stmt.strip()]
        for statement in statements:
            conn.exec_driver_sql(statement)

        # Keep SQL bootstrap and Alembic chain aligned for subsequent upgrades.
        conn.exec_driver_sql('create table if not exists alembic_version (version_num varchar(32) not null)')
        conn.exec_driver_sql('delete from alembic_version')
        conn.exec_driver_sql(f"insert into alembic_version (version_num) values ('{BASELINE_ALEMBIC_REVISION}')")
