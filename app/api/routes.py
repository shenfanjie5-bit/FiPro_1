from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.workflows.graph import run_research_workflow

router = APIRouter()

REPORT_STORE: dict[str, dict] = {}
WATCHLIST_STORE: dict[str, dict] = {}


class GenerateReportRequest(BaseModel):
    ticker: str
    market: str = 'OTHER'
    asof: datetime
    strategy_version_id: str
    tier: str = Field(pattern='^(TIER0|TIER1|TIER2)$')
    run_mode: str = Field(default='LIVE', pattern='^(LIVE|SHADOW|BACKTEST)$')


@router.get('/health')
def health() -> dict:
    return {'status': 'ok'}


@router.get('/version')
def version() -> dict:
    return {'version': '0.1.0'}


@router.post('/reports/generate')
def generate_report(payload: GenerateReportRequest, x_thread_id: str | None = Header(default=None)) -> dict:
    request_data = payload.model_dump(mode='json')
    thread_id = x_thread_id or f"thread_{payload.ticker}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    result = run_research_workflow(request_data=request_data, thread_id=thread_id)
    report_id = result['final_report']['report_id']
    REPORT_STORE[report_id] = result['final_report']

    return {'report_id': report_id, 'final_report': result['final_report']}


@router.get('/reports/{report_id}')
def get_report(report_id: str) -> dict:
    if report_id not in REPORT_STORE:
        raise HTTPException(status_code=404, detail='report not found')
    return {'report_id': report_id, 'final_report': REPORT_STORE[report_id]}


@router.post('/strategies/{strategy_id}/versions')
def create_strategy_version(strategy_id: str, payload: dict) -> dict:
    return {
        'strategy_id': strategy_id,
        'version_id': payload.get('version_id', f'strategy_{uuid.uuid4().hex[:8]}'),
        'status': 'created'
    }


@router.get('/strategies/{strategy_id}/versions/{version_id}')
def get_strategy_version(strategy_id: str, version_id: str) -> dict:
    # TODO: read from DB after persistence layer is wired.
    return {
        'strategy_id': strategy_id,
        'version_id': version_id,
        'weights_hash': 'w_mock_hash_v1',
        'risk_profile': 'LOW'
    }


@router.post('/watchlist')
def upsert_watchlist(payload: dict) -> dict:
    ticker = payload['ticker']
    WATCHLIST_STORE[ticker] = {
        'ticker': ticker,
        'tier': payload.get('tier', 'TIER0'),
        'status': payload.get('status', 'ACTIVE'),
        'updated_at': datetime.now(timezone.utc).isoformat()
    }
    return WATCHLIST_STORE[ticker]


@router.get('/watchlist')
def get_watchlist() -> dict:
    return {'items': list(WATCHLIST_STORE.values())}


@router.get('/graph/subtree')
def graph_subtree(ticker: str, depth: int = 2) -> dict:
    return {'ticker': ticker, 'depth': depth, 'graph_id': f'graph_{ticker}_{depth}', 'nodes': [], 'edges': []}


@router.get('/memory/search')
def memory_search(ticker: str, q: str) -> dict:
    return {'ticker': ticker, 'query': q, 'items': []}
