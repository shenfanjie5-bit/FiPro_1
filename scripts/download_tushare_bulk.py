from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
import time
from pathlib import Path
import re
from typing import Any, Callable
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


DEFAULT_ROOT = '/Volumes/dockcase2tb/database_all'
DEFAULT_CLASSIFICATION_CSV = 'docs/tushare_api_history_classification.csv'
DEFAULT_STORAGE_MAP_CSV = 'docs/tushare_api_storage_map.csv'
DEFAULT_CATALOG_CSV = 'docs/tushare_api_catalog.csv'
DEFAULT_SOURCE_CATALOG = '/Volumes/dockcase2tb/tushare_doc2_leaf_api.csv'
DEFAULT_BASE_URL = 'https://api.tushare.pro'
DEFAULT_START_DATE = '19900101'

MASTER_INDEX_RELATIVE = '_meta/manifests/master_dictionary_index.csv'
ERROR_LOG_RELATIVE = '_meta/qa/tushare_bulk_download_errors.csv'
MANIFEST_RELATIVE = '_meta/manifests/tushare_bulk_download_manifest.json'
TUSHARE_STATUS_RELATIVE = '_meta/manifests/data_source_status/tushare.json'

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


STOCK_PER_SYMBOL_APIS = {
    'daily',
    'weekly',
    'monthly',
    'stk_weekly_monthly',
    'stk_week_month_adj',
    'daily_basic',
    'stk_limit',
    'bak_daily',
    'income',
    'balancesheet',
    'cashflow',
    'forecast',
    'express',
    'dividend',
    'fina_indicator',
    'fina_audit',
    'fina_mainbz',
    'disclosure_date',
    'pledge_stat',
    'pledge_detail',
    'repurchase',
    'share_float',
    'block_trade',
    'stk_holdernumber',
    'stk_holdertrade',
    'report_rc',
    'cyq_perf',
    'cyq_chips',
    'stk_factor_pro',
    'stk_auction_o',
    'stk_auction_c',
    'stk_nineturn',
    'stk_ah_comparison',
    'stk_surv',
    'broker_recommend',
    'margin_detail',
    'moneyflow',
    'moneyflow_ths',
    'moneyflow_dc',
}

INDEX_PER_SYMBOL_APIS = {
    'index_daily',
    'index_weekly',
    'idx_mins',
    'index_monthly',
    'index_weight',
    'index_dailybasic',
    'index_member_all',
    'sw_daily',
    'ci_index_member',
    'ci_daily',
    'index_global',
    'idx_factor_pro',
}

ETF_PER_SYMBOL_APIS = {
    'fund_daily',
    'etf_share_size',
    'stk_mins',
}

FUND_PER_SYMBOL_APIS = {
    'fund_share',
    'fund_nav',
    'fund_div',
    'fund_portfolio',
    'fund_factor_pro',
}

FUTURE_PER_SYMBOL_APIS = {
    'fut_daily',
    'fut_weekly_monthly',
    'ft_mins',
    'fut_settle',
    'fut_holding',
    'ft_limit',
}

OPTION_PER_SYMBOL_APIS = {
    'opt_daily',
    'opt_mins',
}

BOND_PER_SYMBOL_APIS = {
    'cb_issue',
    'cb_call',
    'cb_rate',
    'cb_daily',
    'cb_factor_pro',
    'cb_price_chg',
    'cb_share',
}

HK_PER_SYMBOL_APIS = {
    'hk_daily',
    'hk_daily_adj',
    'hk_adjfactor',
    'hk_mins',
    'hk_income',
    'hk_balancesheet',
    'hk_cashflow',
    'hk_fina_indicator',
}

US_PER_SYMBOL_APIS = {
    'us_daily',
    'us_daily_adj',
    'us_adjfactor',
    'us_income',
    'us_balancesheet',
    'us_cashflow',
    'us_fina_indicator',
}


@dataclass(frozen=True)
class ApiSpec:
    group: str
    api: str
    label: str
    raw_path: str
    normalized_path: str
    rate_limit: str
    note: str

    @property
    def key(self) -> str:
        return f'{self.group}:{self.api}:{self.raw_path}'


@dataclass
class SymbolItem:
    code: str
    name: str


@dataclass(frozen=True)
class QueryStrategy:
    name: str
    use_dates: bool
    use_limit: bool
    use_offset: bool


@dataclass
class FetchOutcome:
    strategy: str = ''
    rows: int = 0
    pages: int = 0
    truncated: bool = False
    skipped_existing: bool = False
    error: str = ''


class TushareApiError(RuntimeError):
    def __init__(self, code: int, msg: str, api_name: str) -> None:
        self.code = int(code)
        self.msg = str(msg)
        self.api_name = api_name
        super().__init__(f'TUSHARE_ERROR api={api_name} code={code} msg={msg}')


class _ApiRateLimiter:
    """Simple per-API min-interval limiter (thread-safe).

    This complements the CLI --sleep-seconds by enforcing a floor per api_name.
    """

    def __init__(self, *, default_min_interval: float) -> None:
        self.default_min_interval = max(0.0, float(default_min_interval))
        self._lock = threading.Lock()
        self._next_at: dict[str, float] = {}

    def wait(self, api_name: str, *, min_interval: float | None = None) -> None:
        interval = self.default_min_interval if min_interval is None else max(self.default_min_interval, float(min_interval))
        if interval <= 0:
            return
        now = time.time()
        with self._lock:
            next_at = self._next_at.get(api_name, 0.0)
            if next_at > now:
                sleep_for = next_at - now
                # update next slot before sleeping to avoid thundering herd
                self._next_at[api_name] = next_at + interval
            else:
                sleep_for = 0.0
                self._next_at[api_name] = now + interval
        if sleep_for > 0:
            time.sleep(sleep_for)


class TushareClient:
    def __init__(
        self,
        *,
        token: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
        http_client: httpx.Client | None = None,
        rate_limiter: _ApiRateLimiter | None = None,
        api_min_intervals: dict[str, float] | None = None,
    ) -> None:
        self.token = token.strip()
        self.base_url = base_url.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._client = http_client
        self._rate_limiter = rate_limiter
        self._api_min_intervals = api_min_intervals or {}

    def query(self, *, api_name: str, params: dict[str, Any], fields: str = '') -> tuple[list[str], list[dict[str, Any]]]:
        payload = {
            'api_name': api_name,
            'token': self.token,
            'params': params,
            'fields': fields,
        }

        # Per-API limiter (thread-safe). This is important for per-symbol concurrency.
        if self._rate_limiter is not None:
            self._rate_limiter.wait(api_name, min_interval=self._api_min_intervals.get(api_name))

        # Retry policy:
        # - network/json: retry (existing)
        # - TushareApiError: retry only if it looks like rate-limit/busy
        attempts = max(1, self.max_retries)
        last_exc: Exception | None = None

        retryable_codes = {-2001, -4000}
        retryable_hints = ('频率', '频控', 'too many', 'busy', 'timeout', 'rate')

        for idx in range(attempts):
            try:
                if self._client is None:
                    response = httpx.post(self.base_url, json=payload, timeout=self.timeout_seconds)
                else:
                    response = self._client.post(self.base_url, json=payload, timeout=self.timeout_seconds)
                response.raise_for_status()
                body = response.json()
                code = int(body.get('code', 0))
                if code != 0:
                    raise TushareApiError(code=code, msg=str(body.get('msg', '')), api_name=api_name)
                data = body.get('data') or {}
                field_names = [str(x) for x in (data.get('fields') or [])]
                items = data.get('items') or []
                rows: list[dict[str, Any]] = []
                for item in items:
                    row = {name: value for name, value in zip(field_names, item, strict=False)}
                    rows.append(row)
                return field_names, rows
            except TushareApiError as exc:
                last_exc = exc
                msg = exc.msg.lower()
                is_retryable = (exc.code in retryable_codes) or any(h in msg for h in retryable_hints)
                if (idx + 1) < attempts and is_retryable:
                    wait = max(0.6, self.retry_backoff_seconds) * (2**idx)
                    time.sleep(wait)
                    continue
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                if idx + 1 >= attempts:
                    raise
                wait = self.retry_backoff_seconds * (2**idx)
                time.sleep(wait)

        if last_exc is None:
            raise RuntimeError('query failed with unknown reason')
        raise last_exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Bulk download Tushare datasets: historical/master both into mapped API folders, plus manifest/index files.'
    )
    parser.add_argument('--root', default=DEFAULT_ROOT, help='Storage root (already prepared by prepare_tushare_storage.py).')
    parser.add_argument('--classification-csv', default=DEFAULT_CLASSIFICATION_CSV)
    parser.add_argument('--storage-map-csv', default=DEFAULT_STORAGE_MAP_CSV)
    parser.add_argument('--catalog-csv', default=DEFAULT_CATALOG_CSV)
    parser.add_argument(
        '--source-catalog',
        default=DEFAULT_SOURCE_CATALOG,
        help='Original Tushare leaf catalog CSV with level1..level4,label,api for direct path matching.',
    )
    parser.add_argument('--token', default='', help='TUSHARE token. If empty, read from env TUSHARE_TOKEN or .env.')
    parser.add_argument('--base-url', default='', help='Tushare base URL. Default: env TUSHARE_BASE_URL or https://api.tushare.pro')
    parser.add_argument('--start-date', default=DEFAULT_START_DATE, help='Historical start date, YYYYMMDD.')
    parser.add_argument('--end-date', default='', help='Historical end date, YYYYMMDD. Default: today.')
    parser.add_argument('--page-size', type=int, default=5000)
    parser.add_argument('--max-pages', type=int, default=300, help='Per-window page cap when offset pagination is enabled.')
    parser.add_argument('--sleep-seconds', type=float, default=0.08, help='Sleep between requests to reduce rate-limit hits.')
    parser.add_argument('--workers', type=int, default=3, help='Concurrent workers for per-symbol APIs (default: 3).')
    parser.add_argument('--mins-start-date', default='', help='Override start date (YYYYMMDD) for minute-level APIs (e.g., stk_mins/idx_mins/ft_mins/opt_mins/hk_mins). Useful for backfilling only recent years.')
    parser.add_argument('--timeout-seconds', type=float, default=30.0)
    parser.add_argument('--max-retries', type=int, default=3)
    parser.add_argument('--retry-backoff-seconds', type=float, default=0.8)
    parser.add_argument('--max-symbols', type=int, default=0, help='Limit symbols per API (0 = all).')
    parser.add_argument(
        '--groups',
        default='historical,master',
        help='Comma-separated groups from classification file, usually historical,master.',
    )
    parser.add_argument(
        '--date-chunk-mode',
        choices=('year', 'none'),
        default='year',
        help='For date-compatible APIs: year chunks usually reduce truncation risk.',
    )
    parser.add_argument('--api-allowlist', default='', help='Optional comma-separated API names to run.')
    parser.add_argument(
        '--disable-date-sort',
        action='store_true',
        help='Disable post-write CSV date ascending normalization.',
    )
    parser.add_argument('--overwrite', action='store_true', help='Overwrite existing output CSV files.')
    parser.add_argument('--dry-run', action='store_true', help='Do not write files or call network.')
    parser.add_argument(
        '--repair-existing-root',
        default='',
        help='Optional root path: recursively repair existing CSV date order under this directory.',
    )
    parser.add_argument('--repair-existing-glob', default='**/*.csv')
    parser.add_argument('--repair-max-files', type=int, default=0, help='0 means no limit.')
    parser.add_argument('--repair-only', action='store_true', help='Run repair mode only, then exit.')
    return parser.parse_args()


def _text(value: Any) -> str:
    return str(value or '').strip()


def _sanitize_path_part(value: str) -> str:
    out = _text(value)
    if not out:
        return ''
    out = out.replace('/', '／')
    out = out.replace(':', '：').replace('*', '＊').replace('?', '？')
    out = out.replace('"', "'").replace('<', '＜').replace('>', '＞').replace('|', '｜')
    return out.strip()


def _normalize_rel_path(raw_path: str) -> str:
    parts = [_sanitize_path_part(x) for x in raw_path.split('/') if _text(x)]
    return '/'.join([x for x in parts if x])


def _safe_filename_component(value: str) -> str:
    out = _text(value)
    out = out.replace('/', '_').replace('\\', '_')
    out = out.replace(':', '_').replace('*', '_').replace('?', '_')
    out = out.replace('"', "'").replace('<', '_').replace('>', '_').replace('|', '_')
    out = out.replace('\n', ' ').replace('\r', ' ')
    return out.strip() or 'UNKNOWN'


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key, raw_value = line.split('=', 1)
        values[key.strip()] = raw_value.strip().strip('"').strip("'")
    return values


def _resolve_token(cli_token: str, project_root: Path) -> str:
    if _text(cli_token):
        return _text(cli_token)
    env_token = _text(os.getenv('TUSHARE_TOKEN', ''))
    if env_token:
        return env_token
    dotenv_values = _read_env_file(project_root / '.env')
    return _text(dotenv_values.get('TUSHARE_TOKEN', ''))


def _resolve_base_url(cli_base_url: str, project_root: Path) -> str:
    if _text(cli_base_url):
        return _text(cli_base_url)
    env_url = _text(os.getenv('TUSHARE_BASE_URL', ''))
    if env_url:
        return env_url
    dotenv_values = _read_env_file(project_root / '.env')
    return _text(dotenv_values.get('TUSHARE_BASE_URL', '')) or DEFAULT_BASE_URL


def _parse_ymd(raw: str) -> date:
    return datetime.strptime(raw, '%Y%m%d').date()


def _ymd(d: date) -> str:
    return d.strftime('%Y%m%d')


def _build_windows(start_ymd: str, end_ymd: str, chunk_mode: str) -> list[tuple[str, str]]:
    start = _parse_ymd(start_ymd)
    end = _parse_ymd(end_ymd)
    if end < start:
        raise ValueError(f'end_date {end_ymd} earlier than start_date {start_ymd}')
    if chunk_mode == 'none':
        return [(start_ymd, end_ymd)]
    windows: list[tuple[str, str]] = []
    current = date(start.year, 1, 1)
    if start > current:
        current = start
    while current <= end:
        year_end = date(current.year, 12, 31)
        boundary = min(year_end, end)
        win_start = max(current, start)
        windows.append((_ymd(win_start), _ymd(boundary)))
        current = boundary + timedelta(days=1)
    return windows


def _load_storage_map(path: Path) -> dict[tuple[str, str, str], str]:
    mapping: dict[tuple[str, str, str], str] = {}
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            api = _text(row.get('api'))
            label = _text(row.get('label'))
            raw_path = _text(row.get('raw_relative_path'))
            normalized = _text(row.get('normalized_relative_path'))
            if not api:
                continue
            mapping[(api, label, raw_path)] = normalized or _normalize_rel_path(raw_path)
    return mapping


def _load_source_catalog_map(path: Path) -> dict[tuple[str, str, str], str]:
    mapping: dict[tuple[str, str, str], str] = {}
    if not path.exists():
        return mapping
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            api = _text(row.get('api'))
            label = _text(row.get('label'))
            raw_parts = [
                _text(row.get('level1')),
                _text(row.get('level2')),
                _text(row.get('level3')),
                _text(row.get('level4')),
            ]
            raw_parts = [x for x in raw_parts if x]
            raw_path = '/'.join(raw_parts)
            if not api or not raw_path:
                continue
            mapping[(api, label, raw_path)] = _normalize_rel_path(raw_path)
    return mapping


def _load_catalog(path: Path) -> dict[tuple[str, str], tuple[str, str]]:
    lookup: dict[tuple[str, str], tuple[str, str]] = {}
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            api = _text(row.get('api'))
            label = _text(row.get('api_name'))
            if not api:
                continue
            rate_limit = _text(row.get('rate_limit'))
            note = _text(row.get('note'))
            lookup[(api, label)] = (rate_limit, note)
    return lookup


def _load_specs(
    *,
    classification_csv: Path,
    storage_map_csv: Path,
    source_catalog_csv: Path,
    catalog_csv: Path,
    groups: set[str],
    allowlist: set[str],
) -> list[ApiSpec]:
    storage_map = _load_storage_map(storage_map_csv)
    source_map = _load_source_catalog_map(source_catalog_csv)
    catalog = _load_catalog(catalog_csv)

    specs: list[ApiSpec] = []
    seen: set[tuple[str, str, str, str]] = set()
    with classification_csv.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            group = _text(row.get('group'))
            api = _text(row.get('api'))
            label = _text(row.get('label'))
            raw_path = _text(row.get('path'))
            if not group or not api:
                continue
            if group not in groups:
                continue
            if allowlist and api not in allowlist:
                continue
            normalized_path = source_map.get((api, label, raw_path)) or storage_map.get((api, label, raw_path), _normalize_rel_path(raw_path))
            rate_limit, note = catalog.get((api, label), ('', ''))
            key = (group, api, label, normalized_path)
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                ApiSpec(
                    group=group,
                    api=api,
                    label=label,
                    raw_path=raw_path,
                    normalized_path=normalized_path,
                    rate_limit=rate_limit,
                    note=note,
                )
            )
    return specs


def _candidate_strategies(enable_dates: bool) -> list[QueryStrategy]:
    strategies: list[QueryStrategy] = []
    if enable_dates:
        strategies.extend(
            [
                QueryStrategy(name='dates+limit+offset', use_dates=True, use_limit=True, use_offset=True),
                QueryStrategy(name='dates+limit', use_dates=True, use_limit=True, use_offset=False),
                QueryStrategy(name='dates_only', use_dates=True, use_limit=False, use_offset=False),
            ]
        )
    strategies.extend(
        [
            QueryStrategy(name='limit+offset', use_dates=False, use_limit=True, use_offset=True),
            QueryStrategy(name='limit_only', use_dates=False, use_limit=True, use_offset=False),
            QueryStrategy(name='no_paging', use_dates=False, use_limit=False, use_offset=False),
        ]
    )
    return strategies


def _looks_like_hard_auth_error(exc: TushareApiError) -> bool:
    text = exc.msg.lower()
    if exc.code in {-2002, -2003, -2016, -1000}:
        return True
    auth_hints = ('permission', 'token', 'unauthorized', 'forbidden', '积分', '权限', 'auth')
    return any(h in text for h in auth_hints)


def _build_query_params(
    *,
    base_params: dict[str, Any],
    strategy: QueryStrategy,
    start_date: str | None,
    end_date: str | None,
    page_size: int,
    offset: int,
) -> dict[str, Any]:
    params = dict(base_params)
    if strategy.use_dates and start_date and end_date:
        params['start_date'] = start_date
        params['end_date'] = end_date
    if strategy.use_limit:
        params['limit'] = page_size
    if strategy.use_offset:
        params['offset'] = offset
    return params


def _run_fetch(
    *,
    client: TushareClient,
    api_name: str,
    base_params: dict[str, Any],
    windows: list[tuple[str, str]],
    enable_dates: bool,
    page_size: int,
    max_pages: int,
    sleep_seconds: float,
    on_page: Callable[[list[str], list[dict[str, Any]]], None],
) -> FetchOutcome:
    outcome = FetchOutcome()
    strategies = _candidate_strategies(enable_dates=enable_dates)
    first_window = windows[0] if windows else ('', '')

    first_probe_fields: list[str] = []
    first_probe_rows: list[dict[str, Any]] = []
    chosen: QueryStrategy | None = None
    last_error: Exception | None = None

    for strategy in strategies:
        params = _build_query_params(
            base_params=base_params,
            strategy=strategy,
            start_date=first_window[0] if strategy.use_dates else None,
            end_date=first_window[1] if strategy.use_dates else None,
            page_size=page_size,
            offset=0,
        )
        try:
            fields, rows = client.query(api_name=api_name, params=params, fields='')
            chosen = strategy
            first_probe_fields = fields
            first_probe_rows = rows
            break
        except TushareApiError as exc:
            last_error = exc
            if _looks_like_hard_auth_error(exc):
                outcome.error = f'api_error code={exc.code} msg={exc.msg}'
                return outcome
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    if chosen is None:
        if last_error is None:
            outcome.error = 'no_valid_strategy'
        else:
            outcome.error = str(last_error)
        return outcome

    outcome.strategy = chosen.name

    active_windows = windows if chosen.use_dates else [('', '')]

    for win_idx, (win_start, win_end) in enumerate(active_windows):
        offset = 0
        page_idx = 0
        while True:
            if win_idx == 0 and page_idx == 0:
                fields = first_probe_fields
                rows = first_probe_rows
            else:
                params = _build_query_params(
                    base_params=base_params,
                    strategy=chosen,
                    start_date=win_start if chosen.use_dates else None,
                    end_date=win_end if chosen.use_dates else None,
                    page_size=page_size,
                    offset=offset,
                )
                try:
                    fields, rows = client.query(api_name=api_name, params=params, fields='')
                except Exception as exc:  # noqa: BLE001
                    outcome.error = str(exc)
                    return outcome

            on_page(fields, rows)
            outcome.pages += 1
            outcome.rows += len(rows)

            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

            if not rows:
                break
            if chosen.use_limit and len(rows) < page_size:
                break
            if chosen.use_limit and (not chosen.use_offset) and len(rows) >= page_size:
                outcome.truncated = True
                break
            if chosen.use_offset:
                page_idx += 1
                if page_idx >= max_pages:
                    outcome.truncated = True
                    break
                offset += page_size
                continue
            break

    return outcome


class CsvStreamWriter:
    def __init__(self, path: Path, overwrite: bool) -> None:
        self.path = path
        self.overwrite = overwrite
        self.tmp_path = self.path.with_name(f'.{self.path.name}.part')
        self.handle: Any = None
        self.writer: csv.DictWriter[str] | None = None
        self.fieldnames: list[str] = []
        self.rows_written = 0

    def _ensure_open(self, fieldnames: list[str]) -> None:
        if self.writer is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.tmp_path.exists():
            self.tmp_path.unlink()
        self.handle = self.tmp_path.open('w', encoding='utf-8', newline='')
        self.fieldnames = list(fieldnames)
        self.writer = csv.DictWriter(self.handle, fieldnames=self.fieldnames, extrasaction='ignore')
        self.writer.writeheader()

    def write_page(self, fields: list[str], rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        fieldnames = list(fields) if fields else list(rows[0].keys())
        if not fieldnames:
            return
        self._ensure_open(fieldnames)
        assert self.writer is not None
        for row in rows:
            self.writer.writerow(row)
            self.rows_written += 1

    def close(self, *, success: bool) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None
            self.writer = None
        if self.tmp_path.exists():
            if success and self.rows_written > 0:
                if self.path.exists() and self.overwrite:
                    self.path.unlink()
                self.tmp_path.replace(self.path)
            else:
                self.tmp_path.unlink()


def _find_sort_fields(fieldnames: list[str]) -> list[str]:
    lower_map = {name.lower(): name for name in fieldnames}
    found: list[str] = []
    for candidate in DATE_SORT_CANDIDATES:
        original = lower_map.get(candidate)
        if original:
            found.append(original)
    return found


def _normalize_sort_token(value: Any) -> tuple[int, str]:
    text = _text(value)
    if not text:
        return (2, '')
    raw = text.strip()
    # YYYYMMDD / YYYYMMDDHHMMSS
    if raw.isdigit():
        if len(raw) == 8:
            return (0, f'{raw}000000')
        if len(raw) == 14:
            return (0, raw)
    # YYYY-MM-DD[ HH:MM:SS] or YYYY/MM/DD[ HH:MM:SS]
    canon = raw.replace('/', '-')
    for fmt in (
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
        '%Y-%m',
    ):
        try:
            dt = datetime.strptime(canon, fmt)
            return (0, dt.strftime('%Y%m%d%H%M%S'))
        except ValueError:
            pass
    # Fallback: try to pick date-like digits from mixed text.
    m = re.search(r'(\d{4})[-/]?(\d{2})[-/]?(\d{2})(?:\D?(\d{2})(\d{2})(\d{2})?)?', raw)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        hh = hh or '00'
        mm = mm or '00'
        ss = ss or '00'
        return (0, f'{y}{mo}{d}{hh}{mm}{ss}')
    return (1, raw)


def _sort_csv_file_by_date(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return (False, 'missing')
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            return (False, 'no_header')
        sort_fields = _find_sort_fields(fieldnames)
        if not sort_fields:
            return (False, 'no_date_field')
        rows = list(reader)
    if len(rows) <= 1:
        return (False, 'rows<=1')
    rows.sort(key=lambda row: tuple(_normalize_sort_token(row.get(f, '')) for f in sort_fields))
    tmp = path.with_name(f'.{path.name}.sorted')
    with tmp.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)
    return (True, ','.join(sort_fields))


def _repair_existing_csv_date_order(root: Path, *, include_glob: str, max_files: int) -> dict[str, int]:
    files = sorted([p for p in root.glob(include_glob) if p.is_file() and p.suffix.lower() == '.csv' and not p.name.startswith('._')])
    if max_files > 0:
        files = files[:max_files]
    total = 0
    sorted_ok = 0
    skipped = 0
    failed = 0
    for idx, file_path in enumerate(files, start=1):
        total += 1
        try:
            changed, reason = _sort_csv_file_by_date(file_path)
            if changed:
                sorted_ok += 1
            else:
                skipped += 1
            if idx % 200 == 0 or idx == len(files):
                print(f'[repair] {idx}/{len(files)} sorted={sorted_ok} skipped={skipped} failed={failed}')
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f'[repair][error] file={file_path} error={exc}')
    return {
        'files': total,
        'sorted': sorted_ok,
        'skipped': skipped,
        'failed': failed,
    }


def _resolve_scope(spec: ApiSpec) -> str:
    root = spec.raw_path.split('/', 1)[0]
    api = spec.api

    if root == '股票数据':
        return 'stock' if api in STOCK_PER_SYMBOL_APIS else 'global'
    if root == 'ETF专题':
        return 'fund' if api in ETF_PER_SYMBOL_APIS else 'global'
    if root == '指数专题':
        return 'index' if api in INDEX_PER_SYMBOL_APIS else 'global'
    if root == '公募基金':
        return 'fund' if api in FUND_PER_SYMBOL_APIS else 'global'
    if root == '期货数据':
        return 'future' if api in FUTURE_PER_SYMBOL_APIS else 'global'
    if root == '期权数据':
        return 'option' if api in OPTION_PER_SYMBOL_APIS else 'global'
    if root == '债券专题':
        return 'bond' if api in BOND_PER_SYMBOL_APIS else 'global'
    if root == '港股数据':
        return 'hk' if api in HK_PER_SYMBOL_APIS else 'global'
    if root == '美股数据':
        return 'us' if api in US_PER_SYMBOL_APIS else 'global'
    return 'global'


def _scope_code_param(scope: str) -> str:
    # Most Tushare per-symbol endpoints use ts_code (including index endpoints like index_daily).
    # A few index-related endpoints instead require index_code; those are handled via per-API overrides.
    return 'ts_code'


def _collect_rows_for_pool(
    *,
    client: TushareClient,
    api_name: str,
    params_list: list[dict[str, Any]],
    page_size: int,
    max_pages: int,
    sleep_seconds: float,
) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []

    for params in params_list:
        windows = [('', '')]

        def on_page(_fields: list[str], rows: list[dict[str, Any]]) -> None:
            rows_out.extend(rows)

        _ = _run_fetch(
            client=client,
            api_name=api_name,
            base_params=params,
            windows=windows,
            enable_dates=False,
            page_size=page_size,
            max_pages=max_pages,
            sleep_seconds=sleep_seconds,
            on_page=on_page,
        )
    return rows_out


def _rows_to_symbol_pool(
    rows: list[dict[str, Any]],
    *,
    code_fields: tuple[str, ...],
    name_fields: tuple[str, ...],
) -> list[SymbolItem]:
    items: dict[str, str] = {}
    for row in rows:
        code = ''
        for field in code_fields:
            value = _text(row.get(field))
            if value:
                code = value
                break
        if not code:
            continue
        name = ''
        for field in name_fields:
            value = _text(row.get(field))
            if value:
                name = value
                break
        if not name:
            name = code
        if code not in items:
            items[code] = name
    return [SymbolItem(code=k, name=v) for k, v in sorted(items.items(), key=lambda x: x[0])]


def _build_symbol_pools(
    *,
    client: TushareClient,
    page_size: int,
    max_pages: int,
    sleep_seconds: float,
) -> tuple[dict[str, list[SymbolItem]], list[dict[str, str]]]:
    pools: dict[str, list[SymbolItem]] = {
        'stock': [],
        'index': [],
        'fund': [],
        'future': [],
        'option': [],
        'bond': [],
        'hk': [],
        'us': [],
    }
    errors: list[dict[str, str]] = []

    def fetch_pool(
        *,
        scope: str,
        api_name: str,
        params_list: list[dict[str, Any]],
        code_fields: tuple[str, ...] = ('ts_code',),
        name_fields: tuple[str, ...] = ('name', 'ts_name', 'symbol', 'bond_short_name'),
    ) -> None:
        try:
            rows = _collect_rows_for_pool(
                client=client,
                api_name=api_name,
                params_list=params_list,
                page_size=page_size,
                max_pages=max_pages,
                sleep_seconds=sleep_seconds,
            )
            pools[scope] = _rows_to_symbol_pool(rows, code_fields=code_fields, name_fields=name_fields)
        except Exception as exc:  # noqa: BLE001
            errors.append({'scope': scope, 'api': api_name, 'error': str(exc)})

    fetch_pool(
        scope='stock',
        api_name='stock_basic',
        params_list=[{'list_status': 'L'}, {'list_status': 'D'}, {'list_status': 'P'}],
        code_fields=('ts_code',),
        name_fields=('name',),
    )
    fetch_pool(
        scope='index',
        api_name='index_basic',
        params_list=[{}],
        code_fields=('ts_code',),
        name_fields=('name',),
    )
    fetch_pool(
        scope='fund',
        api_name='fund_basic',
        params_list=[{'market': 'E'}, {'market': 'O'}, {}],
        code_fields=('ts_code',),
        name_fields=('name',),
    )
    fetch_pool(
        scope='future',
        api_name='fut_basic',
        params_list=[{}],
        code_fields=('ts_code',),
        name_fields=('name', 'symbol'),
    )
    fetch_pool(
        scope='option',
        api_name='opt_basic',
        params_list=[{}],
        code_fields=('ts_code',),
        name_fields=('name',),
    )
    fetch_pool(
        scope='bond',
        api_name='cb_basic',
        params_list=[{}],
        code_fields=('ts_code',),
        name_fields=('bond_short_name', 'name'),
    )
    fetch_pool(
        scope='hk',
        api_name='hk_basic',
        params_list=[{}],
        code_fields=('ts_code',),
        name_fields=('name',),
    )
    fetch_pool(
        scope='us',
        api_name='us_basic',
        params_list=[{}],
        code_fields=('ts_code',),
        name_fields=('name',),
    )
    return pools, errors


def _append_error(
    error_rows: list[dict[str, str]],
    *,
    group: str,
    api: str,
    path: str,
    scope: str,
    identifier: str,
    message: str,
) -> None:
    error_rows.append(
        {
            'timestamp_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'group': group,
            'api': api,
            'path': path,
            'scope': scope,
            'identifier': identifier,
            'error': message,
        }
    )


def _write_errors(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=['timestamp_utc', 'group', 'api', 'path', 'scope', 'identifier', 'error'],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _write_tushare_status(
    *,
    root: Path,
    status: str,
    message: str,
    mode: str,
    run_id: str,
    extra: dict[str, Any] | None = None,
) -> None:
    status_file = root / TUSHARE_STATUS_RELATIVE
    status_file.parent.mkdir(parents=True, exist_ok=True)
    previous: dict[str, Any] = {}
    if status_file.exists():
        try:
            loaded = json.loads(status_file.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                previous = loaded
        except json.JSONDecodeError:
            previous = {}

    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload: dict[str, Any] = {
        'source_id': 'TUSHARE',
        'name': 'TUSHARE数据',
        'status': status,
        'label': '已完成' if status == 'COMPLETED' else ('更新中' if status == 'UPDATING' else '异常'),
        'message': message,
        'mode': mode,
        'run_id': run_id,
        'updated_at_utc': now_utc,
        'last_success_at_utc': previous.get('last_success_at_utc', ''),
        'last_error_at_utc': previous.get('last_error_at_utc', ''),
    }
    if status == 'COMPLETED':
        payload['last_success_at_utc'] = now_utc
    if status == 'ERROR':
        payload['last_error_at_utc'] = now_utc
    if extra:
        payload['extra'] = extra
    status_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _download_historical(
    *,
    client: TushareClient,
    specs: list[ApiSpec],
    root: Path,
    start_date: str,
    end_date: str,
    chunk_mode: str,
    page_size: int,
    max_pages: int,
    sleep_seconds: float,
    workers: int,
    mins_start_date: str,
    max_symbols: int,
    overwrite: bool,
    sort_by_date: bool,
    dry_run: bool,
    symbol_pools: dict[str, list[SymbolItem]],
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    # If an API fails with hard permission/auth errors, block it for the rest of the run
    # to avoid tens-of-thousands of repeated failing calls.
    blocked_apis: dict[str, str] = {}
    blocked_lock = threading.Lock()
    windows = _build_windows(start_date, end_date, chunk_mode)
    mins_windows = []
    mins_start_date = _text(mins_start_date)
    if mins_start_date:
        # Validate format early.
        _ = _parse_ymd(mins_start_date)
        mins_windows = _build_windows(mins_start_date, end_date, chunk_mode)

    minute_apis = {
        'stk_mins',
        'idx_mins',
        'ft_mins',
        'opt_mins',
        'hk_mins',
    }

    for idx, spec in enumerate(specs, start=1):
        with blocked_lock:
            blocked_reason = blocked_apis.get(spec.api, '')
        if blocked_reason:
            print(f'[historical {idx}/{len(specs)}] api={spec.api} label={spec.label} SKIP blocked: {blocked_reason}')
            _append_error(
                errors,
                group=spec.group,
                api=spec.api,
                path=spec.raw_path,
                scope='meta',
                identifier='blocked',
                message=f'blocked_api:{blocked_reason}',
            )
            results.append(
                {
                    'key': spec.key,
                    'api': spec.api,
                    'group': spec.group,
                    'scope': 'blocked',
                    'rows': 0,
                    'pages': 0,
                    'strategy': '',
                    'truncated': False,
                    'skipped_existing': False,
                    'error': f'blocked_api:{blocked_reason}',
                    'output': str(root.joinpath(*spec.normalized_path.split('/'))),
                }
            )
            continue

        scope = _resolve_scope(spec)
        target_dir = root.joinpath(*spec.normalized_path.split('/'))
        print(f'[historical {idx}/{len(specs)}] api={spec.api} label={spec.label} scope={scope}')

        # Per-API window override: minute-level APIs often need a smaller historical range.
        api_windows = mins_windows if (mins_windows and spec.api in minute_apis) else windows

        # Per-API required params.
        api_fixed_params: dict[str, dict[str, Any]] = {
            # These endpoints require freq (W/M). User requested W.
            'stk_weekly_monthly': {'freq': 'W'},
            'stk_week_month_adj': {'freq': 'W'},
        }

        if scope == 'global':
            out_file = target_dir / 'all.csv'
            if out_file.exists() and (not overwrite):
                results.append(
                    {
                        'key': spec.key,
                        'api': spec.api,
                        'group': spec.group,
                        'scope': scope,
                        'rows': 0,
                        'pages': 0,
                        'strategy': '',
                        'truncated': False,
                        'skipped_existing': True,
                        'error': '',
                        'output': str(out_file),
                    }
                )
                continue

            writer = CsvStreamWriter(out_file, overwrite=overwrite)

            def on_page(fields: list[str], rows: list[dict[str, Any]]) -> None:
                if dry_run:
                    return
                writer.write_page(fields, rows)

            if dry_run:
                outcome = FetchOutcome(strategy='dry_run', rows=0, pages=0)
            else:
                base_params = dict(api_fixed_params.get(spec.api, {}))
                outcome = _run_fetch(
                    client=client,
                    api_name=spec.api,
                    base_params=base_params,
                    windows=api_windows,
                    enable_dates=True,
                    page_size=page_size,
                    max_pages=max_pages,
                    sleep_seconds=sleep_seconds,
                    on_page=on_page,
                )
            writer.close(success=not bool(outcome.error))
            if (not outcome.error) and sort_by_date and (not dry_run):
                try:
                    _sort_csv_file_by_date(out_file)
                except Exception as exc:  # noqa: BLE001
                    _append_error(
                        errors,
                        group=spec.group,
                        api=spec.api,
                        path=spec.raw_path,
                        scope=scope,
                        identifier='global_sort',
                        message=f'sort_error:{exc}',
                    )
            if outcome.error:
                _append_error(
                    errors,
                    group=spec.group,
                    api=spec.api,
                    path=spec.raw_path,
                    scope=scope,
                    identifier='global',
                    message=outcome.error,
                )
                if 'api_error code=' in outcome.error and ('权限' in outcome.error or 'permission' in outcome.error.lower()):
                    with blocked_lock:
                        blocked_apis.setdefault(spec.api, outcome.error)
            results.append(
                {
                    'key': spec.key,
                    'api': spec.api,
                    'group': spec.group,
                    'scope': scope,
                    'rows': outcome.rows,
                    'pages': outcome.pages,
                    'strategy': outcome.strategy,
                    'truncated': outcome.truncated,
                    'skipped_existing': False,
                    'error': outcome.error,
                    'output': str(out_file),
                }
            )
            continue

        pool = symbol_pools.get(scope, [])
        if max_symbols > 0:
            pool = pool[:max_symbols]
        if not pool:
            message = f'no_symbols_for_scope:{scope}'
            _append_error(
                errors,
                group=spec.group,
                api=spec.api,
                path=spec.raw_path,
                scope=scope,
                identifier='*',
                message=message,
            )
            results.append(
                {
                    'key': spec.key,
                    'api': spec.api,
                    'group': spec.group,
                    'scope': scope,
                    'rows': 0,
                    'pages': 0,
                    'strategy': '',
                    'truncated': False,
                    'skipped_existing': False,
                    'error': message,
                    'output': str(target_dir / 'by_symbol'),
                }
            )
            continue

        symbol_dir = target_dir / 'by_symbol'
        total_rows = 0
        total_pages = 0
        truncated_any = False
        symbol_errors = 0
        symbol_done = 0
        strategy_name = ''
        code_param = _scope_code_param(scope)

        # Per-API param overrides.
        # Many index endpoints use ts_code, but some specific ones require index_code.
        code_param_override: dict[str, str] = {
            'index_weight': 'index_code',
            'index_member': 'index_code',
            'index_member_all': 'index_code',
            'ci_index_member': 'index_code',
        }

        # Build worklist (skip existing files when not overwriting)
        work: list[tuple[int, SymbolItem, Path]] = []
        for sym_idx, symbol in enumerate(pool, start=1):
            filename = f'{_safe_filename_component(symbol.code)}+{_safe_filename_component(symbol.name)}.csv'
            out_file = symbol_dir / filename
            if out_file.exists() and (not overwrite):
                continue
            work.append((sym_idx, symbol, out_file))

        def run_one(symbol: SymbolItem, out_file: Path) -> FetchOutcome:
            with blocked_lock:
                blocked_reason = blocked_apis.get(spec.api, '')
            if blocked_reason:
                return FetchOutcome(strategy='blocked', rows=0, pages=0, error=f'blocked_api:{blocked_reason}')

            writer = CsvStreamWriter(out_file, overwrite=overwrite)

            def on_page(fields: list[str], rows: list[dict[str, Any]]) -> None:
                if dry_run:
                    return
                writer.write_page(fields, rows)

            if dry_run:
                outcome = FetchOutcome(strategy='dry_run', rows=0, pages=0)
            else:
                code_field = code_param_override.get(spec.api, code_param)
                base_params = {code_field: symbol.code, **api_fixed_params.get(spec.api, {})}
                outcome = _run_fetch(
                    client=client,
                    api_name=spec.api,
                    base_params=base_params,
                    windows=api_windows,
                    enable_dates=True,
                    page_size=page_size,
                    max_pages=max_pages,
                    sleep_seconds=sleep_seconds,
                    on_page=on_page,
                )
            writer.close(success=not bool(outcome.error))

            # If this is a hard permission/auth error, block the API to prevent repeated calls.
            if outcome.error and ('api_error code=' in outcome.error) and (
                '权限' in outcome.error or 'permission' in outcome.error.lower() or '积分' in outcome.error
            ):
                with blocked_lock:
                    blocked_apis.setdefault(spec.api, outcome.error)

            # Optional per-file date sort (can be disabled via --disable-date-sort)
            if (not outcome.error) and sort_by_date and (not dry_run):
                try:
                    _sort_csv_file_by_date(out_file)
                except Exception as exc:  # noqa: BLE001
                    # Encode sort failure as an error outcome (handled in main thread).
                    outcome.error = f'sort_error:{exc}'
            return outcome

        # Run per-symbol downloads with limited concurrency.
        max_workers = max(1, int(workers))
        done = 0
        lock = threading.Lock()

        if max_workers == 1 or len(work) <= 1:
            for sym_idx, symbol, out_file in work:
                outcome = run_one(symbol, out_file)
                done += 1
                if outcome.error:
                    symbol_errors += 1
                    _append_error(
                        errors,
                        group=spec.group,
                        api=spec.api,
                        path=spec.raw_path,
                        scope=scope,
                        identifier=symbol.code,
                        message=outcome.error,
                    )
                if outcome.rows > 0:
                    symbol_done += 1
                total_rows += outcome.rows
                total_pages += outcome.pages
                truncated_any = truncated_any or outcome.truncated
                if outcome.strategy:
                    strategy_name = outcome.strategy
                if done % 50 == 0 or done == len(work):
                    print(f'  progress api={spec.api} symbol={done}/{len(pool)} rows={total_rows} errors={symbol_errors}')
                # Early circuit-break: if first large batch is almost all errors with zero rows,
                # stop this API early to avoid wasting hours on hopeless calls.
                if done >= 300 and total_rows == 0 and symbol_errors >= int(done * 0.95):
                    with blocked_lock:
                        blocked_apis.setdefault(spec.api, f'early_block zero_rows_high_error done={done} errors={symbol_errors}')
                    print(f'  early_block api={spec.api} done={done} rows={total_rows} errors={symbol_errors}')
                    break
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                fut_map = {
                    ex.submit(run_one, symbol, out_file): (sym_idx, symbol)
                    for (sym_idx, symbol, out_file) in work
                }
                for fut in as_completed(fut_map):
                    _sym_idx, symbol = fut_map[fut]
                    try:
                        outcome = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        outcome = FetchOutcome(error=f'exception:{exc}')

                    with lock:
                        done += 1
                        if outcome.error:
                            symbol_errors += 1
                            _append_error(
                                errors,
                                group=spec.group,
                                api=spec.api,
                                path=spec.raw_path,
                                scope=scope,
                                identifier=symbol.code,
                                message=outcome.error,
                            )
                        if outcome.rows > 0:
                            symbol_done += 1
                        total_rows += outcome.rows
                        total_pages += outcome.pages
                        truncated_any = truncated_any or outcome.truncated
                        if outcome.strategy:
                            strategy_name = outcome.strategy
                        if done % 50 == 0 or done == len(work):
                            print(f'  progress api={spec.api} symbol={done}/{len(pool)} rows={total_rows} errors={symbol_errors}')
                        # Early circuit-break for near-100% failures with zero rows.
                        if done >= 300 and total_rows == 0 and symbol_errors >= int(done * 0.95):
                            with blocked_lock:
                                blocked_apis.setdefault(spec.api, f'early_block zero_rows_high_error done={done} errors={symbol_errors}')
                            print(f'  early_block api={spec.api} done={done} rows={total_rows} errors={symbol_errors}')
                            break

        results.append(
            {
                'key': spec.key,
                'api': spec.api,
                'group': spec.group,
                'scope': scope,
                'rows': total_rows,
                'pages': total_pages,
                'strategy': strategy_name,
                'truncated': truncated_any,
                'skipped_existing': False,
                'error': '' if symbol_errors == 0 else f'symbol_errors={symbol_errors}',
                'output': str(symbol_dir),
                'symbols_with_rows': symbol_done,
                'symbols_total': len(pool),
            }
        )

    return results


def _download_master(
    *,
    client: TushareClient,
    specs: list[ApiSpec],
    root: Path,
    page_size: int,
    max_pages: int,
    sleep_seconds: float,
    overwrite: bool,
    sort_by_date: bool,
    dry_run: bool,
    errors: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], Path]:
    results: list[dict[str, Any]] = []
    index_output = root / MASTER_INDEX_RELATIVE

    for idx, spec in enumerate(specs, start=1):
        print(f'[master {idx}/{len(specs)}] api={spec.api} label={spec.label}')
        target_dir = root.joinpath(*spec.normalized_path.split('/'))
        out_file = target_dir / 'all.csv'
        if out_file.exists() and (not overwrite):
            results.append(
                {
                    'key': spec.key,
                    'api': spec.api,
                    'group': spec.group,
                    'scope': 'master',
                    'rows': 0,
                    'pages': 0,
                    'strategy': '',
                    'truncated': False,
                    'skipped_existing': True,
                    'error': '',
                    'output': str(out_file),
                    'path': spec.raw_path,
                    'normalized_path': spec.normalized_path,
                    'rate_limit': spec.rate_limit,
                    'note': spec.note,
                }
            )
            continue

        writer = CsvStreamWriter(out_file, overwrite=overwrite)

        def on_page(fields: list[str], rows: list[dict[str, Any]]) -> None:
            if dry_run:
                return
            writer.write_page(fields, rows)

        if dry_run:
            outcome = FetchOutcome(strategy='dry_run', rows=0, pages=0)
        else:
            outcome = _run_fetch(
                client=client,
                api_name=spec.api,
                base_params={},
                windows=[('', '')],
                enable_dates=False,
                page_size=page_size,
                max_pages=max_pages,
                sleep_seconds=sleep_seconds,
                on_page=on_page,
            )
        writer.close(success=not bool(outcome.error))
        if (not outcome.error) and sort_by_date and (not dry_run):
            try:
                _sort_csv_file_by_date(out_file)
            except Exception as exc:  # noqa: BLE001
                _append_error(
                    errors,
                    group=spec.group,
                    api=spec.api,
                    path=spec.raw_path,
                    scope='master',
                    identifier='global_sort',
                    message=f'sort_error:{exc}',
                )
        if outcome.error:
            _append_error(
                errors,
                group=spec.group,
                api=spec.api,
                path=spec.raw_path,
                scope='master',
                identifier='global',
                message=outcome.error,
            )
        results.append(
            {
                'key': spec.key,
                'api': spec.api,
                'group': spec.group,
                'scope': 'master',
                'rows': outcome.rows,
                'pages': outcome.pages,
                'strategy': outcome.strategy,
                'truncated': outcome.truncated,
                'skipped_existing': False,
                'error': outcome.error,
                'output': str(out_file),
                'path': spec.raw_path,
                'normalized_path': spec.normalized_path,
                'rate_limit': spec.rate_limit,
                'note': spec.note,
            }
        )

    index_output.parent.mkdir(parents=True, exist_ok=True)
    with index_output.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                'group',
                'api',
                'label',
                'path',
                'normalized_path',
                'rate_limit',
                'note',
                'rows',
                'pages',
                'strategy',
                'truncated',
                'skipped_existing',
                'error',
                'output',
            ],
        )
        writer.writeheader()
        for row in results:
            writer.writerow(
                {
                    'group': row.get('group', ''),
                    'api': row.get('api', ''),
                    'label': next((x.label for x in specs if x.key == row.get('key')), ''),
                    'path': row.get('path', ''),
                    'normalized_path': row.get('normalized_path', ''),
                    'rate_limit': row.get('rate_limit', ''),
                    'note': row.get('note', ''),
                    'rows': row.get('rows', 0),
                    'pages': row.get('pages', 0),
                    'strategy': row.get('strategy', ''),
                    'truncated': '1' if row.get('truncated') else '0',
                    'skipped_existing': '1' if row.get('skipped_existing') else '0',
                    'error': row.get('error', ''),
                    'output': row.get('output', ''),
                }
            )
    return results, index_output


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[1]
    root = Path(args.root).expanduser().resolve()
    classification_csv = Path(args.classification_csv).expanduser().resolve()
    storage_map_csv = Path(args.storage_map_csv).expanduser().resolve()
    catalog_csv = Path(args.catalog_csv).expanduser().resolve()
    source_catalog_csv = Path(args.source_catalog).expanduser().resolve()
    sort_by_date = not bool(args.disable_date_sort)

    repair_root_raw = _text(args.repair_existing_root)
    if repair_root_raw:
        repair_root = Path(repair_root_raw).expanduser().resolve()
        print(f'repair_root={repair_root}')
        print(f'repair_glob={args.repair_existing_glob}')
        print(f'repair_max_files={int(args.repair_max_files)}')
        if not repair_root.exists():
            raise FileNotFoundError(f'repair root not found: {repair_root}')
        summary = _repair_existing_csv_date_order(
            repair_root,
            include_glob=args.repair_existing_glob,
            max_files=max(0, int(args.repair_max_files)),
        )
        print(f"repair_done files={summary['files']} sorted={summary['sorted']} skipped={summary['skipped']} failed={summary['failed']}")
        if bool(args.repair_only):
            return 0

    groups = {x.strip() for x in args.groups.split(',') if x.strip()}
    allowlist = {x.strip() for x in args.api_allowlist.split(',') if x.strip()}

    if not classification_csv.exists():
        raise FileNotFoundError(f'classification csv not found: {classification_csv}')
    if not storage_map_csv.exists():
        raise FileNotFoundError(f'storage map csv not found: {storage_map_csv}')
    if not catalog_csv.exists():
        raise FileNotFoundError(f'catalog csv not found: {catalog_csv}')

    token = _resolve_token(args.token, project_root=project_root)
    if not token:
        raise RuntimeError('TUSHARE token missing. Pass --token or set TUSHARE_TOKEN/.env.')
    base_url = _resolve_base_url(args.base_url, project_root=project_root)

    end_date = _text(args.end_date) or date.today().strftime('%Y%m%d')
    start_date = _text(args.start_date)
    _ = _parse_ymd(start_date)
    _ = _parse_ymd(end_date)

    specs = _load_specs(
        classification_csv=classification_csv,
        storage_map_csv=storage_map_csv,
        source_catalog_csv=source_catalog_csv,
        catalog_csv=catalog_csv,
        groups=groups,
        allowlist=allowlist,
    )
    historical_specs = [x for x in specs if x.group == 'historical']
    master_specs = [x for x in specs if x.group == 'master']

    print(f'root={root}')
    print(f'classification={classification_csv}')
    print(f'storage_map={storage_map_csv}')
    print(f'catalog={catalog_csv}')
    print(f'source_catalog={source_catalog_csv} exists={source_catalog_csv.exists()}')
    print(f'groups={sorted(groups)}')
    print(f'spec_count_total={len(specs)} historical={len(historical_specs)} master={len(master_specs)}')
    print(f'period={start_date}..{end_date} chunk={args.date_chunk_mode}')
    print(f'base_url={base_url}')
    print(
        f'dry_run={bool(args.dry_run)} overwrite={bool(args.overwrite)} '
        f'max_symbols={int(args.max_symbols)} sort_by_date={sort_by_date}'
    )

    if args.dry_run:
        print('dry-run enabled, skipping network and filesystem writes.')
        return 0

    run_id = f'bulk_{datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")}'
    _write_tushare_status(
        root=root,
        status='UPDATING',
        message='Tushare 全量下载任务执行中。',
        mode='FULL',
        run_id=run_id,
        extra={
            'start_date': start_date,
            'end_date': end_date,
            'groups': sorted(groups),
        },
    )

    # Reuse HTTP connection pool for better throughput.
    http_client = httpx.Client(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        timeout=float(args.timeout_seconds),
    )
    # Per-API min-interval overrides (seconds). These are conservative defaults to reduce burst errors
    # when workers>1. Tune as needed.
    api_min_intervals: dict[str, float] = {
        'daily_basic': 0.25,
        'moneyflow': 0.25,
        'moneyflow_ths': 0.3,
        'moneyflow_dc': 0.3,
        'index_member_all': 0.35,
        'index_weight': 0.35,
        'fund_daily': 0.25,
        'etf_share_size': 0.25,
    }
    rate_limiter = _ApiRateLimiter(default_min_interval=max(0.0, float(args.sleep_seconds)))

    client = TushareClient(
        token=token,
        base_url=base_url,
        timeout_seconds=float(args.timeout_seconds),
        max_retries=int(args.max_retries),
        retry_backoff_seconds=float(args.retry_backoff_seconds),
        http_client=http_client,
        rate_limiter=rate_limiter,
        api_min_intervals=api_min_intervals,
    )

    try:
        errors: list[dict[str, str]] = []
        pools, pool_errors = _build_symbol_pools(
            client=client,
            page_size=max(100, int(args.page_size)),
            max_pages=max(1, int(args.max_pages)),
            sleep_seconds=max(0.0, float(args.sleep_seconds)),
        )
        for err in pool_errors:
            _append_error(
                errors,
                group='meta',
                api=err['api'],
                path='symbol_pool',
                scope=err['scope'],
                identifier='pool',
                message=err['error'],
            )
        print(
            'symbol_pools='
            + ', '.join(
                [
                    f'stock={len(pools["stock"])}',
                    f'index={len(pools["index"])}',
                    f'fund={len(pools["fund"])}',
                    f'future={len(pools["future"])}',
                    f'option={len(pools["option"])}',
                    f'bond={len(pools["bond"])}',
                    f'hk={len(pools["hk"])}',
                    f'us={len(pools["us"])}',
                ]
            )
        )

        historical_results = _download_historical(
            client=client,
            specs=historical_specs,
            root=root,
            start_date=start_date,
            end_date=end_date,
            chunk_mode=args.date_chunk_mode,
            page_size=max(100, int(args.page_size)),
            max_pages=max(1, int(args.max_pages)),
            sleep_seconds=max(0.0, float(args.sleep_seconds)),
            workers=max(1, int(args.workers)),
            mins_start_date=_text(args.mins_start_date),
            max_symbols=max(0, int(args.max_symbols)),
            overwrite=bool(args.overwrite),
            sort_by_date=sort_by_date,
            dry_run=False,
            symbol_pools=pools,
            errors=errors,
        )
        master_results, master_index_output = _download_master(
            client=client,
            specs=master_specs,
            root=root,
            page_size=max(100, int(args.page_size)),
            max_pages=max(1, int(args.max_pages)),
            sleep_seconds=max(0.0, float(args.sleep_seconds)),
            overwrite=bool(args.overwrite),
            sort_by_date=sort_by_date,
            dry_run=False,
            errors=errors,
        )

        error_log = root / ERROR_LOG_RELATIVE
        _write_errors(error_log, errors)

        manifest = {
            'generated_at_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'root': str(root),
            'classification_csv': str(classification_csv),
            'storage_map_csv': str(storage_map_csv),
            'catalog_csv': str(catalog_csv),
            'params': {
                'start_date': start_date,
                'end_date': end_date,
                'page_size': int(args.page_size),
                'max_pages': int(args.max_pages),
                'sleep_seconds': float(args.sleep_seconds),
                'chunk_mode': args.date_chunk_mode,
                'max_symbols': int(args.max_symbols),
                'groups': sorted(groups),
                'overwrite': bool(args.overwrite),
                'sort_by_date': sort_by_date,
            },
            'summary': {
                'historical_specs': len(historical_specs),
                'master_specs': len(master_specs),
                'historical_rows': sum(int(x.get('rows', 0)) for x in historical_results),
                'master_rows': sum(int(x.get('rows', 0)) for x in master_results),
                'errors': len(errors),
                'error_log': str(error_log),
                'master_index_output': str(master_index_output),
            },
            'historical_results': historical_results,
            'master_results': master_results,
        }
        manifest_path = root / MANIFEST_RELATIVE
        _write_manifest(manifest_path, manifest)

        _write_tushare_status(
            root=root,
            status='COMPLETED',
            message='Tushare 全量下载任务已完成。',
            mode='FULL',
            run_id=run_id,
            extra={
                'historical_rows': manifest['summary']['historical_rows'],
                'master_rows': manifest['summary']['master_rows'],
                'errors': manifest['summary']['errors'],
                'manifest': str(manifest_path),
            },
        )

        print('completed.')
        print(f'master_index_output={master_index_output}')
        print(f'error_log={error_log}')
        print(f'manifest={manifest_path}')
        print(
            f"rows historical={manifest['summary']['historical_rows']} "
            f"master={manifest['summary']['master_rows']} errors={manifest['summary']['errors']}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        _write_tushare_status(
            root=root,
            status='ERROR',
            message=f'Tushare 全量下载任务异常：{exc}',
            mode='FULL',
            run_id=run_id,
            extra={'error_type': exc.__class__.__name__},
        )
        raise
    finally:
        try:
            http_client.close()
        except Exception:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
