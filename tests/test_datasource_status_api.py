from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.tools import datasource_status as datasource_status_module


client = TestClient(app)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _find_source(body: dict, source_id: str) -> dict:
    sources = body.get('sources', [])
    for item in sources:
        if str(item.get('source_id', '')).upper() == source_id.upper():
            return item
    raise AssertionError(f'source not found: {source_id}')


def test_datasource_status_completed_from_status_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('TUSHARE_DATA_ROOT', str(tmp_path))
    monkeypatch.setattr(datasource_status_module, 'list_running_tushare_jobs', lambda: [])
    monkeypatch.setattr(datasource_status_module, 'list_running_watchdog_jobs', lambda: [])
    monkeypatch.setattr(
        datasource_status_module,
        'list_champion_watchdog_runs',
        lambda limit, offset: {'total': 0, 'items': []},  # noqa: ARG005
    )
    monkeypatch.setattr(
        datasource_status_module,
        'list_champion_watchdog_alerts',
        lambda limit, offset, status: {'summary': {'open_count': 0, 'critical_open_count': 0, 'warning_open_count': 0}, 'items': []},  # noqa: ARG005,E501
    )

    _write_json(
        tmp_path / '_meta' / 'manifests' / 'data_source_status' / 'tushare.json',
        {
            'status': 'COMPLETED',
            'message': 'ok',
            'updated_at_utc': '2026-02-13T03:00:00Z',
            'last_success_at_utc': '2026-02-13T03:00:00Z',
            'last_error_at_utc': '',
        },
    )

    resp = client.get('/datasources/status')
    assert resp.status_code == 200
    body = resp.json()
    assert body['overall_status'] == 'COMPLETED'
    assert body['overall_label'] == '已完成'
    tushare_source = _find_source(body, 'TUSHARE')
    assert tushare_source['status'] == 'COMPLETED'
    assert tushare_source['name'] == 'TUSHARE数据'
    watchdog_source = _find_source(body, 'CHAMPION_WATCHDOG')
    assert watchdog_source['status'] == 'COMPLETED'


def test_datasource_status_updating_when_running_job_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('TUSHARE_DATA_ROOT', str(tmp_path))
    monkeypatch.setattr(
        datasource_status_module,
        'list_running_tushare_jobs',
        lambda: [{'pid': 12345, 'mode': 'INCREMENTAL', 'command': 'python tushare_incremental_update.py'}],
    )
    monkeypatch.setattr(datasource_status_module, 'list_running_watchdog_jobs', lambda: [])
    monkeypatch.setattr(
        datasource_status_module,
        'list_champion_watchdog_runs',
        lambda limit, offset: {'total': 0, 'items': []},  # noqa: ARG005
    )
    monkeypatch.setattr(
        datasource_status_module,
        'list_champion_watchdog_alerts',
        lambda limit, offset, status: {'summary': {'open_count': 0, 'critical_open_count': 0, 'warning_open_count': 0}, 'items': []},  # noqa: ARG005,E501
    )

    resp = client.get('/datasources/status')
    assert resp.status_code == 200
    body = resp.json()
    assert body['overall_status'] == 'UPDATING'
    assert body['overall_label'] == '更新中'
    tushare_source = _find_source(body, 'TUSHARE')
    assert tushare_source['status'] == 'UPDATING'
    assert len(tushare_source['running_jobs']) == 1


def test_datasource_status_error_from_incremental_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('TUSHARE_DATA_ROOT', str(tmp_path))
    monkeypatch.setattr(datasource_status_module, 'list_running_tushare_jobs', lambda: [])
    monkeypatch.setattr(datasource_status_module, 'list_running_watchdog_jobs', lambda: [])
    monkeypatch.setattr(
        datasource_status_module,
        'list_champion_watchdog_runs',
        lambda limit, offset: {'total': 0, 'items': []},  # noqa: ARG005
    )
    monkeypatch.setattr(
        datasource_status_module,
        'list_champion_watchdog_alerts',
        lambda limit, offset, status: {'summary': {'open_count': 0, 'critical_open_count': 0, 'warning_open_count': 0}, 'items': []},  # noqa: ARG005,E501
    )

    _write_json(
        tmp_path / '_meta' / 'manifests' / 'tushare_incremental_last_run.json',
        {
            'generated_at_utc': '2026-02-13T03:00:00Z',
            'errors': [{'api': 'daily', 'error': 'rate limited'}],
        },
    )

    resp = client.get('/datasources/status')
    assert resp.status_code == 200
    body = resp.json()
    assert body['overall_status'] == 'ERROR'
    assert body['overall_label'] == '异常'
    tushare_source = _find_source(body, 'TUSHARE')
    assert tushare_source['status'] == 'ERROR'
    assert 'errors=1' in tushare_source['message']


def test_datasource_status_error_when_watchdog_has_open_critical_alert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv('TUSHARE_DATA_ROOT', str(tmp_path))
    monkeypatch.setattr(datasource_status_module, 'list_running_tushare_jobs', lambda: [])
    monkeypatch.setattr(datasource_status_module, 'list_running_watchdog_jobs', lambda: [])
    monkeypatch.setattr(
        datasource_status_module,
        'list_champion_watchdog_runs',
        lambda limit, offset: {  # noqa: ARG005
            'total': 1,
            'items': [
                {
                    'run_id': 'wd_001',
                    'generated_at': '2026-02-14T10:00:00+00:00',
                    'overall_status': 'CRITICAL',
                }
            ],
        },
    )
    monkeypatch.setattr(
        datasource_status_module,
        'list_champion_watchdog_alerts',
        lambda limit, offset, status: {  # noqa: ARG005
            'summary': {
                'open_count': 1,
                'critical_open_count': 1,
                'warning_open_count': 0,
            },
            'items': [
                {
                    'alert_id': 'wd_001_01_test',
                    'severity': 'critical',
                    'status': 'OPEN',
                }
            ],
        },
    )

    _write_json(
        tmp_path / '_meta' / 'manifests' / 'data_source_status' / 'tushare.json',
        {
            'status': 'COMPLETED',
            'message': 'ok',
            'updated_at_utc': '2026-02-13T03:00:00Z',
            'last_success_at_utc': '2026-02-13T03:00:00Z',
            'last_error_at_utc': '',
        },
    )

    resp = client.get('/datasources/status')
    assert resp.status_code == 200
    body = resp.json()
    assert body['overall_status'] == 'ERROR'
    watchdog_source = _find_source(body, 'CHAMPION_WATCHDOG')
    assert watchdog_source['status'] == 'ERROR'
    assert '未关闭的严重告警' in watchdog_source['message']
