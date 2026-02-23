from __future__ import annotations

from typing import Any

import pytest

import app.backtest.portfolio as portfolio_module
from app.backtest.portfolio import run_portfolio_backtest


def _fake_component_result(
    *,
    ticker: str,
    initial_capital_cny: float,
    final_ratio: float,
    benchmark_ratio: float,
    ta_hybrid_applied_runs: int = 0,
    avg_ta_directional_bias: float = 0.0,
    avg_ta_conviction: float = 0.0,
    avg_ta_disagreement: float = 0.0,
) -> dict[str, Any]:
    final_capital = initial_capital_cny * final_ratio
    benchmark_final = initial_capital_cny * benchmark_ratio
    asof_start = '2026-02-10T09:30:00+08:00'
    asof_end = '2026-02-11T09:30:00+08:00'
    return {
        'batch_id': f'bt_{ticker}',
        'request': {'benchmark_ticker': '000300.SH'},
        'summary': {
            'total_runs': 2,
            'completed_runs': 2,
            'failed_runs': 0,
            'skipped_non_trading_runs': 0,
            'action_counts': {'BUY': 1, 'WATCH': 1},
            'initial_capital_cny': round(initial_capital_cny, 2),
            'strategy_final_capital_cny': round(final_capital, 2),
            'strategy_total_return_pct': round((final_ratio - 1.0) * 100.0, 6),
            'benchmark_ticker': '000300.SH',
            'benchmark_final_capital_cny': round(benchmark_final, 2),
            'benchmark_total_return_pct': round((benchmark_ratio - 1.0) * 100.0, 6),
            'excess_return_pct': round((final_ratio - benchmark_ratio) * 100.0, 6),
            'total_trade_cost_cny': 120.0,
            'total_turnover': 1.6,
            'ta_hybrid_applied_runs': ta_hybrid_applied_runs,
            'avg_ta_directional_bias': avg_ta_directional_bias,
            'avg_ta_conviction': avg_ta_conviction,
            'avg_ta_disagreement': avg_ta_disagreement,
        },
        'equity_curve': {
            'strategy': [
                {'asof': asof_start, 'capital_cny': round(initial_capital_cny, 6), 'nav': 1.0, 'step_return_pct': 0.0},
                {'asof': asof_end, 'capital_cny': round(final_capital, 6), 'nav': round(final_ratio, 8), 'step_return_pct': round((final_ratio - 1.0) * 100.0, 6)},
            ],
            'benchmark': [
                {'asof': asof_start, 'capital_cny': round(initial_capital_cny, 6), 'nav': 1.0, 'step_return_pct': 0.0},
                {'asof': asof_end, 'capital_cny': round(benchmark_final, 6), 'nav': round(benchmark_ratio, 8), 'step_return_pct': round((benchmark_ratio - 1.0) * 100.0, 6)},
            ],
        },
    }


def test_run_portfolio_backtest_aggregates_component_results(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_batch_backtest(payload: dict[str, Any], runner, snapshot_loader, benchmark_loader):  # noqa: ANN001
        ticker = str(payload.get('ticker', '')).upper()
        init_cap = float(payload.get('initial_capital_cny', 0))
        if ticker == '600519.SH':
            return _fake_component_result(
                ticker=ticker,
                initial_capital_cny=init_cap,
                final_ratio=1.1,
                benchmark_ratio=1.05,
                ta_hybrid_applied_runs=2,
                avg_ta_directional_bias=0.2,
                avg_ta_conviction=0.6,
                avg_ta_disagreement=0.1,
            )
        if ticker == '000001.SZ':
            return _fake_component_result(
                ticker=ticker,
                initial_capital_cny=init_cap,
                final_ratio=0.9,
                benchmark_ratio=1.0,
                ta_hybrid_applied_runs=1,
                avg_ta_directional_bias=-0.1,
                avg_ta_conviction=0.4,
                avg_ta_disagreement=0.3,
            )
        raise ValueError(f'unexpected ticker: {ticker}')

    monkeypatch.setattr(portfolio_module, 'run_batch_backtest', fake_run_batch_backtest)

    payload = {
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-10',
        'end_date': '2026-02-11',
        'step_days': 1,
        'trading_days_only': True,
        'asof_time': '09:30',
        'timezone_offset': '+08:00',
        'max_runs': 10,
        'evaluation_horizon_days': 1,
        'initial_capital_cny': 1_000_000,
        'portfolio': [
            {'ticker': '600519.SH', 'weight': 0.6},
            {'ticker': '000001.SZ', 'weight': 0.4},
        ],
    }

    result = run_portfolio_backtest(
        payload,
        runner=lambda request_data, thread_id: {'final_report': {}},  # noqa: ARG005
        snapshot_loader=lambda ticker, asof: {},  # noqa: ARG005
        benchmark_loader=lambda ticker, asof: {},  # noqa: ARG005
    )
    summary = result['summary']
    request = result['request']

    assert summary['component_count'] == 2
    assert summary['completed_runs'] == 4
    assert summary['failed_runs'] == 0
    assert summary['strategy_final_capital_cny'] == 1_020_000.0
    assert summary['strategy_total_return_pct'] == 2.0
    assert summary['benchmark_final_capital_cny'] == 1_030_000.0
    assert summary['benchmark_total_return_pct'] == 3.0
    assert summary['excess_return_pct'] == -1.0
    assert summary['total_trade_cost_cny'] == 240.0
    assert summary['benchmark_ticker'] == '000300.SH'
    assert summary['ta_hybrid_applied_runs'] == 3
    assert summary['avg_ta_directional_bias'] == 0.05
    assert summary['avg_ta_conviction'] == 0.5
    assert summary['avg_ta_disagreement'] == 0.2
    assert request['initial_capital_cny'] == 1_000_000.0
    assert len(request['portfolio']) == 2
    assert len(result['components']) == 2
    assert len(result['equity_curve']['strategy']) == 2
    assert len(result['equity_curve']['benchmark']) == 2


def test_run_portfolio_backtest_rejects_duplicate_tickers() -> None:
    payload = {
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-10',
        'end_date': '2026-02-11',
        'portfolio': [
            {'ticker': '600519.SH', 'weight': 0.5},
            {'ticker': '600519.SH', 'weight': 0.5},
        ],
    }
    with pytest.raises(ValueError, match='duplicate ticker'):
        run_portfolio_backtest(
            payload,
            runner=lambda request_data, thread_id: {'final_report': {}},  # noqa: ARG005
            snapshot_loader=lambda ticker, asof: {},  # noqa: ARG005
            benchmark_loader=lambda ticker, asof: {},  # noqa: ARG005
        )


def test_run_portfolio_backtest_requires_portfolio_list() -> None:
    payload = {
        'market': 'CN_A',
        'strategy_version_id': 'stg_v1',
        'tier': 'TIER0',
        'start_date': '2026-02-10',
        'end_date': '2026-02-11',
    }
    with pytest.raises(ValueError, match='portfolio must be list'):
        run_portfolio_backtest(
            payload,
            runner=lambda request_data, thread_id: {'final_report': {}},  # noqa: ARG005
            snapshot_loader=lambda ticker, asof: {},  # noqa: ARG005
            benchmark_loader=lambda ticker, asof: {},  # noqa: ARG005
        )
