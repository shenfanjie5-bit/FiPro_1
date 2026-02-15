from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from app.llm.provider import LLMProvider


TA_HYBRID_VERSION = 'ta_hybrid_m2_v1'


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _quality_penalty(data_quality_status: str) -> float:
    normalized = str(data_quality_status or 'OK').upper()
    if normalized == 'OK':
        return 0.0
    if normalized == 'PARTIAL':
        return 0.12
    return 0.28


def _horizon_hint_by_tier(tier: str) -> int:
    normalized = str(tier or 'TIER0').upper()
    if normalized == 'TIER2':
        return 10
    if normalized == 'TIER1':
        return 7
    return 5


def _normalize_view_payload(view: Any, *, fallback_summary: str) -> dict[str, Any]:
    payload = view if isinstance(view, dict) else {}
    summary = str(payload.get('summary', '')).strip() or fallback_summary
    return {
        'summary': summary[:320],
        'stance': str(payload.get('stance', 'NEUTRAL')).strip().upper()[:32] or 'NEUTRAL',
        'directional_bias': _clamp(_safe_float(payload.get('directional_bias'), 0.0), -1.0, 1.0),
        'risk_bias': _clamp(_safe_float(payload.get('risk_bias'), 0.0), -1.0, 1.0),
        'conviction': _clamp(_safe_float(payload.get('conviction'), 0.0), 0.0, 1.0),
        'disagreement': _clamp(_safe_float(payload.get('disagreement'), 0.0), 0.0, 1.0),
        'horizon_days_hint': max(1, min(120, int(_safe_float(payload.get('horizon_days_hint'), 5)))),
        'rationale_points': [
            str(item).strip()[:120]
            for item in payload.get('rationale_points', [])
            if str(item).strip()
        ][:6],
    }


def _fallback_view(
    *,
    ta_input: dict[str, Any],
    stage: str,
    role: str,
    upstream: dict[str, Any] | None = None,
    round_idx: int = 1,
) -> dict[str, Any]:
    upstream_payload = upstream if isinstance(upstream, dict) else {}
    directional_base = _clamp(_safe_float(ta_input.get('directional_bias_base'), 0.0), -1.0, 1.0)
    risk_base = _clamp(_safe_float(ta_input.get('risk_bias_base'), 0.0), -1.0, 1.0)
    conviction_base = _clamp(_safe_float(ta_input.get('conviction_base'), 0.0), 0.0, 1.0)
    disagreement_base = _clamp(_safe_float(ta_input.get('disagreement_base'), 0.0), 0.0, 1.0)
    horizon_hint = max(1, min(120, int(_safe_float(ta_input.get('horizon_days_hint'), 5))))

    directional = directional_base
    risk_bias = risk_base
    conviction = conviction_base
    disagreement = disagreement_base
    stance = 'NEUTRAL'

    role_key = f'{stage}:{role}'
    if role_key == 'research:bull':
        directional = _clamp(directional_base + 0.12, -1.0, 1.0)
        conviction = _clamp(conviction_base + 0.08, 0.0, 1.0)
        stance = 'BULLISH'
    elif role_key == 'research:bear':
        directional = _clamp(directional_base - 0.12, -1.0, 1.0)
        conviction = _clamp(conviction_base - 0.08, 0.0, 1.0)
        risk_bias = _clamp(risk_base + 0.08, -1.0, 1.0)
        stance = 'BEARISH'
    elif role_key == 'research_judge:judge':
        bull = upstream_payload.get('bull', {}) if isinstance(upstream_payload.get('bull', {}), dict) else {}
        bear = upstream_payload.get('bear', {}) if isinstance(upstream_payload.get('bear', {}), dict) else {}
        directional = _clamp(
            (_safe_float(bull.get('directional_bias'), directional_base) + _safe_float(bear.get('directional_bias'), directional_base)) / 2.0,
            -1.0,
            1.0,
        )
        disagreement = _clamp(
            abs(_safe_float(bull.get('directional_bias'), directional_base) - _safe_float(bear.get('directional_bias'), directional_base)) / 2.0,
            0.0,
            1.0,
        )
        conviction = _clamp(conviction_base - (0.12 * disagreement), 0.0, 1.0)
        stance = 'BULL_LEAN' if directional > 0.1 else ('BEAR_LEAN' if directional < -0.1 else 'NEUTRAL')
    elif role_key == 'risk:aggressive':
        risk_bias = _clamp(risk_base - 0.12, -1.0, 1.0)
        conviction = _clamp(conviction_base + 0.06, 0.0, 1.0)
        stance = 'RISK_ON'
    elif role_key == 'risk:conservative':
        risk_bias = _clamp(risk_base + 0.12, -1.0, 1.0)
        conviction = _clamp(conviction_base - 0.08, 0.0, 1.0)
        stance = 'RISK_OFF'
    elif role_key == 'risk:neutral':
        stance = 'BALANCED'
    elif role_key == 'risk_judge:judge':
        aggr = upstream_payload.get('risk_aggressive', {}) if isinstance(upstream_payload.get('risk_aggressive', {}), dict) else {}
        cons = upstream_payload.get('risk_conservative', {}) if isinstance(upstream_payload.get('risk_conservative', {}), dict) else {}
        neu = upstream_payload.get('risk_neutral', {}) if isinstance(upstream_payload.get('risk_neutral', {}), dict) else {}
        risk_bias = _clamp(
            (
                _safe_float(aggr.get('risk_bias'), risk_base)
                + _safe_float(cons.get('risk_bias'), risk_base)
                + _safe_float(neu.get('risk_bias'), risk_base)
            )
            / 3.0,
            -1.0,
            1.0,
        )
        conviction = _clamp(
            (
                _safe_float(aggr.get('conviction'), conviction_base)
                + _safe_float(cons.get('conviction'), conviction_base)
                + _safe_float(neu.get('conviction'), conviction_base)
            )
            / 3.0,
            0.0,
            1.0,
        )
        stance = 'CAUTIOUS' if risk_bias > 0.2 else ('OPEN' if risk_bias < -0.2 else 'NEUTRAL')

    return {
        'summary': (
            f'{stage}.{role} round={max(1, int(round_idx))}: '
            f'directional_bias={directional:.3f}, risk_bias={risk_bias:.3f}, '
            f'conviction={conviction:.3f}, disagreement={disagreement:.3f}, horizon={horizon_hint}d.'
        ),
        'stance': stance,
        'directional_bias': directional,
        'risk_bias': risk_bias,
        'conviction': conviction,
        'disagreement': disagreement,
        'horizon_days_hint': horizon_hint,
        'rationale_points': [
            f'round={max(1, int(round_idx))}',
            f'policy_signal={_safe_float(ta_input.get("policy_signal"), 0.0):.3f}',
            f'governance_signal={_safe_float(ta_input.get("governance_signal"), 0.0):.3f}',
        ],
    }


def ta_prepare(state: dict[str, Any]) -> dict[str, Any]:
    request = state['request']
    context = state['context']
    if not isinstance(context, dict) or not context:
        state['status'] = 'SKIPPED'
        _append_reason(state, 'ta_prepare skipped: missing context payload')
        context = {}
    features = context.get('features', {}) if isinstance(context.get('features', {}), dict) else {}
    score = context.get('score', {}) if isinstance(context.get('score', {}), dict) else {}
    coverage = context.get('evidence_coverage', {}) if isinstance(context.get('evidence_coverage', {}), dict) else {}
    data_quality = context.get('data_quality', {}) if isinstance(context.get('data_quality', {}), dict) else {}

    policy_signal = _clamp(_safe_float(features.get('event_policy_signal'), 0.0), -1.0, 1.0)
    governance_signal = _clamp(_safe_float(features.get('event_governance_signal'), 0.0), -1.0, 1.0)
    directional_bias = _clamp((0.65 * policy_signal) + (0.35 * governance_signal), -1.0, 1.0)
    disagreement = _clamp(abs(policy_signal - governance_signal) / 2.0, 0.0, 1.0)
    score_confidence = _clamp(_safe_float(score.get('confidence'), 0.5), 0.0, 1.0)
    dq_penalty = _quality_penalty(str(data_quality.get('status', 'OK')))
    coverage_bonus = 0.08 if bool(coverage.get('ok', False)) else -0.04
    conviction = _clamp(
        0.35 + (0.42 * abs(directional_bias)) + (0.25 * score_confidence) + coverage_bonus - dq_penalty,
        0.0,
        1.0,
    )
    risk_bias = _clamp((0.35 * disagreement) + dq_penalty - (0.25 * directional_bias), -1.0, 1.0)
    tier = str(request.get('tier', 'TIER0')).upper()
    horizon_days_hint = _horizon_hint_by_tier(tier)
    used_event_count = int((features.get('event_feature_meta') or {}).get('used_event_count', 0))
    evidence_count = int((coverage.get('actual_total_refs', 0) or 0))

    state['ta_input'] = {
        'ticker': str(request.get('ticker', '')),
        'asof': str(request.get('asof', '')),
        'run_mode': str(request.get('run_mode', 'LIVE')).upper() or 'LIVE',
        'tier': tier,
        'analysis_mode': str(request.get('analysis_mode', 'BASELINE')).upper() or 'BASELINE',
        'ta_hybrid_mode': str(request.get('ta_hybrid_mode', 'OFF')).upper() or 'OFF',
        'policy_signal': round(policy_signal, 6),
        'governance_signal': round(governance_signal, 6),
        'directional_bias_base': round(directional_bias, 6),
        'risk_bias_base': round(risk_bias, 6),
        'conviction_base': round(conviction, 6),
        'disagreement_base': round(disagreement, 6),
        'score_confidence': round(score_confidence, 6),
        'data_quality_status': str(data_quality.get('status', 'OK')).upper(),
        'horizon_days_hint': horizon_days_hint,
        'used_event_count': used_event_count,
        'evidence_count': evidence_count,
    }
    return state


def _append_reason(state: dict[str, Any], reason: str) -> None:
    message = str(reason).strip()
    if not message:
        return
    reasons = [str(item) for item in state.get('degraded_reasons', []) if str(item).strip()]
    if message not in reasons:
        reasons.append(message)
    state['degraded_reasons'] = reasons[:8]


def _consume_llm_call(state: dict[str, Any]) -> bool:
    if state.get('provider_mode', 'mock') == 'mock':
        return True
    used = int(state.get('llm_calls_used', 0))
    cap = int(state.get('llm_call_cap', 0))
    if cap >= 0 and used >= cap:
        return False
    state['llm_calls_used'] = used + 1
    return True


def _run_view_node(
    state: dict[str, Any],
    *,
    stage: str,
    role: str,
    round_idx: int,
    upstream: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider: LLMProvider = state['provider']
    ta_input = state.get('ta_input', {})
    fallback = _fallback_view(ta_input=ta_input, stage=stage, role=role, upstream=upstream, round_idx=round_idx)
    if not _consume_llm_call(state):
        _append_reason(state, f'{stage}.{role} skipped: llm_call_cap reached')
        return _normalize_view_payload(fallback, fallback_summary=fallback['summary'])
    try:
        output = provider.generate_ta_hybrid_view(
            stage=stage,
            role=role,
            ta_input=ta_input,
            upstream=upstream,
            round_idx=round_idx,
        )
        return _normalize_view_payload(output, fallback_summary=fallback['summary'])
    except Exception as exc:  # noqa: BLE001
        _append_reason(state, f'{stage}.{role} degraded: {exc}')
        return _normalize_view_payload(fallback, fallback_summary=fallback['summary'])


def ta_bull(state: dict[str, Any], *, round_idx: int) -> dict[str, Any]:
    state['views']['bull'] = _run_view_node(state, stage='research', role='bull', round_idx=round_idx)
    return state


def ta_bear(state: dict[str, Any], *, round_idx: int) -> dict[str, Any]:
    state['views']['bear'] = _run_view_node(state, stage='research', role='bear', round_idx=round_idx)
    return state


def ta_research_judge(state: dict[str, Any], *, round_idx: int) -> dict[str, Any]:
    upstream = {
        'bull': state['views'].get('bull', {}),
        'bear': state['views'].get('bear', {}),
    }
    state['views']['research_judge'] = _run_view_node(
        state,
        stage='research_judge',
        role='judge',
        round_idx=round_idx,
        upstream=upstream,
    )
    return state


def ta_risk_aggressive(state: dict[str, Any], *, round_idx: int) -> dict[str, Any]:
    upstream = {'research_judge': state['views'].get('research_judge', {})}
    state['views']['risk_aggressive'] = _run_view_node(
        state,
        stage='risk',
        role='aggressive',
        round_idx=round_idx,
        upstream=upstream,
    )
    return state


def ta_risk_conservative(state: dict[str, Any], *, round_idx: int) -> dict[str, Any]:
    upstream = {'research_judge': state['views'].get('research_judge', {})}
    state['views']['risk_conservative'] = _run_view_node(
        state,
        stage='risk',
        role='conservative',
        round_idx=round_idx,
        upstream=upstream,
    )
    return state


def ta_risk_neutral(state: dict[str, Any], *, round_idx: int) -> dict[str, Any]:
    upstream = {'research_judge': state['views'].get('research_judge', {})}
    state['views']['risk_neutral'] = _run_view_node(
        state,
        stage='risk',
        role='neutral',
        round_idx=round_idx,
        upstream=upstream,
    )
    return state


def ta_risk_judge(state: dict[str, Any], *, round_idx: int) -> dict[str, Any]:
    upstream = {
        'risk_aggressive': state['views'].get('risk_aggressive', {}),
        'risk_conservative': state['views'].get('risk_conservative', {}),
        'risk_neutral': state['views'].get('risk_neutral', {}),
    }
    state['views']['risk_judge'] = _run_view_node(
        state,
        stage='risk_judge',
        role='judge',
        round_idx=round_idx,
        upstream=upstream,
    )
    return state


def ta_synthesize(state: dict[str, Any]) -> dict[str, Any]:
    ta_input = state.get('ta_input', {})
    views = state.get('views', {})
    research_judge = views.get('research_judge', {}) if isinstance(views.get('research_judge', {}), dict) else {}
    risk_judge = views.get('risk_judge', {}) if isinstance(views.get('risk_judge', {}), dict) else {}
    directional_bias = _clamp(
        _safe_float(research_judge.get('directional_bias'), _safe_float(ta_input.get('directional_bias_base'), 0.0)),
        -1.0,
        1.0,
    )
    risk_bias = _clamp(
        _safe_float(risk_judge.get('risk_bias'), _safe_float(ta_input.get('risk_bias_base'), 0.0)),
        -1.0,
        1.0,
    )
    conviction = _clamp(
        (
            _safe_float(research_judge.get('conviction'), _safe_float(ta_input.get('conviction_base'), 0.0))
            + _safe_float(risk_judge.get('conviction'), _safe_float(ta_input.get('conviction_base'), 0.0))
        )
        / 2.0,
        0.0,
        1.0,
    )
    disagreement = _clamp(
        (
            _safe_float(research_judge.get('disagreement'), _safe_float(ta_input.get('disagreement_base'), 0.0))
            + _safe_float(risk_judge.get('disagreement'), _safe_float(ta_input.get('disagreement_base'), 0.0))
        )
        / 2.0,
        0.0,
        1.0,
    )
    horizon_days_hint = int(
        max(
            1,
            min(
                120,
                int(
                    _safe_float(
                        risk_judge.get('horizon_days_hint'),
                        _safe_float(research_judge.get('horizon_days_hint'), _safe_float(ta_input.get('horizon_days_hint'), 5)),
                    )
                ),
            ),
        )
    )
    state['signal'] = {
        'directional_bias': round(directional_bias, 6),
        'risk_bias': round(risk_bias, 6),
        'conviction': round(conviction, 6),
        'disagreement': round(disagreement, 6),
        'horizon_days_hint': horizon_days_hint,
    }
    now_iso = datetime.now(timezone.utc).isoformat()
    ticker = str(ta_input.get('ticker', ''))
    asof = str(ta_input.get('asof', ''))
    state['evidence_refs'] = [
        {
            'evidence_id': f"ev_ta_{uuid.uuid4().hex[:10]}",
            'type': 'AGENT_REASONING',
            'title': 'ta_hybrid research synthesis',
            'source': 'ta_hybrid.research',
            'captured_at': now_iso,
            'uri': None,
            'snippet': str(research_judge.get('summary', ''))[:240],
            'checksum': f'ta_research_{ticker}_{asof}',
        },
        {
            'evidence_id': f"ev_ta_{uuid.uuid4().hex[:10]}",
            'type': 'AGENT_REASONING',
            'title': 'ta_hybrid risk synthesis',
            'source': 'ta_hybrid.risk',
            'captured_at': now_iso,
            'uri': None,
            'snippet': str(risk_judge.get('summary', ''))[:240],
            'checksum': f'ta_risk_{ticker}_{asof}',
        },
    ]
    return state


def run_ta_hybrid_subgraph(
    *,
    request: dict[str, Any],
    context: dict[str, Any],
    ta_research_rounds: int,
    ta_risk_rounds: int,
    ta_llm_call_cap: int,
) -> dict[str, Any]:
    research_rounds = max(1, min(3, int(ta_research_rounds)))
    risk_rounds = max(1, min(3, int(ta_risk_rounds)))
    llm_call_cap = max(0, min(20, int(ta_llm_call_cap)))
    provider = LLMProvider()
    provider_mode = str(getattr(provider, 'provider', 'mock') or 'mock').strip().lower() or 'mock'

    substate: dict[str, Any] = {
        'request': request if isinstance(request, dict) else {},
        'context': context if isinstance(context, dict) else {},
        'provider': provider,
        'provider_mode': provider_mode,
        'ta_input': {},
        'views': {},
        'signal': {},
        'evidence_refs': [],
        'degraded_reasons': [],
        'llm_calls_used': 0,
        'llm_call_cap': llm_call_cap,
        'status': 'ANALYZED',
        'research_rounds_used': research_rounds,
        'risk_rounds_used': risk_rounds,
    }

    ta_prepare(substate)
    if str(substate.get('status', 'ANALYZED')).upper() == 'SKIPPED':
        signal = {
            'directional_bias': 0.0,
            'risk_bias': 0.0,
            'conviction': 0.0,
            'disagreement': 0.0,
            'horizon_days_hint': _horizon_hint_by_tier(str(request.get('tier', 'TIER0'))),
        }
        return {
            'state': {
                'mode': str(request.get('ta_hybrid_mode', 'OFF')).upper() or 'OFF',
                'status': 'SKIPPED',
                'applied': False,
                'version': TA_HYBRID_VERSION,
                'require_evidence_refs': bool(request.get('ta_require_evidence_refs', True)),
                'research_rounds_used': 0,
                'risk_rounds_used': 0,
                'llm_calls_used': 0,
                'llm_call_cap': llm_call_cap,
                'degraded_reasons': [str(item) for item in substate.get('degraded_reasons', []) if str(item).strip()][:8],
            },
            'views': {
                key: _normalize_view_payload({}, fallback_summary=f'ta_hybrid.{key} skipped')
                for key in (
                    'bull',
                    'bear',
                    'research_judge',
                    'risk_aggressive',
                    'risk_conservative',
                    'risk_neutral',
                    'risk_judge',
                )
            },
            'signal': signal,
            'evidence_refs': [],
        }
    for round_idx in range(1, research_rounds + 1):
        ta_bull(substate, round_idx=round_idx)
        ta_bear(substate, round_idx=round_idx)
        ta_research_judge(substate, round_idx=round_idx)
    for round_idx in range(1, risk_rounds + 1):
        ta_risk_aggressive(substate, round_idx=round_idx)
        ta_risk_conservative(substate, round_idx=round_idx)
        ta_risk_neutral(substate, round_idx=round_idx)
        ta_risk_judge(substate, round_idx=round_idx)
    ta_synthesize(substate)

    required_view_ids = (
        'bull',
        'bear',
        'research_judge',
        'risk_aggressive',
        'risk_conservative',
        'risk_neutral',
        'risk_judge',
    )
    views = {}
    for key in required_view_ids:
        view_payload = substate.get('views', {}).get(key, {})
        views[key] = _normalize_view_payload(
            view_payload,
            fallback_summary=f'ta_hybrid.{key} fallback',
        )

    return {
        'state': {
            'mode': str(request.get('ta_hybrid_mode', 'OFF')).upper() or 'OFF',
            'status': str(substate.get('status', 'ANALYZED')),
            'applied': False,
            'version': TA_HYBRID_VERSION,
            'require_evidence_refs': bool(request.get('ta_require_evidence_refs', True)),
            'research_rounds_used': int(substate.get('research_rounds_used', research_rounds)),
            'risk_rounds_used': int(substate.get('risk_rounds_used', risk_rounds)),
            'llm_calls_used': int(substate.get('llm_calls_used', 0)),
            'llm_call_cap': llm_call_cap,
            'degraded_reasons': [str(item) for item in substate.get('degraded_reasons', []) if str(item).strip()][:8],
        },
        'views': views,
        'signal': substate.get('signal', {}),
        'evidence_refs': substate.get('evidence_refs', []),
    }
