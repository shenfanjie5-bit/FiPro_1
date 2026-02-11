from __future__ import annotations

import uuid

import app.tools.facts as facts_module
from app.tools.graph import reset_graph_runtime_state
from app.tools.memory import reset_memory_runtime_state
from app.tools.rag import reset_rag_runtime_state
from app.validation.consistency import check_consistency
from app.validation.schema_validator import validate_report_schema
from app.workflows.checkpoint import get_latest_checkpoint
from app.workflows.graph import _route_need_review, run_research_workflow
import app.workflows.nodes as nodes_module


def test_route_need_review_for_buy_action_in_tier1() -> None:
    state = {
        'request': {'tier': 'TIER1'},
        'config': {'tier_policy': nodes_module._default_tier_policy()},
        'report_draft': {
            'decision': {'action': 'BUY'},
            'data_quality': {'status': 'OK'},
            'risk_flags': [],
        },
    }
    assert _route_need_review(state) == 'review'


def test_tier2_workflow_runs_graph_and_reviewer(monkeypatch) -> None:
    monkeypatch.setenv('GRAPH_DISABLE_NEO4J', '1')
    facts_module.reset_facts_runtime_state()
    reset_rag_runtime_state()
    reset_memory_runtime_state()
    reset_graph_runtime_state()

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER2',
        'run_mode': 'LIVE',
    }
    thread_id = f"thread_m5_tier2_{uuid.uuid4().hex[:8]}"
    result = run_research_workflow(request_data=payload, thread_id=thread_id)
    report = result['final_report']

    ok, errors = validate_report_schema(report)
    assert ok, errors
    assert check_consistency(report) == []

    assert report['provenance']['router_policy'] == 'router_m6_v1'
    assert report['provenance']['model']['reviewer'] == 'rule-reviewer-v1'
    assert any(str(ref.get('type', '')).upper() == 'GRAPH_QUERY' for ref in report['evidence_refs'])
    graph_checksums = {
        str(ref.get('checksum', '')).strip()
        for ref in report['evidence_refs']
        if str(ref.get('type', '')).upper() == 'GRAPH_QUERY'
    }
    graph_checksums.discard('')
    driver_refs = []
    for item in report.get('key_drivers_to_watch', []):
        if isinstance(item, dict):
            driver_refs.extend([str(ref).strip() for ref in item.get('graph_refs', []) if str(ref).strip()])
    assert driver_refs
    assert set(driver_refs).issubset(graph_checksums)

    checkpoint_state = get_latest_checkpoint(thread_id)
    assert checkpoint_state is not None
    tool_names = {trace.get('tool_name') for trace in checkpoint_state.get('tool_traces', [])}
    assert 'query_supply_chain_subtree' in tool_names
    assert 'find_impact_paths' in tool_names
    assert checkpoint_state.get('reviewer_notes')
