from __future__ import annotations

from datetime import datetime, timezone

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
from app.tools.wrapper import execute_tool


def load_strategy_config(state: dict) -> dict:
    # TODO: replace with DB-backed strategy config loading.
    state['config'] = {
        'weights': {
            'hotness': 0.25,
            'fundamental': 0.30,
            'volatility': 0.20,
            'liquidity': 0.15,
            'event_impact': 0.10,
        },
        'risk_profile': 'LOW',
    }
    state['weights_hash'] = 'w_mock_hash_v1'
    return state


def init_data_quality(state: dict) -> dict:
    state['data_quality'] = {'status': 'OK', 'missing_fields': [], 'notes': ''}
    state['tool_traces'] = []
    state['repair_attempts'] = 0
    state['max_repairs'] = 2
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
            snapshot_ids.append(result['output']['snapshot_id'])
        else:
            state['data_quality']['status'] = 'DEGRADED'
            state['data_quality']['notes'] = 'One or more facts tools failed'

    if req['tier'] in ('TIER1', 'TIER2'):
        macro = execute_tool(
            'get_macro_commodity_logistics_snapshot',
            {'ticker': ticker, 'asof': asof},
            get_macro_commodity_logistics_snapshot,
        )
        state['tool_traces'].append(macro['trace'])
        if macro['ok']:
            snapshots['get_macro_commodity_logistics_snapshot'] = macro['output']
            snapshot_ids.append(macro['output']['snapshot_id'])

    state['snapshots'] = snapshots
    state['snapshot_ids'] = snapshot_ids
    return state


def build_features(state: dict) -> dict:
    market = state['snapshots'].get('get_market_snapshot', {})
    funda = state['snapshots'].get('get_fundamentals_snapshot', {})
    flow = state['snapshots'].get('get_flow_sentiment_snapshot', {})

    state['features'] = {
        'hotness': flow.get('hotness_score', 50),
        'fundamentals': int(min(100, funda.get('roe', 0.1) * 400)),
        'volatility': int(min(100, market.get('volatility_20d', 0.2) * 200)),
        'liquidity': int(min(100, market.get('volume_ratio', 1.0) * 60)),
    }
    return state


def score_signal_node(state: dict) -> dict:
    weights = state['config']['weights']
    res = execute_tool('score_signal', {'features': state['features'], 'weights': weights}, score_signal)
    state['tool_traces'].append(res['trace'])
    state['score'] = res['output']
    return state


def generate_price_bands_node(state: dict) -> dict:
    base_price = state['snapshots'].get('get_market_snapshot', {}).get('close', 100.0)
    res = execute_tool('generate_price_bands', {'base_price': base_price, 'score': state['score']['overall_score']}, generate_price_bands)
    state['tool_traces'].append(res['trace'])
    state['price_bands'] = res['output']['price_bands']
    return state


def retrieve_memory_node(state: dict) -> dict:
    req = state['request']
    res = execute_tool(
        'retrieve_memory_notes',
        {'ticker': req['ticker'], 'query': 'latest thesis', 'top_k': 5, 'time_range': None},
        retrieve_memory_notes,
    )
    state['tool_traces'].append(res['trace'])
    state['memory_notes'] = res['output'].get('notes', [])
    return state


def build_context(state: dict) -> dict:
    evidence_refs = [
        {
            'evidence_id': 'ev_snapshot_001',
            'type': 'SNAPSHOT_FIELD',
            'title': 'Market snapshot close and volatility',
            'source': 'facts.market',
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'uri': None,
            'snippet': 'close=100.0 volatility_20d=0.18',
            'checksum': 'chk_mock_001',
        }
    ]

    state['context'] = {
        'request': state['request'],
        'score': state['score'],
        'price_bands': state['price_bands'],
        'memory_notes': state.get('memory_notes', []),
        'evidence_refs': evidence_refs,
        'data_quality': state['data_quality'],
        'snapshot_ids': state['snapshot_ids'],
        'weights_hash': state['weights_hash'],
        'tool_call_stats': {
            'tool_calls': len(state.get('tool_traces', [])),
            'latency_ms': sum(t['latency_ms'] for t in state.get('tool_traces', [])),
            'cost_usd_est': 0,
        },
    }
    return state


def draft_report_node(state: dict) -> dict:
    provider = LLMProvider(primary_model='mock-primary-v1', reviewer_model='NONE')
    state['report_draft'] = provider.generate_report_draft(state['context'])
    return state


def risk_gate_node(state: dict) -> dict:
    gate = execute_tool('risk_gate', {'report': state['report_draft'], 'risk_profile': state['config']['risk_profile']}, risk_gate)
    state['tool_traces'].append(gate['trace'])
    state['report_draft'] = gate['output']['report']
    return state


def validate_node(state: dict) -> dict:
    schema_result = validate_report_schema_tool(state['report_draft'])
    consistency_result = consistency_check(state['report_draft'])

    state['validation_errors'] = schema_result['errors']
    state['consistency_errors'] = consistency_result['errors']
    return state


def repair_report_node(state: dict) -> dict:
    # TODO: replace with model-based minimal patching by validation errors.
    state['repair_attempts'] += 1
    report = state['report_draft']
    if not report.get('evidence_refs'):
        report['evidence_refs'] = [
            {
                'evidence_id': 'ev_repair_001',
                'type': 'MANUAL_NOTE',
                'title': 'Repair fallback evidence',
                'source': 'repair',
                'captured_at': datetime.now(timezone.utc).isoformat(),
            }
        ]
    if report.get('data_quality', {}).get('status') != 'OK' and report['decision']['action'] == 'BUY':
        report['decision']['action'] = 'WATCH'
        report['decision']['confidence'] = min(report['decision']['confidence'], 0.55)
    state['report_draft'] = report
    return state


def persist_node(state: dict) -> dict:
    report = state['report_draft']
    memory_note = {
        'ticker': report['ticker'],
        'summary': report['memory_update']['summary'],
        'tags': report['memory_update']['tags'],
        'importance': report['memory_update']['importance'],
    }
    execute_tool('write_memory_note', {'note': memory_note}, write_memory_note)

    # TODO: persist report/decision/tool traces to Postgres.
    state['final_report'] = report
    return state


def publish_node(state: dict) -> dict:
    return state
