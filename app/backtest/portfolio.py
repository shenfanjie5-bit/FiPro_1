from __future__ import annotations

from datetime import datetime
from typing import Any

from app.backtest.batch import Runner, SnapshotLoader, run_batch_backtest


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_text(value: Any) -> str:
    return str(value or '').strip()


def _normalize_portfolio_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    provided_total = 0.0
    missing_weight_indices: list[int] = []

    for row in items:
        if not isinstance(row, dict):
            continue
        ticker = _safe_text(row.get('ticker')).upper()
        if not ticker:
            continue
        weight_raw = row.get('weight')
        if weight_raw is None:
            weight = None
        else:
            parsed = _safe_float(weight_raw, default=-1.0)
            if parsed <= 0:
                raise ValueError(f'portfolio weight must be positive: {ticker}')
            weight = parsed
            provided_total += parsed
        normalized.append({'ticker': ticker, 'weight': weight})
        if weight is None:
            missing_weight_indices.append(len(normalized) - 1)

    if not normalized:
        raise ValueError('portfolio must contain at least one ticker')
    if len(normalized) > 50:
        raise ValueError('portfolio supports at most 50 tickers per run')

    if missing_weight_indices:
        remaining = max(0.0, 1.0 - provided_total)
        fallback_weight = (remaining / len(missing_weight_indices)) if remaining > 0 else (1.0 / len(normalized))
        for idx in missing_weight_indices:
            normalized[idx]['weight'] = fallback_weight

    total_weight = sum(_safe_float(item.get('weight'), 0.0) for item in normalized)
    if total_weight <= 0:
        raise ValueError('portfolio total weight must be positive')

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in normalized:
        ticker = _safe_text(item.get('ticker')).upper()
        if ticker in seen:
            raise ValueError(f'duplicate ticker in portfolio: {ticker}')
        seen.add(ticker)
        weight = _safe_float(item.get('weight'), 0.0) / total_weight
        deduped.append({'ticker': ticker, 'weight': round(weight, 10)})
    return deduped


def _collect_curve_points(result: dict[str, Any], key: str) -> dict[str, float]:
    curve = ((result.get('equity_curve') or {}).get(key) or []) if isinstance(result, dict) else []
    if not isinstance(curve, list):
        return {}
    points: dict[str, float] = {}
    for item in curve:
        if not isinstance(item, dict):
            continue
        asof = _safe_text(item.get('asof'))
        capital = _safe_float(item.get('capital_cny'), -1.0)
        if not asof or capital < 0:
            continue
        points[asof] = capital
    return points


def _sorted_asof_keys(component_results: list[dict[str, Any]], curve_key: str) -> list[str]:
    values: set[str] = set()
    for row in component_results:
        for key in _collect_curve_points(row, curve_key):
            values.add(key)
    if not values:
        return []
    return sorted(values, key=lambda text: datetime.fromisoformat(text.replace('Z', '+00:00')))


def _build_portfolio_curve(
    *,
    component_results: list[dict[str, Any]],
    curve_key: str,
    initial_capital_cny: float,
) -> list[dict[str, Any]]:
    asof_keys = _sorted_asof_keys(component_results, curve_key)
    if not asof_keys:
        return []

    by_component: list[dict[str, float]] = [_collect_curve_points(item, curve_key) for item in component_results]
    last_capitals: list[float] = []
    for item in component_results:
        summary = item.get('summary') if isinstance(item.get('summary'), dict) else {}
        component_initial = _safe_float(summary.get('initial_capital_cny'), 0.0)
        if component_initial <= 0:
            component_initial = initial_capital_cny / max(1, len(component_results))
        last_capitals.append(component_initial)

    curve: list[dict[str, Any]] = []
    prev_capital = initial_capital_cny
    for idx, asof in enumerate(asof_keys):
        total_capital = 0.0
        for comp_idx, points in enumerate(by_component):
            point_capital = points.get(asof)
            if point_capital is None:
                point_capital = last_capitals[comp_idx]
            else:
                last_capitals[comp_idx] = point_capital
            total_capital += point_capital
        nav = (total_capital / initial_capital_cny) if initial_capital_cny > 0 else 0.0
        step_return_pct = 0.0
        if idx > 0 and prev_capital > 0:
            step_return_pct = ((total_capital / prev_capital) - 1.0) * 100.0
        curve.append(
            {
                'asof': asof,
                'capital_cny': round(total_capital, 6),
                'nav': round(nav, 8),
                'step_return_pct': round(step_return_pct, 6),
            }
        )
        prev_capital = total_capital
    return curve


def run_portfolio_backtest(
    payload: dict[str, Any],
    *,
    runner: Runner,
    snapshot_loader: SnapshotLoader,
    benchmark_loader: SnapshotLoader | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError('payload must be object')

    raw_portfolio = payload.get('portfolio')
    if not isinstance(raw_portfolio, list):
        raise ValueError('portfolio must be list')
    items = _normalize_portfolio_items(raw_portfolio)

    total_initial_capital = _safe_float(payload.get('initial_capital_cny'), 1_000_000.0)
    if total_initial_capital <= 0:
        raise ValueError('initial_capital_cny must be positive')

    component_results: list[dict[str, Any]] = []
    for item in items:
        ticker = _safe_text(item.get('ticker')).upper()
        weight = _safe_float(item.get('weight'), 0.0)
        if weight <= 0:
            continue
        component_payload = dict(payload)
        component_payload['ticker'] = ticker
        component_payload['initial_capital_cny'] = round(total_initial_capital * weight, 8)
        if not _safe_text(component_payload.get('thread_prefix')):
            component_payload['thread_prefix'] = f'portfolio_{ticker.replace(".", "_")}'
        component_payload.pop('portfolio', None)
        try:
            result = run_batch_backtest(
                component_payload,
                runner=runner,
                snapshot_loader=snapshot_loader,
                benchmark_loader=benchmark_loader,
            )
        except ValueError as exc:
            raise ValueError(f'component backtest failed for {ticker}: {exc}') from exc
        component_results.append(result)

    if not component_results:
        raise ValueError('no valid component result in portfolio backtest')

    strategy_curve = _build_portfolio_curve(
        component_results=component_results,
        curve_key='strategy',
        initial_capital_cny=total_initial_capital,
    )
    benchmark_curve = _build_portfolio_curve(
        component_results=component_results,
        curve_key='benchmark',
        initial_capital_cny=total_initial_capital,
    )

    strategy_final = strategy_curve[-1]['capital_cny'] if strategy_curve else total_initial_capital
    benchmark_final = benchmark_curve[-1]['capital_cny'] if benchmark_curve else total_initial_capital
    strategy_return_pct = ((strategy_final / total_initial_capital) - 1.0) * 100.0
    benchmark_return_pct = ((benchmark_final / total_initial_capital) - 1.0) * 100.0

    action_counts: dict[str, int] = {}
    total_runs = 0
    completed_runs = 0
    failed_runs = 0
    skipped_non_trading_runs = 0
    total_trade_cost_cny = 0.0
    total_turnover = 0.0
    benchmark_ticker = ''
    component_items: list[dict[str, Any]] = []

    for item, result in zip(items, component_results):
        summary = result.get('summary') if isinstance(result.get('summary'), dict) else {}
        request = result.get('request') if isinstance(result.get('request'), dict) else {}
        component_action_counts = summary.get('action_counts') if isinstance(summary.get('action_counts'), dict) else {}
        for action, count in component_action_counts.items():
            action_key = _safe_text(action).upper()
            if not action_key:
                continue
            action_counts[action_key] = action_counts.get(action_key, 0) + _safe_int(count, 0)
        total_runs += _safe_int(summary.get('total_runs'), 0)
        completed_runs += _safe_int(summary.get('completed_runs'), 0)
        failed_runs += _safe_int(summary.get('failed_runs'), 0)
        skipped_non_trading_runs += _safe_int(summary.get('skipped_non_trading_runs'), 0)
        total_trade_cost_cny += _safe_float(summary.get('total_trade_cost_cny'), 0.0)
        total_turnover += _safe_float(summary.get('total_turnover'), 0.0)
        benchmark_ticker = benchmark_ticker or _safe_text(summary.get('benchmark_ticker')) or _safe_text(request.get('benchmark_ticker'))
        component_items.append(
            {
                'ticker': _safe_text(item.get('ticker')).upper(),
                'weight': _safe_float(item.get('weight'), 0.0),
                'batch_id': _safe_text(result.get('batch_id')),
                'summary': summary,
            }
        )

    return {
        'portfolio_id': f'portfolio_bt_{datetime.now().strftime("%Y%m%d%H%M%S")}',
        'request': {
            'market': _safe_text(payload.get('market')).upper() or 'OTHER',
            'strategy_version_id': _safe_text(payload.get('strategy_version_id')),
            'tier': _safe_text(payload.get('tier')).upper() or 'TIER0',
            'start_date': _safe_text(payload.get('start_date')),
            'end_date': _safe_text(payload.get('end_date')),
            'step_days': _safe_int(payload.get('step_days'), 1),
            'max_runs': _safe_int(payload.get('max_runs'), 60),
            'evaluation_horizon_days': _safe_int(payload.get('evaluation_horizon_days'), 5),
            'initial_capital_cny': round(total_initial_capital, 2),
            'portfolio': [{'ticker': _safe_text(item.get('ticker')).upper(), 'weight': _safe_float(item.get('weight'), 0.0)} for item in items],
        },
        'summary': {
            'component_count': len(component_items),
            'total_runs': total_runs,
            'completed_runs': completed_runs,
            'failed_runs': failed_runs,
            'skipped_non_trading_runs': skipped_non_trading_runs,
            'action_counts': action_counts,
            'initial_capital_cny': round(total_initial_capital, 2),
            'strategy_final_capital_cny': round(strategy_final, 2),
            'strategy_total_return_pct': round(strategy_return_pct, 6),
            'benchmark_ticker': benchmark_ticker,
            'benchmark_final_capital_cny': round(benchmark_final, 2),
            'benchmark_total_return_pct': round(benchmark_return_pct, 6),
            'excess_return_pct': round(strategy_return_pct - benchmark_return_pct, 6),
            'total_trade_cost_cny': round(total_trade_cost_cny, 6),
            'total_turnover': round(total_turnover, 6),
            'avg_turnover': round(total_turnover / max(1, len(strategy_curve) - 1), 6),
        },
        'equity_curve': {
            'base_currency': 'CNY',
            'strategy': strategy_curve,
            'benchmark': benchmark_curve,
            'benchmark_ticker': benchmark_ticker,
        },
        'components': component_items,
    }
