from __future__ import annotations

from app.backtest import champion_watchdog as watchdog_module


def test_build_champion_watchdog_report_recommends_rollback() -> None:
    health_checks = [
        {
            'run_id': 'hc_002',
            'generated_at': '2026-02-14T10:00:00+00:00',
            'health_status': 'FAIL',
            'decision': 'BLOCK',
            'champion_version': '0.1.4',
            'baseline_version': '0.1.3',
            'rollback_executed': False,
        },
        {
            'run_id': 'hc_001',
            'generated_at': '2026-02-14T09:00:00+00:00',
            'health_status': 'FAIL',
            'decision': 'BLOCK',
            'champion_version': '0.1.4',
            'baseline_version': '0.1.3',
            'rollback_executed': False,
        },
    ]
    report = watchdog_module.build_champion_watchdog_report(
        health_check_items=health_checks,
        release_items=[],
        lookback_runs=20,
        consecutive_fail_critical=2,
        fail_rate_warn=0.25,
        fail_rate_critical=0.5,
    )
    assert report['overall_status'] == 'CRITICAL'
    assert report['summary']['consecutive_failures'] == 2
    assert report['rollback_recommendation']['should_rollback'] is True
    assert report['rollback_recommendation']['target_version'] == '0.1.3'


def test_run_champion_watchdog_persists_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        watchdog_module,
        'list_champion_health_checks',
        lambda limit, offset, root_dir=None: {  # noqa: ARG005
            'total': 1,
            'items': [
                {
                    'run_id': 'hc_ok_001',
                    'generated_at': '2026-02-14T10:00:00+00:00',
                    'health_status': 'PASS',
                    'decision': 'ALLOW',
                    'champion_version': '0.1.4',
                    'baseline_version': '0.1.3',
                    'rollback_executed': False,
                }
            ],
        },
    )
    monkeypatch.setattr(
        watchdog_module,
        'list_release_events',
        lambda limit, offset, root_dir=None: {'total': 0, 'items': []},  # noqa: ARG005
    )

    result = watchdog_module.run_champion_watchdog(
        run_health_check=False,
        lookback_runs=20,
        root_dir=tmp_path,
    )
    assert result['overall_status'] == 'PASS'
    assert result['alert_count'] == 0

    listed = watchdog_module.list_champion_watchdog_runs(limit=10, offset=0, root_dir=tmp_path)
    assert listed['total'] == 1
    fetched = watchdog_module.get_champion_watchdog_run(result['run_id'], root_dir=tmp_path)
    assert fetched is not None
    assert fetched['run_id'] == result['run_id']


def test_run_champion_watchdog_calls_health_check_when_enabled(monkeypatch, tmp_path) -> None:
    called = {'count': 0}

    def fake_run_champion_health_check(**kwargs):  # noqa: ANN003
        called['count'] += 1
        return {
            'run_id': 'hc_generated_001',
            'health_status': 'FAIL',
            'champion_version': '0.1.4',
            'baseline_version': '0.1.3',
            'rollback_execution': None,
        }

    monkeypatch.setattr(watchdog_module, 'run_champion_health_check', fake_run_champion_health_check)
    monkeypatch.setattr(
        watchdog_module,
        'list_champion_health_checks',
        lambda limit, offset, root_dir=None: {  # noqa: ARG005
            'total': 1,
            'items': [
                {
                    'run_id': 'hc_generated_001',
                    'generated_at': '2026-02-14T10:00:00+00:00',
                    'health_status': 'FAIL',
                    'decision': 'BLOCK',
                    'champion_version': '0.1.4',
                    'baseline_version': '0.1.3',
                    'rollback_executed': False,
                }
            ],
        },
    )
    monkeypatch.setattr(
        watchdog_module,
        'list_release_events',
        lambda limit, offset, root_dir=None: {'total': 0, 'items': []},  # noqa: ARG005
    )

    result = watchdog_module.run_champion_watchdog(
        run_health_check=True,
        health_check_request={
            'skill_pack_id': 'cn_a_core',
            'backtest': {
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
        },
        runner=lambda request_data, thread_id: {},  # noqa: ARG005
        snapshot_loader=lambda ticker, asof: {},  # noqa: ARG005
        benchmark_loader=lambda ticker, asof: {},  # noqa: ARG005
        root_dir=tmp_path,
    )

    assert called['count'] == 1
    assert result['executed_health_check'] is not None
    assert result['executed_health_check']['run_id'] == 'hc_generated_001'


def test_run_champion_watchdog_generates_ticket_and_alert_ids(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        watchdog_module,
        'list_champion_health_checks',
        lambda limit, offset, root_dir=None: {  # noqa: ARG005
            'total': 2,
            'items': [
                {
                    'run_id': 'hc_002',
                    'generated_at': '2026-02-14T10:00:00+00:00',
                    'health_status': 'FAIL',
                    'decision': 'BLOCK',
                    'champion_version': '0.1.4',
                    'baseline_version': '0.1.3',
                    'rollback_executed': False,
                },
                {
                    'run_id': 'hc_001',
                    'generated_at': '2026-02-14T09:00:00+00:00',
                    'health_status': 'FAIL',
                    'decision': 'BLOCK',
                    'champion_version': '0.1.4',
                    'baseline_version': '0.1.3',
                    'rollback_executed': False,
                },
            ],
        },
    )
    monkeypatch.setattr(
        watchdog_module,
        'list_release_events',
        lambda limit, offset, root_dir=None: {'total': 0, 'items': []},  # noqa: ARG005
    )

    result = watchdog_module.run_champion_watchdog(
        run_health_check=False,
        lookback_runs=20,
        root_dir=tmp_path,
    )
    assert result['overall_status'] == 'CRITICAL'
    assert result['alert_count'] >= 1
    assert result['ticket'] is not None
    assert str(result['ticket'].get('ticket_id', '')).startswith('wdt_')
    assert all(str(item.get('alert_id', '')).strip() for item in result['alerts'])
    assert all(str(item.get('status', '')).upper() == 'OPEN' for item in result['alerts'])

    listed_tickets = watchdog_module.list_champion_watchdog_tickets(limit=10, offset=0, root_dir=tmp_path)
    assert listed_tickets['total'] == 1
    ticket_id = listed_tickets['items'][0]['ticket_id']
    fetched_ticket = watchdog_module.get_champion_watchdog_ticket(ticket_id, root_dir=tmp_path)
    assert fetched_ticket is not None
    assert fetched_ticket['run_id'] == result['run_id']


def test_watchdog_alert_ack_and_close(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        watchdog_module,
        'list_champion_health_checks',
        lambda limit, offset, root_dir=None: {  # noqa: ARG005
            'total': 2,
            'items': [
                {
                    'run_id': 'hc_002',
                    'generated_at': '2026-02-14T10:00:00+00:00',
                    'health_status': 'FAIL',
                    'decision': 'BLOCK',
                    'champion_version': '0.1.4',
                    'baseline_version': '0.1.3',
                    'rollback_executed': False,
                },
                {
                    'run_id': 'hc_001',
                    'generated_at': '2026-02-14T09:00:00+00:00',
                    'health_status': 'FAIL',
                    'decision': 'BLOCK',
                    'champion_version': '0.1.4',
                    'baseline_version': '0.1.3',
                    'rollback_executed': False,
                },
            ],
        },
    )
    monkeypatch.setattr(
        watchdog_module,
        'list_release_events',
        lambda limit, offset, root_dir=None: {'total': 0, 'items': []},  # noqa: ARG005
    )

    result = watchdog_module.run_champion_watchdog(
        run_health_check=False,
        lookback_runs=20,
        root_dir=tmp_path,
    )
    alert_id = str(result['alerts'][0]['alert_id'])

    acked = watchdog_module.acknowledge_champion_watchdog_alert(
        alert_id,
        operator='tester',
        note='ack for investigation',
        root_dir=tmp_path,
    )
    assert acked['status'] == 'ACKED'
    assert acked['acknowledged_by'] == 'tester'

    closed = watchdog_module.close_champion_watchdog_alert(
        alert_id,
        operator='tester',
        note='closed after fix',
        root_dir=tmp_path,
    )
    assert closed['status'] == 'CLOSED'
    assert closed['closed_by'] == 'tester'

    open_alerts = watchdog_module.list_champion_watchdog_alerts(limit=100, offset=0, status='OPEN', root_dir=tmp_path)
    assert all(item['alert_id'] != alert_id for item in open_alerts['items'])


def test_run_champion_watchdog_executes_rollback_on_recommendation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        watchdog_module,
        'list_champion_health_checks',
        lambda limit, offset, root_dir=None: {  # noqa: ARG005
            'total': 2,
            'items': [
                {
                    'run_id': 'hc_010',
                    'generated_at': '2026-02-14T10:00:00+00:00',
                    'skill_pack_id': 'cn_a_core',
                    'health_status': 'FAIL',
                    'decision': 'BLOCK',
                    'champion_version': '0.1.4',
                    'baseline_version': '0.1.3',
                    'rollback_executed': False,
                },
                {
                    'run_id': 'hc_009',
                    'generated_at': '2026-02-14T09:00:00+00:00',
                    'skill_pack_id': 'cn_a_core',
                    'health_status': 'FAIL',
                    'decision': 'BLOCK',
                    'champion_version': '0.1.4',
                    'baseline_version': '0.1.3',
                    'rollback_executed': False,
                },
            ],
        },
    )
    monkeypatch.setattr(
        watchdog_module,
        'list_release_events',
        lambda limit, offset, root_dir=None: {'total': 0, 'items': []},  # noqa: ARG005
    )
    captured: dict = {}

    def fake_switch_skill_pack_champion(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {
            'executed': True,
            'dry_run': kwargs.get('dry_run', False),
            'skill_pack_id': kwargs.get('skill_pack_id'),
            'target_version': kwargs.get('target_version'),
            'champion_version_before': kwargs.get('champion_version_hint', ''),
            'champion_version_after': kwargs.get('target_version'),
            'archived_previous_champion': True,
            'reason': kwargs.get('reason', ''),
            'operator': kwargs.get('operator', ''),
            'switch_mode': kwargs.get('switch_mode', ''),
            'release_event_id': 'release_rollback_001',
        }

    monkeypatch.setattr(watchdog_module, 'switch_skill_pack_champion', fake_switch_skill_pack_champion)

    result = watchdog_module.run_champion_watchdog(
        run_health_check=False,
        lookback_runs=20,
        execute_rollback_on_recommendation=True,
        rollback_dry_run=True,
        rollback_reason='watchdog_recommendation',
        rollback_operator='watchdog_engine',
        root_dir=tmp_path,
    )

    assert captured['skill_pack_id'] == 'cn_a_core'
    assert captured['target_version'] == '0.1.3'
    assert captured['switch_mode'] == 'watchdog_auto_rollback'
    assert result['rollback_execution'] is not None
    assert result['rollback_execution']['executed'] is True
    assert result['rollback_execution']['release_event_id'] == 'release_rollback_001'
