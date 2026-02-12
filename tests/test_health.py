from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health() -> None:
    resp = client.get('/health')
    assert resp.status_code == 200
    assert resp.json()['status'] == 'ok'


def test_health_returns_request_id() -> None:
    resp = client.get('/health')
    assert resp.status_code == 200
    assert resp.headers.get('x-request-id')


def test_root_redirects_to_gui() -> None:
    resp = client.get('/', follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers.get('location') == '/gui'


def test_gui_page() -> None:
    resp = client.get('/gui')
    assert resp.status_code == 200
    assert 'text/html' in resp.headers.get('content-type', '')
    assert 'FiPro_1 Minimal GUI' in resp.text
    assert '/reports/generate' in resp.text
