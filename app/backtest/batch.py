from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
import statistics
import time
import uuid
from typing import Any, Callable

from app.backtest.promotion import resolve_champion_version
from app.backtest.skill_pack import load_skill_pack


Runner = Callable[[dict[str, Any], str], dict[str, Any]]
SnapshotLoader = Callable[[str, str], dict[str, Any]]
ProgressCallback = Callable[[dict[str, Any]], None]
CancelChecker = Callable[[], bool]

DEFAULT_INITIAL_CAPITAL_CNY = 1_000_000.0
DEFAULT_FEE_BPS_BY_MARKET = {
    'CN_A': 5.0,
    'US': 1.0,
    'HK': 5.0,
    'CRYPTO': 2.0,
    'OTHER': 5.0,
}
DEFAULT_SLIPPAGE_BPS_BY_MARKET = {
    'CN_A': 8.0,
    'US': 3.0,
    'HK': 8.0,
    'CRYPTO': 10.0,
    'OTHER': 8.0,
}
DEFAULT_SELL_TAX_BPS_BY_MARKET = {
    'CN_A': 10.0,
    'US': 0.0,
    'HK': 0.0,
    'CRYPTO': 0.0,
    'OTHER': 0.0,
}
HORIZON_DECISION_KEYS = (
    'evaluation_horizon_days',
    'horizon_days',
    'holding_days',
    'target_holding_days',
    'evaluation_window_days',
    'holding_period_days',
)
TIME_HORIZON_DAY_MAPPING = {
    'T+0': 1,
    'T+1': 1,
    'INTRADAY': 1,
    'SCALP': 1,
    'DAY': 1,
    'DAILY': 1,
    'SHORT': 3,
    'SHORT_TERM': 5,
    'SWING': 5,
    'MEDIUM': 10,
    'MEDIUM_TERM': 10,
    'MID': 10,
    'MID_TERM': 10,
    'POSITION': 20,
    'LONG': 60,
    'LONG_TERM': 60,
    'INVESTMENT': 60,
}


def _as_date(value: Any, *, field_name: str) -> date:
    if isinstance(value, date):
        return value
    text = str(value or '').strip()
    if not text:
        raise ValueError(f'{field_name} is required')
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f'{field_name} must use YYYY-MM-DD format') from exc


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_text(value: Any) -> str:
    return str(value or '').strip()


def _try_parse_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        parsed = int(value)
        return parsed if parsed > 0 else None
    text = _safe_text(value)
    if not text:
        return None
    try:
        parsed = int(float(text))
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        pass
    matched = re.search(r'(\d{1,3})', text)
    if not matched:
        return None
    parsed = int(matched.group(1))
    return parsed if parsed > 0 else None


def _resolve_horizon_from_time_horizon(value: Any) -> int | None:
    text = _safe_text(value)
    if not text:
        return None
    numeric = _try_parse_positive_int(text)
    if numeric is not None:
        return numeric

    normalized = text.upper().replace('-', '_').replace(' ', '_')
    if normalized in TIME_HORIZON_DAY_MAPPING:
        return TIME_HORIZON_DAY_MAPPING[normalized]
    if normalized.startswith('SWING'):
        return TIME_HORIZON_DAY_MAPPING['SWING']
    if normalized.startswith('LONG'):
        return TIME_HORIZON_DAY_MAPPING['LONG']
    if normalized.startswith('SHORT'):
        return TIME_HORIZON_DAY_MAPPING['SHORT_TERM']
    if '短' in text:
        return TIME_HORIZON_DAY_MAPPING['SWING']
    if '中' in text:
        return TIME_HORIZON_DAY_MAPPING['POSITION']
    if '长' in text:
        return TIME_HORIZON_DAY_MAPPING['LONG']
    return None


def _resolve_evaluation_horizon_days(decision: Any, *, default_horizon_days: int) -> tuple[int, str]:
    fallback_source = 'payload.default_evaluation_horizon_days'
    if not isinstance(decision, dict):
        return default_horizon_days, fallback_source
    for key in HORIZON_DECISION_KEYS:
        parsed = _try_parse_positive_int(decision.get(key))
        if parsed is None:
            continue
        return _safe_int(parsed, default=default_horizon_days, minimum=1, maximum=120), f'decision.{key}'
    time_horizon_days = _resolve_horizon_from_time_horizon(decision.get('time_horizon'))
    if time_horizon_days is not None:
        return _safe_int(time_horizon_days, default=default_horizon_days, minimum=1, maximum=120), 'decision.time_horizon'
    return default_horizon_days, fallback_source


def _parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def _resolve_skill_pack_version(skill_pack_id: str, requested_version: Any) -> tuple[str, str]:
    normalized = _safe_text(requested_version)
    if normalized and normalized.lower() not in {'champion', 'auto'}:
        return normalized, 'explicit'
    champion_version = resolve_champion_version(skill_pack_id)
    if champion_version:
        return champion_version, 'champion'
    return '0.1.0', 'default'


def _parse_hhmmss(value: str) -> tuple[int, int, int]:
    tokens = value.split(':')
    if len(tokens) not in (2, 3):
        raise ValueError('asof_time must be HH:MM or HH:MM:SS')
    hour = int(tokens[0])
    minute = int(tokens[1])
    second = int(tokens[2]) if len(tokens) == 3 else 0
    if hour < 0 or hour > 23 or minute < 0 or minute > 59 or second < 0 or second > 59:
        raise ValueError('asof_time out of range')
    return hour, minute, second


def _parse_offset(value: str) -> timezone:
    text = value.strip()
    if len(text) != 6 or text[0] not in ('+', '-') or text[3] != ':':
        raise ValueError('timezone_offset must match +/-HH:MM')
    sign = 1 if text[0] == '+' else -1
    hour = int(text[1:3])
    minute = int(text[4:6])
    if hour > 23 or minute > 59:
        raise ValueError('timezone_offset out of range')
    return timezone(sign * timedelta(hours=hour, minutes=minute))


def _build_asof(day: date, *, asof_time: str, timezone_offset: str) -> datetime:
    hour, minute, second = _parse_hhmmss(asof_time)
    tz = _parse_offset(timezone_offset)
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=tz)


def _extract_price(snapshot: dict[str, Any] | None) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    for key in ('close', 'last_price'):
        if key in snapshot:
            price = _safe_float(snapshot.get(key), default=0.0)
            if price > 0:
                return price
    return None


def _is_non_trading_day_snapshot(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    meta = snapshot.get('meta')
    if not isinstance(meta, dict):
        return False
    upstream_error = meta.get('upstream_error')
    if not isinstance(upstream_error, dict):
        return False
    code = _safe_text(upstream_error.get('code')).upper()
    message = _safe_text(upstream_error.get('message')).lower()
    return code == 'DATA_UNAVAILABLE' and 'rows empty' in message


def _emit_progress(progress_callback: ProgressCallback | None, payload: dict[str, Any]) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(payload)
    except Exception:
        # Progress callbacks are best-effort and should never interrupt backtest execution.
        return


def _is_cancel_requested(cancel_requested: CancelChecker | None) -> bool:
    return bool(cancel_requested is not None and cancel_requested())


def _sleep_with_cancel_check(
    total_seconds: float,
    *,
    cancel_requested: CancelChecker | None = None,
    interval_seconds: float = 0.2,
) -> bool:
    remaining = max(0.0, float(total_seconds))
    step = max(0.01, float(interval_seconds))
    while remaining > 0:
        if _is_cancel_requested(cancel_requested):
            return True
        sleep_seconds = min(step, remaining)
        time.sleep(sleep_seconds)
        remaining -= sleep_seconds
    return _is_cancel_requested(cancel_requested)


def _resolve_benchmark_ticker(market: str, override_ticker: str | None = None) -> str:
    custom = _safe_text(override_ticker).upper()
    if custom:
        return custom
    mapping = {
        'CN_A': '000300.SH',  # CSI300
        'US': 'SPY',
        'HK': '^HSI',
        'CRYPTO': 'BTCUSDT',
    }
    return mapping.get(_safe_text(market).upper(), '000300.SH')


def _calc_step_return_pct(prev_price: float | None, current_price: float | None) -> float | None:
    if prev_price is None or current_price is None:
        return None
    if prev_price <= 0:
        return None
    return ((current_price / prev_price) - 1.0) * 100.0


def _calc_forward_return(
    *,
    ticker: str,
    asof: datetime,
    horizon_days: int,
    snapshot_loader: SnapshotLoader,
    entry_price: float | None = None,
) -> tuple[float | None, str]:
    try:
        future_snapshot = snapshot_loader(ticker=ticker, asof=(asof + timedelta(days=horizon_days)).isoformat())
    except Exception as exc:  # noqa: BLE001
        return None, f'snapshot_error: {exc}'

    start_price = entry_price
    if start_price is None:
        try:
            current_snapshot = snapshot_loader(ticker=ticker, asof=asof.isoformat())
        except Exception as exc:  # noqa: BLE001
            return None, f'snapshot_error: {exc}'
        start_price = _extract_price(current_snapshot)
    end_price = _extract_price(future_snapshot)
    if start_price is None or end_price is None or start_price <= 0:
        return None, 'missing_price'
    pct = ((end_price / start_price) - 1.0) * 100.0
    return round(pct, 4), ''


def _is_fallback(report: dict[str, Any]) -> bool:
    decision = report.get('decision', {}) if isinstance(report.get('decision', {}), dict) else {}
    summary = _safe_text(decision.get('summary')).lower()
    primary_model = _safe_text(((report.get('provenance') or {}).get('model') or {}).get('primary')).lower()
    data_quality_status = _safe_text(((report.get('data_quality') or {}).get('status')).upper())
    return 'fallback' in summary or primary_model.startswith('rule-fallback') or data_quality_status == 'DEGRADED'


def _mean_or_zero(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(float(statistics.fmean(values)), 6)


def _resolve_bps(value: Any, *, default: float) -> float:
    parsed = _safe_float(value, default=default)
    if parsed < 0:
        return 0.0
    return min(parsed, 500.0)


def _target_exposure_from_action(action: str, current_exposure: float) -> float:
    normalized = _safe_text(action).upper()
    if normalized in {'BUY', 'ADD'}:
        return 1.0
    if normalized == 'REDUCE':
        return 0.5
    if normalized in {'SELL', 'AVOID'}:
        return 0.0
    if normalized == 'HOLD':
        return _safe_float(current_exposure, default=0.0)
    if normalized == 'WATCH':
        return 0.0
    return _safe_float(current_exposure, default=0.0)


def _build_equity_curve(
    *,
    completed_runs: list[dict[str, Any]],
    initial_capital_cny: float,
    transaction_fee_bps: float,
    slippage_bps: float,
    sell_tax_bps: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, float, float, float, float]:
    if not completed_runs:
        return [], [], initial_capital_cny, initial_capital_cny, initial_capital_cny, 0.0, 0.0

    ordered = sorted(completed_runs, key=lambda item: _parse_iso_datetime(str(item.get('asof', ''))))
    strategy_capital = float(initial_capital_cny)
    strategy_capital_gross = float(initial_capital_cny)
    benchmark_capital = float(initial_capital_cny)
    strategy_curve: list[dict[str, Any]] = []
    benchmark_curve: list[dict[str, Any]] = []
    current_exposure = 0.0
    total_trade_cost_cny = 0.0
    total_turnover = 0.0

    # First point starts at NAV=1
    first = ordered[0]
    strategy_curve.append(
        {
            'asof': str(first.get('asof', '')),
            'capital_cny': round(strategy_capital, 4),
            'nav': round(strategy_capital / initial_capital_cny, 8),
            'capital_cny_gross': round(strategy_capital_gross, 4),
            'nav_gross': round(strategy_capital_gross / initial_capital_cny, 8),
            'step_return_pct': 0.0,
            'step_return_pct_gross': 0.0,
            'trade_turnover': 0.0,
            'trade_cost_cny': 0.0,
            'exposure': 0.0,
            'signal': 'INIT',
        }
    )
    benchmark_curve.append(
        {
            'asof': str(first.get('asof', '')),
            'capital_cny': round(benchmark_capital, 4),
            'nav': round(benchmark_capital / initial_capital_cny, 8),
            'step_return_pct': 0.0,
        }
    )

    for idx in range(1, len(ordered)):
        prev = ordered[idx - 1]
        current = ordered[idx]
        prev_action = _safe_text(prev.get('action')).upper()
        target_exposure = max(0.0, min(1.0, _target_exposure_from_action(prev_action, current_exposure)))
        turnover = abs(target_exposure - current_exposure)
        sell_turnover = max(current_exposure - target_exposure, 0.0)
        cost_rate = (
            (turnover * (_safe_float(transaction_fee_bps) + _safe_float(slippage_bps)) / 10_000.0)
            + (sell_turnover * _safe_float(sell_tax_bps) / 10_000.0)
        )
        trade_cost_cny = strategy_capital * cost_rate
        if trade_cost_cny > 0:
            total_trade_cost_cny += trade_cost_cny
        total_turnover += turnover

        ticker_step_return = _calc_step_return_pct(
            _safe_float(prev.get('ticker_price'), default=0.0) or None,
            _safe_float(current.get('ticker_price'), default=0.0) or None,
        )
        benchmark_step_return = _calc_step_return_pct(
            _safe_float(prev.get('benchmark_price'), default=0.0) or None,
            _safe_float(current.get('benchmark_price'), default=0.0) or None,
        )

        applied_strategy_return = 0.0
        if ticker_step_return is not None:
            applied_strategy_return = target_exposure * ticker_step_return
        strategy_capital_gross *= 1.0 + (applied_strategy_return / 100.0)
        strategy_capital = max(0.0, strategy_capital - trade_cost_cny)
        strategy_capital *= 1.0 + (applied_strategy_return / 100.0)
        current_exposure = target_exposure

        applied_benchmark_return = 0.0
        if benchmark_step_return is not None:
            applied_benchmark_return = benchmark_step_return
            benchmark_capital *= 1.0 + (benchmark_step_return / 100.0)

        prev_nav_net = _safe_float(strategy_curve[-1].get('nav'), default=1.0)
        prev_nav_gross = _safe_float(strategy_curve[-1].get('nav_gross'), default=1.0)
        current_nav_net = strategy_capital / initial_capital_cny
        current_nav_gross = strategy_capital_gross / initial_capital_cny
        step_return_net_pct = ((current_nav_net / prev_nav_net) - 1.0) * 100.0 if prev_nav_net > 0 else 0.0
        step_return_gross_pct = ((current_nav_gross / prev_nav_gross) - 1.0) * 100.0 if prev_nav_gross > 0 else 0.0

        strategy_curve.append(
            {
                'asof': str(current.get('asof', '')),
                'capital_cny': round(strategy_capital, 4),
                'nav': round(strategy_capital / initial_capital_cny, 8),
                'capital_cny_gross': round(strategy_capital_gross, 4),
                'nav_gross': round(strategy_capital_gross / initial_capital_cny, 8),
                'step_return_pct': round(step_return_net_pct, 6),
                'step_return_pct_gross': round(step_return_gross_pct, 6),
                'trade_turnover': round(turnover, 6),
                'trade_cost_cny': round(trade_cost_cny, 6),
                'exposure': round(current_exposure, 6),
                'signal': prev_action or 'WATCH',
            }
        )
        benchmark_curve.append(
            {
                'asof': str(current.get('asof', '')),
                'capital_cny': round(benchmark_capital, 4),
                'nav': round(benchmark_capital / initial_capital_cny, 8),
                'step_return_pct': round(applied_benchmark_return, 6),
            }
        )

    return (
        strategy_curve,
        benchmark_curve,
        strategy_capital,
        strategy_capital_gross,
        benchmark_capital,
        total_trade_cost_cny,
        total_turnover,
    )


def run_batch_backtest(
    payload: dict[str, Any],
    *,
    runner: Runner,
    snapshot_loader: SnapshotLoader,
    benchmark_loader: SnapshotLoader | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_requested: CancelChecker | None = None,
) -> dict[str, Any]:
    ticker = _safe_text(payload.get('ticker')).upper()
    strategy_version_id = _safe_text(payload.get('strategy_version_id'))
    tier = _safe_text(payload.get('tier')).upper() or 'TIER0'
    market = _safe_text(payload.get('market')).upper() or 'OTHER'
    skill_pack_id = _safe_text(payload.get('skill_pack_id')) or 'cn_a_core'
    requested_skill_pack_version = payload.get('skill_pack_version')
    skill_pack_version, skill_pack_version_source = _resolve_skill_pack_version(skill_pack_id, requested_skill_pack_version)
    try:
        resolved_skill_pack = load_skill_pack(skill_pack_id=skill_pack_id, version=skill_pack_version)
    except ValueError as exc:
        raise ValueError(f'invalid skill pack configuration: {exc}') from exc
    skill_pack_summary = dict(resolved_skill_pack.get('summary', {}))
    skill_pack_summary['version_source'] = skill_pack_version_source
    benchmark_ticker = _resolve_benchmark_ticker(market, payload.get('benchmark_ticker'))
    benchmark_snapshot_loader = benchmark_loader or snapshot_loader
    transaction_fee_bps = _resolve_bps(
        payload.get('transaction_fee_bps'),
        default=DEFAULT_FEE_BPS_BY_MARKET.get(market, DEFAULT_FEE_BPS_BY_MARKET['OTHER']),
    )
    slippage_bps = _resolve_bps(
        payload.get('slippage_bps'),
        default=DEFAULT_SLIPPAGE_BPS_BY_MARKET.get(market, DEFAULT_SLIPPAGE_BPS_BY_MARKET['OTHER']),
    )
    sell_tax_bps = _resolve_bps(
        payload.get('sell_tax_bps'),
        default=DEFAULT_SELL_TAX_BPS_BY_MARKET.get(market, DEFAULT_SELL_TAX_BPS_BY_MARKET['OTHER']),
    )
    initial_capital_cny = _safe_float(payload.get('initial_capital_cny'), default=DEFAULT_INITIAL_CAPITAL_CNY)
    if initial_capital_cny <= 0:
        raise ValueError('initial_capital_cny must be positive')
    if not ticker:
        raise ValueError('ticker is required')
    if not strategy_version_id:
        raise ValueError('strategy_version_id is required')

    start_date = _as_date(payload.get('start_date'), field_name='start_date')
    end_date = _as_date(payload.get('end_date'), field_name='end_date')
    if end_date < start_date:
        raise ValueError('end_date must be on or after start_date')

    step_days = _safe_int(payload.get('step_days'), default=1, minimum=1, maximum=30)
    max_runs = _safe_int(payload.get('max_runs'), default=60, minimum=1, maximum=500)
    default_horizon_days = _safe_int(payload.get('evaluation_horizon_days'), default=5, minimum=1, maximum=120)
    trading_days_only = bool(payload.get('trading_days_only', True))
    asof_time = _safe_text(payload.get('asof_time')) or '09:30'
    timezone_offset = _safe_text(payload.get('timezone_offset')) or '+08:00'
    thread_prefix = _safe_text(payload.get('thread_prefix')) or f"backtest_{ticker.replace('.', '_')}"

    cursor = start_date
    asof_days: list[date] = []
    while cursor <= end_date:
        if (not trading_days_only) or cursor.weekday() < 5:
            asof_days.append(cursor)
            if len(asof_days) > max_runs:
                raise ValueError(
                    f'generated {len(asof_days)} runs which exceeds max_runs={max_runs}; '
                    'narrow date range, increase step_days, or raise max_runs'
                )
        cursor += timedelta(days=step_days)
    if not asof_days:
        raise ValueError('date range produced zero runnable points; check trading_days_only and step_days')

    resume_state_raw = payload.get('_resume_state')
    resume_state = resume_state_raw if isinstance(resume_state_raw, dict) else {}
    resume_next_index = _safe_int(
        resume_state.get('next_index'),
        default=0,
        minimum=0,
        maximum=len(asof_days),
    )
    runs_raw = resume_state.get('runs')
    runs: list[dict[str, Any]] = [dict(item) for item in runs_raw if isinstance(item, dict)] if isinstance(runs_raw, list) else []
    skipped_dates_raw = resume_state.get('skipped_non_trading_dates')
    skipped_non_trading_dates = [str(item) for item in skipped_dates_raw if str(item).strip()] if isinstance(skipped_dates_raw, list) else []
    skipped_non_trading_runs = _safe_int(
        resume_state.get('skipped_non_trading_runs'),
        default=len(skipped_non_trading_dates),
        minimum=0,
        maximum=max_runs,
    )
    completed_runs_count = _safe_int(
        resume_state.get('completed_runs'),
        default=sum(1 for item in runs if _safe_text(item.get('status')).upper() == 'COMPLETED'),
        minimum=0,
        maximum=max_runs,
    )
    failed_runs_count = _safe_int(
        resume_state.get('failed_runs'),
        default=0,
        minimum=0,
        maximum=max_runs,
    )
    processed_points = _safe_int(
        resume_state.get('processed_points'),
        default=resume_next_index,
        minimum=0,
        maximum=len(asof_days),
    )
    if processed_points < resume_next_index:
        processed_points = resume_next_index
    started_at = datetime.now(timezone.utc)
    cancelled = False
    interrupted = False
    interruption_reason = ''
    interrupted_at = ''
    retry_attempts = _safe_int(payload.get('main_chain_retry_attempts'), default=3, minimum=1, maximum=8)
    retry_backoff_ms = _safe_int(payload.get('main_chain_retry_backoff_ms'), default=1200, minimum=0, maximum=15000)
    _emit_progress(
        progress_callback,
        {
            'status': 'RUNNING',
            'generated_points': len(asof_days),
            'processed_points': processed_points,
            'completed_runs': completed_runs_count,
            'failed_runs': failed_runs_count,
            'skipped_non_trading_runs': skipped_non_trading_runs,
            'current_date': '',
            'last_outcome': '',
        },
    )
    for day_index in range(resume_next_index, len(asof_days)):
        if _is_cancel_requested(cancel_requested):
            cancelled = True
            break
        day = asof_days[day_index]
        try:
            asof_dt = _build_asof(day, asof_time=asof_time, timezone_offset=timezone_offset)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        ticker_snapshot: dict[str, Any] | None = None
        benchmark_snapshot: dict[str, Any] | None = None
        snapshot_notes: list[str] = []
        try:
            ticker_snapshot = snapshot_loader(ticker=ticker, asof=asof_dt.isoformat())
        except Exception as exc:  # noqa: BLE001
            snapshot_notes.append(f'ticker_price_error: {exc}')
        try:
            benchmark_snapshot = benchmark_snapshot_loader(ticker=benchmark_ticker, asof=asof_dt.isoformat())
        except Exception as exc:  # noqa: BLE001
            snapshot_notes.append(f'benchmark_price_error: {exc}')

        if trading_days_only and (
            _is_non_trading_day_snapshot(ticker_snapshot)
            or _is_non_trading_day_snapshot(benchmark_snapshot)
        ):
            skipped_non_trading_runs += 1
            skipped_non_trading_dates.append(day.isoformat())
            processed_points += 1
            _emit_progress(
                progress_callback,
                {
                    'status': 'RUNNING',
                    'generated_points': len(asof_days),
                    'processed_points': processed_points,
                    'completed_runs': completed_runs_count,
                    'failed_runs': failed_runs_count,
                    'skipped_non_trading_runs': skipped_non_trading_runs,
                    'current_date': day.isoformat(),
                    'last_outcome': 'SKIPPED_NON_TRADING',
                },
            )
            continue

        index = len(runs) + 1
        thread_id = f"{thread_prefix}_{day.strftime('%Y%m%d')}_{index:03d}_{uuid.uuid4().hex[:8]}"
        request_data = {
            'ticker': ticker,
            'market': market,
            'asof': asof_dt.isoformat(),
            'strategy_version_id': strategy_version_id,
            'tier': tier,
            'run_mode': 'BACKTEST',
            'skill_pack_id': skill_pack_summary.get('skill_pack_id', skill_pack_id),
            'skill_pack_version': skill_pack_summary.get('version', skill_pack_version),
            'skill_pack_version_source': skill_pack_version_source,
        }
        for key in (
            'analysis_mode',
            'ta_hybrid_mode',
            'ta_research_rounds',
            'ta_risk_rounds',
            'ta_llm_call_cap',
            'ta_require_evidence_refs',
        ):
            if key in payload:
                request_data[key] = payload.get(key)
        wall_started = time.perf_counter()
        try:
            result = None
            runner_error_messages: list[str] = []
            for attempt in range(1, retry_attempts + 1):
                if _is_cancel_requested(cancel_requested):
                    cancelled = True
                    break
                try:
                    result = runner(request_data=request_data, thread_id=thread_id)
                    break
                except Exception as exc:  # noqa: BLE001
                    runner_error_messages.append(f'[{attempt}/{retry_attempts}] {exc}')
                    if attempt < retry_attempts and retry_backoff_ms > 0:
                        wait_seconds = (retry_backoff_ms * attempt) / 1000.0
                        if _sleep_with_cancel_check(wait_seconds, cancel_requested=cancel_requested):
                            cancelled = True
                            break
            if cancelled:
                _emit_progress(
                    progress_callback,
                    {
                        'status': 'CANCELLING',
                        'generated_points': len(asof_days),
                        'processed_points': processed_points,
                        'completed_runs': completed_runs_count,
                        'failed_runs': failed_runs_count,
                        'skipped_non_trading_runs': skipped_non_trading_runs,
                        'current_date': day.isoformat(),
                        'last_outcome': 'CANCELLING',
                    },
                )
                break
            if result is None:
                raise RuntimeError(' | '.join(runner_error_messages) or 'main chain unavailable')
            report = result.get('final_report', {}) if isinstance(result, dict) else {}
            persist_refs = result.get('persist_refs', {}) if isinstance(result, dict) else {}
            decision = report.get('decision', {}) if isinstance(report.get('decision', {}), dict) else {}
            data_quality = report.get('data_quality', {}) if isinstance(report.get('data_quality', {}), dict) else {}
            provenance = report.get('provenance', {}) if isinstance(report.get('provenance', {}), dict) else {}
            tool_stats = provenance.get('tool_call_stats', {}) if isinstance(provenance.get('tool_call_stats', {}), dict) else {}
            ta_hybrid = provenance.get('ta_hybrid', {}) if isinstance(provenance.get('ta_hybrid', {}), dict) else {}
            wall_time_ms = int((time.perf_counter() - wall_started) * 1000)
            evaluation_horizon_days_used, evaluation_horizon_source = _resolve_evaluation_horizon_days(
                decision,
                default_horizon_days=default_horizon_days,
            )

            ticker_price = _extract_price(ticker_snapshot)
            benchmark_price = _extract_price(benchmark_snapshot)

            forward_return_pct, return_note = _calc_forward_return(
                ticker=ticker,
                asof=asof_dt,
                horizon_days=evaluation_horizon_days_used,
                snapshot_loader=snapshot_loader,
                entry_price=ticker_price,
            )
            if snapshot_notes:
                return_note = f'{return_note} | {" | ".join(snapshot_notes)}'.strip(' |')
            runs.append(
                {
                    'index': index,
                    'asof': asof_dt.isoformat(),
                    'thread_id': thread_id,
                    'status': 'COMPLETED',
                    'report_id': _safe_text(report.get('report_id')),
                    'action': _safe_text(decision.get('action')).upper() or 'WATCH',
                    'overall_score': _safe_int(decision.get('overall_score'), default=0, minimum=0, maximum=100),
                    'confidence': round(_safe_float(decision.get('confidence'), default=0.0), 6),
                    'data_quality_status': _safe_text(data_quality.get('status')).upper() or 'UNKNOWN',
                    'fallback': _is_fallback(report),
                    'model_primary': _safe_text(((provenance.get('model') or {}).get('primary'))),
                    'tool_calls': _safe_int(tool_stats.get('tool_calls'), default=0, minimum=0, maximum=100000),
                    'cost_usd_est': round(_safe_float(tool_stats.get('cost_usd_est'), default=0.0), 6),
                    'latency_ms': _safe_int(tool_stats.get('latency_ms'), default=0, minimum=0, maximum=10_000_000),
                    'wall_time_ms': wall_time_ms,
                    'skill_note_id': _safe_text((persist_refs or {}).get('skill_note_id')),
                    'ticker_price': round(ticker_price, 6) if isinstance(ticker_price, (int, float)) else None,
                    'benchmark_ticker': benchmark_ticker,
                    'benchmark_price': round(benchmark_price, 6) if isinstance(benchmark_price, (int, float)) else None,
                    'evaluation_horizon_days_used': evaluation_horizon_days_used,
                    'evaluation_horizon_source': evaluation_horizon_source,
                    'forward_return_pct': forward_return_pct,
                    'forward_return_note': return_note,
                    'skill_pack_id': skill_pack_summary.get('skill_pack_id', skill_pack_id),
                    'skill_pack_version': skill_pack_summary.get('version', skill_pack_version),
                    'skill_pack_version_source': skill_pack_version_source,
                    'ta_hybrid_mode': _safe_text(ta_hybrid.get('mode')).upper() or _safe_text(payload.get('ta_hybrid_mode')).upper() or 'OFF',
                    'ta_hybrid_status': _safe_text(ta_hybrid.get('status')).upper() or 'OFF',
                    'ta_hybrid_applied': bool(ta_hybrid.get('applied', False)),
                    'ta_directional_bias': round(_safe_float(ta_hybrid.get('directional_bias'), default=0.0), 6),
                    'ta_conviction': round(_safe_float(ta_hybrid.get('conviction'), default=0.0), 6),
                    'ta_disagreement': round(_safe_float(ta_hybrid.get('disagreement'), default=0.0), 6),
                    'ta_llm_calls_used': _safe_int(ta_hybrid.get('llm_calls_used'), default=0, minimum=0, maximum=1000),
                }
            )
            processed_points += 1
            completed_runs_count += 1
            _emit_progress(
                progress_callback,
                {
                    'status': 'RUNNING',
                    'generated_points': len(asof_days),
                    'processed_points': processed_points,
                    'completed_runs': completed_runs_count,
                    'failed_runs': failed_runs_count,
                    'skipped_non_trading_runs': skipped_non_trading_runs,
                    'current_date': day.isoformat(),
                    'last_outcome': 'COMPLETED',
                },
            )
            if _is_cancel_requested(cancel_requested):
                cancelled = True
                _emit_progress(
                    progress_callback,
                    {
                        'status': 'CANCELLING',
                        'generated_points': len(asof_days),
                        'processed_points': processed_points,
                        'completed_runs': completed_runs_count,
                        'failed_runs': failed_runs_count,
                        'skipped_non_trading_runs': skipped_non_trading_runs,
                        'current_date': day.isoformat(),
                        'last_outcome': 'CANCELLING',
                    },
                )
                break
        except Exception as exc:  # noqa: BLE001
            failed_runs_count += 1
            interrupted = True
            interruption_reason = f'main chain unavailable after retries at {day.isoformat()}: {exc}'
            interrupted_at = asof_dt.isoformat()
            _emit_progress(
                progress_callback,
                {
                    'status': 'FAILED',
                    'generated_points': len(asof_days),
                    'processed_points': processed_points,
                    'completed_runs': completed_runs_count,
                    'failed_runs': failed_runs_count,
                    'skipped_non_trading_runs': skipped_non_trading_runs,
                    'current_date': day.isoformat(),
                    'last_outcome': 'FAILED',
                },
            )
            break

    finished_at = datetime.now(timezone.utc)
    completed = [item for item in runs if item.get('status') == 'COMPLETED']
    action_counts = {'BUY': 0, 'WATCH': 0, 'AVOID': 0}
    dq_counts: dict[str, int] = {}
    fallback_count = 0
    cost_values: list[float] = []
    latency_values: list[float] = []
    wall_values: list[float] = []
    score_values: list[float] = []
    conf_values: list[float] = []
    forward_values: list[float] = []
    horizon_values: list[float] = []
    horizon_source_counts: dict[str, int] = {}
    buy_signals = 0
    buy_hits = 0
    avoid_signals = 0
    avoid_hits = 0
    ta_hybrid_applied_runs = 0
    ta_directional_bias_values: list[float] = []
    ta_conviction_values: list[float] = []
    ta_disagreement_values: list[float] = []

    for item in completed:
        action = _safe_text(item.get('action')).upper()
        if action in action_counts:
            action_counts[action] += 1
        dq_status = _safe_text(item.get('data_quality_status')).upper() or 'UNKNOWN'
        dq_counts[dq_status] = dq_counts.get(dq_status, 0) + 1
        if bool(item.get('fallback')):
            fallback_count += 1
        cost_values.append(_safe_float(item.get('cost_usd_est')))
        latency_values.append(_safe_float(item.get('latency_ms')))
        wall_values.append(_safe_float(item.get('wall_time_ms')))
        score_values.append(_safe_float(item.get('overall_score')))
        conf_values.append(_safe_float(item.get('confidence')))
        horizon_values.append(
            float(
                _safe_int(
                    item.get('evaluation_horizon_days_used'),
                    default=default_horizon_days,
                    minimum=1,
                    maximum=120,
                )
            )
        )
        horizon_source = _safe_text(item.get('evaluation_horizon_source')) or 'UNKNOWN'
        horizon_source_counts[horizon_source] = horizon_source_counts.get(horizon_source, 0) + 1
        forward_return = item.get('forward_return_pct')
        if isinstance(forward_return, (int, float)):
            forward_values.append(float(forward_return))
            if action == 'BUY':
                buy_signals += 1
                if forward_return > 0:
                    buy_hits += 1
            elif action == 'AVOID':
                avoid_signals += 1
                if forward_return <= 0:
                    avoid_hits += 1
        if bool(item.get('ta_hybrid_applied', False)):
            ta_hybrid_applied_runs += 1
        ta_directional_bias_values.append(_safe_float(item.get('ta_directional_bias'), default=0.0))
        ta_conviction_values.append(_safe_float(item.get('ta_conviction'), default=0.0))
        ta_disagreement_values.append(_safe_float(item.get('ta_disagreement'), default=0.0))

    directional_total = buy_signals + avoid_signals
    directional_hits = buy_hits + avoid_hits
    (
        strategy_curve,
        benchmark_curve,
        strategy_final_capital,
        strategy_gross_final_capital,
        benchmark_final_capital,
        total_trade_cost_cny,
        total_turnover,
    ) = _build_equity_curve(
        completed_runs=completed,
        initial_capital_cny=initial_capital_cny,
        transaction_fee_bps=transaction_fee_bps,
        slippage_bps=slippage_bps,
        sell_tax_bps=sell_tax_bps,
    )
    strategy_total_return_pct = ((strategy_final_capital / initial_capital_cny) - 1.0) * 100.0
    strategy_gross_total_return_pct = ((strategy_gross_final_capital / initial_capital_cny) - 1.0) * 100.0
    benchmark_total_return_pct = ((benchmark_final_capital / initial_capital_cny) - 1.0) * 100.0
    excess_return_pct = strategy_total_return_pct - benchmark_total_return_pct
    resume_state_payload = None
    if interrupted and not cancelled:
        resume_state_payload = {
            'next_index': processed_points,
            'processed_points': processed_points,
            'completed_runs': completed_runs_count,
            'failed_runs': failed_runs_count,
            'skipped_non_trading_runs': skipped_non_trading_runs,
            'skipped_non_trading_dates': skipped_non_trading_dates,
            'runs': runs,
        }

    return {
        'batch_id': f'bt_{uuid.uuid4().hex[:12]}',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'request': {
            'ticker': ticker,
            'market': market,
            'strategy_version_id': strategy_version_id,
            'tier': tier,
            'analysis_mode': _safe_text(payload.get('analysis_mode')).upper() or 'BASELINE',
            'ta_hybrid_mode': _safe_text(payload.get('ta_hybrid_mode')).upper() or 'OFF',
            'ta_research_rounds': _safe_int(payload.get('ta_research_rounds'), default=1, minimum=1, maximum=3),
            'ta_risk_rounds': _safe_int(payload.get('ta_risk_rounds'), default=1, minimum=1, maximum=3),
            'ta_llm_call_cap': _safe_int(payload.get('ta_llm_call_cap'), default=6, minimum=0, maximum=20),
            'ta_require_evidence_refs': bool(payload.get('ta_require_evidence_refs', True)),
            'run_mode': 'BACKTEST',
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'step_days': step_days,
            'trading_days_only': trading_days_only,
            'asof_time': asof_time,
            'timezone_offset': timezone_offset,
            'max_runs': max_runs,
            'evaluation_horizon_days': default_horizon_days,
            'evaluation_horizon_days_default': default_horizon_days,
            'evaluation_horizon_mode': 'model_first',
            'generated_points': len(asof_days),
            'skill_pack': skill_pack_summary,
            'benchmark_ticker': benchmark_ticker,
            'initial_capital_cny': round(initial_capital_cny, 2),
            'main_chain_retry_attempts': retry_attempts,
            'main_chain_retry_backoff_ms': retry_backoff_ms,
            'resumed': bool(resume_next_index > 0),
            'transaction_cost_model': {
                'transaction_fee_bps': round(transaction_fee_bps, 6),
                'slippage_bps': round(slippage_bps, 6),
                'sell_tax_bps': round(sell_tax_bps, 6),
            },
        },
        'summary': {
            'total_runs': completed_runs_count + failed_runs_count,
            'completed_runs': completed_runs_count,
            'failed_runs': failed_runs_count,
            'cancelled': cancelled,
            'interrupted': interrupted,
            'interruption_reason': interruption_reason,
            'interrupted_at': interrupted_at,
            'resumable': bool(resume_state_payload),
            'processed_points': processed_points,
            'remaining_points': max(0, len(asof_days) - processed_points),
            'skipped_non_trading_runs': skipped_non_trading_runs,
            'skipped_non_trading_dates': skipped_non_trading_dates,
            'action_counts': action_counts,
            'data_quality_counts': dict(sorted(dq_counts.items())),
            'fallback_runs': fallback_count,
            'avg_score': _mean_or_zero(score_values),
            'avg_confidence': _mean_or_zero(conf_values),
            'avg_cost_usd_est': _mean_or_zero(cost_values),
            'avg_latency_ms': round(_mean_or_zero(latency_values), 3),
            'avg_wall_time_ms': round(_mean_or_zero(wall_values), 3),
            'avg_evaluation_horizon_days': round(_mean_or_zero(horizon_values), 3),
            'median_evaluation_horizon_days': round(statistics.median(horizon_values), 3) if horizon_values else float(default_horizon_days),
            'min_evaluation_horizon_days': int(min(horizon_values)) if horizon_values else default_horizon_days,
            'max_evaluation_horizon_days': int(max(horizon_values)) if horizon_values else default_horizon_days,
            'evaluation_horizon_source_counts': dict(sorted(horizon_source_counts.items())),
            'evaluated_forward_runs': len(forward_values),
            'avg_forward_return_pct': _mean_or_zero(forward_values),
            'median_forward_return_pct': round(statistics.median(forward_values), 6) if forward_values else 0.0,
            'buy_signal_count': buy_signals,
            'buy_hit_rate': round((buy_hits / buy_signals), 6) if buy_signals else 0.0,
            'avoid_signal_count': avoid_signals,
            'avoid_hit_rate': round((avoid_hits / avoid_signals), 6) if avoid_signals else 0.0,
            'directional_signal_count': directional_total,
            'directional_hit_rate': round((directional_hits / directional_total), 6) if directional_total else 0.0,
            'ta_hybrid_applied_runs': ta_hybrid_applied_runs,
            'ta_hybrid_applied_rate': round((ta_hybrid_applied_runs / len(completed)), 6) if completed else 0.0,
            'avg_ta_directional_bias': _mean_or_zero(ta_directional_bias_values),
            'avg_ta_conviction': _mean_or_zero(ta_conviction_values),
            'avg_ta_disagreement': _mean_or_zero(ta_disagreement_values),
            'initial_capital_cny': round(initial_capital_cny, 2),
            'strategy_final_capital_cny': round(strategy_final_capital, 2),
            'strategy_total_return_pct': round(strategy_total_return_pct, 6),
            'strategy_gross_final_capital_cny': round(strategy_gross_final_capital, 2),
            'strategy_gross_total_return_pct': round(strategy_gross_total_return_pct, 6),
            'total_trade_cost_cny': round(total_trade_cost_cny, 6),
            'avg_trade_cost_cny': round((total_trade_cost_cny / max(1, len(strategy_curve) - 1)), 6),
            'total_turnover': round(total_turnover, 6),
            'avg_turnover': round((total_turnover / max(1, len(strategy_curve) - 1)), 6),
            'benchmark_ticker': benchmark_ticker,
            'benchmark_final_capital_cny': round(benchmark_final_capital, 2),
            'benchmark_total_return_pct': round(benchmark_total_return_pct, 6),
            'excess_return_pct': round(excess_return_pct, 6),
        },
        'equity_curve': {
            'base_currency': 'CNY',
            'strategy': strategy_curve,
            'benchmark': benchmark_curve,
            'benchmark_ticker': benchmark_ticker,
            'transaction_cost_model': {
                'transaction_fee_bps': round(transaction_fee_bps, 6),
                'slippage_bps': round(slippage_bps, 6),
                'sell_tax_bps': round(sell_tax_bps, 6),
            },
        },
        'resume_state': resume_state_payload,
        'runs': runs,
    }
