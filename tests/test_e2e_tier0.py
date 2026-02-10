import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.validation.consistency import check_consistency
from app.validation.schema_validator import validate_report_schema
from app.workflows.checkpoint import get_latest_checkpoint
from app.workflows.persistence import get_report_artifact_counts


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
    thread_id = f"thread_test_{uuid.uuid4().hex[:8]}"
    resp = client.post('/reports/generate', json=payload, headers={'x-thread-id': thread_id})
    assert resp.status_code == 200
    body = resp.json()
    assert 'report_id' in body
    assert 'final_report' in body
    assert body['final_report']['schema_version'] == '0.1'
    assert body['final_report']['decision']['action'] in ('BUY', 'WATCH', 'AVOID')

    ok, errors = validate_report_schema(body['final_report'])
    assert ok, errors
    consistency_errors = check_consistency(body['final_report'])
    assert consistency_errors == []

    get_resp = client.get(f"/reports/{body['report_id']}")
    assert get_resp.status_code == 200
    get_body = get_resp.json()
    assert get_body['report_id'] == body['report_id']
    assert get_body['final_report']['report_id'] == body['report_id']

    artifacts = get_report_artifact_counts(body['report_id'])
    assert artifacts['decision_logs'] >= 1
    assert artifacts['tool_traces'] >= 1
    assert artifacts['memory_notes'] >= 1

    checkpoint_state = get_latest_checkpoint(thread_id)
    assert checkpoint_state is not None
    assert checkpoint_state.get('final_report', {}).get('report_id') == body['report_id']
