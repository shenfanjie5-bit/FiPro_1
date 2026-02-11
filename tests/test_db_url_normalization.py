from app.db.session import _normalize_database_url


def test_normalize_database_url_postgresql_scheme() -> None:
    raw = 'postgresql://postgres:postgres@localhost:5432/fipro1'
    assert _normalize_database_url(raw) == 'postgresql+psycopg://postgres:postgres@localhost:5432/fipro1'


def test_normalize_database_url_postgres_alias() -> None:
    raw = 'postgres://postgres:postgres@localhost:5432/fipro1'
    assert _normalize_database_url(raw) == 'postgresql+psycopg://postgres:postgres@localhost:5432/fipro1'


def test_normalize_database_url_keeps_driver_specific_url() -> None:
    raw = 'postgresql+psycopg://postgres:postgres@localhost:5432/fipro1'
    assert _normalize_database_url(raw) == raw


def test_normalize_database_url_keeps_non_postgres_url() -> None:
    raw = 'sqlite+pysqlite:///:memory:'
    assert _normalize_database_url(raw) == raw
