from __future__ import annotations

from datetime import datetime, timezone
import uuid

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
from app.workflows.persistence import persist_workflow_state


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
    state['tool_call_stats'] = {'tool_calls': 0, 'latency_ms': 0, 'cost_usd_est': 0}
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
        'feature_id': f"feat_{uuid.uuid4().hex[:12]}",
        'hotness': flow.get('hotness_score', 50),
        'fundamentals': int(min(100, funda.get('roe', 0.1) * 400)),
        'volatility': int(min(100, market.get('volatility_20d', 0.2) * 200)),
        'liquidity': int(min(100, market.get('volume_ratio', 1.0) * 60)),
    }
    state['feature_id'] = state['features']['feature_id']
    return state


def score_signal_node(state: dict) -> dict:
    weights = state['config']['weights']
    res = execute_tool('score_signal', {'features': state['features'], 'weights': weights}, score_signal)
    state['tool_traces'].append(res['trace'])
    state['score'] = res['output']
    state['score_id'] = res['output']['score_id']
    return state


def generate_price_bands_node(state: dict) -> dict:
    base_price = state['snapshots'].get('get_market_snapshot', {}).get('close', 100.0)
    res = execute_tool('generate_price_bands', {'base_price': base_price, 'score': state['score']['overall_score']}, generate_price_bands)
    state['tool_traces'].append(res['trace'])
    state['price_band_set_id'] = res['output']['price_band_set_id']
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
    if res['ok']:
        state['memory_notes'] = res['output'].get('notes', [])
    else:
        state['memory_notes'] = []
        state['data_quality']['status'] = 'DEGRADED'
        state['data_quality']['notes'] = 'Memory retrieval failed'
    return state


def build_context(state: dict) -> dict:
    evidence_refs = []
    for tool_name, snapshot in state.get('snapshots', {}).items():
        snapshot_id = snapshot.get('snapshot_id', 'snap_unknown')
        evidence_refs.append(
            {
                'evidence_id': f"ev_{snapshot_id}",
                'type': 'SNAPSHOT_FIELD',
                'title': f'{tool_name} summary',
                'source': f'facts.{tool_name}',
                'captured_at': snapshot.get('captured_at', datetime.now(timezone.utc).isoformat()),
                'uri': None,
                'snippet': f"snapshot_id={snapshot_id}",
                'checksum': snapshot_id,
            }
        )

    if not evidence_refs:
        evidence_refs = [
            {
                'evidence_id': 'ev_fallback_001',
                'type': 'MANUAL_NOTE',
                'title': 'Fallback evidence when facts are unavailable',
                'source': 'fallback',
                'captured_at': datetime.now(timezone.utc).isoformat(),
                'uri': None,
                'snippet': 'no facts snapshot found',
                'checksum': 'fallback',
            }
        ]

    tool_call_stats = {
        'tool_calls': len(state.get('tool_traces', [])),
        'latency_ms': sum(t['latency_ms'] for t in state.get('tool_traces', [])),
        'cost_usd_est': 0,
    }
    state['tool_call_stats'] = tool_call_stats

    state['context'] = {
        'request': state['request'],
        'features': state['features'],
        'score_id': state.get('score_id'),
        'score': state['score'],
        'price_band_set_id': state.get('price_band_set_id'),
        'price_bands': state['price_bands'],
        'memory_notes': state.get('memory_notes', []),
        'evidence_refs': evidence_refs,
        'data_quality': state['data_quality'],
        'snapshot_ids': state['snapshot_ids'],
        'weights_hash': state['weights_hash'],
        'tool_call_stats': tool_call_stats,
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
    return state


def validate_node(state: dict) -> dict:
    schema_result = validate_report_schema_tool(state['report_draft'])
    consistency_result = consistency_check(state['report_draft'])

    state['validation_errors'] = schema_result['errors']
    state['consistency_errors'] = consistency_result['errors']
    return state


def repair_report_node(state: dict) -> dict:
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
                'uri': None,
                'snippet': 'added by repair loop',
                'checksum': 'repair',
            }
        ]
    if not report.get('risk_flags'):
        report['risk_flags'] = []
    if not report.get('invalidations'):
        report['invalidations'] = [
            {
                'invalidation_id': 'inv_repair_001',
                'description': 'Repair fallback invalidation',
                'priority': 'HIGH',
                'evidence_ids': [report['evidence_refs'][0]['evidence_id']],
            }
        ]
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
    }
    write_memory = execute_tool('write_memory_note', {'note': memory_note}, write_memory_note)
    state['tool_traces'].append(write_memory['trace'])
    state['tool_call_stats'] = {
        'tool_calls': len(state.get('tool_traces', [])),
        'latency_ms': sum(t['latency_ms'] for t in state.get('tool_traces', [])),
        'cost_usd_est': 0,
    }
    report['provenance']['tool_call_stats'] = state['tool_call_stats']
    persist_refs = persist_workflow_state(state, thread_id=state['thread_id'])
    persist_refs['memory_note_id'] = write_memory['output'].get('note_id', persist_refs.get('memory_note_id', ''))
    state['persist_refs'] = persist_refs
    state['final_report'] = report
    return state


def publish_node(state: dict) -> dict:
    return state
