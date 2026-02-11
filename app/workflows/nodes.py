from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from app.llm.provider import LLMProvider
from app.tools.deterministic import generate_price_bands, risk_gate, score_signal
from app.tools.facts import (
    get_flow_sentiment_snapshot,
    get_fundamentals_snapshot,
    get_macro_commodity_logistics_snapshot,
    get_market_snapshot,
)
from app.tools.memory import retrieve_memory_notes, write_memory_note
from app.tools.qa import consistency_check, validate_report_schema_tool
from app.tools.rag import extract_events_from_docs, rerank_docs, search_event_docs
from app.tools.wrapper import execute_tool
from app.workflows.persistence import persist_workflow_state


STATUS_RANK = {'OK': 0, 'PARTIAL': 1, 'DEGRADED': 2}


def _merge_status(a: str, b: str) -> str:
    return a if STATUS_RANK.get(a, 0) >= STATUS_RANK.get(b, 0) else b


def _merge_data_quality(base: dict, incoming: dict | None, prefix: str = '') -> dict:
    if not incoming:
        return base
    merged = {
        'status': _merge_status(str(base.get('status', 'OK')), str(incoming.get('status', 'OK'))),
        'missing_fields': list(base.get('missing_fields', [])),
        'notes': str(base.get('notes', '')),
    }
    for field in incoming.get('missing_fields', []):
        normalized = f'{prefix}.{field}' if prefix else str(field)
        if normalized not in merged['missing_fields']:
            merged['missing_fields'].append(normalized)
    incoming_notes = str(incoming.get('notes', '')).strip()
    if incoming_notes:
        merged['notes'] = f"{merged['notes']} | {incoming_notes}".strip(' |')
    return merged


def _default_tier_policy() -> dict[str, dict[str, Any]]:
    return {
        'TIER0': {
            'budget': {'max_tool_calls': 20, 'max_cost_usd': 0.2},
            'memory': {'top_k': 5, 'time_range_days': 120},
            'rag': {'enabled': False, 'search_top_k': 0, 'rerank_top_k': 0, 'query_count': 0, 'time_window_days': 0},
            'graph': {'depth': 1},
            'coverage': {'min_total_refs': 1, 'min_type_count': 1, 'required_types': ['SNAPSHOT_FIELD']},
        },
        'TIER1': {
            'budget': {'max_tool_calls': 45, 'max_cost_usd': 0.8},
            'memory': {'top_k': 8, 'time_range_days': 180},
            'rag': {'enabled': True, 'search_top_k': 12, 'rerank_top_k': 8, 'query_count': 3, 'time_window_days': 7},
            'graph': {'depth': 2},
            'coverage': {'min_total_refs': 4, 'min_type_count': 2, 'required_types': ['SNAPSHOT_FIELD', 'NEWS_DOC']},
        },
        'TIER2': {
            'budget': {'max_tool_calls': 90, 'max_cost_usd': 2.5},
            'memory': {'top_k': 10, 'time_range_days': 365},
            'rag': {'enabled': True, 'search_top_k': 16, 'rerank_top_k': 12, 'query_count': 4, 'time_window_days': 14},
            'graph': {'depth': 3},
            'coverage': {'min_total_refs': 6, 'min_type_count': 2, 'required_types': ['SNAPSHOT_FIELD', 'NEWS_DOC']},
        },
    }


def _refresh_tool_stats(state: dict) -> None:
    state['tool_call_stats'] = {
        'tool_calls': len(state.get('tool_traces', [])),
        'latency_ms': sum(trace.get('latency_ms', 0) for trace in state.get('tool_traces', [])),
        'cost_usd_est': round(sum(float(trace.get('cost_est', 0.0)) for trace in state.get('tool_traces', [])), 6),
    }
    if 'budget' in state:
        state['budget']['used_tool_calls'] = state['tool_call_stats']['tool_calls']
        state['budget']['used_cost_usd'] = state['tool_call_stats']['cost_usd_est']


def _tier_params(state: dict) -> dict[str, Any]:
    req_tier = str(state.get('request', {}).get('tier', 'TIER0'))
    policies = state.get('config', {}).get('tier_policy', _default_tier_policy())
    return dict(policies.get(req_tier, policies.get('TIER0', {})))


def _budget_allows_more_tools(state: dict) -> bool:
    _refresh_tool_stats(state)
    budget = state.get('budget', {})
    max_calls = int(budget.get('max_tool_calls', 0))
    max_cost = float(budget.get('max_cost_usd', 0.0))
    used_calls = int(budget.get('used_tool_calls', 0))
    used_cost = float(budget.get('used_cost_usd', 0.0))
    return used_calls < max_calls and used_cost <= max_cost


def _mark_budget_degraded(state: dict, reason: str) -> None:
    state.setdefault('budget', {})
    state['budget']['degraded'] = True
    state['budget']['degrade_reason'] = reason
    state['data_quality'] = _merge_data_quality(
        state['data_quality'],
        {
            'status': 'PARTIAL',
            'missing_fields': ['budget.capacity'],
            'notes': reason,
        },
    )


def load_strategy_config(state: dict) -> dict:
    tier_policy = _default_tier_policy()
    req = state.get('request', {})
    req_tier = str(req.get('tier', 'TIER0'))
    selected_tier = tier_policy.get(req_tier, tier_policy['TIER0'])
    state['config'] = {
        'weights': {
            'hotness': 0.25,
            'fundamental': 0.30,
            'volatility': 0.20,
            'liquidity': 0.15,
            'event_impact': 0.10,
        },
        'risk_profile': 'LOW',
        'router_policy_version': 'router_m4_v1',
        'tier_policy': tier_policy,
    }
    state['weights_hash'] = 'w_mock_hash_v1'
    state['budget'] = {
        'max_tool_calls': selected_tier['budget']['max_tool_calls'],
        'max_cost_usd': selected_tier['budget']['max_cost_usd'],
        'used_tool_calls': 0,
        'used_cost_usd': 0.0,
        'degraded': False,
        'degrade_reason': '',
    }
    return state


def init_data_quality(state: dict) -> dict:
    state['data_quality'] = {'status': 'OK', 'missing_fields': [], 'notes': ''}
    state['tool_traces'] = []
    state['tool_call_stats'] = {'tool_calls': 0, 'latency_ms': 0, 'cost_usd_est': 0}
    state['doc_queries'] = []
    state['doc_candidates'] = []
    state['ranked_docs'] = []
    state['ranked_doc_ids'] = []
    state['extracted_events'] = []
    state['event_docs'] = []
    state['evidence_coverage'] = {'ok': False, 'missing_rules': []}
    state['repair_attempts'] = 0
    state['max_repairs'] = 2
    state['workflow_invalid'] = False
    state['validation_errors'] = []
    state['consistency_errors'] = []
    state['risk_gate_hard_blocks'] = []
    state['persist_refs'] = {}
    return state


def build_facts(state: dict) -> dict:
    req = state['request']
    ticker = req['ticker']
    asof = req['asof']

    snapshots = {}
    snapshot_ids = []

    for tool_name, payload, fn in [
        ('get_market_snapshot', {'ticker': ticker, 'asof': asof}, get_market_snapshot),
        ('get_fundamentals_snapshot', {'ticker': ticker, 'asof': asof}, get_fundamentals_snapshot),
        ('get_flow_sentiment_snapshot', {'ticker': ticker, 'asof': asof}, get_flow_sentiment_snapshot),
    ]:
        result = execute_tool(tool_name, payload, fn)
        state['tool_traces'].append(result['trace'])
        if result['ok']:
            snapshots[tool_name] = result['output']
            snapshot_id = result['output'].get('snapshot_id')
            if snapshot_id:
                snapshot_ids.append(snapshot_id)
            state['data_quality'] = _merge_data_quality(
                state['data_quality'],
                result['output'].get('data_quality'),
                prefix=tool_name,
            )
        else:
            state['data_quality'] = _merge_data_quality(
                state['data_quality'],
                {
                    'status': 'DEGRADED',
                    'missing_fields': [f'{tool_name}.__upstream__'],
                    'notes': f'{tool_name} failed: {result["output"].get("error", {}).get("message", "unknown error")}',
                },
            )

    if req['tier'] in ('TIER1', 'TIER2'):
        macro = execute_tool(
            'get_macro_commodity_logistics_snapshot',
            {'ticker': ticker, 'asof': asof},
            get_macro_commodity_logistics_snapshot,
        )
        state['tool_traces'].append(macro['trace'])
        if macro['ok']:
            snapshots['get_macro_commodity_logistics_snapshot'] = macro['output']
            macro_id = macro['output'].get('snapshot_id')
            if macro_id:
                snapshot_ids.append(macro_id)
            state['data_quality'] = _merge_data_quality(
                state['data_quality'],
                macro['output'].get('data_quality'),
                prefix='get_macro_commodity_logistics_snapshot',
            )
        else:
            state['data_quality'] = _merge_data_quality(
                state['data_quality'],
                {
                    'status': 'DEGRADED',
                    'missing_fields': ['get_macro_commodity_logistics_snapshot.__upstream__'],
                    'notes': macro['output'].get('error', {}).get('message', 'macro snapshot failed'),
                },
            )

    state['snapshots'] = snapshots
    state['snapshot_ids'] = snapshot_ids
    _refresh_tool_stats(state)
    return state


def build_features(state: dict) -> dict:
    market = state['snapshots'].get('get_market_snapshot', {})
    funda = state['snapshots'].get('get_fundamentals_snapshot', {})
    flow = state['snapshots'].get('get_flow_sentiment_snapshot', {})

    hotness = flow.get('hotness_score')
    if hotness is None:
        hotness = flow.get('hotness', {}).get('hot_score')
    hotness = int(max(0, min(100, hotness if hotness is not None else 50)))

    roe = funda.get('roe')
    if roe is None:
        roe = funda.get('quality', {}).get('roe')
    fundamentals = int(max(0, min(100, (roe if roe is not None else 0.1) * 400)))

    volatility_20d = market.get('volatility_20d')
    if volatility_20d is None:
        volatility_20d = market.get('volatility', {}).get('stdev_20')
    volatility = int(max(0, min(100, (volatility_20d if volatility_20d is not None else 0.2) * 200)))

    volume_ratio = market.get('volume_ratio')
    if volume_ratio is None:
        turnover = market.get('liquidity', {}).get('avg_turnover_20d')
        volume_ratio = turnover if turnover is not None else 1.0
    liquidity = int(max(0, min(100, float(volume_ratio) * 60)))

    state['features'] = {
        'feature_id': f"feat_{uuid.uuid4().hex[:12]}",
        'hotness': hotness,
        'fundamentals': fundamentals,
        'volatility': volatility,
        'liquidity': liquidity,
    }
    state['feature_id'] = state['features']['feature_id']
    return state


def score_signal_node(state: dict) -> dict:
    weights = state['config']['weights']
    res = execute_tool('score_signal', {'features': state['features'], 'weights': weights}, score_signal)
    state['tool_traces'].append(res['trace'])
    state['score'] = res['output']
    state['score_id'] = res['output']['score_id']
    _refresh_tool_stats(state)
    return state


def generate_price_bands_node(state: dict) -> dict:
    market = state['snapshots'].get('get_market_snapshot', {})
    base_price = market.get('close', market.get('last_price', 100.0))
    res = execute_tool('generate_price_bands', {'base_price': base_price, 'score': state['score']['overall_score']}, generate_price_bands)
    state['tool_traces'].append(res['trace'])
    state['price_band_set_id'] = res['output']['price_band_set_id']
    state['price_bands'] = res['output']['price_bands']
    _refresh_tool_stats(state)
    return state


def retrieve_memory_node(state: dict) -> dict:
    req = state['request']
    tier_params = _tier_params(state)
    memory_cfg = dict(tier_params.get('memory', {}))
    memory_top_k = int(memory_cfg.get('top_k', 5))
    memory_window_days = int(memory_cfg.get('time_range_days', 180))
    res = execute_tool(
        'retrieve_memory_notes',
        {
            'ticker': req['ticker'],
            'query': f"{req['ticker']} 关键风险 催化 证伪 结论",
            'top_k': memory_top_k,
            'time_range_days': memory_window_days,
        },
        retrieve_memory_notes,
    )
    state['tool_traces'].append(res['trace'])
    if res['ok']:
        state['memory_notes'] = res['output'].get('notes', [])
    else:
        state['memory_notes'] = []
        state['data_quality'] = _merge_data_quality(
            state['data_quality'],
            {
                'status': 'DEGRADED',
                'missing_fields': ['memory.notes'],
                'notes': 'Memory retrieval failed',
            },
        )
    _refresh_tool_stats(state)
    rag_cfg = dict(_tier_params(state).get('rag', {}))
    req_tier = str(req.get('tier', 'TIER0'))
    if req_tier in ('TIER1', 'TIER2') and rag_cfg.get('enabled', False) and not _budget_allows_more_tools(state):
        _mark_budget_degraded(state, 'RAG disabled: remaining budget is insufficient after memory retrieval')
    return state


def _build_doc_queries(state: dict) -> list[str]:
    req = state['request']
    ticker = str(req.get('ticker', '')).strip()
    queries = [f'{ticker} latest events', f'{ticker} policy risk', f'{ticker} industry supply chain']
    for note in state.get('memory_notes', [])[:2]:
        for tag in note.get('tags', [])[:2]:
            candidate = f'{ticker} {tag}'
            if candidate not in queries:
                queries.append(candidate)
    max_queries = int(_tier_params(state).get('rag', {}).get('query_count', 3))
    output: list[str] = []
    for item in queries:
        normalized = str(item).strip()
        if normalized and normalized not in output:
            output.append(normalized)
    return output[:max(1, max_queries)]


def search_docs_node(state: dict) -> dict:
    req = state['request']
    rag_cfg = dict(_tier_params(state).get('rag', {}))
    if not rag_cfg.get('enabled', False):
        state['doc_queries'] = []
        state['doc_candidates'] = []
        return state
    if not _budget_allows_more_tools(state):
        _mark_budget_degraded(state, 'RAG search skipped: budget limit reached')
        state['doc_queries'] = []
        state['doc_candidates'] = []
        return state

    asof = datetime.fromisoformat(str(req['asof']).replace('Z', '+00:00'))
    if asof.tzinfo is None:
        asof = asof.replace(tzinfo=timezone.utc)
    time_window_days = int(rag_cfg.get('time_window_days', 7))
    asof_range = {'start': (asof - timedelta(days=time_window_days)).isoformat(), 'end': asof.isoformat()}
    queries = _build_doc_queries(state)
    state['doc_queries'] = queries
    search_top_k = int(rag_cfg.get('search_top_k', 8))
    docs_result = execute_tool(
        'search_event_docs',
        {
            'query': queries,
            'asof_range': asof_range,
            'top_k': search_top_k,
            'sources': ['NEWS', 'FILINGS', 'REPORT'],
        },
        search_event_docs,
    )
    state['tool_traces'].append(docs_result['trace'])
    if docs_result['ok']:
        state['doc_candidates'] = docs_result['output'].get('docs', [])
    else:
        state['doc_candidates'] = []
        state['data_quality'] = _merge_data_quality(
            state['data_quality'],
            {
                'status': 'PARTIAL',
                'missing_fields': ['event_docs.search'],
                'notes': docs_result['output'].get('error', {}).get('message', 'event docs search failed'),
            },
        )
    _refresh_tool_stats(state)
    return state


def rerank_docs_node(state: dict) -> dict:
    candidates = list(state.get('doc_candidates', []))
    if not candidates:
        state['ranked_docs'] = []
        state['ranked_doc_ids'] = []
        return state
    if not _budget_allows_more_tools(state):
        _mark_budget_degraded(state, 'Doc rerank skipped: budget limit reached')
        state['ranked_docs'] = candidates
        state['ranked_doc_ids'] = [doc.get('doc_id') for doc in candidates if doc.get('doc_id')]
        return state

    rerank_top_k = int(_tier_params(state).get('rag', {}).get('rerank_top_k', 8))
    query_text = ' | '.join(state.get('doc_queries', []))
    rerank_result = execute_tool(
        'rerank_docs',
        {'query': query_text, 'docs': candidates, 'top_k': rerank_top_k},
        rerank_docs,
    )
    state['tool_traces'].append(rerank_result['trace'])
    if rerank_result['ok']:
        state['ranked_docs'] = rerank_result['output'].get('docs', [])
        state['ranked_doc_ids'] = rerank_result['output'].get('ranked_doc_ids', [])
    else:
        state['ranked_docs'] = candidates[:rerank_top_k]
        state['ranked_doc_ids'] = [doc.get('doc_id') for doc in state['ranked_docs'] if doc.get('doc_id')]
        state['data_quality'] = _merge_data_quality(
            state['data_quality'],
            {
                'status': 'PARTIAL',
                'missing_fields': ['event_docs.rerank'],
                'notes': rerank_result['output'].get('error', {}).get('message', 'doc rerank failed'),
            },
        )
    _refresh_tool_stats(state)
    return state


def extract_events_node(state: dict) -> dict:
    ranked_docs = list(state.get('ranked_docs', []))
    if not ranked_docs:
        state['extracted_events'] = []
        state['event_docs'] = []
        return state
    if not _budget_allows_more_tools(state):
        _mark_budget_degraded(state, 'Event extraction skipped: budget limit reached')
        state['extracted_events'] = []
        state['event_docs'] = ranked_docs
        return state

    extract_result = execute_tool('extract_events_from_docs', {'docs': ranked_docs}, extract_events_from_docs)
    state['tool_traces'].append(extract_result['trace'])
    if extract_result['ok']:
        state['extracted_events'] = extract_result['output'].get('events', [])
    else:
        state['extracted_events'] = []
        state['data_quality'] = _merge_data_quality(
            state['data_quality'],
            {
                'status': 'PARTIAL',
                'missing_fields': ['event_docs.extract_events'],
                'notes': extract_result['output'].get('error', {}).get('message', 'event extraction failed'),
            },
        )
    state['event_docs'] = ranked_docs
    _refresh_tool_stats(state)
    return state


def _coverage_details_for_refs(state: dict, evidence_refs: list[dict[str, Any]]) -> dict[str, Any]:
    coverage_cfg = dict(_tier_params(state).get('coverage', {}))
    min_total_refs = int(coverage_cfg.get('min_total_refs', 1))
    min_type_count = int(coverage_cfg.get('min_type_count', 1))
    required_types = set(str(item) for item in coverage_cfg.get('required_types', []))
    present_types = {str(ref.get('type', '')) for ref in evidence_refs if str(ref.get('type', '')).strip()}
    expanded_types = set(present_types)
    if 'FILINGS' in present_types:
        expanded_types.add('NEWS_DOC')
    if 'NEWS_DOC' in present_types:
        expanded_types.add('FILINGS')

    missing_rules: list[str] = []
    if len(evidence_refs) < min_total_refs:
        missing_rules.append(f'min_total_refs<{min_total_refs}')
    if len(present_types) < min_type_count:
        missing_rules.append(f'min_type_count<{min_type_count}')
    for req_type in sorted(required_types):
        if req_type not in expanded_types:
            missing_rules.append(f'missing_type={req_type}')

    return {
        'ok': not missing_rules,
        'min_total_refs': min_total_refs,
        'actual_total_refs': len(evidence_refs),
        'min_type_count': min_type_count,
        'actual_type_count': len(present_types),
        'required_types': sorted(required_types),
        'present_types': sorted(present_types),
        'missing_rules': missing_rules,
    }


def _enforce_evidence_coverage(state: dict, evidence_refs: list[dict[str, Any]]) -> dict[str, Any]:
    coverage = _coverage_details_for_refs(state, evidence_refs)
    state['evidence_coverage'] = coverage
    missing_rules = list(coverage.get('missing_rules', []))
    if missing_rules:
        state['data_quality'] = _merge_data_quality(
            state['data_quality'],
            {
                'status': 'PARTIAL',
                'missing_fields': ['evidence.coverage'],
                'notes': f'evidence coverage degraded: {",".join(missing_rules)}',
            },
        )
    return coverage


def _dedupe_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for doc in docs:
        doc_id = str(doc.get('doc_id', '')).strip() or f"doc_{uuid.uuid4().hex[:8]}"
        merged[doc_id] = dict(doc)
        merged[doc_id]['doc_id'] = doc_id
    return sorted(merged.values(), key=lambda item: str(item.get('published_at', '')), reverse=True)


def _append_doc_evidence_refs(evidence_refs: list[dict[str, Any]], docs: list[dict[str, Any]], now_iso: str) -> None:
    existing_ids = {str(ref.get('evidence_id', '')) for ref in evidence_refs}
    for doc in docs:
        doc_id = str(doc.get('doc_id', '')).strip() or uuid.uuid4().hex[:8]
        evidence_id = f'ev_{doc_id}'
        if evidence_id in existing_ids:
            continue
        source = str(doc.get('source', 'NEWS')).upper()
        evidence_type = 'FILINGS' if source == 'FILINGS' else 'NEWS_DOC'
        evidence_refs.append(
            {
                'evidence_id': evidence_id,
                'type': evidence_type,
                'title': str(doc.get('title', 'event_doc'))[:140],
                'source': f"event_docs.{source}",
                'captured_at': doc.get('captured_at', now_iso),
                'uri': doc.get('uri'),
                'snippet': doc.get('snippet'),
                'checksum': doc.get('checksum'),
            }
        )
        existing_ids.add(evidence_id)


def _attempt_tier1_coverage_repair(state: dict, evidence_refs: list[dict[str, Any]], now_iso: str) -> None:
    req = state.get('request', {})
    tier = str(req.get('tier', 'TIER0'))
    if tier not in ('TIER1', 'TIER2'):
        return
    coverage = dict(state.get('evidence_coverage', {}))
    missing_rules = [str(item) for item in coverage.get('missing_rules', [])]
    need_news_doc = any(rule == 'missing_type=NEWS_DOC' for rule in missing_rules)
    if not need_news_doc:
        return
    if not _budget_allows_more_tools(state):
        _mark_budget_degraded(state, 'Tier1 coverage repair skipped: budget limit reached')
        return

    asof = datetime.fromisoformat(str(req['asof']).replace('Z', '+00:00'))
    if asof.tzinfo is None:
        asof = asof.replace(tzinfo=timezone.utc)
    time_window_days = int(_tier_params(state).get('rag', {}).get('time_window_days', 7))
    asof_range = {'start': (asof - timedelta(days=time_window_days)).isoformat(), 'end': asof.isoformat()}
    fallback_queries = list(state.get('doc_queries', [])) or [f"{req.get('ticker', '')} latest events"]
    fallback_query = [fallback_queries[0], f"{req.get('ticker', '')} news filing"]

    fallback_result = execute_tool(
        'search_event_docs',
        {
            'query': fallback_query,
            'asof_range': asof_range,
            'top_k': 3,
            'sources': ['NEWS', 'FILINGS', 'REPORT'],
        },
        search_event_docs,
    )
    state['tool_traces'].append(fallback_result['trace'])
    if fallback_result['ok']:
        fallback_docs = list(fallback_result['output'].get('docs', []))
        if fallback_docs:
            state['event_docs'] = _dedupe_docs(list(state.get('event_docs', [])) + fallback_docs)
            _append_doc_evidence_refs(evidence_refs, fallback_docs, now_iso)
        else:
            state['data_quality'] = _merge_data_quality(
                state['data_quality'],
                {
                    'status': 'PARTIAL',
                    'missing_fields': ['event_docs.coverage_repair'],
                    'notes': 'coverage repair search returned no documents',
                },
            )
    else:
        state['data_quality'] = _merge_data_quality(
            state['data_quality'],
            {
                'status': 'PARTIAL',
                'missing_fields': ['event_docs.coverage_repair'],
                'notes': fallback_result['output'].get('error', {}).get('message', 'coverage repair search failed'),
            },
        )
    _refresh_tool_stats(state)


def build_context(state: dict) -> dict:
    evidence_refs: list[dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for tool_name, snapshot in state.get('snapshots', {}).items():
        snapshot_id = snapshot.get('snapshot_id', 'snap_unknown')
        evidence_refs.append(
            {
                'evidence_id': f"ev_{snapshot_id}",
                'type': 'SNAPSHOT_FIELD',
                'title': f'{tool_name} summary',
                'source': f'facts.{tool_name}',
                'captured_at': snapshot.get('captured_at', now_iso),
                'uri': None,
                'snippet': f"snapshot_id={snapshot_id}",
                'checksum': snapshot.get('checksum', snapshot_id),
            }
        )

    ranked_docs = list(state.get('ranked_docs', []))
    if not ranked_docs:
        ranked_docs = list(state.get('doc_candidates', []))
    state['event_docs'] = _dedupe_docs(ranked_docs)
    _append_doc_evidence_refs(evidence_refs, state['event_docs'], now_iso)

    for note in state.get('memory_notes', [])[:2]:
        note_id = str(note.get('note_id', '')).strip() or uuid.uuid4().hex[:8]
        evidence_refs.append(
            {
                'evidence_id': f'ev_mem_{note_id}',
                'type': 'MANUAL_NOTE',
                'title': f'memory note {note_id}'[:140],
                'source': 'memory.retrieve',
                'captured_at': note.get('created_at', now_iso),
                'uri': None,
                'snippet': str(note.get('summary', ''))[:240],
                'checksum': note_id,
            }
        )

    if not evidence_refs:
        evidence_refs = [
            {
                'evidence_id': 'ev_fallback_001',
                'type': 'MANUAL_NOTE',
                'title': 'Fallback evidence when facts are unavailable',
                'source': 'fallback',
                'captured_at': now_iso,
                'uri': None,
                'snippet': 'no facts snapshot found',
                'checksum': 'fallback',
            }
        ]

    coverage = _enforce_evidence_coverage(state, evidence_refs)
    if not coverage.get('ok', False):
        _attempt_tier1_coverage_repair(state, evidence_refs, now_iso)
        _enforce_evidence_coverage(state, evidence_refs)
    _refresh_tool_stats(state)
    req = state['request']
    state['context'] = {
        'request': req,
        'features': state['features'],
        'score_id': state.get('score_id'),
        'score': state['score'],
        'price_band_set_id': state.get('price_band_set_id'),
        'price_bands': state['price_bands'],
        'memory_notes': state.get('memory_notes', []),
        'doc_queries': state.get('doc_queries', []),
        'event_docs': state.get('event_docs', []),
        'ranked_doc_ids': state.get('ranked_doc_ids', []),
        'extracted_events': state.get('extracted_events', []),
        'evidence_refs': evidence_refs,
        'evidence_coverage': state.get('evidence_coverage', {}),
        'data_quality': state['data_quality'],
        'snapshot_ids': state['snapshot_ids'],
        'weights_hash': state['weights_hash'],
        'tool_call_stats': state['tool_call_stats'],
        'budget': state.get('budget', {}),
        'router_policy': state.get('config', {}).get('router_policy_version', 'router_m4_v1'),
    }
    return state


def draft_report_node(state: dict) -> dict:
    provider = LLMProvider(primary_model='mock-primary-v1', reviewer_model='NONE')
    state['report_draft'] = provider.generate_report_draft(state['context'])
    return state


def risk_gate_node(state: dict) -> dict:
    gate = execute_tool('risk_gate', {'report': state['report_draft'], 'risk_profile': state['config']['risk_profile']}, risk_gate)
    state['tool_traces'].append(gate['trace'])
    state['risk_gate_hard_blocks'] = gate['output'].get('hard_blocks', [])
    state['report_draft'] = gate['output']['report']
    _refresh_tool_stats(state)
    return state


def validate_node(state: dict) -> dict:
    schema_result = validate_report_schema_tool(state['report_draft'])
    consistency_result = consistency_check(state['report_draft'])

    state['validation_errors'] = schema_result['errors']
    state['consistency_errors'] = consistency_result['errors']
    return state


def _normalize_report_data_quality(report: dict[str, Any]) -> dict[str, Any]:
    data_quality = report.get('data_quality', {})
    if not isinstance(data_quality, dict):
        data_quality = {}
    return {
        'status': str(data_quality.get('status', 'OK')),
        'missing_fields': list(data_quality.get('missing_fields', [])),
        'notes': str(data_quality.get('notes', '')),
    }


def _normalize_report_evidence_refs(report: dict[str, Any]) -> list[dict[str, Any]]:
    refs = report.get('evidence_refs')
    if isinstance(refs, list) and refs:
        return [dict(item) for item in refs if isinstance(item, dict)]
    return []


def _repair_report_evidence_links(report: dict[str, Any], fallback_evidence_id: str) -> None:
    for field_name in ('risk_flags', 'invalidations', 'key_drivers_to_watch'):
        items = report.get(field_name, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            evidence_ids = [str(eid) for eid in item.get('evidence_ids', []) if str(eid).strip()]
            if not evidence_ids:
                item['evidence_ids'] = [fallback_evidence_id]
            else:
                item['evidence_ids'] = evidence_ids


def repair_report_node(state: dict) -> dict:
    state['repair_attempts'] += 1
    report = state['report_draft']
    context_evidence = [dict(item) for item in state.get('context', {}).get('evidence_refs', []) if isinstance(item, dict)]
    report_refs = _normalize_report_evidence_refs(report)
    report_coverage = _coverage_details_for_refs(state, report_refs)
    if context_evidence and (not report_refs or not report_coverage.get('ok', False)):
        report_refs = context_evidence
    if not report_refs:
        report_refs = [
            {
                'evidence_id': 'ev_repair_001',
                'type': 'MANUAL_NOTE',
                'title': 'Repair fallback evidence',
                'source': 'repair',
                'captured_at': datetime.now(timezone.utc).isoformat(),
                'uri': None,
                'snippet': 'added by repair loop',
                'checksum': 'repair',
            }
        ]
    report['evidence_refs'] = report_refs

    primary_evidence_id = str(report['evidence_refs'][0].get('evidence_id', 'ev_repair_001'))
    valid_evidence_ids = {str(ref.get('evidence_id', '')).strip() for ref in report['evidence_refs']}
    valid_evidence_ids.discard('')
    if not report.get('risk_flags'):
        report['risk_flags'] = []
    if not report.get('invalidations'):
        report['invalidations'] = [
            {
                'invalidation_id': 'inv_repair_001',
                'description': 'Repair fallback invalidation',
                'priority': 'HIGH',
                'evidence_ids': [primary_evidence_id],
            }
        ]
    _repair_report_evidence_links(report, primary_evidence_id)
    for field_name in ('risk_flags', 'invalidations', 'key_drivers_to_watch'):
        for item in report.get(field_name, []):
            evidence_ids = [eid for eid in item.get('evidence_ids', []) if eid in valid_evidence_ids]
            item['evidence_ids'] = evidence_ids or [primary_evidence_id]

    repaired_coverage = _coverage_details_for_refs(state, report['evidence_refs'])
    if not repaired_coverage.get('ok', False):
        report_data_quality = _normalize_report_data_quality(report)
        if 'evidence.coverage' not in report_data_quality['missing_fields']:
            report_data_quality['missing_fields'].append('evidence.coverage')
        report_data_quality['status'] = 'PARTIAL' if report_data_quality['status'] == 'OK' else report_data_quality['status']
        report_data_quality['notes'] = (
            f"{report_data_quality['notes']} | repair coverage missing={','.join(repaired_coverage.get('missing_rules', []))}"
        ).strip(' |')
        report['data_quality'] = report_data_quality

    if report.get('data_quality', {}).get('status') != 'OK' and report.get('decision', {}).get('action') == 'BUY':
        report['decision']['action'] = 'WATCH'
        report['decision']['confidence'] = min(report['decision']['confidence'], 0.55)
    state['report_draft'] = report
    return state


def mark_invalid_node(state: dict) -> dict:
    # After max repairs, emit a conservative but schema-valid fallback report.
    provider = LLMProvider(primary_model='mock-primary-v1', reviewer_model='NONE')
    fallback = provider.generate_report_draft(state['context'])
    fallback['decision']['action'] = 'AVOID'
    fallback['decision']['confidence'] = min(float(fallback['decision']['confidence']), 0.45)
    fallback['decision']['summary'] = 'Fallback output: validation failed after max repairs.'
    fallback['data_quality']['status'] = 'DEGRADED'
    fallback['data_quality']['notes'] = 'Fallback output after repair loop exhaustion.'
    state['report_draft'] = fallback
    state['workflow_invalid'] = True
    state['validation_errors'] = []
    state['consistency_errors'] = []
    return state


def persist_node(state: dict) -> dict:
    report = state['report_draft']
    memory_note = {
        'ticker': report['ticker'],
        'summary': report['memory_update']['summary'],
        'tags': report['memory_update']['tags'],
        'importance': report['memory_update']['importance'],
        'links': report['memory_update'].get('followups', []),
        'created_at': report.get('generated_at'),
        'dedupe_key': report.get('report_id'),
    }
    write_memory = execute_tool('write_memory_note', {'note': memory_note}, write_memory_note)
    state['tool_traces'].append(write_memory['trace'])
    _refresh_tool_stats(state)
    report['provenance']['tool_call_stats'] = state['tool_call_stats']
    report['provenance']['router_policy'] = state.get('config', {}).get('router_policy_version', 'router_m4_v1')
    persist_refs = persist_workflow_state(state, thread_id=state['thread_id'])
    persist_refs['memory_note_id'] = write_memory['output'].get('note_id', persist_refs.get('memory_note_id', ''))
    state['persist_refs'] = persist_refs
    state['final_report'] = report
    return state


def publish_node(state: dict) -> dict:
    return state
