from __future__ import annotations

import csv
from datetime import datetime, timezone
from functools import lru_cache
import os
from pathlib import Path
from typing import Any


DEFAULT_CLASSIFICATION_RELATIVE = 'docs/tushare_api_history_classification.csv'
DEFAULT_STORAGE_MAP_RELATIVE = 'docs/tushare_api_storage_map.csv'
DEFAULT_CATALOG_RELATIVE = 'docs/tushare_api_catalog.csv'
DEFAULT_TUSHARE_DATA_ROOT = '/Volumes/dockcase2tb/database_all'

GROUP_HISTORICAL = 'historical'
GROUP_MASTER = 'master'
ALLOWED_GROUPS = {GROUP_HISTORICAL, GROUP_MASTER}

DOMAIN_RULES = (
    ('event', ('新闻', '公告', '研报', '快讯', 'news', 'announce', 'report')),
    ('flow', ('资金', 'moneyflow', 'margin', 'block_trade', 'hsgt', 'north', 'south')),
    ('fundamental', ('财务', '财报', '估值', 'fina', 'income', 'balancesheet', 'cashflow', 'dividend', 'audit', 'basic')),
    ('macro', ('宏观', '利率', '汇率', 'gdp', 'pmi', 'ppi', 'shibor', 'libor', 'fx')),
    ('price', ('行情', 'k线', '日线', '周线', '月线', 'daily', 'weekly', 'monthly', 'mins', 'index')),
    ('risk', ('风险', '波动', 'st', '违约', '预警', '回撤', 'drawdown', 'volatility')),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _text(value: Any) -> str:
    return str(value or '').strip()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _resolve_path(env_key: str, default_relative_path: str) -> Path:
    env_value = _text(os.getenv(env_key, ''))
    if env_value:
        return Path(env_value).expanduser().resolve()
    return (_repo_root() / default_relative_path).resolve()


def _resolve_tushare_root() -> Path:
    env_value = _text(os.getenv('TUSHARE_DATA_ROOT', ''))
    if env_value:
        return Path(env_value).expanduser().resolve()
    return Path(DEFAULT_TUSHARE_DATA_ROOT).expanduser().resolve()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        return [{str(k): _text(v) for k, v in row.items()} for row in csv.DictReader(handle)]


def _load_storage_map(path: Path) -> tuple[dict[tuple[str, str, str], str], dict[str, str]]:
    full: dict[tuple[str, str, str], str] = {}
    by_api: dict[str, str] = {}
    for row in _read_csv(path):
        api = _text(row.get('api'))
        label = _text(row.get('label'))
        raw_path = _text(row.get('raw_relative_path'))
        normalized = _text(row.get('normalized_relative_path'))
        if not api or not normalized:
            continue
        full[(api, label, raw_path)] = normalized
        by_api.setdefault(api, normalized)
    return full, by_api


def _load_catalog(path: Path) -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {}
    for row in _read_csv(path):
        api = _text(row.get('api'))
        if not api:
            continue
        item = mapping.setdefault(api, {'rate_limit': '', 'note': ''})
        rate_limit = _text(row.get('rate_limit'))
        note = _text(row.get('note'))
        if rate_limit and not item['rate_limit']:
            item['rate_limit'] = rate_limit
        if note and not item['note']:
            item['note'] = note
    return mapping


def _group_rank(group: str) -> int:
    if group == GROUP_HISTORICAL:
        return 0
    if group == GROUP_MASTER:
        return 1
    return 9


def _infer_domain(*, api: str, label: str, path_text: str) -> str:
    joined = f'{api} {label} {path_text}'.lower()
    for domain, keywords in DOMAIN_RULES:
        if any(keyword.lower() in joined for keyword in keywords):
            return domain
    return 'other'


def _local_path_hint(*, root: Path, group: str, normalized_path: str) -> str:
    base = root.joinpath(*[part for part in normalized_path.split('/') if part])
    if group == GROUP_MASTER:
        return str(base / 'all.csv')
    by_symbol_hint = str(base / 'by_symbol' / '*.csv')
    all_csv = str(base / 'all.csv')
    if (base / 'by_symbol').exists():
        return by_symbol_hint
    if (base / 'all.csv').exists():
        return all_csv
    return by_symbol_hint


def _dedupe_rows_by_api(
    rows: list[dict[str, str]],
    storage_map_full: dict[tuple[str, str, str], str],
    storage_map_by_api: dict[str, str],
) -> list[dict[str, str]]:
    chosen: dict[str, dict[str, str]] = {}
    for row in rows:
        group = _text(row.get('group')).lower()
        api = _text(row.get('api'))
        label = _text(row.get('label'))
        path_text = _text(row.get('path'))
        if group not in ALLOWED_GROUPS or not api:
            continue
        normalized_path = storage_map_full.get((api, label, path_text), '') or storage_map_by_api.get(api, '') or path_text
        candidate = {
            'group': group,
            'api': api,
            'label': label,
            'path': path_text,
            'normalized_path': normalized_path,
        }
        existing = chosen.get(api)
        if existing is None:
            chosen[api] = candidate
            continue
        if _group_rank(group) < _group_rank(existing['group']):
            chosen[api] = candidate
            continue
        if group == existing['group']:
            current_has_normalized = bool(_text(existing.get('normalized_path')))
            candidate_has_normalized = bool(_text(candidate.get('normalized_path')))
            if candidate_has_normalized and not current_has_normalized:
                chosen[api] = candidate
    return sorted(chosen.values(), key=lambda item: (item['group'], item['api']))


@lru_cache(maxsize=8)
def _load_registry_cached(
    classification_csv: str,
    storage_map_csv: str,
    catalog_csv: str,
    tushare_root: str,
) -> list[dict[str, Any]]:
    classification_rows = _read_csv(Path(classification_csv))
    storage_map_full, storage_map_by_api = _load_storage_map(Path(storage_map_csv))
    catalog = _load_catalog(Path(catalog_csv))
    selected_rows = _dedupe_rows_by_api(classification_rows, storage_map_full, storage_map_by_api)

    root = Path(tushare_root).expanduser().resolve()
    entries: list[dict[str, Any]] = []
    for row in selected_rows:
        api = row['api']
        label = row['label']
        group = row['group']
        path_text = row['path']
        normalized_path = row['normalized_path']
        meta = catalog.get(api, {})
        entries.append(
            {
                'factor_id': f'tushare.{api}',
                'source': 'tushare',
                'endpoint': api,
                'api': api,
                'label': label,
                'group': group,
                'domain': _infer_domain(api=api, label=label, path_text=path_text),
                'path': path_text,
                'normalized_path': normalized_path,
                'local_path_hint': _local_path_hint(root=root, group=group, normalized_path=normalized_path),
                'enabled_default': True,
                'weight_default': 0.0,
                'transform_default': 'zscore_then_clip',
                'missing_policy_default': 'use_default_and_penalty',
                'rate_limit': _text(meta.get('rate_limit')),
                'note': _text(meta.get('note')),
            }
        )
    return entries


def clear_tushare_factor_registry_cache() -> None:
    _load_registry_cached.cache_clear()


def get_tushare_factor_registry(*, limit: int = 200, offset: int = 0, include_entries: bool = True) -> dict[str, Any]:
    classification_csv = _resolve_path('TUSHARE_CLASSIFICATION_CSV', DEFAULT_CLASSIFICATION_RELATIVE)
    storage_map_csv = _resolve_path('TUSHARE_STORAGE_MAP_CSV', DEFAULT_STORAGE_MAP_RELATIVE)
    catalog_csv = _resolve_path('TUSHARE_CATALOG_CSV', DEFAULT_CATALOG_RELATIVE)
    root = _resolve_tushare_root()

    all_entries = _load_registry_cached(
        str(classification_csv),
        str(storage_map_csv),
        str(catalog_csv),
        str(root),
    )
    safe_offset = max(0, int(offset))
    safe_limit = max(0, int(limit))
    if safe_limit == 0:
        sliced = all_entries[safe_offset:]
    else:
        sliced = all_entries[safe_offset : safe_offset + safe_limit]
    entry_payload = [dict(item) for item in sliced] if include_entries else []

    group_counts: dict[str, int] = {GROUP_HISTORICAL: 0, GROUP_MASTER: 0}
    domain_counts: dict[str, int] = {}
    for item in all_entries:
        group = _text(item.get('group')).lower()
        if group in group_counts:
            group_counts[group] += 1
        domain = _text(item.get('domain')).lower() or 'other'
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    return {
        'generated_at_utc': _now_utc_iso(),
        'source_id': 'TUSHARE',
        'source_name': 'TUSHARE数据',
        'root': str(root),
        'classification_csv': str(classification_csv),
        'storage_map_csv': str(storage_map_csv),
        'catalog_csv': str(catalog_csv),
        'total_endpoints': len(all_entries),
        'group_counts': group_counts,
        'domain_counts': dict(sorted(domain_counts.items())),
        'offset': safe_offset,
        'limit': safe_limit,
        'entries': entry_payload,
    }


def get_tushare_registry_for_context(*, max_endpoints: int = 260) -> dict[str, Any]:
    payload = get_tushare_factor_registry(limit=max(0, int(max_endpoints)), offset=0, include_entries=True)
    entries = list(payload.get('entries', []))
    return {
        'source_id': payload.get('source_id', 'TUSHARE'),
        'source_name': payload.get('source_name', 'TUSHARE数据'),
        'total_endpoints': int(payload.get('total_endpoints', len(entries))),
        'group_counts': dict(payload.get('group_counts', {})),
        'domain_counts': dict(payload.get('domain_counts', {})),
        'truncated': int(payload.get('total_endpoints', len(entries))) > len(entries),
        'entries': entries,
        'endpoints': [str(item.get('endpoint', '')) for item in entries if _text(item.get('endpoint'))],
    }

