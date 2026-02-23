from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.tools.factor_registry import get_tushare_factor_registry


DATE_SORT_CANDIDATES = (
    'trade_date',
    'date',
    'cal_date',
    'end_date',
    'ann_date',
    'f_ann_date',
    'start_date',
    'list_date',
    'ipo_date',
    'report_date',
    'pub_date',
    'publish_date',
    'datetime',
    'trade_time',
    'time',
)


def _text(value: Any) -> str:
    return str(value or '').strip()


def _parse_ymd(value: str) -> str:
    raw = _text(value)
    if not raw:
        return ''
    if len(raw) == 8 and raw.isdigit():
        return raw
    for fmt in (
        '%Y-%m-%d',
        '%Y/%m/%d',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S',
        '%Y/%m/%d %H:%M:%S',
    ):
        try:
            return datetime.strptime(raw[:19], fmt).strftime('%Y%m%d')
        except ValueError:
            continue
    head = raw[:10]
    head = head.replace('/', '-')
    try:
        return datetime.strptime(head, '%Y-%m-%d').strftime('%Y%m%d')
    except ValueError:
        pass
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        token = digits[:8]
        try:
            return datetime.strptime(token, '%Y%m%d').strftime('%Y%m%d')
        except ValueError:
            return ''
    return ''


def _normalize_window(start_date: str, end_date: str) -> tuple[str, str]:
    start = _parse_ymd(start_date)
    end = _parse_ymd(end_date)
    if start and end and end < start:
        return end, start
    return start, end


def _detect_date_field(fieldnames: list[str]) -> str:
    normalized = {str(name).strip().lower(): str(name).strip() for name in fieldnames}
    for candidate in DATE_SORT_CANDIDATES:
        if candidate in normalized:
            return normalized[candidate]
    return ''


def _endpoint_lookup() -> dict[str, dict[str, Any]]:
    payload = get_tushare_factor_registry(limit=0, offset=0, include_entries=True)
    entries = payload.get('entries', [])
    lookup: dict[str, dict[str, Any]] = {}
    for item in entries:
        endpoint = _text(item.get('endpoint'))
        if endpoint and endpoint not in lookup:
            lookup[endpoint] = item
    return lookup


def _resolve_file(endpoint_meta: dict[str, Any], ticker: str) -> tuple[Path, str]:
    file_hint = _text(endpoint_meta.get('local_path_hint'))
    if not file_hint:
        return Path(''), 'FILE_HINT_MISSING'
    if file_hint.endswith('*.csv'):
        base = Path(file_hint[:-5])
        fallback_all = base.parent / 'all.csv'
        if not base.exists() or not base.is_dir():
            if fallback_all.exists() and fallback_all.is_file():
                return fallback_all, 'OK'
            return Path(''), 'SYMBOL_DIR_MISSING'
        normalized_ticker = _text(ticker)
        if normalized_ticker:
            candidates = sorted(base.glob(f'{normalized_ticker}*.csv'))
            if candidates:
                return candidates[0], 'OK'
            if fallback_all.exists() and fallback_all.is_file():
                return fallback_all, 'OK'
            return Path(''), 'SYMBOL_FILE_MISSING'
        candidates = sorted(base.glob('*.csv'))
        if candidates:
            return candidates[0], 'OK'
        if fallback_all.exists() and fallback_all.is_file():
            return fallback_all, 'OK'
        return Path(''), 'SYMBOL_DIR_EMPTY'
    path = Path(file_hint)
    if not path.exists() or not path.is_file():
        return Path(''), 'CSV_FILE_MISSING'
    return path, 'OK'


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = [str(x).strip() for x in (reader.fieldnames or [])]
        rows = [{str(k): _text(v) for k, v in row.items()} for row in reader]
    return fields, rows


def _slice_rows(
    rows: list[dict[str, str]],
    *,
    date_field: str,
    start_date: str,
    end_date: str,
    limit: int,
    order: str,
) -> tuple[list[dict[str, str]], int, str]:
    prepared: list[tuple[str, dict[str, str]]] = []
    latest_date = ''
    for row in rows:
        token = _parse_ymd(row.get(date_field, '')) if date_field else ''
        if date_field and not token:
            continue
        if start_date and token and token < start_date:
            continue
        if end_date and token and token > end_date:
            continue
        prepared.append((token, row))
        if token and token > latest_date:
            latest_date = token

    if date_field:
        prepared.sort(key=lambda item: item[0])
    matched = len(prepared)
    ordered_rows = [item[1] for item in prepared]
    if order == 'desc':
        ordered_rows = list(reversed(ordered_rows))
    safe_limit = max(1, int(limit))
    return ordered_rows[:safe_limit], matched, latest_date


def _build_slice_summary(
    *,
    endpoint: str,
    endpoint_meta: dict[str, Any],
    ticker: str,
    start_date: str,
    end_date: str,
    limit: int,
    order: str,
    include_rows: bool,
) -> dict[str, Any]:
    file_path, resolve_code = _resolve_file(endpoint_meta, ticker)
    if resolve_code != 'OK':
        return {
            'endpoint': endpoint,
            'group': endpoint_meta.get('group', ''),
            'domain': endpoint_meta.get('domain', ''),
            'ticker': ticker,
            'status': 'ERROR',
            'message': resolve_code,
            'file': '',
            'date_field': '',
            'requested_start_date': start_date,
            'requested_end_date': end_date,
            'total_rows': 0,
            'matched_rows': 0,
            'returned_rows': 0,
            'latest_date': '',
            'stale': False,
            'fields': [],
            'rows': [],
        }

    try:
        fields, all_rows = _read_rows(file_path)
    except Exception as exc:  # noqa: BLE001
        return {
            'endpoint': endpoint,
            'group': endpoint_meta.get('group', ''),
            'domain': endpoint_meta.get('domain', ''),
            'ticker': ticker,
            'status': 'ERROR',
            'message': f'CSV_READ_ERROR: {exc}',
            'file': str(file_path),
            'date_field': '',
            'requested_start_date': start_date,
            'requested_end_date': end_date,
            'total_rows': 0,
            'matched_rows': 0,
            'returned_rows': 0,
            'latest_date': '',
            'stale': False,
            'fields': [],
            'rows': [],
        }

    date_field = _detect_date_field(fields)
    sliced_rows, matched_rows, latest_date = _slice_rows(
        all_rows,
        date_field=date_field,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        order=order,
    )
    returned_rows = len(sliced_rows)
    stale = bool(end_date and latest_date and latest_date < end_date)
    if returned_rows > 0:
        status = 'OK'
        message = 'ok'
    elif matched_rows == 0 and all_rows:
        status = 'PARTIAL'
        message = 'WINDOW_EMPTY'
    else:
        status = 'ERROR'
        message = 'NO_DATA'

    return {
        'endpoint': endpoint,
        'group': endpoint_meta.get('group', ''),
        'domain': endpoint_meta.get('domain', ''),
        'ticker': ticker,
        'status': status,
        'message': message,
        'file': str(file_path),
        'date_field': date_field,
        'requested_start_date': start_date,
        'requested_end_date': end_date,
        'total_rows': len(all_rows),
        'matched_rows': matched_rows,
        'returned_rows': returned_rows,
        'latest_date': latest_date,
        'stale': stale,
        'fields': fields,
        'rows': sliced_rows if include_rows else [],
    }


def query_local_datasets(
    *,
    endpoints: list[str],
    ticker: str = '',
    start_date: str = '',
    end_date: str = '',
    limit_per_endpoint: int = 10,
    max_endpoints: int = 16,
    order: str = 'desc',
    include_rows: bool = True,
) -> dict[str, Any]:
    resolved_order = 'asc' if str(order).strip().lower() == 'asc' else 'desc'
    start_token, end_token = _normalize_window(start_date, end_date)
    normalized_ticker = _text(ticker)
    endpoint_lookup = _endpoint_lookup()

    requested: list[str] = []
    for item in endpoints:
        endpoint = _text(item)
        if not endpoint or endpoint in requested:
            continue
        requested.append(endpoint)
    safe_max_endpoints = max(1, int(max_endpoints))
    selected_endpoints = requested[:safe_max_endpoints]

    slices: list[dict[str, Any]] = []
    for endpoint in selected_endpoints:
        meta = endpoint_lookup.get(endpoint)
        if meta is None:
            slices.append(
                {
                    'endpoint': endpoint,
                    'group': '',
                    'domain': '',
                    'ticker': normalized_ticker,
                    'status': 'ERROR',
                    'message': 'ENDPOINT_NOT_REGISTERED',
                    'file': '',
                    'date_field': '',
                    'requested_start_date': start_token,
                    'requested_end_date': end_token,
                    'total_rows': 0,
                    'matched_rows': 0,
                    'returned_rows': 0,
                    'latest_date': '',
                    'stale': False,
                    'fields': [],
                    'rows': [],
                }
            )
            continue
        slices.append(
            _build_slice_summary(
                endpoint=endpoint,
                endpoint_meta=meta,
                ticker=normalized_ticker,
                start_date=start_token,
                end_date=end_token,
                limit=max(1, int(limit_per_endpoint)),
                order=resolved_order,
                include_rows=include_rows,
            )
        )

    ok_slices = sum(1 for item in slices if item.get('status') == 'OK')
    partial_slices = sum(1 for item in slices if item.get('status') == 'PARTIAL')
    error_slices = sum(1 for item in slices if item.get('status') == 'ERROR')

    status = 'OK'
    if error_slices > 0 and ok_slices == 0 and partial_slices == 0:
        status = 'ERROR'
    elif error_slices > 0 or partial_slices > 0:
        status = 'PARTIAL'

    return {
        'status': status,
        'requested_endpoints': requested,
        'resolved_endpoints': [str(item.get('endpoint', '')) for item in slices if _text(item.get('endpoint'))],
        'ticker': normalized_ticker,
        'requested_start_date': start_token,
        'requested_end_date': end_token,
        'slices': slices,
        'audit': {
            'requested_count': len(requested),
            'resolved_count': len(slices),
            'ok_slices': ok_slices,
            'partial_slices': partial_slices,
            'error_slices': error_slices,
        },
    }


def default_backtest_window(asof_iso: str, *, lookback_days: int = 120) -> tuple[str, str]:
    normalized_lookback_days = max(1, int(lookback_days))
    asof_date = _parse_ymd(asof_iso)
    if asof_date:
        end = datetime.strptime(asof_date, '%Y%m%d').date()
    else:
        end = date.today()
    start = end - timedelta(days=normalized_lookback_days)
    return start.strftime('%Y%m%d'), end.strftime('%Y%m%d')
