from __future__ import annotations

from app.tools.deterministic import risk_gate, score_signal


def test_score_signal_high_score_results_in_buy() -> None:
    result = score_signal(
        features={'hotness': 100, 'fundamentals': 100, 'volatility': 0, 'liquidity': 100},
        weights={'hotness': 0.25, 'fundamental': 0.30, 'volatility': 0.20, 'liquidity': 0.15},
    )
    assert result['overall_score'] == 90
    assert result['proposed_action'] == 'BUY'
    assert result['confidence'] == 0.85


def test_score_signal_low_score_results_in_avoid() -> None:
    result = score_signal(
        features={'hotness': 0, 'fundamentals': 0, 'volatility': 100, 'liquidity': 0},
        weights={'hotness': 0.25, 'fundamental': 0.30, 'volatility': 0.20, 'liquidity': 0.15},
    )
    assert result['overall_score'] == 0
    assert result['proposed_action'] == 'AVOID'
    assert result['confidence'] == 0.45


def _base_report(*, action: str, confidence: float, dq_status: str) -> dict:
    return {
        'decision': {'action': action, 'confidence': confidence},
        'data_quality': {'status': dq_status},
    }


def test_risk_gate_caps_buy_confidence_for_low_risk_profile() -> None:
    result = risk_gate(_base_report(action='BUY', confidence=0.82, dq_status='OK'), risk_profile='LOW')
    assert result['report']['decision']['action'] == 'BUY'
    assert result['report']['decision']['confidence'] == 0.7
    assert result['hard_blocks'] == []


def test_risk_gate_partial_data_forces_watch_and_lower_confidence() -> None:
    result = risk_gate(_base_report(action='BUY', confidence=0.82, dq_status='PARTIAL'), risk_profile='LOW')
    assert result['report']['decision']['action'] == 'WATCH'
    assert result['report']['decision']['confidence'] <= 0.5
    assert 'DATA_QUALITY_PARTIAL' in result['hard_blocks']


def test_risk_gate_degraded_data_forces_watch_and_hard_block() -> None:
    result = risk_gate(_base_report(action='BUY', confidence=0.82, dq_status='DEGRADED'), risk_profile='LOW')
    assert result['report']['decision']['action'] == 'WATCH'
    assert result['report']['decision']['confidence'] <= 0.45
    assert 'DATA_QUALITY_NOT_OK' in result['hard_blocks']
