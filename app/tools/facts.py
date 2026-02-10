from __future__ import annotations

from datetime import datetime, timezone
import uuid


# TODO: Replace mock snapshots with real source adapters (M3).
def get_market_snapshot(ticker: str, asof: str) -> dict:
    return {
        'snapshot_id': f'snap_mkt_{uuid.uuid4().hex[:10]}',
        'ticker': ticker,
        'asof': asof,
        'close': 100.0,
        'volatility_20d': 0.18,
        'volume_ratio': 1.1,
        'captured_at': datetime.now(timezone.utc).isoformat()
    }


# TODO: Wire fundamentals source.
def get_fundamentals_snapshot(ticker: str, asof: str) -> dict:
    return {
        'snapshot_id': f'snap_fund_{uuid.uuid4().hex[:10]}',
        'ticker': ticker,
        'asof': asof,
        'pe_ttm': 19.5,
        'roe': 0.16,
        'revenue_growth_yoy': 0.09,
        'captured_at': datetime.now(timezone.utc).isoformat()
    }


# TODO: Wire flow and sentiment source.
def get_flow_sentiment_snapshot(ticker: str, asof: str) -> dict:
    return {
        'snapshot_id': f'snap_flow_{uuid.uuid4().hex[:10]}',
        'ticker': ticker,
        'asof': asof,
        'northbound_flow': 1.2,
        'hotness_score': 65,
        'captured_at': datetime.now(timezone.utc).isoformat()
    }


# TODO: Wire macro/commodity/logistics source for tier>=1.
def get_macro_commodity_logistics_snapshot(ticker: str, asof: str) -> dict:
    return {
        'snapshot_id': f'snap_macro_{uuid.uuid4().hex[:10]}',
        'ticker': ticker,
        'asof': asof,
        'freight_index_change': 0.02,
        'commodity_basket_change': 0.01,
        'captured_at': datetime.now(timezone.utc).isoformat()
    }
