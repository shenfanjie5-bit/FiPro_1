from __future__ import annotations

import csv
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.tools.factor_registry import clear_tushare_factor_registry_cache, get_tushare_factor_registry


client = TestClient(app)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture()
def mock_registry_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    classification = tmp_path / 'classification.csv'
    storage_map = tmp_path / 'storage_map.csv'
    catalog = tmp_path / 'catalog.csv'
    data_root = tmp_path / 'data_root'
    data_root.mkdir(parents=True, exist_ok=True)

    _write_csv(
        classification,
        ['group', 'api', 'label', 'path', 'reason'],
        [
            {'group': 'master', 'api': 'trade_cal', 'label': '交易日历', 'path': '股票数据/基础数据/交易日历', 'reason': 'master'},
            {'group': 'historical', 'api': 'trade_cal', 'label': '交易日历', 'path': '股票数据/基础数据/交易日历', 'reason': 'historical preferred'},
            {'group': 'historical', 'api': 'daily', 'label': '历史日线', 'path': '股票数据/行情数据/历史日线', 'reason': 'historical'},
        ],
    )
    _write_csv(
        storage_map,
        ['api', 'label', 'raw_relative_path', 'normalized_relative_path', 'exists', 'reason'],
        [
            {
                'api': 'trade_cal',
                'label': '交易日历',
                'raw_relative_path': '股票数据/基础数据/交易日历',
                'normalized_relative_path': '股票数据/基础数据/交易日历',
                'exists': '1',
                'reason': '',
            },
            {
                'api': 'daily',
                'label': '历史日线',
                'raw_relative_path': '股票数据/行情数据/历史日线',
                'normalized_relative_path': '股票数据/行情数据/历史日线',
                'exists': '1',
                'reason': '',
            },
        ],
    )
    _write_csv(
        catalog,
        ['api_name', 'api', 'rate_limit', 'note'],
        [
            {'api_name': '交易日历', 'api': 'trade_cal', 'rate_limit': '200/min', 'note': 'calendar'},
            {'api_name': '历史日线', 'api': 'daily', 'rate_limit': '500/min', 'note': 'ohlc'},
        ],
    )

    monkeypatch.setenv('TUSHARE_CLASSIFICATION_CSV', str(classification))
    monkeypatch.setenv('TUSHARE_STORAGE_MAP_CSV', str(storage_map))
    monkeypatch.setenv('TUSHARE_CATALOG_CSV', str(catalog))
    monkeypatch.setenv('TUSHARE_DATA_ROOT', str(data_root))
    clear_tushare_factor_registry_cache()
    yield {
        'classification': classification,
        'storage_map': storage_map,
        'catalog': catalog,
        'data_root': data_root,
    }
    clear_tushare_factor_registry_cache()


def test_factor_registry_deduplicates_and_sets_zero_default_weight(mock_registry_files: dict[str, Path]) -> None:
    payload = get_tushare_factor_registry(limit=0, offset=0, include_entries=True)
    assert payload['total_endpoints'] == 2

    by_endpoint = {item['endpoint']: item for item in payload['entries']}
    assert set(by_endpoint.keys()) == {'trade_cal', 'daily'}
    assert by_endpoint['trade_cal']['group'] == 'historical'
    assert by_endpoint['trade_cal']['weight_default'] == 0.0
    assert by_endpoint['trade_cal']['factor_id'] == 'tushare.trade_cal'
    assert by_endpoint['daily']['rate_limit'] == '500/min'
    assert str(mock_registry_files['data_root']) in by_endpoint['daily']['local_path_hint']


def test_factor_registry_api_supports_pagination(mock_registry_files: dict[str, Path]) -> None:
    resp = client.get('/factors/registry?limit=1&offset=1')
    assert resp.status_code == 200
    body = resp.json()
    assert body['total_endpoints'] == 2
    assert body['limit'] == 1
    assert body['offset'] == 1
    assert len(body['entries']) == 1

