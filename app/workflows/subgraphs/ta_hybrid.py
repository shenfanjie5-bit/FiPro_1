from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid


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


def run_ta_hybrid_subgraph(
    *,
    request: dict[str, Any],
    context: dict[str, Any],
    ta_research_rounds: int,
    ta_risk_rounds: int,
    ta_llm_call_cap: int,
) -> dict[str, Any]:
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
    conviction = _clamp(0.35 + (0.42 * abs(directional_bias)) + (0.25 * score_confidence) + coverage_bonus - dq_penalty, 0.0, 1.0)
    risk_bias = _clamp((0.35 * disagreement) + dq_penalty - (0.25 * directional_bias), -1.0, 1.0)

    tier = str(request.get('tier', 'TIER0')).upper()
    horizon_days_hint = _horizon_hint_by_tier(tier)
    used_event_count = int((features.get('event_feature_meta') or {}).get('used_event_count', 0))
    evidence_count = int((coverage.get('actual_total_refs', 0) or 0))
    now_iso = datetime.now(timezone.utc).isoformat()

    views = {
        'bull': {
            'stance': 'POSITIVE' if directional_bias >= 0 else 'NEGATIVE',
            'summary': f'Bull view: directional_bias={directional_bias:.3f}, conviction={conviction:.3f}, events_used={used_event_count}.',
        },
        'bear': {
            'stance': 'NEGATIVE' if directional_bias >= 0 else 'POSITIVE',
            'summary': f'Bear view: disagreement={disagreement:.3f}, dq_status={str(data_quality.get("status", "OK")).upper()}, risk_bias={risk_bias:.3f}.',
        },
        'research_judge': {
            'stance': 'NEUTRAL' if abs(directional_bias) < 0.1 else ('BULL_LEAN' if directional_bias > 0 else 'BEAR_LEAN'),
            'summary': f'Research judge: integrates policy={policy_signal:.3f}, governance={governance_signal:.3f}, evidence_count={evidence_count}.',
        },
        'risk_aggressive': {
            'summary': f'Aggressive risk view: confidence={score_confidence:.3f}, conviction={conviction:.3f}.',
        },
        'risk_conservative': {
            'summary': f'Conservative risk view: dq_penalty={dq_penalty:.3f}, disagreement={disagreement:.3f}.',
        },
        'risk_neutral': {
            'summary': f'Neutral risk view: risk_bias={risk_bias:.3f}, horizon_hint={horizon_days_hint}d.',
        },
        'risk_judge': {
            'summary': 'Risk judge: ANALYZE_ONLY mode, no execution-level override.',
        },
    }

    evidence_refs: list[dict[str, Any]] = [
        {
            'evidence_id': f"ev_ta_{uuid.uuid4().hex[:10]}",
            'type': 'AGENT_REASONING',
            'title': 'ta_hybrid research synthesis',
            'source': 'ta_hybrid.research',
            'captured_at': now_iso,
            'uri': None,
            'snippet': views['research_judge']['summary'][:240],
            'checksum': f"ta_research_{request.get('ticker', '')}_{request.get('asof', '')}",
        },
        {
            'evidence_id': f"ev_ta_{uuid.uuid4().hex[:10]}",
            'type': 'AGENT_REASONING',
            'title': 'ta_hybrid risk synthesis',
            'source': 'ta_hybrid.risk',
            'captured_at': now_iso,
            'uri': None,
            'snippet': views['risk_judge']['summary'][:240],
            'checksum': f"ta_risk_{request.get('ticker', '')}_{request.get('asof', '')}",
        },
    ]

    return {
        'state': {
            'mode': str(request.get('ta_hybrid_mode', 'OFF')).upper() or 'OFF',
            'status': 'ANALYZED',
            'applied': False,
            'version': 'ta_hybrid_m2_v1',
            'require_evidence_refs': bool(request.get('ta_require_evidence_refs', True)),
            'research_rounds_used': max(1, min(3, int(ta_research_rounds))),
            'risk_rounds_used': max(1, min(3, int(ta_risk_rounds))),
            'llm_calls_used': 0,
            'llm_call_cap': max(0, min(20, int(ta_llm_call_cap))),
            'degraded_reasons': [],
        },
        'views': views,
        'signal': {
            'directional_bias': round(directional_bias, 6),
            'risk_bias': round(risk_bias, 6),
            'conviction': round(conviction, 6),
            'disagreement': round(disagreement, 6),
            'horizon_days_hint': horizon_days_hint,
        },
        'evidence_refs': evidence_refs,
    }
