from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import app.tools.facts as facts_module
import app.workflows.nodes as nodes_module
from app.tools.memory import reset_memory_runtime_state, retrieve_memory_notes, write_memory_note
from app.tools.rag import extract_events_from_docs, rerank_docs, reset_rag_runtime_state, search_event_docs
from app.workflows.checkpoint import get_latest_checkpoint
from app.workflows.graph import run_research_workflow


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_search_event_docs_supports_multi_query_and_sources_filter() -> None:
    reset_rag_runtime_state()
    asof_range = {'start': '2026-02-01T00:00:00+00:00', 'end': '2026-02-10T00:00:00+00:00'}
    result = search_event_docs(
        query=['茅台 供需', '白酒 政策'],
        asof_range=asof_range,
        sources=['FILINGS', 'REPORT'],
        top_k=5,
    )

    assert result['meta']['queries'] == ['茅台 供需', '白酒 政策']
    assert result['meta']['sources'] == ['FILINGS', 'REPORT']
    assert result['docs']
    assert len(result['docs']) <= 5
    assert all(doc['source'] in {'FILINGS', 'REPORT'} for doc in result['docs'])


def test_rerank_docs_returns_explainable_fields() -> None:
    docs = [
        {
            'doc_id': 'doc_1',
            'title': '茅台供需改善，渠道库存下降',
            'snippet': '供需结构改善，渠道反馈积极',
            'source': 'REPORT',
            'published_at': _now_iso(),
        },
        {
            'doc_id': 'doc_2',
            'title': '白酒行业政策观察',
            'snippet': '政策中性，短期博弈增强',
            'source': 'NEWS',
            'published_at': (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        },
    ]

    result = rerank_docs(query='茅台 供需', docs=docs, top_k=2)
    assert result['ranked_doc_ids']
    assert len(result['ranked_doc_ids']) == len(result['scores']) == len(result['reasons'])
    assert all('overlap=' in reason for reason in result['reasons'])
    assert all('rank_score' in doc and 'rank_reason' in doc for doc in result['docs'])


def test_extract_events_from_docs_returns_structured_events() -> None:
    docs = [
        {
            'doc_id': 'doc_evt_1',
            'title': '政策调整影响白酒渠道价格',
            'snippet': '渠道反馈显示价格承压但需求恢复',
            'source': 'NEWS',
        },
        {
            'doc_id': 'doc_evt_2',
            'title': '运价回落改善原料运输成本',
            'snippet': '物流效率提升，边际利好成本端',
            'source': 'REPORT',
        },
    ]

    result = extract_events_from_docs(docs)
    assert len(result['events']) == 2
    for event in result['events']:
        assert event['event_id']
        assert event['type']
        assert event['direction'] in {'POS', 'NEG', 'MIXED', 'UNCERTAIN'}
        assert isinstance(event['confidence'], float)
        assert event['summary']
        assert event['evidence_doc_ids']


def test_event_signal_rollup_maps_policy_and_governance() -> None:
    state = {
        'extracted_events': [
            {'type': 'POLICY', 'direction': 'POS', 'confidence': 0.8},
            {'type': 'GEOPOLITICS', 'direction': 'NEG', 'confidence': 0.6},
            {'type': 'EARNINGS', 'direction': 'POS', 'confidence': 0.7},
        ]
    }
    rolled = nodes_module._event_signal_rollup(state)
    assert rolled['event_count'] == 3
    assert rolled['used_event_count'] == 3
    assert isinstance(rolled['policy_signal'], float)
    assert isinstance(rolled['governance_signal'], float)
    assert -1.0 <= rolled['policy_signal'] <= 1.0
    assert -1.0 <= rolled['governance_signal'] <= 1.0


def test_memory_write_and_retrieve_supports_dedupe(monkeypatch, tmp_path) -> None:
    runtime_db = tmp_path / 'memory-runtime.db'
    monkeypatch.setenv('WORKFLOW_RUNTIME_DB', str(runtime_db))
    monkeypatch.setenv('WORKFLOW_CHECKPOINT_DB', str(runtime_db))
    reset_memory_runtime_state()

    first = write_memory_note(
        {
            'ticker': '600519.SH',
            'summary': '渠道库存下降，后续关注动销节奏',
            'tags': ['渠道', '库存'],
            'importance': 76,
            'links': ['followup:渠道调研'],
            'dedupe_key': 'report-001',
            'created_at': _now_iso(),
        }
    )
    second = write_memory_note(
        {
            'ticker': '600519.SH',
            'summary': '同一报告重复写入应被去重',
            'tags': ['重复'],
            'importance': 50,
            'dedupe_key': 'report-001',
        }
    )

    assert first['ok'] is True
    assert second['ok'] is True
    assert second['deduped'] is True
    assert second['note_id'] == first['note_id']

    retrieved = retrieve_memory_notes(
        ticker='600519.SH',
        query='库存 渠道',
        top_k=5,
        time_range_days=180,
    )
    assert retrieved['notes']
    assert retrieved['notes'][0]['note_id'] == first['note_id']


def test_tier1_workflow_runs_rag_chain_and_router_policy() -> None:
    facts_module.reset_facts_runtime_state()
    reset_rag_runtime_state()
    reset_memory_runtime_state()

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER1',
        'run_mode': 'LIVE',
    }
    thread_id = f"thread_m4_chain_{uuid.uuid4().hex[:8]}"
    result = run_research_workflow(request_data=payload, thread_id=thread_id)
    final_report = result['final_report']

    checkpoint_state = get_latest_checkpoint(thread_id)
    assert checkpoint_state is not None
    tool_names = {trace.get('tool_name') for trace in checkpoint_state.get('tool_traces', [])}
    assert {'search_event_docs', 'rerank_docs', 'extract_events_from_docs'}.issubset(tool_names)
    features = checkpoint_state.get('features', {})
    assert 'event_feature_meta' in features
    assert isinstance(features.get('event_policy_signal', 0.0), float)
    assert isinstance(features.get('evidence_coverage', 0.0), float)
    score = checkpoint_state.get('score', {})
    assert isinstance(score.get('factor_values', {}), dict)
    assert 'event.policy_signal' in score.get('factor_values', {})
    assert 'event.governance_signal' in score.get('factor_values', {})

    assert final_report['provenance']['router_policy'] == 'router_m6_v1'
    assert any(ref.get('source', '').startswith('event_docs.') for ref in final_report['evidence_refs'])


def test_tier1_budget_exhaustion_skips_rag_chain(monkeypatch) -> None:
    facts_module.reset_facts_runtime_state()
    reset_rag_runtime_state()

    original_factory = nodes_module._default_tier_policy

    def tiny_budget_policy() -> dict:
        policy = original_factory()
        policy['TIER1']['budget']['max_tool_calls'] = 7
        return policy

    monkeypatch.setattr(nodes_module, '_default_tier_policy', tiny_budget_policy)

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER1',
        'run_mode': 'LIVE',
    }
    thread_id = f"thread_m4_budget_{uuid.uuid4().hex[:8]}"
    result = run_research_workflow(request_data=payload, thread_id=thread_id)
    final_report = result['final_report']

    checkpoint_state = get_latest_checkpoint(thread_id)
    assert checkpoint_state is not None
    tool_names = {trace.get('tool_name') for trace in checkpoint_state.get('tool_traces', [])}
    assert 'search_event_docs' not in tool_names
    assert 'rerank_docs' not in tool_names
    assert 'extract_events_from_docs' not in tool_names
    assert checkpoint_state.get('budget', {}).get('degraded') is True
    assert final_report['data_quality']['status'] in {'PARTIAL', 'DEGRADED'}


def test_tier1_low_coverage_triggers_fallback_doc_repair(monkeypatch) -> None:
    facts_module.reset_facts_runtime_state()
    reset_rag_runtime_state()
    reset_memory_runtime_state()

    calls = {'count': 0}

    def fake_search_event_docs(query, asof_range, top_k=8, sources=None):  # noqa: ANN001
        calls['count'] += 1
        if calls['count'] == 1:
            return {'docs': [], 'meta': {'queries': query, 'window': asof_range}}
        return {
            'docs': [
                {
                    'doc_id': 'doc_repair_001',
                    'query': 'fallback',
                    'title': 'fallback news',
                    'source': 'NEWS',
                    'published_at': _now_iso(),
                    'captured_at': _now_iso(),
                    'uri': None,
                    'snippet': 'fallback snippet',
                    'checksum': 'fallback_ck',
                }
            ],
            'meta': {'queries': query, 'window': asof_range},
        }

    monkeypatch.setattr(nodes_module, 'search_event_docs', fake_search_event_docs)

    payload = {
        'ticker': '600519.SH',
        'market': 'CN_A',
        'asof': '2026-02-10T09:30:00+08:00',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER1',
        'run_mode': 'LIVE',
    }
    thread_id = f"thread_m4_covrepair_{uuid.uuid4().hex[:8]}"
    result = run_research_workflow(request_data=payload, thread_id=thread_id)
    final_report = result['final_report']

    assert calls['count'] >= 2
    assert any(ref.get('type') in {'NEWS_DOC', 'FILINGS'} for ref in final_report['evidence_refs'])
    checkpoint_state = get_latest_checkpoint(thread_id)
    assert checkpoint_state is not None
    search_traces = [trace for trace in checkpoint_state.get('tool_traces', []) if trace.get('tool_name') == 'search_event_docs']
    assert len(search_traces) >= 2


def test_repair_report_node_repairs_missing_evidence_ids() -> None:
    context_evidence = [
        {
            'evidence_id': 'ev_snap_1',
            'type': 'SNAPSHOT_FIELD',
            'title': 'snapshot',
            'source': 'facts',
            'captured_at': _now_iso(),
        },
        {
            'evidence_id': 'ev_doc_1',
            'type': 'NEWS_DOC',
            'title': 'news',
            'source': 'event_docs.NEWS',
            'captured_at': _now_iso(),
        },
    ]
    state = {
        'repair_attempts': 0,
        'request': {'tier': 'TIER1'},
        'config': {'tier_policy': nodes_module._default_tier_policy()},
        'context': {'evidence_refs': context_evidence},
        'report_draft': {
            'tier': 'TIER1',
            'decision': {'action': 'BUY', 'confidence': 0.8},
            'data_quality': {'status': 'PARTIAL', 'missing_fields': [], 'notes': ''},
            'evidence_refs': [
                {
                    'evidence_id': 'ev_bad_only',
                    'type': 'MANUAL_NOTE',
                    'title': 'bad',
                    'source': 'bad',
                    'captured_at': _now_iso(),
                }
            ],
            'risk_flags': [{'risk_id': 'r1', 'severity': 'LOW', 'description': 'd', 'evidence_ids': ['ev_missing']}],
            'invalidations': [],
            'key_drivers_to_watch': [],
        },
    }

    updated = nodes_module.repair_report_node(state)
    report = updated['report_draft']
    evidence_ids = {ref['evidence_id'] for ref in report['evidence_refs']}

    assert {'ev_snap_1', 'ev_doc_1'}.issubset(evidence_ids)
    assert report['risk_flags'][0]['evidence_ids'][0] in evidence_ids
    assert report['decision']['action'] == 'WATCH'
    assert report['decision']['confidence'] <= 0.55
