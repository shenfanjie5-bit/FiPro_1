from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import statistics
import time
import uuid
from typing import Any, Callable

from app.backtest.skill_pack import load_skill_pack


Runner = Callable[[dict[str, Any], str], dict[str, Any]]
SnapshotLoader = Callable[[str, str], dict[str, Any]]
ProgressCallback = Callable[[dict[str, Any]], None]
CancelChecker = Callable[[], bool]

DEFAULT_INITIAL_CAPITAL_CNY = 1_000_000.0


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


def _parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


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


def _build_equity_curve(
    *,
    completed_runs: list[dict[str, Any]],
    initial_capital_cny: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float, float]:
    if not completed_runs:
        return [], [], initial_capital_cny, initial_capital_cny

    ordered = sorted(completed_runs, key=lambda item: _parse_iso_datetime(str(item.get('asof', ''))))
    strategy_capital = float(initial_capital_cny)
    benchmark_capital = float(initial_capital_cny)
    strategy_curve: list[dict[str, Any]] = []
    benchmark_curve: list[dict[str, Any]] = []

    # First point starts at NAV=1
    first = ordered[0]
    strategy_curve.append(
        {
            'asof': str(first.get('asof', '')),
            'capital_cny': round(strategy_capital, 4),
            'nav': round(strategy_capital / initial_capital_cny, 8),
            'step_return_pct': 0.0,
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

        ticker_step_return = _calc_step_return_pct(
            _safe_float(prev.get('ticker_price'), default=0.0) or None,
            _safe_float(current.get('ticker_price'), default=0.0) or None,
        )
        benchmark_step_return = _calc_step_return_pct(
            _safe_float(prev.get('benchmark_price'), default=0.0) or None,
            _safe_float(current.get('benchmark_price'), default=0.0) or None,
        )

        applied_strategy_return = 0.0
        if prev_action == 'BUY' and ticker_step_return is not None:
            applied_strategy_return = ticker_step_return
        strategy_capital *= 1.0 + (applied_strategy_return / 100.0)

        applied_benchmark_return = 0.0
        if benchmark_step_return is not None:
            applied_benchmark_return = benchmark_step_return
            benchmark_capital *= 1.0 + (benchmark_step_return / 100.0)

        strategy_curve.append(
            {
                'asof': str(current.get('asof', '')),
                'capital_cny': round(strategy_capital, 4),
                'nav': round(strategy_capital / initial_capital_cny, 8),
                'step_return_pct': round(applied_strategy_return, 6),
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

    return strategy_curve, benchmark_curve, strategy_capital, benchmark_capital


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
    skill_pack_version = _safe_text(payload.get('skill_pack_version')) or '0.1.0'
    try:
        resolved_skill_pack = load_skill_pack(skill_pack_id=skill_pack_id, version=skill_pack_version)
    except ValueError as exc:
        raise ValueError(f'invalid skill pack configuration: {exc}') from exc
    skill_pack_summary = dict(resolved_skill_pack.get('summary', {}))
    benchmark_ticker = _resolve_benchmark_ticker(market, payload.get('benchmark_ticker'))
    benchmark_snapshot_loader = benchmark_loader or snapshot_loader
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
    horizon_days = _safe_int(payload.get('evaluation_horizon_days'), default=5, minimum=1, maximum=120)
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

    started_at = datetime.now(timezone.utc)
    runs: list[dict[str, Any]] = []
    skipped_non_trading_runs = 0
    skipped_non_trading_dates: list[str] = []
    processed_points = 0
    cancelled = False
    completed_runs_count = 0
    failed_runs_count = 0
    _emit_progress(
        progress_callback,
        {
            'status': 'RUNNING',
            'generated_points': len(asof_days),
            'processed_points': 0,
            'completed_runs': 0,
            'failed_runs': 0,
            'skipped_non_trading_runs': 0,
            'current_date': '',
            'last_outcome': '',
        },
    )
    for day in asof_days:
        if cancel_requested is not None and cancel_requested():
            cancelled = True
            break
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
        }
        wall_started = time.perf_counter()
        try:
            result = runner(request_data=request_data, thread_id=thread_id)
            report = result.get('final_report', {}) if isinstance(result, dict) else {}
            persist_refs = result.get('persist_refs', {}) if isinstance(result, dict) else {}
            decision = report.get('decision', {}) if isinstance(report.get('decision', {}), dict) else {}
            data_quality = report.get('data_quality', {}) if isinstance(report.get('data_quality', {}), dict) else {}
            provenance = report.get('provenance', {}) if isinstance(report.get('provenance', {}), dict) else {}
            tool_stats = provenance.get('tool_call_stats', {}) if isinstance(provenance.get('tool_call_stats', {}), dict) else {}
            wall_time_ms = int((time.perf_counter() - wall_started) * 1000)

            ticker_price = _extract_price(ticker_snapshot)
            benchmark_price = _extract_price(benchmark_snapshot)

            forward_return_pct, return_note = _calc_forward_return(
                ticker=ticker,
                asof=asof_dt,
                horizon_days=horizon_days,
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
                    'forward_return_pct': forward_return_pct,
                    'forward_return_note': return_note,
                    'skill_pack_id': skill_pack_summary.get('skill_pack_id', skill_pack_id),
                    'skill_pack_version': skill_pack_summary.get('version', skill_pack_version),
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
        except Exception as exc:  # noqa: BLE001
            wall_time_ms = int((time.perf_counter() - wall_started) * 1000)
            runs.append(
                {
                    'index': index,
                    'asof': asof_dt.isoformat(),
                    'thread_id': thread_id,
                    'status': 'FAILED',
                    'report_id': '',
                    'action': '',
                    'overall_score': 0,
                    'confidence': 0.0,
                    'data_quality_status': 'UNKNOWN',
                    'fallback': False,
                    'model_primary': '',
                    'tool_calls': 0,
                    'cost_usd_est': 0.0,
                    'latency_ms': 0,
                    'wall_time_ms': wall_time_ms,
                    'skill_note_id': '',
                    'ticker_price': None,
                    'benchmark_ticker': benchmark_ticker,
                    'benchmark_price': None,
                    'forward_return_pct': None,
                    'forward_return_note': '',
                    'skill_pack_id': skill_pack_summary.get('skill_pack_id', skill_pack_id),
                    'skill_pack_version': skill_pack_summary.get('version', skill_pack_version),
                    'error': str(exc),
                }
            )
            processed_points += 1
            failed_runs_count += 1
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
                    'last_outcome': 'FAILED',
                },
            )

    finished_at = datetime.now(timezone.utc)
    completed = [item for item in runs if item.get('status') == 'COMPLETED']
    failed = [item for item in runs if item.get('status') != 'COMPLETED']
    action_counts = {'BUY': 0, 'WATCH': 0, 'AVOID': 0}
    dq_counts: dict[str, int] = {}
    fallback_count = 0
    cost_values: list[float] = []
    latency_values: list[float] = []
    wall_values: list[float] = []
    score_values: list[float] = []
    conf_values: list[float] = []
    forward_values: list[float] = []
    buy_signals = 0
    buy_hits = 0
    avoid_signals = 0
    avoid_hits = 0

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

    directional_total = buy_signals + avoid_signals
    directional_hits = buy_hits + avoid_hits
    strategy_curve, benchmark_curve, strategy_final_capital, benchmark_final_capital = _build_equity_curve(
        completed_runs=completed,
        initial_capital_cny=initial_capital_cny,
    )
    strategy_total_return_pct = ((strategy_final_capital / initial_capital_cny) - 1.0) * 100.0
    benchmark_total_return_pct = ((benchmark_final_capital / initial_capital_cny) - 1.0) * 100.0
    excess_return_pct = strategy_total_return_pct - benchmark_total_return_pct

    return {
        'batch_id': f'bt_{uuid.uuid4().hex[:12]}',
        'started_at': started_at.isoformat(),
        'finished_at': finished_at.isoformat(),
        'request': {
            'ticker': ticker,
            'market': market,
            'strategy_version_id': strategy_version_id,
            'tier': tier,
            'run_mode': 'BACKTEST',
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'step_days': step_days,
            'trading_days_only': trading_days_only,
            'asof_time': asof_time,
            'timezone_offset': timezone_offset,
            'max_runs': max_runs,
            'evaluation_horizon_days': horizon_days,
            'generated_points': len(asof_days),
            'skill_pack': skill_pack_summary,
            'benchmark_ticker': benchmark_ticker,
            'initial_capital_cny': round(initial_capital_cny, 2),
        },
        'summary': {
            'total_runs': len(runs),
            'completed_runs': len(completed),
            'failed_runs': len(failed),
            'cancelled': cancelled,
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
            'evaluated_forward_runs': len(forward_values),
            'avg_forward_return_pct': _mean_or_zero(forward_values),
            'median_forward_return_pct': round(statistics.median(forward_values), 6) if forward_values else 0.0,
            'buy_signal_count': buy_signals,
            'buy_hit_rate': round((buy_hits / buy_signals), 6) if buy_signals else 0.0,
            'avoid_signal_count': avoid_signals,
            'avoid_hit_rate': round((avoid_hits / avoid_signals), 6) if avoid_signals else 0.0,
            'directional_signal_count': directional_total,
            'directional_hit_rate': round((directional_hits / directional_total), 6) if directional_total else 0.0,
            'initial_capital_cny': round(initial_capital_cny, 2),
            'strategy_final_capital_cny': round(strategy_final_capital, 2),
            'strategy_total_return_pct': round(strategy_total_return_pct, 6),
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
        },
        'runs': runs,
    }
