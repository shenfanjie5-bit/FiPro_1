from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_generate_report_tier0() -> None:
    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'run_mode': 'LIVE',
    }
    resp = client.post('/reports/generate', json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert 'report_id' in body
    assert 'final_report' in body
    assert body['final_report']['schema_version'] == '0.1'
    assert body['final_report']['decision']['action'] in ('BUY', 'WATCH', 'AVOID')
