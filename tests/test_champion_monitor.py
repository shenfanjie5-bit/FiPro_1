from __future__ import annotations

from app.backtest import champion_monitor as monitor_module


def _fake_backtest_result(version: str, *, excess_return_pct: float) -> dict:
    return {
        'batch_id': f'bt_{version}',
        'request': {'skill_pack_version': version},
        'summary': {'excess_return_pct': excess_return_pct},
    }


def test_run_champion_health_check_no_rollback_when_allow(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(monitor_module, 'resolve_champion_version', lambda skill_pack_id, root_dir=None: '0.1.4')  # noqa: ARG005
    monkeypatch.setattr(monitor_module, 'load_promotion_gate', lambda skill_pack_id, version, root_dir=None: {'promotion_rule': {'all_of': []}})  # noqa: ARG005

    def fake_run_batch_backtest(payload, **kwargs):  # noqa: ANN001, ARG001
        version = payload.get('skill_pack_version')
        return _fake_backtest_result(str(version), excess_return_pct=1.5 if version == '0.1.4' else 1.0)

    monkeypatch.setattr(monitor_module, 'run_batch_backtest', fake_run_batch_backtest)
    monkeypatch.setattr(
        monitor_module,
        'evaluate_skill_pack_promotion',
        lambda **kwargs: {'decision': 'ALLOW', 'failed_checks': [], 'candidate_metrics': {}},  # noqa: ARG005
    )

    result = monitor_module.run_champion_health_check(
        backtest_payload={
            'ticker': '600519.SH',
            'market': 'CN_A',
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER0',
            'start_date': '2026-02-10',
            'end_date': '2026-02-11',
            'step_days': 1,
            'trading_days_only': True,
            'max_runs': 10,
        },
        runner=lambda request_data, thread_id: {},  # noqa: ARG005
        snapshot_loader=lambda ticker, asof: {},  # noqa: ARG005
        benchmark_loader=lambda ticker, asof: {},  # noqa: ARG005
        skill_pack_id='cn_a_core',
        baseline_version='0.1.3',
        auto_rollback=False,
        root_dir=tmp_path,
    )

    assert result['health_status'] == 'PASS'
    assert result['evaluation']['decision'] == 'ALLOW'
    assert result['rollback_execution'] is None

    listed = monitor_module.list_champion_health_checks(limit=10, offset=0, root_dir=tmp_path)
    assert listed['total'] == 1
    assert listed['items'][0]['health_status'] == 'PASS'
    fetched = monitor_module.get_champion_health_check(result['run_id'], root_dir=tmp_path)
    assert fetched is not None
    assert fetched['run_id'] == result['run_id']


def test_run_champion_health_check_auto_rollback_when_block(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(monitor_module, 'resolve_champion_version', lambda skill_pack_id, root_dir=None: '0.1.4')  # noqa: ARG005
    monkeypatch.setattr(monitor_module, 'load_promotion_gate', lambda skill_pack_id, version, root_dir=None: {'promotion_rule': {'all_of': []}})  # noqa: ARG005
    monkeypatch.setattr(
        monitor_module,
        'run_batch_backtest',
        lambda payload, **kwargs: _fake_backtest_result(str(payload.get('skill_pack_version')), excess_return_pct=0.2),  # noqa: ARG005
    )
    monkeypatch.setattr(
        monitor_module,
        'evaluate_skill_pack_promotion',
        lambda **kwargs: {'decision': 'BLOCK', 'failed_checks': ['excess_return_delta_pct'], 'candidate_metrics': {}},  # noqa: ARG005
    )

    called = {'count': 0, 'target_version': ''}

    def fake_switch_skill_pack_champion(**kwargs):  # noqa: ANN003
        called['count'] += 1
        called['target_version'] = str(kwargs.get('target_version', ''))
        return {
            'executed': False,
            'dry_run': True,
            'target_version': called['target_version'],
            'reason': str(kwargs.get('reason', '')),
            'switch_mode': str(kwargs.get('switch_mode', '')),
        }

    monkeypatch.setattr(monitor_module, 'switch_skill_pack_champion', fake_switch_skill_pack_champion)

    result = monitor_module.run_champion_health_check(
        backtest_payload={
            'ticker': '600519.SH',
            'market': 'CN_A',
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER0',
            'start_date': '2026-02-10',
            'end_date': '2026-02-11',
            'step_days': 1,
            'trading_days_only': True,
            'max_runs': 10,
        },
        runner=lambda request_data, thread_id: {},  # noqa: ARG005
        snapshot_loader=lambda ticker, asof: {},  # noqa: ARG005
        benchmark_loader=lambda ticker, asof: {},  # noqa: ARG005
        skill_pack_id='cn_a_core',
        baseline_version='0.1.3',
        auto_rollback=True,
        rollback_dry_run=True,
        root_dir=tmp_path,
    )

    assert result['health_status'] == 'FAIL'
    assert result['evaluation']['decision'] == 'BLOCK'
    assert called['count'] == 1
    assert called['target_version'] == '0.1.3'
    assert result['rollback_execution'] is not None
    assert result['rollback_execution']['switch_mode'] == 'auto_rollback'
