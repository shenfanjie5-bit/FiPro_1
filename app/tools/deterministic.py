from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def _stable_id(prefix: str, payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:10]}"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _map_to_unit(value: Any, minimum: float, maximum: float, *, default: float = 0.0) -> float:
    parsed = _safe_float(value, default=float('nan'))
    if math.isnan(parsed):
        return default
    if maximum <= minimum:
        return default
    ratio = (parsed - minimum) / (maximum - minimum)
    return _clamp((ratio * 2.0) - 1.0, -1.0, 1.0)


def _nested(snapshot: dict[str, Any], path: str) -> Any:
    current: Any = snapshot
    for token in path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(token)
    return current


def _factor_value(
    factor_id: str,
    *,
    features: dict[str, Any],
    snapshots: dict[str, Any],
    default_value: float,
) -> tuple[float, bool]:
    market = snapshots.get('get_market_snapshot', {}) if isinstance(snapshots, dict) else {}
    fundamentals = snapshots.get('get_fundamentals_snapshot', {}) if isinstance(snapshots, dict) else {}
    flow = snapshots.get('get_flow_sentiment_snapshot', {}) if isinstance(snapshots, dict) else {}

    if factor_id == 'price.momentum_20d':
        value = _nested(market, 'returns.m1')
        if value is None:
            value = _safe_float(_nested(market, 'returns.d1'), 0.0) * 20.0
        if value is None:
            return default_value, True
        return _map_to_unit(value, -0.3, 0.3, default=default_value), False

    if factor_id == 'price.momentum_60d':
        ma20 = _nested(market, 'trend.ma_20')
        ma60 = _nested(market, 'trend.ma_60')
        if ma20 is not None and ma60 is not None and _safe_float(ma60, 0.0) > 0:
            ratio = (_safe_float(ma20) / _safe_float(ma60)) - 1.0
            return _map_to_unit(ratio, -0.2, 0.2, default=default_value), False
        fallback = _nested(market, 'returns.w1')
        if fallback is None:
            return default_value, True
        return _map_to_unit(fallback, -0.2, 0.2, default=default_value), False

    if factor_id == 'price.volatility_20d':
        value = _nested(market, 'volatility_20d')
        if value is None:
            value = _nested(market, 'volatility.stdev_20')
        if value is None:
            return default_value, True
        return _map_to_unit(value, 0.05, 0.6, default=default_value), False

    if factor_id == 'valuation.pe_percentile':
        value = _nested(fundamentals, 'valuation.pe_ttm')
        if value is None:
            value = fundamentals.get('pe_ttm') if isinstance(fundamentals, dict) else None
        if value is None:
            return default_value, True
        return _map_to_unit(value, 5.0, 80.0, default=default_value), False

    if factor_id == 'flow.moneyflow_5d':
        value = _nested(flow, 'sentiment.polarity')
        if value is None:
            value = flow.get('northbound_flow') if isinstance(flow, dict) else None
            if value is not None:
                value = _map_to_unit(value, -20.0, 20.0, default=default_value)
                return value, False
            return default_value, True
        return _clamp(_safe_float(value, default_value), -1.0, 1.0), False

    if factor_id == 'flow.block_trade_net':
        value = _nested(flow, 'flows.main_force_net')
        if value is None:
            return default_value, True
        return _map_to_unit(value, -20.0, 20.0, default=default_value), False

    if factor_id == 'fundamental.roe_quality':
        value = _nested(fundamentals, 'quality.roe')
        if value is None:
            value = fundamentals.get('roe') if isinstance(fundamentals, dict) else None
        if value is None:
            return default_value, True
        return _map_to_unit(value, 0.0, 0.30, default=default_value), False

    if factor_id == 'risk.leverage_pressure':
        value = _nested(fundamentals, 'quality.debt_to_assets')
        if value is None:
            return default_value, True
        return _map_to_unit(value, 0.1, 1.0, default=default_value), False

    if factor_id == 'event.policy_signal':
        event_value = features.get('event_policy_signal')
        if event_value is None:
            return default_value, True
        return _clamp(_safe_float(event_value, default_value), -1.0, 1.0), False

    if factor_id == 'event.governance_signal':
        event_value = features.get('event_governance_signal')
        if event_value is None:
            return default_value, True
        return _clamp(_safe_float(event_value, default_value), -1.0, 1.0), False

    return default_value, True


def _quality_score(status: str) -> float:
    normalized = str(status or 'OK').upper()
    if normalized == 'OK':
        return 1.0
    if normalized == 'PARTIAL':
        return 0.65
    return 0.35


def score_signal_skill_pack(
    *,
    features: dict[str, Any],
    snapshots: dict[str, Any],
    skill_pack: dict[str, Any],
    data_quality_status: str = 'OK',
    current_pos: float = 0.0,
    hard_risk_triggered: bool = False,
) -> dict:
    factors_payload = skill_pack.get('factors', {}) if isinstance(skill_pack, dict) else {}
    formula_payload = skill_pack.get('formula', {}) if isinstance(skill_pack, dict) else {}
    policy_payload = skill_pack.get('policy', {}) if isinstance(skill_pack, dict) else {}
    risk_payload = skill_pack.get('risk', {}) if isinstance(skill_pack, dict) else {}

    factor_items = factors_payload.get('factors', []) if isinstance(factors_payload, dict) else []
    if not isinstance(factor_items, list):
        factor_items = []

    normalization = factors_payload.get('normalization', {}) if isinstance(factors_payload, dict) else {}
    default_missing_penalty = _safe_float(normalization.get('default_missing_penalty'), 3.0)

    weighted_sum = 0.0
    enabled_factor_count = 0
    missing_factor_count = 0
    factor_values: dict[str, float] = {}
    contributions: list[float] = []
    for factor in factor_items:
        if not isinstance(factor, dict):
            continue
        factor_id = str(factor.get('factor_id', '')).strip()
        if not factor_id:
            continue
        if not bool(factor.get('enabled', True)):
            continue
        enabled_factor_count += 1
        weight = _safe_float(factor.get('weight'), 0.0)
        default_value = _safe_float(factor.get('default_value'), 0.0)
        value, missing = _factor_value(
            factor_id,
            features=features,
            snapshots=snapshots,
            default_value=default_value,
        )
        if missing:
            missing_factor_count += 1
        value = _clamp(_safe_float(value, default_value), -1.0, 1.0)
        factor_values[factor_id] = value
        contribution = weight * value
        contributions.append(contribution)
        weighted_sum += contribution

    score_formula = formula_payload.get('score_formula', {}) if isinstance(formula_payload, dict) else {}
    intercept = _safe_float(score_formula.get('intercept'), 0.0)
    base_transform = score_formula.get('base_transform', {}) if isinstance(score_formula, dict) else {}
    center = _safe_float(base_transform.get('center'), 50.0)
    scale = _safe_float(base_transform.get('scale'), 50.0)
    z = intercept + weighted_sum
    base_score = center + scale * math.tanh(z)

    missing_ratio = (missing_factor_count / enabled_factor_count) if enabled_factor_count > 0 else 1.0
    dq_penalty = missing_ratio * default_missing_penalty
    risk_penalty = 0.0

    risk_penalty_rules = risk_payload.get('penalty_rules', []) if isinstance(risk_payload, dict) else []
    if isinstance(risk_penalty_rules, list):
        for rule in risk_penalty_rules:
            if not isinstance(rule, dict):
                continue
            rule_id = str(rule.get('rule_id', ''))
            penalty = _safe_float(rule.get('penalty'), 0.0)
            if rule_id == 'penalty_high_volatility' and factor_values.get('price.volatility_20d', 0.0) > 0.45:
                risk_penalty += penalty
            elif rule_id == 'penalty_event_cluster_negative' and factor_values.get('event.policy_signal', 0.0) < -0.6:
                risk_penalty += penalty
            elif rule_id == 'penalty_data_quality_partial' and str(data_quality_status).upper() == 'PARTIAL':
                dq_penalty += penalty
            elif rule_id == 'penalty_data_quality_degraded' and str(data_quality_status).upper() == 'DEGRADED':
                dq_penalty += penalty

    trend_regime = _nested(snapshots.get('get_market_snapshot', {}), 'trend.regime')
    regime_bonus = 0.0
    if str(trend_regime).upper() == 'UP':
        regime_bonus = 1.5
    elif str(trend_regime).upper() == 'DOWN':
        regime_bonus = -1.5

    clamp_range = score_formula.get('clamp', [0, 100]) if isinstance(score_formula, dict) else [0, 100]
    score_min = _safe_float(clamp_range[0] if isinstance(clamp_range, list) and len(clamp_range) > 0 else 0.0, 0.0)
    score_max = _safe_float(clamp_range[1] if isinstance(clamp_range, list) and len(clamp_range) > 1 else 100.0, 100.0)
    final_score = _clamp(base_score - risk_penalty - dq_penalty + regime_bonus, score_min, score_max)

    confidence_formula = formula_payload.get('confidence_formula', {}) if isinstance(formula_payload, dict) else {}
    coeffs = confidence_formula.get('coefficients', {}) if isinstance(confidence_formula, dict) else {}
    c0 = _safe_float(coeffs.get('c0'), 0.25)
    c1 = _safe_float(coeffs.get('c1_data_quality_score'), 0.35)
    c2 = _safe_float(coeffs.get('c2_evidence_coverage'), 0.30)
    c3 = _safe_float(coeffs.get('c3_factor_conflict'), 0.20)
    c4 = _safe_float(coeffs.get('c4_staleness'), 0.15)

    quality_score = _quality_score(data_quality_status)
    evidence_coverage = _safe_float(features.get('evidence_coverage', 0.5), 0.5)
    positive = sum(1 for item in contributions if item > 0)
    negative = sum(1 for item in contributions if item < 0)
    factor_conflict = 0.0
    if positive + negative > 0:
        factor_conflict = min(positive, negative) / float(positive + negative)
    staleness = _safe_float(features.get('staleness', 0.0), 0.0)

    confidence_raw = c0 + c1 * quality_score + c2 * evidence_coverage - c3 * factor_conflict - c4 * staleness
    conf_clamp = confidence_formula.get('clamp', [0, 1]) if isinstance(confidence_formula, dict) else [0, 1]
    conf_min = _safe_float(conf_clamp[0] if isinstance(conf_clamp, list) and len(conf_clamp) > 0 else 0.0, 0.0)
    conf_max = _safe_float(conf_clamp[1] if isinstance(conf_clamp, list) and len(conf_clamp) > 1 else 1.0, 1.0)
    confidence = _clamp(confidence_raw, conf_min, conf_max)

    positioning = policy_payload.get('positioning', {}) if isinstance(policy_payload, dict) else {}
    max_position = _safe_float(positioning.get('max_position'), 1.0)
    target_pos = _clamp(((final_score - 50.0) / 50.0) * confidence, 0.0, max_position)

    thresholds = policy_payload.get('thresholds', {}) if isinstance(policy_payload, dict) else {}
    buy_score_min = _safe_float(thresholds.get('buy_score_min'), 72.0)
    buy_confidence_min = _safe_float(thresholds.get('buy_confidence_min'), 0.62)
    add_gap_min = _safe_float(thresholds.get('add_gap_min'), 0.15)
    hold_gap_max = _safe_float(thresholds.get('hold_gap_max'), 0.10)
    reduce_gap_min = _safe_float(thresholds.get('reduce_gap_min', add_gap_min), add_gap_min)
    sell_score_max = _safe_float(thresholds.get('sell_score_max'), 35.0)
    flat_non_buy_action = str(thresholds.get('flat_non_buy_action', 'AVOID')).upper() or 'AVOID'

    action = 'HOLD'
    if hard_risk_triggered or (current_pos > 0 and final_score < sell_score_max):
        action = 'SELL'
    elif current_pos <= 0:
        if final_score >= buy_score_min and confidence >= buy_confidence_min and str(data_quality_status).upper() != 'DEGRADED':
            action = 'BUY'
        else:
            action = flat_non_buy_action
    else:
        delta = target_pos - _clamp(current_pos, 0.0, max_position)
        if delta >= add_gap_min:
            action = 'ADD'
        elif abs(delta) < hold_gap_max:
            action = 'HOLD'
        elif -delta >= reduce_gap_min:
            action = 'REDUCE'
        else:
            action = 'HOLD'

    return {
        'score_id': _stable_id(
            'score',
            {
                'mode': 'skill_pack',
                'skill_pack': skill_pack.get('summary', {}),
                'factor_values': factor_values,
                'weighted_sum': round(weighted_sum, 8),
                'risk_penalty': round(risk_penalty, 8),
                'dq_penalty': round(dq_penalty, 8),
            },
        ),
        'overall_score': int(round(_clamp(final_score, 0.0, 100.0))),
        'confidence': round(_clamp(confidence, 0.0, 1.0), 3),
        'proposed_action': action,
        'target_position': round(target_pos, 4),
        'factor_values': factor_values,
        'factor_stats': {
            'enabled_factor_count': enabled_factor_count,
            'missing_factor_count': missing_factor_count,
            'zero_weight_factor_count': sum(
                1
                for item in factor_items
                if isinstance(item, dict)
                and bool(item.get('enabled', True))
                and _safe_float(item.get('weight'), 0.0) == 0.0
            ),
        },
        'components': {
            'weighted_sum': round(weighted_sum, 8),
            'base_score': round(base_score, 8),
            'risk_penalty': round(risk_penalty, 8),
            'dq_penalty': round(dq_penalty, 8),
            'regime_bonus': round(regime_bonus, 8),
            'factor_conflict': round(factor_conflict, 8),
        },
    }


def score_signal(features: dict, weights: dict) -> dict:
    hotness = features.get('hotness', 50)
    fundamentals = features.get('fundamentals', 50)
    volatility = features.get('volatility', 50)
    liquidity = features.get('liquidity', 50)

    score = int(
        hotness * weights.get('hotness', 0.25)
        + fundamentals * weights.get('fundamental', 0.3)
        + (100 - volatility) * weights.get('volatility', 0.2)
        + liquidity * weights.get('liquidity', 0.15)
    )
    score = max(0, min(100, score))

    confidence = round(min(0.85, 0.45 + score / 200), 3)
    action = 'WATCH'
    if score >= 75:
        action = 'BUY'
    elif score < 45:
        action = 'AVOID'

    return {
        'score_id': _stable_id('score', {'features': features, 'weights': weights}),
        'overall_score': score,
        'confidence': confidence,
        'proposed_action': action,
    }


def generate_price_bands(base_price: float, score: int) -> dict:
    gap = round(base_price * 0.03, 2)
    bands = [
        {
            'band_id': 'B1',
            'range': {'currency': 'CNY', 'min': round(base_price - 2 * gap, 2), 'max': round(base_price - gap, 2)},
            'score': max(0, score - 10),
            'confidence': 0.55,
            'rationale': 'Lower band with better margin of safety.',
            'entry_conditions': [{'type': 'TECHNICAL', 'description': 'Hold support zone', 'priority': 'MEDIUM'}],
            'exit_conditions': [{'type': 'RISK', 'description': 'Breakdown with volume expansion', 'priority': 'HIGH'}],
        },
        {
            'band_id': 'B2',
            'range': {'currency': 'CNY', 'min': round(base_price - gap, 2), 'max': round(base_price + gap, 2)},
            'score': score,
            'confidence': 0.62,
            'rationale': 'Balanced band under current momentum.',
            'entry_conditions': [{'type': 'FLOW', 'description': 'Net inflow remains positive', 'priority': 'MEDIUM'}],
            'exit_conditions': [{'type': 'RISK', 'description': 'Flow reversal for 2 sessions', 'priority': 'MEDIUM'}],
        },
        {
            'band_id': 'B3',
            'range': {'currency': 'CNY', 'min': round(base_price + gap, 2), 'max': round(base_price + 2 * gap, 2)},
            'score': max(0, score - 15),
            'confidence': 0.5,
            'rationale': 'Upper band has higher momentum risk.',
            'entry_conditions': [{'type': 'EVENT', 'description': 'Positive catalyst confirmed', 'priority': 'HIGH'}],
            'exit_conditions': [{'type': 'RISK', 'description': 'Catalyst invalidated', 'priority': 'HIGH'}],
        },
    ]
    return {
        'price_band_set_id': _stable_id('bands', {'base_price': base_price, 'score': score}),
        'price_bands': bands,
    }


def risk_gate(report: dict, risk_profile: str = 'LOW') -> dict:
    gated = dict(report)
    decision = dict(gated.get('decision', {}))
    dq = gated.get('data_quality', {}).get('status', 'OK')

    hard_blocks = []
    if risk_profile == 'LOW' and decision.get('action') == 'BUY' and decision.get('confidence', 0) > 0.7:
        decision['confidence'] = 0.7

    if dq == 'PARTIAL':
        decision['confidence'] = min(decision.get('confidence', 0.5), 0.5)
        if decision.get('action') == 'BUY':
            decision['action'] = 'WATCH'
            hard_blocks.append('DATA_QUALITY_PARTIAL')
    elif dq != 'OK':
        decision['confidence'] = min(decision.get('confidence', 0.5), 0.45)
        if decision.get('action') == 'BUY':
            decision['action'] = 'WATCH'
        hard_blocks.append('DATA_QUALITY_NOT_OK')

    gated['decision'] = decision
    return {'report': gated, 'hard_blocks': hard_blocks}
