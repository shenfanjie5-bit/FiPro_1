from __future__ import annotations

import csv
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.tools.factor_registry import clear_tushare_factor_registry_cache
from app.tools.local_data_gateway import query_local_datasets


client = TestClient(app)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture()
def mock_local_data_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    classification = tmp_path / 'classification.csv'
    storage_map = tmp_path / 'storage_map.csv'
    catalog = tmp_path / 'catalog.csv'
    data_root = tmp_path / 'data_root'
    data_root.mkdir(parents=True, exist_ok=True)

    _write_csv(
        classification,
        ['group', 'api', 'label', 'path', 'reason'],
        [
            {'group': 'historical', 'api': 'daily', 'label': '历史日线', 'path': '股票数据/行情数据/历史日线', 'reason': 'test'},
            {'group': 'master', 'api': 'trade_cal', 'label': '交易日历', 'path': '股票数据/基础数据/交易日历', 'reason': 'test'},
        ],
    )
    _write_csv(
        storage_map,
        ['api', 'label', 'raw_relative_path', 'normalized_relative_path', 'exists', 'reason'],
        [
            {
                'api': 'daily',
                'label': '历史日线',
                'raw_relative_path': '股票数据/行情数据/历史日线',
                'normalized_relative_path': '股票数据/行情数据/历史日线',
                'exists': '1',
                'reason': '',
            },
            {
                'api': 'trade_cal',
                'label': '交易日历',
                'raw_relative_path': '股票数据/基础数据/交易日历',
                'normalized_relative_path': '股票数据/基础数据/交易日历',
                'exists': '1',
                'reason': '',
            },
        ],
    )
    _write_csv(
        catalog,
        ['api_name', 'api', 'rate_limit', 'note'],
        [
            {'api_name': '历史日线', 'api': 'daily', 'rate_limit': '500/min', 'note': 'ohlc'},
            {'api_name': '交易日历', 'api': 'trade_cal', 'rate_limit': '200/min', 'note': 'calendar'},
        ],
    )

    _write_csv(
        data_root / '股票数据' / '行情数据' / '历史日线' / 'by_symbol' / '600519.SH+贵州茅台.csv',
        ['ts_code', 'trade_date', 'close', 'vol'],
        [
            {'ts_code': '600519.SH', 'trade_date': '20260210', 'close': '1500.0', 'vol': '1000'},
            {'ts_code': '600519.SH', 'trade_date': '20260211', 'close': '1510.0', 'vol': '1200'},
            {'ts_code': '600519.SH', 'trade_date': '20260212', 'close': '1520.0', 'vol': '1500'},
        ],
    )
    _write_csv(
        data_root / '股票数据' / '基础数据' / '交易日历' / 'all.csv',
        ['cal_date', 'is_open'],
        [
            {'cal_date': '20260210', 'is_open': '1'},
            {'cal_date': '20260211', 'is_open': '1'},
            {'cal_date': '20260212', 'is_open': '1'},
        ],
    )

    monkeypatch.setenv('TUSHARE_CLASSIFICATION_CSV', str(classification))
    monkeypatch.setenv('TUSHARE_STORAGE_MAP_CSV', str(storage_map))
    monkeypatch.setenv('TUSHARE_CATALOG_CSV', str(catalog))
    monkeypatch.setenv('TUSHARE_DATA_ROOT', str(data_root))
    clear_tushare_factor_registry_cache()
    yield data_root
    clear_tushare_factor_registry_cache()


def test_query_local_datasets_returns_standardized_slices(mock_local_data_env: Path) -> None:
    payload = query_local_datasets(
        endpoints=['daily', 'trade_cal'],
        ticker='600519.SH',
        start_date='20260211',
        end_date='20260212',
        limit_per_endpoint=2,
        max_endpoints=8,
        order='desc',
        include_rows=True,
    )
    assert payload['status'] in {'OK', 'PARTIAL'}
    assert payload['audit']['requested_count'] == 2
    assert payload['audit']['resolved_count'] == 2

    by_endpoint = {item['endpoint']: item for item in payload['slices']}
    daily = by_endpoint['daily']
    assert daily['status'] == 'OK'
    assert daily['returned_rows'] == 2
    assert daily['latest_date'] == '20260212'
    assert daily['rows'][0]['trade_date'] == '20260212'

    trade_cal = by_endpoint['trade_cal']
    assert trade_cal['status'] == 'OK'
    assert trade_cal['file'].endswith('all.csv')


def test_query_local_datasets_marks_unknown_endpoint_as_error(mock_local_data_env: Path) -> None:
    payload = query_local_datasets(
        endpoints=['unknown_api'],
        ticker='600519.SH',
        start_date='20260210',
        end_date='20260212',
        include_rows=False,
    )
    assert payload['status'] == 'ERROR'
    assert payload['audit']['error_slices'] == 1
    assert payload['slices'][0]['message'] == 'ENDPOINT_NOT_REGISTERED'


def test_local_data_batch_query_api(mock_local_data_env: Path) -> None:
    resp = client.post(
        '/local-data/batch-query',
        json={
            'endpoints': ['daily'],
            'ticker': '600519.SH',
            'start_date': '20260210',
            'end_date': '20260212',
            'limit_per_endpoint': 1,
            'max_endpoints': 4,
            'order': 'desc',
            'include_rows': True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['audit']['requested_count'] == 1
    assert body['slices'][0]['endpoint'] == 'daily'
    assert body['slices'][0]['returned_rows'] == 1

