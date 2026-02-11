from __future__ import annotations

import uuid

from app.workflows.graph import run_research_workflow


def test_live_request_can_spawn_shadow_without_affecting_primary(monkeypatch) -> None:
    monkeypatch.setenv('M7_SHADOW_ENABLED', 'true')

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER1',
        'run_mode': 'LIVE',
    }
    thread_id = f"thread_m7_shadow_{uuid.uuid4().hex[:8]}"
    result = run_research_workflow(request_data=payload, thread_id=thread_id)

    final_report = result['final_report']
    persist_refs = result.get('persist_refs', {})
    assert final_report['provenance']['run_mode'] == 'LIVE'
    assert persist_refs.get('shadow_report_id')
    assert persist_refs.get('shadow_report_id') != final_report['report_id']
