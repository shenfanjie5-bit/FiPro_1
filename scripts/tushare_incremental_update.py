from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any

import httpx


DEFAULT_ROOT = '/Volumes/dockcase2tb/database_all'
DEFAULT_CLASSIFICATION_CSV = 'docs/tushare_api_history_classification.csv'
DEFAULT_STORAGE_MAP_CSV = 'docs/tushare_api_storage_map.csv'
DEFAULT_SOURCE_CATALOG = '/Volumes/dockcase2tb/tushare_doc2_leaf_api.csv'
DEFAULT_BASE_URL = 'https://api.tushare.pro'

STATE_RELATIVE = '_meta/checkpoints/tushare_incremental_state.json'
RUN_REPORT_RELATIVE = '_meta/manifests/tushare_incremental_last_run.json'
TUSHARE_STATUS_RELATIVE = '_meta/manifests/data_source_status/tushare.json'

NEWS_ALWAYS_KEYWORDS = ('新闻', '快讯', '公告', '研报', '政策', '资讯')
NEWS_ALWAYS_APIS = {
    'news',
    'research_report',
    'npr',
}

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

CODE_FIELDS = ('ts_code', 'index_code', 'fund_code', 'opt_code', 'bond_code', 'con_code', 'symbol')

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

    @property
    def key(self) -> str:
        return f'{self.group}:{self.api}:{self.raw_path}'


@dataclass(frozen=True)
class TargetFile:
    spec: ApiSpec
    scope: str
    path: Path
    symbol_code: str


class TushareApiError(RuntimeError):
    def __init__(self, code: int, msg: str, api_name: str) -> None:
        self.code = int(code)
        self.msg = str(msg)
        self.api_name = api_name
        super().__init__(f'TUSHARE_ERROR api={api_name} code={code} msg={msg}')


class TushareClient:
    def __init__(self, *, token: str, base_url: str, timeout_seconds: float) -> None:
        self.token = token.strip()
        self.base_url = base_url.strip()
        self.timeout_seconds = timeout_seconds

    def query(self, *, api_name: str, params: dict[str, Any], fields: str = '') -> tuple[list[str], list[dict[str, Any]]]:
        payload = {'api_name': api_name, 'token': self.token, 'params': params, 'fields': fields}
        response = httpx.post(self.base_url, json=payload, timeout=self.timeout_seconds)
        response.raise_for_status()
        body = response.json()
        code = int(body.get('code', 0))
        if code != 0:
            raise TushareApiError(code=code, msg=str(body.get('msg', '')), api_name=api_name)
        data = body.get('data') or {}
        fields_out = [str(x) for x in (data.get('fields') or [])]
        items = data.get('items') or []
        rows: list[dict[str, Any]] = []
        for item in items:
            rows.append({name: value for name, value in zip(fields_out, item, strict=False)})
        return fields_out, rows


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Incremental Tushare updater: trading-day updates + news updates + 3-day completeness check.')
    parser.add_argument('--root', default=DEFAULT_ROOT)
    parser.add_argument('--classification-csv', default=DEFAULT_CLASSIFICATION_CSV)
    parser.add_argument('--storage-map-csv', default=DEFAULT_STORAGE_MAP_CSV)
    parser.add_argument('--source-catalog', default=DEFAULT_SOURCE_CATALOG)
    parser.add_argument('--token', default='')
    parser.add_argument('--base-url', default='')
    parser.add_argument('--timeout-seconds', type=float, default=30.0)
    parser.add_argument('--page-size', type=int, default=5000)
    parser.add_argument('--max-pages', type=int, default=6, help='Incremental mode default is small pages to prevent full-table pulls.')
    parser.add_argument('--sleep-seconds', type=float, default=0.05)
    parser.add_argument('--trade-cal-exchange', default='SSE')
    parser.add_argument('--completeness-interval-trading-days', type=int, default=3)
    parser.add_argument('--disable-completeness-check', action='store_true')
    parser.add_argument('--disable-news-always', action='store_true')
    parser.add_argument('--api-allowlist', default='')
    parser.add_argument('--limit-files', type=int, default=0, help='Debug only. 0 means no limit.')
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


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_token(cli_token: str, project_root: Path) -> str:
    if _text(cli_token):
        return _text(cli_token)
    env_token = _text(os.getenv('TUSHARE_TOKEN', ''))
    if env_token:
        return env_token
    return _text(_read_env_file(project_root / '.env').get('TUSHARE_TOKEN', ''))


def _resolve_base_url(cli_base_url: str, project_root: Path) -> str:
    if _text(cli_base_url):
        return _text(cli_base_url)
    env_url = _text(os.getenv('TUSHARE_BASE_URL', ''))
    if env_url:
        return env_url
    return _text(_read_env_file(project_root / '.env').get('TUSHARE_BASE_URL', '')) or DEFAULT_BASE_URL


def _load_storage_map(path: Path) -> dict[tuple[str, str, str], str]:
    mapping: dict[tuple[str, str, str], str] = {}
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            api = _text(row.get('api'))
            label = _text(row.get('label'))
            raw_path = _text(row.get('raw_relative_path'))
            normalized = _text(row.get('normalized_relative_path'))
            if api and raw_path:
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
            parts = [_text(row.get('level1')), _text(row.get('level2')), _text(row.get('level3')), _text(row.get('level4'))]
            raw_path = '/'.join([x for x in parts if x])
            if api and raw_path:
                mapping[(api, label, raw_path)] = _normalize_rel_path(raw_path)
    return mapping


def _load_specs(
    *,
    classification_csv: Path,
    storage_map_csv: Path,
    source_catalog_csv: Path,
    allowlist: set[str],
) -> list[ApiSpec]:
    storage_map = _load_storage_map(storage_map_csv)
    source_map = _load_source_catalog_map(source_catalog_csv)
    specs: list[ApiSpec] = []
    seen: set[tuple[str, str, str, str]] = set()
    with classification_csv.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            group = _text(row.get('group'))
            api = _text(row.get('api'))
            label = _text(row.get('label'))
            raw_path = _text(row.get('path'))
            if group not in {'historical', 'master'}:
                continue
            if not api or not raw_path:
                continue
            if allowlist and api not in allowlist:
                continue
            normalized = source_map.get((api, label, raw_path)) or storage_map.get((api, label, raw_path), _normalize_rel_path(raw_path))
            key = (group, api, label, normalized)
            if key in seen:
                continue
            seen.add(key)
            specs.append(ApiSpec(group=group, api=api, label=label, raw_path=raw_path, normalized_path=normalized))
    return specs


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
    if scope == 'index':
        return 'index_code'
    return 'ts_code'


def _is_news_always(spec: ApiSpec, disable_news_always: bool) -> bool:
    if disable_news_always:
        return False
    if spec.api in NEWS_ALWAYS_APIS:
        return True
    if '大模型语料专题数据' in spec.raw_path:
        return True
    return any(x in spec.label for x in NEWS_ALWAYS_KEYWORDS)


def _extract_symbol_from_filename(path: Path) -> str:
    stem = path.stem
    if '+' in stem:
        return stem.split('+', 1)[0].strip()
    return stem.strip()


def _build_targets(specs: list[ApiSpec], root: Path, limit_files: int) -> list[TargetFile]:
    targets: list[TargetFile] = []
    for spec in specs:
        base_dir = root.joinpath(*spec.normalized_path.split('/'))
        scope = _resolve_scope(spec)
        if scope == 'global':
            file_path = base_dir / 'all.csv'
            if file_path.exists():
                targets.append(TargetFile(spec=spec, scope=scope, path=file_path, symbol_code=''))
            continue
        symbol_dir = base_dir / 'by_symbol'
        if not symbol_dir.exists():
            continue
        symbol_files = sorted([p for p in symbol_dir.glob('*.csv') if p.is_file() and not p.name.startswith('._')])
        for file_path in symbol_files:
            symbol_code = _extract_symbol_from_filename(file_path)
            if symbol_code:
                targets.append(TargetFile(spec=spec, scope=scope, path=file_path, symbol_code=symbol_code))
    if limit_files > 0:
        targets = targets[:limit_files]
    return targets


def _parse_ymd(raw: str) -> date:
    return datetime.strptime(raw, '%Y%m%d').date()


def _ymd(d: date) -> str:
    return d.strftime('%Y%m%d')


def _prev_day(d: date) -> date:
    return d - timedelta(days=1)


def _next_day_ymd(raw_ymd: str) -> str:
    return _ymd(_parse_ymd(raw_ymd) + timedelta(days=1))


def _normalize_sort_token(value: Any) -> tuple[int, str]:
    text = _text(value)
    if not text:
        return (2, '')
    raw = text.strip()
    if raw.isdigit():
        if len(raw) == 8:
            return (0, f'{raw}000000')
        if len(raw) == 14:
            return (0, raw)
    canon = raw.replace('/', '-')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y-%m'):
        try:
            dt = datetime.strptime(canon, fmt)
            return (0, dt.strftime('%Y%m%d%H%M%S'))
        except ValueError:
            pass
    m = re.search(r'(\d{4})[-/]?(\d{2})[-/]?(\d{2})(?:\D?(\d{2})(\d{2})(\d{2})?)?', raw)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        return (0, f'{y}{mo}{d}{hh or "00"}{mm or "00"}{ss or "00"}')
    return (1, raw)


def _find_date_fields(fieldnames: list[str]) -> list[str]:
    lower_map = {x.lower(): x for x in fieldnames}
    found: list[str] = []
    for candidate in DATE_SORT_CANDIDATES:
        original = lower_map.get(candidate)
        if original:
            found.append(original)
    return found


def _row_key(row: dict[str, Any], date_fields: list[str]) -> str:
    key_parts: list[str] = []
    for code_field in CODE_FIELDS:
        value = _text(row.get(code_field))
        if value:
            key_parts.append(f'{code_field}:{value}')
    for date_field in date_fields:
        value = _text(row.get(date_field))
        if value:
            key_parts.append(f'{date_field}:{value}')
    if key_parts:
        return '|'.join(key_parts)
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def _atomic_write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.part')
    if tmp.exists():
        tmp.unlink()
    with tmp.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def _merge_rows_into_file(path: Path, new_rows: list[dict[str, Any]]) -> tuple[int, int, str]:
    if not new_rows:
        return (0, 0, '')

    existing_rows: list[dict[str, Any]] = []
    fieldnames: list[str] = []
    if path.exists():
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            existing_rows = list(reader)

    if not fieldnames:
        fieldnames = list(new_rows[0].keys())
    for row in new_rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    date_fields = _find_date_fields(fieldnames)

    merged: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        merged[_row_key(row, date_fields)] = row
    before = len(merged)
    for row in new_rows:
        merged[_row_key(row, date_fields)] = row

    merged_rows = list(merged.values())
    if date_fields:
        merged_rows.sort(key=lambda row: tuple(_normalize_sort_token(row.get(f, '')) for f in date_fields))
    _atomic_write_csv(path, fieldnames, merged_rows)
    return (len(merged_rows) - before, len(merged_rows), ','.join(date_fields))


def _fetch_incremental_rows(
    *,
    client: TushareClient,
    api_name: str,
    base_params: dict[str, Any],
    start_date: str,
    end_date: str,
    page_size: int,
    max_pages: int,
    sleep_seconds: float,
) -> tuple[list[str], list[dict[str, Any]], str]:
    last_error = ''
    strategies = (
        ('dates+limit+offset', True, True),
        ('dates+limit', True, False),
        ('dates_only', False, False),
    )
    for strategy_name, use_limit, use_offset in strategies:
        all_rows: list[dict[str, Any]] = []
        fields_out: list[str] = []
        offset = 0
        ok = True
        for _ in range(max_pages):
            params = dict(base_params)
            params['start_date'] = start_date
            params['end_date'] = end_date
            if use_limit:
                params['limit'] = page_size
            if use_offset:
                params['offset'] = offset
            try:
                fields, rows = client.query(api_name=api_name, params=params, fields='')
            except Exception as exc:  # noqa: BLE001
                ok = False
                last_error = str(exc)
                break
            if fields and (not fields_out):
                fields_out = fields
            all_rows.extend(rows)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            if not rows:
                break
            if use_limit and len(rows) < page_size:
                break
            if use_offset:
                offset += page_size
                continue
            break
        if ok:
            return fields_out, all_rows, strategy_name
    raise RuntimeError(last_error or 'incremental_fetch_failed')


def _fetch_trading_calendar(client: TushareClient, exchange: str, start_date: str, end_date: str) -> list[str]:
    _, rows = client.query(api_name='trade_cal', params={'exchange': exchange, 'start_date': start_date, 'end_date': end_date}, fields='')
    trade_dates: list[str] = []
    for row in rows:
        cal_date = _text(row.get('cal_date'))
        is_open = _text(row.get('is_open'))
        if cal_date and is_open == '1':
            trade_dates.append(cal_date)
    return sorted(set(trade_dates))


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {'pending_trading_days': [], 'last_run_utc': '', 'last_completeness_check_trade_date': ''}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {'pending_trading_days': [], 'last_run_utc': '', 'last_completeness_check_trade_date': ''}


def _save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _build_file_check_targets(targets: list[TargetFile], *, include_news: bool) -> list[TargetFile]:
    selected: list[TargetFile] = []
    for target in targets:
        news = _is_news_always(target.spec, disable_news_always=False)
        if (not include_news) and news:
            continue
        selected.append(target)
    return selected


def _max_date_in_csv(path: Path) -> str:
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        date_fields = _find_date_fields(fieldnames)
        if not date_fields:
            return ''
        max_date = ''
        date_field = date_fields[0]
        for row in reader:
            value = _text(row.get(date_field))
            if value and value > max_date:
                max_date = value
        return max_date


def _write_report(path: Path, payload: dict[str, Any]) -> None:
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


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[1]
    root = Path(args.root).expanduser().resolve()
    classification_csv = Path(args.classification_csv).expanduser().resolve()
    storage_map_csv = Path(args.storage_map_csv).expanduser().resolve()
    source_catalog_csv = Path(args.source_catalog).expanduser().resolve()
    allowlist = {x.strip() for x in args.api_allowlist.split(',') if x.strip()}

    token = _resolve_token(args.token, project_root)
    if not token:
        raise RuntimeError('TUSHARE token missing. Please set --token or TUSHARE_TOKEN/.env.')
    base_url = _resolve_base_url(args.base_url, project_root)
    client = TushareClient(token=token, base_url=base_url, timeout_seconds=float(args.timeout_seconds))
    run_id = f'incremental_{datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")}'
    _write_tushare_status(
        root=root,
        status='UPDATING',
        message='Tushare 增量更新任务执行中。',
        mode='INCREMENTAL',
        run_id=run_id,
    )

    specs = _load_specs(
        classification_csv=classification_csv,
        storage_map_csv=storage_map_csv,
        source_catalog_csv=source_catalog_csv,
        allowlist=allowlist,
    )
    targets = _build_targets(specs, root=root, limit_files=max(0, int(args.limit_files)))
    if not targets:
        print('no target files found under root, nothing to update.')
        _write_tushare_status(
            root=root,
            status='COMPLETED',
            message='增量更新结束：未发现可更新目标文件。',
            mode='INCREMENTAL',
            run_id=run_id,
            extra={'target_files': 0},
        )
        return 0

    try:
        today = date.today()
        yesterday = _prev_day(today)
        today_ymd = _ymd(today)
        yesterday_ymd = _ymd(yesterday)

        cal_start = _ymd(today - timedelta(days=20))
        trade_days = _fetch_trading_calendar(client, exchange=args.trade_cal_exchange, start_date=cal_start, end_date=yesterday_ymd)
        is_yesterday_trade_day = yesterday_ymd in set(trade_days)
        last_trade_day = trade_days[-1] if trade_days else ''

        print(f'root={root}')
        print(f'target_files={len(targets)}')
        print(f'today={today_ymd} yesterday={yesterday_ymd} is_yesterday_trade_day={is_yesterday_trade_day} last_trade_day={last_trade_day}')

        updates = 0
        rows_added = 0
        errors: list[dict[str, str]] = []
        per_api_counter: dict[str, int] = {}

        for idx, target in enumerate(targets, start=1):
            spec = target.spec
            news_always = _is_news_always(spec, disable_news_always=bool(args.disable_news_always))
            if (not news_always) and (not is_yesterday_trade_day):
                continue

            start_date = yesterday_ymd
            end_date = today_ymd if news_always else yesterday_ymd

            base_params: dict[str, Any] = {}
            if target.symbol_code:
                base_params[_scope_code_param(target.scope)] = target.symbol_code

            try:
                _, rows, strategy = _fetch_incremental_rows(
                    client=client,
                    api_name=spec.api,
                    base_params=base_params,
                    start_date=start_date,
                    end_date=end_date,
                    page_size=max(100, int(args.page_size)),
                    max_pages=max(1, int(args.max_pages)),
                    sleep_seconds=max(0.0, float(args.sleep_seconds)),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        'api': spec.api,
                        'path': spec.raw_path,
                        'file': str(target.path),
                        'symbol': target.symbol_code,
                        'error': str(exc),
                    }
                )
                continue

            if not rows:
                continue

            try:
                added, total, sort_fields = _merge_rows_into_file(target.path, rows)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        'api': spec.api,
                        'path': spec.raw_path,
                        'file': str(target.path),
                        'symbol': target.symbol_code,
                        'error': f'merge_error:{exc}',
                    }
                )
                continue

            updates += 1
            rows_added += max(0, added)
            per_api_counter[spec.api] = per_api_counter.get(spec.api, 0) + 1

            if idx % 200 == 0 or idx == len(targets):
                print(
                    f'progress {idx}/{len(targets)} updates={updates} rows_added={rows_added} '
                    f'api={spec.api} strategy={strategy} sort_fields={sort_fields} file_rows={total}'
                )

        state_path = root / STATE_RELATIVE
        state = _load_state(state_path)
        pending_days = list(state.get('pending_trading_days', []))
        if is_yesterday_trade_day and yesterday_ymd not in pending_days:
            pending_days.append(yesterday_ymd)
            pending_days = sorted(pending_days)[-10:]

        completeness_report: dict[str, Any] = {'checked': False}
        if (not bool(args.disable_completeness_check)) and last_trade_day and len(pending_days) >= int(args.completeness_interval_trading_days):
            check_targets = _build_file_check_targets(targets, include_news=False)
            incomplete: list[dict[str, str]] = []
            for target in check_targets:
                if not target.path.exists():
                    continue
                try:
                    max_date = _max_date_in_csv(target.path)
                except Exception as exc:  # noqa: BLE001
                    incomplete.append(
                        {
                            'api': target.spec.api,
                            'file': str(target.path),
                            'symbol': target.symbol_code,
                            'max_date': '',
                            'expected': last_trade_day,
                            'status': f'check_error:{exc}',
                        }
                    )
                    continue
                if not max_date:
                    continue
                if max_date < last_trade_day:
                    incomplete.append(
                        {
                            'api': target.spec.api,
                            'file': str(target.path),
                            'symbol': target.symbol_code,
                            'max_date': max_date,
                            'expected': last_trade_day,
                            'status': 'missing_tail',
                        }
                    )

            qa_path = root / '_meta' / 'qa' / f'incremental_completeness_{today_ymd}.csv'
            qa_path.parent.mkdir(parents=True, exist_ok=True)
            with qa_path.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=['api', 'file', 'symbol', 'max_date', 'expected', 'status'])
                writer.writeheader()
                writer.writerows(incomplete)

            completeness_report = {
                'checked': True,
                'expected_trade_day': last_trade_day,
                'target_files': len(check_targets),
                'incomplete_files': len(incomplete),
                'qa_csv': str(qa_path),
            }
            pending_days = []
            state['last_completeness_check_trade_date'] = last_trade_day

        state['pending_trading_days'] = pending_days
        state['last_run_utc'] = datetime.now().astimezone().isoformat()
        _save_state(state_path, state)

        report = {
            'generated_at_utc': datetime.now().astimezone().isoformat(),
            'root': str(root),
            'yesterday': yesterday_ymd,
            'is_yesterday_trade_day': is_yesterday_trade_day,
            'last_trade_day': last_trade_day,
            'target_files': len(targets),
            'updated_files': updates,
            'rows_added': rows_added,
            'per_api_updated_files': per_api_counter,
            'errors': errors,
            'completeness': completeness_report,
            'state_file': str(state_path),
        }
        _write_report(root / RUN_REPORT_RELATIVE, report)

        _write_tushare_status(
            root=root,
            status='COMPLETED' if not errors else 'ERROR',
            message='Tushare 增量更新任务已完成。' if not errors else f'Tushare 增量更新存在异常（errors={len(errors)}）。',
            mode='INCREMENTAL',
            run_id=run_id,
            extra={
                'updated_files': updates,
                'rows_added': rows_added,
                'errors': len(errors),
                'run_report': str(root / RUN_REPORT_RELATIVE),
            },
        )

        print(f'updated_files={updates} rows_added={rows_added} errors={len(errors)}')
        if completeness_report.get('checked'):
            print(
                f"completeness_checked target={completeness_report['target_files']} "
                f"incomplete={completeness_report['incomplete_files']} qa={completeness_report['qa_csv']}"
            )
        print(f'run_report={root / RUN_REPORT_RELATIVE}')
        return 0
    except Exception as exc:  # noqa: BLE001
        _write_tushare_status(
            root=root,
            status='ERROR',
            message=f'Tushare 增量更新任务异常：{exc}',
            mode='INCREMENTAL',
            run_id=run_id,
            extra={'error_type': exc.__class__.__name__},
        )
        raise


if __name__ == '__main__':
    raise SystemExit(main())
