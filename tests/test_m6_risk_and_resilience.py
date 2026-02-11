from __future__ import annotations

import uuid

import app.tools.facts as facts_module
from app.tools.graph import reset_graph_runtime_state
from app.tools.memory import reset_memory_runtime_state
from app.tools.rag import reset_rag_runtime_state
from app.validation.consistency import check_consistency
from app.validation.schema_validator import validate_report_schema
from app.workflows.checkpoint import get_latest_checkpoint
from app.workflows.graph import run_research_workflow
import app.workflows.nodes as nodes_module


def _payload(tier: str) -> dict:
    return {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': tier,
        'run_mode': 'LIVE',
    }


def test_llm_failure_falls_back_to_conservative_report(monkeypatch) -> None:
    monkeypatch.setenv('LLM_FORCE_FAILURE', 'timeout')
    monkeypatch.setenv('LLM_RETRY_MAX_ATTEMPTS', '2')
    facts_module.reset_facts_runtime_state()
    reset_rag_runtime_state()
    reset_memory_runtime_state()
    reset_graph_runtime_state()

    thread_id = f"thread_m6_llm_{uuid.uuid4().hex[:8]}"
    result = run_research_workflow(request_data=_payload('TIER1'), thread_id=thread_id)
    report = result['final_report']

    ok, errors = validate_report_schema(report)
    assert ok, errors
    assert check_consistency(report) == []
    assert report['provenance']['model']['primary'] == 'rule-fallback-v1'
    assert report['decision']['action'] in {'WATCH', 'AVOID'}
    assert report['data_quality']['status'] in {'PARTIAL', 'DEGRADED'}

    checkpoint_state = get_latest_checkpoint(thread_id)
    assert checkpoint_state is not None
    llm_traces = [trace for trace in checkpoint_state.get('tool_traces', []) if trace.get('tool_name') == 'llm_generate_report_draft']
    assert llm_traces
    assert int(llm_traces[-1].get('retry_count', 0)) >= 1
    assert checkpoint_state.get('degradation_matrix', {}).get('llm', {}).get('status') == 'DEGRADED'


def test_risk_gate_keeps_highest_priority_on_partial_data_quality() -> None:
    state = {
        'config': {'risk_profile': 'LOW'},
        'tool_traces': [],
        'data_quality': {'status': 'PARTIAL', 'missing_fields': ['x'], 'notes': 'partial for test'},
        'degradation_matrix': nodes_module._default_degradation_matrix(),
        'report_draft': {
            'schema_version': '0.1',
            'report_id': 'r_m6_gate',
            'generated_at': '2026-02-10T00:00:00+00:00',
            'ticker': '600519.SH',
            'market': 'CN_A',
            'asof': '2026-02-10T09:30:00+08:00',
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER1',
            'decision': {'action': 'BUY', 'overall_score': 86, 'confidence': 0.91, 'time_horizon': 'SWING', 'summary': 'buy'},
            'price_bands': [],
            'key_drivers_to_watch': [],
            'thesis': {'base_case': 'b', 'bull_case': 'b', 'bear_case': 'b', 'next_steps': ['n']},
            'risk_flags': [],
            'invalidations': [],
            'evidence_refs': [{'evidence_id': 'ev_1', 'type': 'SNAPSHOT_FIELD', 'title': 't', 'source': 's', 'captured_at': '2026-02-10T00:00:00+00:00'}],
            'data_quality': {'status': 'PARTIAL', 'missing_fields': ['x'], 'notes': ''},
            'provenance': {'model': {'primary': 'mock', 'reviewer': 'NONE'}, 'router_policy': 'router_m6_v1', 'snapshot_ids': [], 'weights_hash': 'w', 'run_mode': 'LIVE'},
            'memory_update': {'summary': 'm', 'tags': ['a'], 'importance': 50, 'followups': []},
        },
    }

    updated = nodes_module.risk_gate_node(state)
    report = updated['report_draft']
    assert report['decision']['action'] == 'WATCH'
    assert float(report['decision']['confidence']) <= 0.5
    assert 'DATA_QUALITY_PARTIAL' in updated.get('risk_gate_hard_blocks', [])


def test_tier2_graph_failure_uses_degradation_matrix(monkeypatch) -> None:
    facts_module.reset_facts_runtime_state()
    reset_rag_runtime_state()
    reset_memory_runtime_state()
    reset_graph_runtime_state()

    def graph_fail(*args, **kwargs):  # noqa: ANN002, ANN003
        return {'error': {'code': 'UPSTREAM_ERROR', 'message': 'graph offline', 'retryable': True, 'details': {}}}

    monkeypatch.setattr(nodes_module, 'query_supply_chain_subtree', graph_fail)
    thread_id = f"thread_m6_graph_{uuid.uuid4().hex[:8]}"
    result = run_research_workflow(request_data=_payload('TIER2'), thread_id=thread_id)
    report = result['final_report']

    ok, errors = validate_report_schema(report)
    assert ok, errors
    assert check_consistency(report) == []
    graph_status = report.get('provenance', {}).get('degradation_matrix', {}).get('graph', {}).get('status')
    assert graph_status in {'PARTIAL', 'DEGRADED'}
    assert report['decision']['action'] != 'BUY'
