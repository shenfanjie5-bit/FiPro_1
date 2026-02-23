from __future__ import annotations

import json
from pathlib import Path

from app.backtest.promotion import (
    evaluate_skill_pack_promotion,
    execute_skill_pack_promotion,
    extract_backtest_gate_metrics,
    switch_skill_pack_champion,
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
        anti_overfit_evidence={
            'train_window': {'start_date': '2018-01-01', 'end_date': '2023-12-31'},
            'validation_window': {'start_date': '2024-01-01', 'end_date': '2025-12-31'},
            'sensitivity': {'scenario_count': 6, 'pass_rate': 0.85, 'min_pass_rate': 0.7},
            'param_change_count': 3,
        },
    )
    checks = {str(item.get('metric', '')): item for item in result.get('checks', [])}
    assert checks['excess_return_delta_pct']['actual'] == 1.3
    assert checks['segment_win_rate']['actual'] == 0.75
    assert result['decision'] == 'PENDING_MANUAL_APPROVAL'
    assert not result['failed_checks']
    assert result['anti_overfit']['pass'] is True


def test_evaluate_skill_pack_promotion_blocks_when_anti_overfit_evidence_missing() -> None:
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
        anti_overfit_evidence={},
    )
    assert result['decision'] == 'BLOCK'
    assert result['anti_overfit']['pass'] is False
    assert 'anti_overfit.train_validation_split' in result['failed_checks']
    assert 'anti_overfit.threshold_sensitivity' in result['failed_checks']


def test_evaluate_skill_pack_promotion_passes_walk_forward_and_bootstrap_checks() -> None:
    gate_config = load_skill_pack('cn_a_core', '0.1.0')['gate']
    gate_config = {
        **gate_config,
        'anti_overfit': {
            **(gate_config.get('anti_overfit') or {}),
            'require_walk_forward_check': True,
            'walk_forward_window_points': 3,
            'walk_forward_min_pass_rate': 0.66,
            'require_bootstrap_significance_check': True,
            'bootstrap_samples': 200,
            'bootstrap_seed': 17,
            'bootstrap_min_confidence': 0.6,
        },
    }

    candidate = _mock_backtest(
        strategy_total_return_pct=18.0,
        excess_return_pct=3.1,
        directional_hit_rate=0.75,
        action_counts={'BUY': 4, 'ADD': 2, 'REDUCE': 1, 'SELL': 1, 'WATCH': 2, 'AVOID': 0},
        degraded_runs=0,
        completed_runs=10,
        nav_points=[1.0, 1.02, 1.05, 1.07, 1.11, 1.14, 1.18],
    )
    champion = _mock_backtest(
        strategy_total_return_pct=10.0,
        excess_return_pct=0.8,
        directional_hit_rate=0.58,
        action_counts={'BUY': 3, 'ADD': 1, 'REDUCE': 2, 'SELL': 1, 'WATCH': 3, 'AVOID': 0},
        degraded_runs=0,
        completed_runs=10,
        nav_points=[1.0, 1.01, 1.01, 1.02, 1.04, 1.05, 1.06],
    )

    result = evaluate_skill_pack_promotion(
        candidate_backtest_result=candidate,
        champion_backtest_result=champion,
        gate_config=gate_config,
        candidate_version='0.1.0',
        champion_version='0.0.1',
        manual_approved=True,
        anti_overfit_evidence={
            'train_window': {'start_date': '2018-01-01', 'end_date': '2023-12-31'},
            'validation_window': {'start_date': '2024-01-01', 'end_date': '2025-12-31'},
            'sensitivity': {'scenario_count': 6, 'pass_rate': 0.88, 'min_pass_rate': 0.7},
            'param_change_count': 3,
        },
    )
    anti_overfit = result['anti_overfit']
    by_rule = {str(item.get('rule', '')): item for item in anti_overfit.get('checks', [])}
    assert anti_overfit['pass'] is True
    assert by_rule['walk_forward_stability']['pass'] is True
    assert by_rule['bootstrap_significance']['pass'] is True
    assert result['decision'] == 'ALLOW'


def test_evaluate_skill_pack_promotion_blocks_when_robustness_checks_fail() -> None:
    gate_config = {
        'required_metrics': ['strategy_total_return_pct'],
        'promotion_rule': {'all_of': [{'metric': 'strategy_total_return_pct', 'op': '>=', 'value': -100.0}]},
        'manual_approval_required': False,
        'anti_overfit': {
            'require_train_validation_split': False,
            'require_threshold_sensitivity_check': False,
            'max_param_changes_per_iteration': 0,
            'require_walk_forward_check': True,
            'walk_forward_window_points': 3,
            'walk_forward_min_pass_rate': 0.7,
            'require_bootstrap_significance_check': True,
            'bootstrap_samples': 200,
            'bootstrap_seed': 17,
            'bootstrap_min_confidence': 0.7,
        },
    }
    candidate = _mock_backtest(
        strategy_total_return_pct=5.0,
        excess_return_pct=-1.0,
        directional_hit_rate=0.5,
        action_counts={'BUY': 2, 'ADD': 1, 'REDUCE': 1, 'SELL': 1, 'WATCH': 5, 'AVOID': 0},
        degraded_runs=0,
        completed_runs=10,
        nav_points=[1.0, 1.0, 1.01, 0.99, 1.0, 0.98, 0.99],
    )
    champion = _mock_backtest(
        strategy_total_return_pct=12.0,
        excess_return_pct=2.0,
        directional_hit_rate=0.62,
        action_counts={'BUY': 3, 'ADD': 1, 'REDUCE': 1, 'SELL': 1, 'WATCH': 4, 'AVOID': 0},
        degraded_runs=0,
        completed_runs=10,
        nav_points=[1.0, 1.01, 1.03, 1.04, 1.05, 1.07, 1.08],
    )

    result = evaluate_skill_pack_promotion(
        candidate_backtest_result=candidate,
        champion_backtest_result=champion,
        gate_config=gate_config,
        candidate_version='0.1.0',
        champion_version='0.0.1',
        manual_approved=True,
        anti_overfit_evidence={},
    )
    assert result['decision'] == 'BLOCK'
    assert 'anti_overfit.walk_forward_stability' in result['failed_checks']
    assert 'anti_overfit.bootstrap_significance' in result['failed_checks']


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
    assert execution['release_event_id']

    new_candidate = json.loads((candidate_dir / 'manifest.json').read_text(encoding='utf-8'))
    old_champion = json.loads((champion_dir / 'manifest.json').read_text(encoding='utf-8'))
    assert new_candidate['status'] == 'champion'
    assert old_champion['status'] == 'archived'


def test_switch_skill_pack_champion_supports_manual_rollback(tmp_path: Path) -> None:
    root = tmp_path / 'skill_packs' / 'cn_a_core'
    champion_dir = root / '0.0.1'
    archived_dir = root / '0.0.2'
    champion_dir.mkdir(parents=True, exist_ok=True)
    archived_dir.mkdir(parents=True, exist_ok=True)

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
    archived_manifest = {
        'skill_pack_id': 'cn_a_core',
        'version': '0.0.2',
        'market': 'CN_A',
        'author': 'test',
        'status': 'archived',
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
    (archived_dir / 'manifest.json').write_text(json.dumps(archived_manifest), encoding='utf-8')

    switched = switch_skill_pack_champion(
        skill_pack_id='cn_a_core',
        target_version='0.0.2',
        reason='rollback_after_regression',
        operator='qa_user',
        switch_mode='manual',
        dry_run=False,
        root_dir=tmp_path / 'skill_packs',
    )
    assert switched['executed'] is True
    assert switched['release_event_id']
    assert switched['archived_previous_champion'] is True
    assert switched['champion_version_before'] == '0.0.1'
    assert switched['champion_version_after'] == '0.0.2'

    new_champion = json.loads((archived_dir / 'manifest.json').read_text(encoding='utf-8'))
    old_champion = json.loads((champion_dir / 'manifest.json').read_text(encoding='utf-8'))
    assert new_champion['status'] == 'champion'
    assert old_champion['status'] == 'archived'
    assert new_champion['champion_switch']['reason'] == 'rollback_after_regression'
    assert new_champion['champion_switch']['operator'] == 'qa_user'
