from fastapi.testclient import TestClient

import app.main as main_module


def test_startup_calls_initialize_database_schema(monkeypatch) -> None:
    called = {'count': 0}

    def fake_initialize() -> None:
        called['count'] += 1

    monkeypatch.setattr(main_module, 'initialize_database_schema', fake_initialize)

    with TestClient(main_module.app):
        pass

    assert called['count'] >= 1
