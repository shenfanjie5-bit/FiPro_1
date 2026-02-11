from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _generate_report() -> str:
    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'run_mode': 'LIVE',
    }
    thread_id = f"thread_feedback_{uuid.uuid4().hex[:8]}"
    resp = client.post('/reports/generate', json=payload, headers={'x-thread-id': thread_id})
    assert resp.status_code == 200
    return str(resp.json()['report_id'])


def test_submit_and_list_report_feedback() -> None:
    report_id = _generate_report()

    submit_resp = client.post(
        f'/reports/{report_id}/feedback',
        json={'feedback_label': 'USEFUL', 'comment': 'clear and actionable'},
    )
    assert submit_resp.status_code == 201
    submit_payload = submit_resp.json()
    assert submit_payload['report_id'] == report_id
    assert submit_payload['feedback_label'] == 'USEFUL'

    list_resp = client.get(f'/reports/{report_id}/feedback')
    assert list_resp.status_code == 200
    items = list_resp.json()['items']
    assert items
    assert items[0]['report_id'] == report_id
