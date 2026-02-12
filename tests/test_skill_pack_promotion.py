from __future__ import annotations

import json
from pathlib import Path

from app.backtest.promotion import (
    evaluate_skill_pack_promotion,
    execute_skill_pack_promotion,
    extract_backtest_gate_metrics,
)
from app.backtest.skill_pack import load_skill_pack


def _mock_backtest(
    *,
    strategy_total_return_pct: float,
    excess_return_pct: float,
    directional_hit_rate: float,
    action_counts: dict[str, int],
    degraded_runs: int,
    completed_runs: int,
    nav_points: list[float],
) -> dict:
    strategy_curve = []
    for idx, nav in enumerate(nav_points):
        strategy_curve.append(
            {
                'asof': f'2026-02-{idx + 1:02d}T09:30:00+08:00',
                'nav': nav,
                'step_return_pct': 0.0 if idx == 0 else (nav / nav_points[idx - 1] - 1.0) * 100.0,
            }
        )
    return {
        'summary': {
            'strategy_total_return_pct': strategy_total_return_pct,
            'excess_return_pct': excess_return_pct,
            'directional_hit_rate': directional_hit_rate,
            'action_counts': action_counts,
            'total_runs': sum(action_counts.values()),
            'completed_runs': completed_runs,
            'data_quality_counts': {'DEGRADED': degraded_runs},
        },
        'equity_curve': {'strategy': strategy_curve},
    }


def test_extract_backtest_gate_metrics_computes_core_values() -> None:
    report = _mock_backtest(
        strategy_total_return_pct=12.5,
        excess_return_pct=3.2,
        directional_hit_rate=0.66,
        action_counts={'BUY': 3, 'ADD': 2, 'REDUCE': 1, 'SELL': 1, 'WATCH': 3, 'AVOID': 0},
        degraded_runs=1,
        completed_runs=10,
        nav_points=[1.0, 1.02, 0.97, 1.05, 1.08],
    )
    metrics = extract_backtest_gate_metrics(report)
    assert metrics['strategy_total_return_pct'] == 12.5
    assert metrics['excess_return_pct'] == 3.2
    assert metrics['max_drawdown'] > 0
    assert metrics['volatility'] >= 0
    assert metrics['turnover'] > 0
    assert metrics['win_rate'] == 66.0
    assert metrics['data_quality_degraded_rate'] == 10.0


def test_evaluate_skill_pack_promotion_pending_manual_approval() -> None:
    gate_config = load_skill_pack('cn_a_core', '0.1.0')['gate']
    candidate = _mock_backtest(
        strategy_total_return_pct=14.0,
        excess_return_pct=1.6,
        directional_hit_rate=0.74,
        action_counts={'BUY': 3, 'ADD': 1, 'REDUCE': 1, 'SELL': 1, 'WATCH': 4, 'AVOID': 0},
        degraded_runs=0,
        completed_runs=10,
        nav_points=[1.0, 1.02, 1.03, 1.04, 1.08],
    )
    champion = _mock_backtest(
        strategy_total_return_pct=10.0,
        excess_return_pct=0.3,
        directional_hit_rate=0.6,
        action_counts={'BUY': 3, 'ADD': 2, 'REDUCE': 2, 'SELL': 1, 'WATCH': 2, 'AVOID': 0},
        degraded_runs=1,
        completed_runs=10,
        nav_points=[1.0, 1.01, 1.0, 1.02, 1.03],
    )

    result = evaluate_skill_pack_promotion(
        candidate_backtest_result=candidate,
        champion_backtest_result=champion,
        gate_config=gate_config,
        candidate_version='0.1.0',
        champion_version='0.0.1',
        manual_approved=False,
    )
    assert result['decision'] == 'PENDING_MANUAL_APPROVAL'
    assert not result['failed_checks']


def test_execute_skill_pack_promotion_updates_manifest_statuses(tmp_path: Path) -> None:
    root = tmp_path / 'skill_packs' / 'cn_a_core'
    champion_dir = root / '0.0.1'
    candidate_dir = root / '0.1.0'
    champion_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir.mkdir(parents=True, exist_ok=True)

    champion_manifest = {
        'skill_pack_id': 'cn_a_core',
        'version': '0.0.1',
        'market': 'CN_A',
        'author': 'test',
        'status': 'champion',
        'inputs': {
            'manifest_file': 'manifest.json',
            'factors_file': 'factors.json',
            'formula_file': 'formula.json',
            'policy_file': 'policy.json',
            'risk_file': 'risk.json',
            'llm_mapping_file': 'llm_mapping.json',
            'gate_file': 'gate.json',
        },
    }
    candidate_manifest = {
        'skill_pack_id': 'cn_a_core',
        'version': '0.1.0',
        'market': 'CN_A',
        'author': 'test',
        'status': 'candidate',
        'inputs': {
            'manifest_file': 'manifest.json',
            'factors_file': 'factors.json',
            'formula_file': 'formula.json',
            'policy_file': 'policy.json',
            'risk_file': 'risk.json',
            'llm_mapping_file': 'llm_mapping.json',
            'gate_file': 'gate.json',
        },
    }
    (champion_dir / 'manifest.json').write_text(json.dumps(champion_manifest), encoding='utf-8')
    (candidate_dir / 'manifest.json').write_text(json.dumps(candidate_manifest), encoding='utf-8')

    execution = execute_skill_pack_promotion(
        skill_pack_id='cn_a_core',
        candidate_version='0.1.0',
        evaluation={'decision': 'ALLOW'},
        champion_version='0.0.1',
        dry_run=False,
        root_dir=tmp_path / 'skill_packs',
    )
    assert execution['executed'] is True

    new_candidate = json.loads((candidate_dir / 'manifest.json').read_text(encoding='utf-8'))
    old_champion = json.loads((champion_dir / 'manifest.json').read_text(encoding='utf-8'))
    assert new_candidate['status'] == 'champion'
    assert old_champion['status'] == 'archived'
