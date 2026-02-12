from __future__ import annotations

from datetime import datetime, timezone

from app.tools.skills import reset_skills_runtime_state, retrieve_skill_notes, write_skill_from_report, write_skill_note
import app.workflows.nodes as nodes_module


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_skill_write_and_retrieve(monkeypatch, tmp_path) -> None:
    runtime_db = tmp_path / 'skills-runtime.db'
    monkeypatch.setenv('WORKFLOW_RUNTIME_DB', str(runtime_db))
    monkeypatch.setenv('WORKFLOW_CHECKPOINT_DB', str(runtime_db))
    reset_skills_runtime_state()

    saved = write_skill_note(
        {
            'ticker': '600519.SH',
            'title': 'Breakout with risk cap',
            'summary': 'When liquidity improves and volatility remains controlled, prefer WATCH then BUY on confirmation.',
            'decision_bias': 'WATCH',
            'confidence': 0.71,
            'tags': ['breakout', 'risk-cap'],
            'run_mode': 'BACKTEST',
        }
    )
    assert saved.get('ok') is True

    retrieved = retrieve_skill_notes(ticker='600519.SH', query='liquidity volatility breakout', top_k=3)
    skills = retrieved.get('skills', [])
    assert skills
    assert skills[0]['title'] == 'Breakout with risk cap'
    assert skills[0]['decision_bias'] == 'WATCH'


def test_write_skill_from_report_generates_distilled_entry(monkeypatch, tmp_path) -> None:
    runtime_db = tmp_path / 'skills-runtime-from-report.db'
    monkeypatch.setenv('WORKFLOW_RUNTIME_DB', str(runtime_db))
    monkeypatch.setenv('WORKFLOW_CHECKPOINT_DB', str(runtime_db))
    reset_skills_runtime_state()

    result = write_skill_from_report(
        {
            'report_id': 'rpt_001',
            'generated_at': _now_iso(),
            'ticker': '600519.SH',
            'tier': 'TIER1',
            'decision': {'action': 'WATCH', 'confidence': 0.6, 'summary': 'Prefer staged entry under controlled volatility.'},
            'thesis': {'base_case': 'Demand and flows stabilize.', 'bull_case': 'x', 'bear_case': 'y', 'next_steps': ['a']},
            'risk_flags': [{'description': 'Macro uncertainty may increase drawdowns'}],
            'invalidations': [{'description': 'Breakdown below key support with heavy volume'}],
            'memory_update': {'summary': 'm', 'tags': ['supply-chain'], 'importance': 60, 'followups': []},
            'provenance': {'run_mode': 'BACKTEST'},
        }
    )
    assert result.get('ok') is True

    retrieved = retrieve_skill_notes(ticker='600519.SH', query='macro support volume', top_k=5)
    skills = retrieved.get('skills', [])
    assert skills
    assert skills[0]['source_report_id'] == 'rpt_001'
    assert 'auto_skill' in skills[0]['tags']
    assert skills[0]['run_mode'] == 'BACKTEST'


def test_persist_node_backtest_updates_skill_ref(monkeypatch) -> None:
    def fake_execute_tool(tool_name, payload, fn):  # noqa: ANN001
        _ = payload
        _ = fn
        return {
            'ok': True,
            'output': {'ok': True, 'note_id': 'note_001'},
            'trace': {
                'trace_id': 'trace_001',
                'tool_name': tool_name,
                'latency_ms': 1,
                'cost_est': 0.0,
                'error_code': None,
                'retry_count': 0,
                'retry_wait_ms': 0,
            },
        }

    monkeypatch.setattr(nodes_module, 'execute_tool', fake_execute_tool)
    monkeypatch.setattr(nodes_module, 'persist_workflow_state', lambda state, thread_id: {'report_id': state['report_draft']['report_id']})  # noqa: ARG005
    monkeypatch.setattr(nodes_module, 'write_skill_from_report', lambda report: {'ok': True, 'skill_id': 'skill_from_backtest'})  # noqa: ARG005

    state = {
        'thread_id': 'thread_backtest_skill_1',
        'request': {'run_mode': 'BACKTEST'},
        'config': {'router_policy_version': 'router_m6_v1'},
        'budget': {'max_tool_calls': 20, 'max_cost_usd': 1.0},
        'data_quality': {'status': 'OK', 'missing_fields': [], 'notes': ''},
        'degradation_matrix': nodes_module._default_degradation_matrix(),
        'tool_traces': [],
        'tool_call_stats': {'tool_calls': 0, 'latency_ms': 0, 'cost_usd_est': 0, 'tool_failures': 0, 'retry_count': 0, 'retry_wait_ms': 0},
        'report_draft': {
            'report_id': 'rpt_backtest_001',
            'schema_version': '0.1',
            'ticker': '600519.SH',
            'asof': _now_iso(),
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER0',
            'decision': {'action': 'WATCH', 'overall_score': 50, 'confidence': 0.5, 'time_horizon': 'SWING', 'summary': 'summary'},
            'price_bands': [
                {
                    'band_id': 'B1',
                    'range': {'currency': 'CNY', 'min': 1, 'max': 2},
                    'score': 50,
                    'confidence': 0.5,
                    'rationale': 'r',
                    'entry_conditions': [{'type': 'A', 'description': 'a', 'priority': 'LOW'}],
                    'exit_conditions': [{'type': 'A', 'description': 'a', 'priority': 'LOW'}],
                }
            ],
            'key_drivers_to_watch': [
                {
                    'driver_id': 'd1',
                    'type': 'RISK',
                    'what': 'w',
                    'direction': 'NEG',
                    'urgency': 'LOW',
                    'impact_hypothesis': 'h',
                    'monitor': {'signals': [], 'triggers': []},
                }
            ],
            'thesis': {'base_case': 'b', 'bull_case': 'b', 'bear_case': 'b', 'next_steps': ['n']},
            'risk_flags': [],
            'invalidations': [{'invalidation_id': 'inv1', 'description': 'd', 'priority': 'LOW', 'evidence_ids': []}],
            'evidence_refs': [{'evidence_id': 'ev_1', 'type': 'SNAPSHOT_FIELD', 'title': 't', 'source': 's', 'captured_at': _now_iso()}],
            'data_quality': {'status': 'OK', 'missing_fields': [], 'notes': ''},
            'provenance': {
                'model': {'primary': 'mock-primary-v1', 'reviewer': 'NONE'},
                'router_policy': 'router_m6_v1',
                'snapshot_ids': ['snap_1'],
                'weights_hash': 'w_mock_hash_v1',
                'run_mode': 'BACKTEST',
                'tool_call_stats': {'tool_calls': 0, 'latency_ms': 0, 'cost_usd_est': 0},
            },
            'memory_update': {'summary': 'm', 'tags': ['x'], 'importance': 50, 'followups': []},
        },
    }

    updated = nodes_module.persist_node(state)
    assert updated['persist_refs']['memory_note_id'] == 'note_001'
    assert updated['persist_refs']['skill_note_id'] == 'skill_from_backtest'
