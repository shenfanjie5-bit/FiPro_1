from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import routes as routes_module
from app.core.runtime_config import reset_runtime_config_overrides
from app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_runtime_config_state() -> None:
    reset_runtime_config_overrides()
    yield
    reset_runtime_config_overrides()


def test_runtime_config_get_and_put_cycle() -> None:
    get_resp = client.get('/runtime/config')
    assert get_resp.status_code == 200
    initial = get_resp.json()
    assert 'default_run_mode' in initial
    assert 'llm_provider' in initial
    assert 'llm_api_key_set' in initial
    assert 'llm_api_key_masked' in initial

    put_resp = client.put(
        '/runtime/config',
        json={
            'default_run_mode': 'BACKTEST',
            'llm_provider': 'openai',
            'llm_base_url': 'https://api.openai.com/v1/',
            'llm_primary_model': 'gpt-4o-mini',
            'llm_reviewer_model': 'NONE',
            'llm_shadow_model': 'gpt-4o-mini',
            'llm_shadow_reviewer_model': 'NONE',
            'llm_api_key': 'sk-test-runtime',
        },
    )
    assert put_resp.status_code == 200
    updated = put_resp.json()
    assert updated['default_run_mode'] == 'BACKTEST'
    assert updated['llm_provider'] == 'openai'
    assert updated['llm_base_url'] == 'https://api.openai.com/v1'
    assert updated['llm_api_key_set'] is True
    assert updated['llm_api_key_masked']


def test_generate_uses_runtime_default_run_mode_when_request_omits_it(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_run_research_workflow(request_data: dict, thread_id: str) -> dict:
        captured['run_mode'] = str(request_data.get('run_mode', ''))
        return {
            'final_report': {
                'report_id': f'report_{uuid.uuid4().hex[:8]}',
                'provenance': {'run_mode': captured['run_mode']},
            }
        }

    monkeypatch.setattr(routes_module, 'run_research_workflow', fake_run_research_workflow)
    update_resp = client.put('/runtime/config', json={'default_run_mode': 'SHADOW'})
    assert update_resp.status_code == 200

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
    }
    resp = client.post('/reports/generate', json=payload, headers={'x-thread-id': 'thread_runtime_default'})
    assert resp.status_code == 200
    body = resp.json()
    assert captured['run_mode'] == 'SHADOW'
    assert body['final_report']['provenance']['run_mode'] == 'SHADOW'


def test_generate_explicit_run_mode_overrides_runtime_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_run_research_workflow(request_data: dict, thread_id: str) -> dict:
        captured['run_mode'] = str(request_data.get('run_mode', ''))
        return {
            'final_report': {
                'report_id': f'report_{uuid.uuid4().hex[:8]}',
                'provenance': {'run_mode': captured['run_mode']},
            }
        }

    monkeypatch.setattr(routes_module, 'run_research_workflow', fake_run_research_workflow)
    update_resp = client.put('/runtime/config', json={'default_run_mode': 'BACKTEST'})
    assert update_resp.status_code == 200

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'run_mode': 'LIVE',
    }
    resp = client.post('/reports/generate', json=payload, headers={'x-thread-id': 'thread_runtime_override'})
    assert resp.status_code == 200
    body = resp.json()
    assert captured['run_mode'] == 'LIVE'
    assert body['final_report']['provenance']['run_mode'] == 'LIVE'
