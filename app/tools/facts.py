from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import httpx

from app.tools.cache import TTLCache


STATUS_RANK = {'OK': 0, 'PARTIAL': 1, 'DEGRADED': 2}
MARKET_TTL_SECONDS = 300
FUNDAMENTALS_TTL_SECONDS = 24 * 60 * 60
FLOW_TTL_SECONDS = 300
MACRO_TTL_SECONDS = 60 * 60

_SNAPSHOT_CACHE: TTLCache[dict[str, Any]] = TTLCache()
_TUSHARE_ADAPTER: 'TushareProAdapter | None' = None
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE_PATH = _PROJECT_ROOT / '.env'


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_to_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        normalized = value.replace('Z', '+00:00')
        dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_iso(value: str | datetime) -> str:
    return _parse_iso_to_utc(value).isoformat()


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True, default=str))


def _hash_digest(payload: Any, length: int = 16) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:length]


@lru_cache(maxsize=1)
def _read_dotenv_values() -> dict[str, str]:
    try:
        lines = _ENV_FILE_PATH.read_text(encoding='utf-8').splitlines()
    except OSError:
        return {}

    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, raw_value = line.split('=', 1)
        key = key.strip()
        if not key:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _path_get(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for key in dotted_path.split('.'):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _max_status(a: str, b: str) -> str:
    return a if STATUS_RANK.get(a, 0) >= STATUS_RANK.get(b, 0) else b


def _merge_data_quality(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged_missing = sorted(
        set(base.get('missing_fields', [])).union(set(incoming.get('missing_fields', [])))
    )
    notes = [x for x in [base.get('notes', ''), incoming.get('notes', '')] if str(x).strip()]
    return {
        'status': _max_status(str(base.get('status', 'OK')), str(incoming.get('status', 'OK'))),
        'missing_fields': merged_missing,
        'notes': ' | '.join(notes),
    }


def _quality_gate(
    payload: dict[str, Any],
    *,
    required_fields: tuple[str, ...],
    numeric_bounds: dict[str, tuple[float | None, float | None]],
    freshness_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    missing_fields: list[str] = []
    outliers: list[str] = []

    for field in required_fields:
        value = _path_get(payload, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing_fields.append(field)

    for field, (min_value, max_value) in numeric_bounds.items():
        raw = _path_get(payload, field)
        value = _to_float(raw)
        if value is None:
            continue
        if min_value is not None and value < min_value:
            outliers.append(field)
            continue
        if max_value is not None and value > max_value:
            outliers.append(field)

    captured_at = _parse_iso_to_utc(payload['captured_at'])
    freshness_age = int((_now_utc() - captured_at).total_seconds())
    stale = freshness_age > freshness_seconds
    if stale:
        missing_fields.append('__freshness__')

    null_ratio = len(missing_fields) / max(1, len(required_fields))
    status = 'OK'
    if outliers or stale or null_ratio >= 0.5:
        status = 'DEGRADED'
    elif missing_fields:
        status = 'PARTIAL'

    notes: list[str] = []
    if stale:
        notes.append(f'stale_snapshot(age={freshness_age}s, threshold={freshness_seconds}s)')
    if outliers:
        notes.append(f'outlier_fields={",".join(sorted(set(outliers)))}')
    if missing_fields and not stale:
        notes.append(f'missing_fields={",".join(sorted(set(missing_fields)))}')

    return (
        {
            'status': status,
            'missing_fields': sorted(set(missing_fields)),
            'notes': '; '.join(notes),
        },
        {
            'freshness_age_seconds': freshness_age,
            'freshness_threshold_seconds': freshness_seconds,
            'null_ratio': round(null_ratio, 4),
            'outlier_fields': sorted(set(outliers)),
        },
    )


def _mark_fallback_status(
    quality: dict[str, Any], *, reason_code: str, source: str, details: str
) -> dict[str, Any]:
    fallback_quality = {
        'status': 'DEGRADED',
        'missing_fields': ['__upstream__'],
        'notes': f'{reason_code}; source={source}; details={details}',
    }
    return _merge_data_quality(quality, fallback_quality)


def _retryable_error_code(code: str) -> bool:
    return code in {'UPSTREAM_TIMEOUT', 'RATE_LIMITED', 'UPSTREAM_ERROR'}


def _normalize_upstream_trace(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    endpoints = raw.get('endpoints', [])
    if isinstance(endpoints, str):
        endpoints = [endpoints]
    normalized_endpoints = [str(item).strip() for item in endpoints if str(item).strip()]
    params_digest = str(raw.get('params_digest', '')).strip()
    ts_code = str(raw.get('ts_code', '')).strip()
    if not normalized_endpoints and not params_digest and not ts_code:
        return None
    return {
        'ts_code': ts_code,
        'endpoints': normalized_endpoints,
        'params_digest': params_digest,
    }


def _build_tushare_source_id(upstream_trace: dict[str, Any] | None, fallback_source_id: str) -> str:
    if upstream_trace is None:
        return fallback_source_id
    ts_code = upstream_trace.get('ts_code', '') or 'UNKNOWN'
    endpoints = '+'.join(upstream_trace.get('endpoints', [])) or 'UNKNOWN'
    params_digest = upstream_trace.get('params_digest', '') or 'UNKNOWN'
    return f"ts_code={ts_code};endpoints={endpoints};params_digest={params_digest}"


def _stable_snapshot_material(snapshot: dict[str, Any]) -> dict[str, Any]:
    stable = _deepcopy_json(snapshot)
    stable.pop('ingested_at', None)
    stable.pop('captured_at', None)
    stable.pop('checksum', None)
    stable.pop('data_quality', None)
    stable.pop('meta', None)
    return stable


def _classify_upstream_error(exc: Exception) -> str:
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return 'UPSTREAM_TIMEOUT'
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return 'RATE_LIMITED'
        return 'UPSTREAM_ERROR'
    message = str(exc).upper()
    if 'TOKEN_NOT_CONFIGURED' in message:
        return 'DATA_UNAVAILABLE'
    if 'DATA_UNAVAILABLE' in message:
        return 'DATA_UNAVAILABLE'
    return 'UPSTREAM_ERROR'


def _cache_wrap(snapshot: dict[str, Any], *, cache_key: str, ttl_seconds: int, cache_hit: bool) -> dict[str, Any]:
    output = _deepcopy_json(snapshot)
    meta = output.setdefault('meta', {})
    meta['cache'] = {'key': cache_key, 'hit': cache_hit, 'ttl_seconds': ttl_seconds}
    meta['cache_stats'] = _SNAPSHOT_CACHE.stats()
    return output


def _finalize_snapshot(
    payload: dict[str, Any],
    *,
    snapshot_type: str,
    ticker: str,
    asof_utc: str,
    source: str,
    source_id: str,
    required_fields: tuple[str, ...],
    numeric_bounds: dict[str, tuple[float | None, float | None]],
    freshness_seconds: int,
    fallback_reason: Exception | None = None,
) -> dict[str, Any]:
    snapshot = _deepcopy_json(payload)
    upstream_trace = _normalize_upstream_trace(snapshot.pop('_upstream_trace', None))
    snapshot['ticker'] = ticker
    snapshot['asof'] = asof_utc
    snapshot['captured_at'] = _utc_iso(snapshot.get('captured_at', _now_utc().isoformat()))
    snapshot['ingested_at'] = _now_utc().isoformat()
    snapshot['source'] = source
    if source.startswith('TUSHARE'):
        snapshot['source_id'] = _build_tushare_source_id(upstream_trace, source_id)
    else:
        snapshot['source_id'] = source_id
    snapshot['snapshot_type'] = snapshot_type

    stable_content = _stable_snapshot_material(snapshot)
    snapshot['checksum'] = _hash_digest(stable_content, length=20)
    snapshot['snapshot_id'] = snapshot.get('snapshot_id') or f"snap_{snapshot_type.lower()}_{_hash_digest({'ticker': ticker, 'asof': asof_utc, 'source_id': snapshot['source_id'], 'checksum': snapshot['checksum']}, length=12)}"

    quality, metrics = _quality_gate(
        snapshot,
        required_fields=required_fields,
        numeric_bounds=numeric_bounds,
        freshness_seconds=freshness_seconds,
    )
    if fallback_reason is not None:
        reason_code = _classify_upstream_error(fallback_reason)
        quality = _mark_fallback_status(
            quality,
            reason_code=reason_code,
            source=source,
            details=str(fallback_reason),
        )
    snapshot['data_quality'] = quality
    snapshot.setdefault('meta', {})
    if upstream_trace is not None:
        snapshot['meta']['upstream_trace'] = upstream_trace
    if fallback_reason is not None:
        reason_code = _classify_upstream_error(fallback_reason)
        snapshot['meta']['upstream_error'] = {
            'code': reason_code,
            'message': str(fallback_reason),
            'retryable': _retryable_error_code(reason_code),
        }
    snapshot['meta']['quality_metrics'] = metrics
    return snapshot


def _scaled_hash_value(seed: str, minimum: float, maximum: float, digits: int = 4) -> float:
    digest = int(hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12], 16)
    ratio = digest / float(16**12 - 1)
    return round(minimum + (maximum - minimum) * ratio, digits)


def _synthetic_market_snapshot(ticker: str, asof_utc: str) -> dict[str, Any]:
    last_price = _scaled_hash_value(f'{ticker}:{asof_utc}:price', 60, 260, 2)
    day_return = _scaled_hash_value(f'{ticker}:{asof_utc}:return', -0.04, 0.04, 4)
    vol = _scaled_hash_value(f'{ticker}:{asof_utc}:volatility', 0.08, 0.42, 4)
    turnover = _scaled_hash_value(f'{ticker}:{asof_utc}:turnover', 0.5, 5.2, 4)
    volume_ratio = _scaled_hash_value(f'{ticker}:{asof_utc}:volume_ratio', 0.5, 2.4, 4)
    regime = 'RANGE'
    if day_return >= 0.012:
        regime = 'UP'
    elif day_return <= -0.012:
        regime = 'DOWN'
    return {
        'currency': 'CNY',
        'last_price': last_price,
        'close': last_price,
        'returns': {'d1': day_return, 'w1': round(day_return * 2.5, 4), 'm1': round(day_return * 4, 4)},
        'volatility': {'atr_14': round(vol * 0.6, 4), 'stdev_20': vol},
        'volatility_20d': vol,
        'trend': {'ma_20': round(last_price * 0.98, 2), 'ma_60': round(last_price * 0.95, 2), 'regime': regime},
        'liquidity': {'avg_turnover_20d': turnover, 'spread_est': round(0.001 + abs(day_return) / 5, 5)},
        'volume_ratio': volume_ratio,
        'captured_at': _now_utc().isoformat(),
    }


def _synthetic_fundamentals_snapshot(ticker: str, asof_utc: str) -> dict[str, Any]:
    roe = _scaled_hash_value(f'{ticker}:{asof_utc}:roe', 0.06, 0.24, 4)
    revenue_yoy = _scaled_hash_value(f'{ticker}:{asof_utc}:revenue_yoy', -0.15, 0.35, 4)
    pe_ttm = _scaled_hash_value(f'{ticker}:{asof_utc}:pe_ttm', 8, 45, 3)
    pb = _scaled_hash_value(f'{ticker}:{asof_utc}:pb', 0.8, 8, 3)
    debt_to_assets = _scaled_hash_value(f'{ticker}:{asof_utc}:debt_ratio', 0.1, 0.8, 4)
    gross_margin = _scaled_hash_value(f'{ticker}:{asof_utc}:gross_margin', 0.12, 0.68, 4)
    profit_yoy = _scaled_hash_value(f'{ticker}:{asof_utc}:profit_yoy', -0.2, 0.4, 4)
    return {
        'quality': {'roe': roe, 'gross_margin': gross_margin, 'debt_to_assets': debt_to_assets},
        'growth': {'revenue_yoy': revenue_yoy, 'profit_yoy': profit_yoy},
        'valuation': {'pe_ttm': pe_ttm, 'pb': pb},
        'roe': roe,
        'pe_ttm': pe_ttm,
        'revenue_growth_yoy': revenue_yoy,
        'captured_at': _now_utc().isoformat(),
    }


def _synthetic_flow_snapshot(ticker: str, asof_utc: str) -> dict[str, Any]:
    net_flow = _scaled_hash_value(f'{ticker}:{asof_utc}:net_flow', -12.0, 12.0, 4)
    hot_score = int(round(_scaled_hash_value(f'{ticker}:{asof_utc}:hot_score', 30, 88, 0)))
    polarity = round(max(-1.0, min(1.0, net_flow / 12.0)), 4)
    confidence = round(0.45 + min(0.45, abs(polarity) * 0.4), 4)
    return {
        'hotness': {'mentions': int(180 + abs(net_flow) * 40), 'hot_score': hot_score},
        'sentiment': {'polarity': polarity, 'confidence': confidence},
        'flows': {'northbound_net': net_flow, 'main_force_net': round(net_flow * 0.6, 4)},
        'crowding': {'crowding_score': int(min(100, 40 + abs(net_flow) * 4))},
        'northbound_flow': net_flow,
        'hotness_score': hot_score,
        'captured_at': _now_utc().isoformat(),
    }


def _synthetic_macro_snapshot(ticker: str, asof_utc: str) -> dict[str, Any]:
    _ = ticker
    freight_change = _scaled_hash_value(f'GLOBAL:{asof_utc}:freight', -0.08, 0.08, 4)
    commodity_change = _scaled_hash_value(f'GLOBAL:{asof_utc}:commodity', -0.06, 0.06, 4)
    shibor_on = _scaled_hash_value(f'GLOBAL:{asof_utc}:shibor_on', 1.3, 2.8, 4)
    return {
        'series': [
            {'name': 'shibor_on', 'value': shibor_on, 'unit': 'pct', 'change_1w': _scaled_hash_value(f'{asof_utc}:shibor_change', -0.1, 0.1, 4)},
            {'name': 'freight_index_change', 'value': freight_change, 'unit': 'pct', 'change_1w': freight_change},
            {'name': 'commodity_basket_change', 'value': commodity_change, 'unit': 'pct', 'change_1w': commodity_change},
        ],
        'events': [],
        'freight_index_change': freight_change,
        'commodity_basket_change': commodity_change,
        'captured_at': _now_utc().isoformat(),
    }


def _estimate_market_regime(day_return: float | None) -> str:
    if day_return is None:
        return 'RANGE'
    if day_return >= 0.012:
        return 'UP'
    if day_return <= -0.012:
        return 'DOWN'
    return 'RANGE'


def _estimate_volatility(day_return: float | None) -> float | None:
    if day_return is None:
        return None
    return round(max(0.03, min(1.0, abs(day_return) * 1.8)), 4)


def _estimate_volume_ratio(volume: float | None) -> float | None:
    if volume is None or volume <= 0:
        return None
    # log scale keeps very large notional volumes within a compact range.
    return round(max(0.3, min(4.0, 0.7 + math.log10(max(volume, 1.0)) / 2.5)), 4)


@dataclass
class TushareProAdapter:
    token: str
    base_url: str
    timeout_seconds: float

    def _request(self, *, api_name: str, params: dict[str, Any], fields: str) -> list[dict[str, Any]]:
        response = httpx.post(
            self.base_url,
            json={'api_name': api_name, 'token': self.token, 'params': params, 'fields': fields},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get('code', 0) != 0:
            raise RuntimeError(f"TUSHARE_ERROR code={payload.get('code')} msg={payload.get('msg', '')}")
        data = payload.get('data') or {}
        field_names = data.get('fields') or []
        items = data.get('items') or []
        rows: list[dict[str, Any]] = []
        for item in items:
            rows.append({name: value for name, value in zip(field_names, item, strict=False)})
        return rows

    def fetch_market_snapshot(self, ticker: str, asof_utc: str) -> dict[str, Any]:
        trade_date = _parse_iso_to_utc(asof_utc).strftime('%Y%m%d')
        params_daily = {'ts_code': ticker, 'trade_date': trade_date}
        daily_rows = self._request(
            api_name='daily',
            params=params_daily,
            fields='ts_code,trade_date,open,high,low,close,vol,pct_chg',
        )
        if not daily_rows:
            raise ValueError('DATA_UNAVAILABLE: daily rows empty')
        row = daily_rows[0]

        params_basic = {'ts_code': ticker, 'trade_date': trade_date}
        basic_rows = self._request(
            api_name='daily_basic',
            params=params_basic,
            fields='ts_code,trade_date,turnover_rate,pe_ttm,pb',
        )
        basic = basic_rows[0] if basic_rows else {}
        close = _to_float(row.get('close'))
        day_return = None
        pct_chg = _to_float(row.get('pct_chg'))
        if pct_chg is not None:
            day_return = round(pct_chg / 100.0, 6)
        volatility = _estimate_volatility(day_return)
        return {
            'currency': 'CNY',
            'last_price': close,
            'close': close,
            'returns': {'d1': day_return, 'w1': None, 'm1': None},
            'volatility': {'atr_14': None, 'stdev_20': volatility},
            'volatility_20d': volatility,
            'trend': {'ma_20': None, 'ma_60': None, 'regime': _estimate_market_regime(day_return)},
            'liquidity': {'avg_turnover_20d': _to_float(basic.get('turnover_rate')), 'spread_est': None},
            'volume_ratio': _estimate_volume_ratio(_to_float(row.get('vol'))),
            '_upstream_trace': {
                'ts_code': ticker,
                'endpoints': ['daily', 'daily_basic'],
                'params_digest': _hash_digest({'daily': params_daily, 'daily_basic': params_basic}, length=16),
            },
            'captured_at': _now_utc().isoformat(),
        }

    def fetch_index_market_snapshot(self, ticker: str, asof_utc: str) -> dict[str, Any]:
        trade_date = _parse_iso_to_utc(asof_utc).strftime('%Y%m%d')
        params_index_daily = {'ts_code': ticker, 'trade_date': trade_date}
        rows = self._request(
            api_name='index_daily',
            params=params_index_daily,
            fields='ts_code,trade_date,open,high,low,close,vol,amount,pct_chg',
        )
        if not rows:
            raise ValueError('DATA_UNAVAILABLE: index_daily rows empty')
        row = rows[0]
        close = _to_float(row.get('close'))
        pct_chg = _to_float(row.get('pct_chg'))
        day_return = None
        if pct_chg is not None:
            day_return = round(pct_chg / 100.0, 6)
        volatility = _estimate_volatility(day_return)
        amount = _to_float(row.get('amount'))
        volume = _to_float(row.get('vol'))
        # index_daily has no daily_basic turnover fields, so we proxy liquidity from volume/amount.
        turnover_proxy = amount if amount is not None else volume
        spread_proxy = None
        if volatility is not None:
            spread_proxy = round(max(0.0002, volatility / 60), 6)
        return {
            'currency': 'CNY',
            'last_price': close,
            'close': close,
            'returns': {'d1': day_return, 'w1': None, 'm1': None},
            'volatility': {'atr_14': None, 'stdev_20': volatility},
            'volatility_20d': volatility,
            'trend': {'ma_20': None, 'ma_60': None, 'regime': _estimate_market_regime(day_return)},
            'liquidity': {'avg_turnover_20d': turnover_proxy, 'spread_est': spread_proxy},
            'volume_ratio': _estimate_volume_ratio(volume),
            '_upstream_trace': {
                'ts_code': ticker,
                'endpoints': ['index_daily'],
                'params_digest': _hash_digest({'index_daily': params_index_daily}, length=16),
            },
            'captured_at': _now_utc().isoformat(),
        }

    def fetch_fundamentals_snapshot(self, ticker: str, asof_utc: str) -> dict[str, Any]:
        trade_date = _parse_iso_to_utc(asof_utc).strftime('%Y%m%d')
        params_basic = {'ts_code': ticker, 'trade_date': trade_date}
        basic_rows = self._request(
            api_name='daily_basic',
            params=params_basic,
            fields='ts_code,trade_date,pe_ttm,pb',
        )
        basic = basic_rows[0] if basic_rows else {}

        params_indicator = {'ts_code': ticker, 'limit': 1}
        indicator_rows = self._request(
            api_name='fina_indicator',
            params=params_indicator,
            fields='ts_code,end_date,roe_dt,or_yoy,netprofit_yoy,grossprofit_margin,debt_to_assets',
        )
        indicator = indicator_rows[0] if indicator_rows else {}

        roe = _to_float(indicator.get('roe_dt'))
        revenue_yoy = _to_float(indicator.get('or_yoy'))
        profit_yoy = _to_float(indicator.get('netprofit_yoy'))
        gross_margin = _to_float(indicator.get('grossprofit_margin'))
        debt_to_assets = _to_float(indicator.get('debt_to_assets'))
        if revenue_yoy is not None:
            revenue_yoy = round(revenue_yoy / 100.0, 6)
        if profit_yoy is not None:
            profit_yoy = round(profit_yoy / 100.0, 6)
        if gross_margin is not None:
            gross_margin = round(gross_margin / 100.0, 6)
        if debt_to_assets is not None:
            debt_to_assets = round(debt_to_assets / 100.0, 6)
        if roe is not None:
            roe = round(roe / 100.0, 6)

        return {
            'quality': {'roe': roe, 'gross_margin': gross_margin, 'debt_to_assets': debt_to_assets},
            'growth': {'revenue_yoy': revenue_yoy, 'profit_yoy': profit_yoy},
            'valuation': {'pe_ttm': _to_float(basic.get('pe_ttm')), 'pb': _to_float(basic.get('pb'))},
            'roe': roe,
            'pe_ttm': _to_float(basic.get('pe_ttm')),
            'revenue_growth_yoy': revenue_yoy,
            '_upstream_trace': {
                'ts_code': ticker,
                'endpoints': ['daily_basic', 'fina_indicator'],
                'params_digest': _hash_digest({'daily_basic': params_basic, 'fina_indicator': params_indicator}, length=16),
            },
            'captured_at': _now_utc().isoformat(),
        }

    def fetch_flow_snapshot(self, ticker: str, asof_utc: str) -> dict[str, Any]:
        trade_date = _parse_iso_to_utc(asof_utc).strftime('%Y%m%d')
        params_moneyflow = {'ts_code': ticker, 'trade_date': trade_date}
        rows = self._request(
            api_name='moneyflow',
            params=params_moneyflow,
            fields='ts_code,trade_date,buy_lg_amt,buy_elg_amt,sell_lg_amt,sell_elg_amt',
        )
        if not rows:
            raise ValueError('DATA_UNAVAILABLE: moneyflow rows empty')
        row = rows[0]
        buy_lg_amt = _to_float(row.get('buy_lg_amt')) or 0.0
        buy_elg_amt = _to_float(row.get('buy_elg_amt')) or 0.0
        sell_lg_amt = _to_float(row.get('sell_lg_amt')) or 0.0
        sell_elg_amt = _to_float(row.get('sell_elg_amt')) or 0.0
        net_main_force = round((buy_lg_amt + buy_elg_amt) - (sell_lg_amt + sell_elg_amt), 4)
        denominator = abs(buy_lg_amt + buy_elg_amt) + abs(sell_lg_amt + sell_elg_amt) + 1e-6
        polarity = round(max(-1.0, min(1.0, net_main_force / denominator)), 6)
        hot_score = int(max(0, min(100, round(45 + abs(polarity) * 45))))
        return {
            'hotness': {'mentions': int(120 + abs(net_main_force) * 0.6), 'hot_score': hot_score},
            'sentiment': {'polarity': polarity, 'confidence': round(0.5 + min(0.4, abs(polarity) * 0.35), 6)},
            'flows': {'northbound_net': net_main_force, 'main_force_net': net_main_force},
            'crowding': {'crowding_score': int(max(0, min(100, 35 + abs(polarity) * 60)))},
            'northbound_flow': net_main_force,
            'hotness_score': hot_score,
            '_upstream_trace': {
                'ts_code': ticker,
                'endpoints': ['moneyflow'],
                'params_digest': _hash_digest({'moneyflow': params_moneyflow}, length=16),
            },
            'captured_at': _now_utc().isoformat(),
        }

    def fetch_macro_snapshot(self, ticker: str, asof_utc: str) -> dict[str, Any]:
        _ = ticker
        date_ymd = _parse_iso_to_utc(asof_utc).strftime('%Y%m%d')
        params_shibor = {'start_date': date_ymd, 'end_date': date_ymd}
        shibor_rows = self._request(
            api_name='shibor',
            params=params_shibor,
            fields='date,on,1w,1m',
        )
        row = shibor_rows[0] if shibor_rows else {}
        on_rate = _to_float(row.get('on'))
        week_rate = _to_float(row.get('1w'))
        month_rate = _to_float(row.get('1m'))
        return {
            'series': [
                {'name': 'shibor_on', 'value': on_rate, 'unit': 'pct', 'change_1w': None},
                {'name': 'shibor_1w', 'value': week_rate, 'unit': 'pct', 'change_1w': None},
                {'name': 'shibor_1m', 'value': month_rate, 'unit': 'pct', 'change_1w': None},
            ],
            'events': [],
            'freight_index_change': None,
            'commodity_basket_change': None,
            '_upstream_trace': {
                'ts_code': ticker,
                'endpoints': ['shibor'],
                'params_digest': _hash_digest({'shibor': params_shibor}, length=16),
            },
            'captured_at': _now_utc().isoformat(),
        }


def _get_tushare_adapter() -> TushareProAdapter | None:
    global _TUSHARE_ADAPTER
    dotenv_values = _read_dotenv_values()
    token = os.getenv('TUSHARE_TOKEN', '').strip() or dotenv_values.get('TUSHARE_TOKEN', '').strip()
    if not token:
        return None
    base_url = (
        os.getenv('TUSHARE_BASE_URL', '').strip()
        or dotenv_values.get('TUSHARE_BASE_URL', '').strip()
        or 'https://api.tushare.pro'
    )
    timeout_raw = (
        os.getenv('DATASOURCE_TIMEOUT_SECONDS', '').strip()
        or dotenv_values.get('DATASOURCE_TIMEOUT_SECONDS', '').strip()
        or '6'
    )
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError:
        timeout_seconds = 6.0
    if _TUSHARE_ADAPTER is None or _TUSHARE_ADAPTER.token != token or _TUSHARE_ADAPTER.base_url != base_url:
        _TUSHARE_ADAPTER = TushareProAdapter(token=token, base_url=base_url, timeout_seconds=timeout_seconds)
    return _TUSHARE_ADAPTER


def _fetch_market_live(ticker: str, asof_utc: str) -> dict[str, Any]:
    adapter = _get_tushare_adapter()
    if adapter is None:
        raise ValueError('DATA_UNAVAILABLE: TOKEN_NOT_CONFIGURED')
    return adapter.fetch_market_snapshot(ticker=ticker, asof_utc=asof_utc)


def _fetch_index_market_live(ticker: str, asof_utc: str) -> dict[str, Any]:
    adapter = _get_tushare_adapter()
    if adapter is None:
        raise ValueError('DATA_UNAVAILABLE: TOKEN_NOT_CONFIGURED')
    return adapter.fetch_index_market_snapshot(ticker=ticker, asof_utc=asof_utc)


def _fetch_fundamentals_live(ticker: str, asof_utc: str) -> dict[str, Any]:
    adapter = _get_tushare_adapter()
    if adapter is None:
        raise ValueError('DATA_UNAVAILABLE: TOKEN_NOT_CONFIGURED')
    return adapter.fetch_fundamentals_snapshot(ticker=ticker, asof_utc=asof_utc)


def _fetch_flow_live(ticker: str, asof_utc: str) -> dict[str, Any]:
    adapter = _get_tushare_adapter()
    if adapter is None:
        raise ValueError('DATA_UNAVAILABLE: TOKEN_NOT_CONFIGURED')
    return adapter.fetch_flow_snapshot(ticker=ticker, asof_utc=asof_utc)


def _fetch_macro_live(ticker: str, asof_utc: str) -> dict[str, Any]:
    adapter = _get_tushare_adapter()
    if adapter is None:
        raise ValueError('DATA_UNAVAILABLE: TOKEN_NOT_CONFIGURED')
    return adapter.fetch_macro_snapshot(ticker=ticker, asof_utc=asof_utc)


def _load_snapshot(
    *,
    cache_key: str,
    ttl_seconds: int,
    ticker: str,
    asof_utc: str,
    snapshot_type: str,
    source_id_live: str,
    source_id_fallback: str,
    required_fields: tuple[str, ...],
    numeric_bounds: dict[str, tuple[float | None, float | None]],
    freshness_seconds: int,
    live_loader: Any,
    fallback_loader: Any,
) -> dict[str, Any]:
    cached = _SNAPSHOT_CACHE.get(cache_key)
    if cached is not None:
        return _cache_wrap(cached.value, cache_key=cache_key, ttl_seconds=ttl_seconds, cache_hit=True)

    fallback_reason: Exception | None = None
    source = 'TUSHARE_PRO'
    source_id = source_id_live
    try:
        payload = live_loader(ticker, asof_utc)
    except Exception as exc:  # noqa: BLE001
        fallback_reason = exc
        stale = _SNAPSHOT_CACHE.get_stale(cache_key)
        if stale is not None:
            payload = _deepcopy_json(stale.value)
            source = 'TUSHARE_PRO_CACHE'
            source_id = 'cache.previous_snapshot'
        else:
            payload = fallback_loader(ticker, asof_utc)
            source = 'SYNTHETIC_FALLBACK'
            source_id = source_id_fallback

    finalized = _finalize_snapshot(
        payload,
        snapshot_type=snapshot_type,
        ticker=ticker,
        asof_utc=asof_utc,
        source=source,
        source_id=source_id,
        required_fields=required_fields,
        numeric_bounds=numeric_bounds,
        freshness_seconds=freshness_seconds,
        fallback_reason=fallback_reason,
    )
    _SNAPSHOT_CACHE.set(cache_key, finalized, ttl_seconds)
    return _cache_wrap(finalized, cache_key=cache_key, ttl_seconds=ttl_seconds, cache_hit=False)


def get_market_snapshot(ticker: str, asof: str) -> dict:
    asof_utc = _utc_iso(asof)
    cache_key = f'market:{ticker}:{asof_utc}'
    return _load_snapshot(
        cache_key=cache_key,
        ttl_seconds=MARKET_TTL_SECONDS,
        ticker=ticker,
        asof_utc=asof_utc,
        snapshot_type='MARKET',
        source_id_live='tushare.daily+tushare.daily_basic',
        source_id_fallback='synthetic.market',
        required_fields=('last_price', 'returns.d1', 'volatility.stdev_20', 'liquidity.avg_turnover_20d'),
        numeric_bounds={
            'last_price': (0.0001, None),
            'returns.d1': (-0.35, 0.35),
            'volatility.stdev_20': (0, 1),
            'volume_ratio': (0, 10),
        },
        freshness_seconds=5 * 60,
        live_loader=_fetch_market_live,
        fallback_loader=_synthetic_market_snapshot,
    )


def get_index_market_snapshot(ticker: str, asof: str) -> dict:
    asof_utc = _utc_iso(asof)
    cache_key = f'index_market:{ticker}:{asof_utc}'
    return _load_snapshot(
        cache_key=cache_key,
        ttl_seconds=MARKET_TTL_SECONDS,
        ticker=ticker,
        asof_utc=asof_utc,
        snapshot_type='MARKET',
        source_id_live='tushare.index_daily',
        source_id_fallback='synthetic.market.index',
        required_fields=('last_price', 'returns.d1', 'volatility.stdev_20', 'liquidity.avg_turnover_20d'),
        numeric_bounds={
            'last_price': (0.0001, None),
            'returns.d1': (-0.35, 0.35),
            'volatility.stdev_20': (0, 1),
            'volume_ratio': (0, 10),
        },
        freshness_seconds=5 * 60,
        live_loader=_fetch_index_market_live,
        fallback_loader=_synthetic_market_snapshot,
    )


def get_fundamentals_snapshot(ticker: str, asof: str) -> dict:
    asof_utc = _utc_iso(asof)
    cache_key = f'fundamentals:{ticker}:{asof_utc[:10]}'
    return _load_snapshot(
        cache_key=cache_key,
        ttl_seconds=FUNDAMENTALS_TTL_SECONDS,
        ticker=ticker,
        asof_utc=asof_utc,
        snapshot_type='FUNDAMENTALS',
        source_id_live='tushare.daily_basic+tushare.fina_indicator',
        source_id_fallback='synthetic.fundamentals',
        required_fields=('quality.roe', 'valuation.pe_ttm', 'growth.revenue_yoy'),
        numeric_bounds={
            'quality.roe': (-1, 1),
            'quality.debt_to_assets': (0, 1.5),
            'growth.revenue_yoy': (-1, 3),
            'valuation.pe_ttm': (0, 500),
            'valuation.pb': (0, 50),
        },
        freshness_seconds=36 * 60 * 60,
        live_loader=_fetch_fundamentals_live,
        fallback_loader=_synthetic_fundamentals_snapshot,
    )


def get_flow_sentiment_snapshot(ticker: str, asof: str) -> dict:
    asof_utc = _utc_iso(asof)
    cache_key = f'flow:{ticker}:{asof_utc}'
    return _load_snapshot(
        cache_key=cache_key,
        ttl_seconds=FLOW_TTL_SECONDS,
        ticker=ticker,
        asof_utc=asof_utc,
        snapshot_type='FLOW_SENTIMENT',
        source_id_live='tushare.moneyflow',
        source_id_fallback='synthetic.flow_sentiment',
        required_fields=('hotness.hot_score', 'sentiment.polarity', 'flows.main_force_net'),
        numeric_bounds={
            'hotness.hot_score': (0, 100),
            'sentiment.polarity': (-1, 1),
            'sentiment.confidence': (0, 1),
            'crowding.crowding_score': (0, 100),
        },
        freshness_seconds=15 * 60,
        live_loader=_fetch_flow_live,
        fallback_loader=_synthetic_flow_snapshot,
    )


def get_macro_commodity_logistics_snapshot(ticker: str, asof: str) -> dict:
    asof_utc = _utc_iso(asof)
    cache_key = f'macro:{ticker}:{asof_utc[:13]}'
    return _load_snapshot(
        cache_key=cache_key,
        ttl_seconds=MACRO_TTL_SECONDS,
        ticker=ticker,
        asof_utc=asof_utc,
        snapshot_type='MACRO',
        source_id_live='tushare.shibor',
        source_id_fallback='synthetic.macro_commodity_logistics',
        required_fields=('series',),
        numeric_bounds={
            'freight_index_change': (-1, 1),
            'commodity_basket_change': (-1, 1),
        },
        freshness_seconds=60 * 60,
        live_loader=_fetch_macro_live,
        fallback_loader=_synthetic_macro_snapshot,
    )


def reset_facts_runtime_state() -> None:
    global _TUSHARE_ADAPTER
    _SNAPSHOT_CACHE.clear()
    _TUSHARE_ADAPTER = None
    _read_dotenv_values.cache_clear()
