from __future__ import annotations

import hashlib
import json


def _stable_id(prefix: str, payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:10]}"


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

    if dq != 'OK' and decision.get('action') == 'BUY':
        decision['action'] = 'WATCH'
        decision['confidence'] = min(decision.get('confidence', 0.5), 0.55)
        hard_blocks.append('DATA_QUALITY_NOT_OK')

    gated['decision'] = decision
    return {'report': gated, 'hard_blocks': hard_blocks}
