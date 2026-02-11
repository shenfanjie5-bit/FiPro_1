from pathlib import Path


def test_alembic_basics_exist() -> None:
    assert Path('alembic.ini').exists()
    assert Path('app/db/migrations/env.py').exists()
    assert Path('app/db/migrations/script.py.mako').exists()


def test_initial_migration_exists() -> None:
    versions_dir = Path('app/db/migrations/versions')
    revisions = list(versions_dir.glob('*_m1_contract_baseline.py'))
    assert revisions, 'Missing initial M1 migration revision file'


def test_m6_trace_audit_migration_exists() -> None:
    versions_dir = Path('app/db/migrations/versions')
    revisions = list(versions_dir.glob('*_m6_trace_audit_fields.py'))
    assert revisions, 'Missing M6 trace audit migration revision file'
