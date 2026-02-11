from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.tools.facts import get_market_snapshot
from app.tools.graph import compute_exposure_score, find_impact_paths, query_supply_chain_subtree
from app.tools.memory import retrieve_memory_notes, write_memory_note
from app.workflows.graph import run_research_workflow
from app.workflows.persistence import get_report as get_persisted_report

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


class CreateStrategyRequest(BaseModel):
    name: str


class MemoryWriteRequest(BaseModel):
    ticker: str
    content: str
    tags: list[str] = Field(default_factory=list)


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
    report = REPORT_STORE.get(report_id)
    if report is None:
        report = get_persisted_report(report_id)
        if report is not None:
            REPORT_STORE[report_id] = report
    if report is None:
        raise HTTPException(status_code=404, detail='report not found')
    return {'report_id': report_id, 'final_report': report}


@router.post('/strategies', status_code=201)
def create_strategy(payload: CreateStrategyRequest) -> dict:
    strategy_id = f'strategy_{uuid.uuid4().hex[:8]}'
    return {'strategy_id': strategy_id, 'name': payload.name, 'status': 'created'}


@router.post('/strategies/{id}/versions', status_code=201)
def create_strategy_version(id: str, payload: dict) -> dict:
    return {
        'strategy_id': id,
        'version_id': payload.get('version_id', f'strategy_{uuid.uuid4().hex[:8]}'),
        'status': 'created'
    }


@router.get('/strategies/{id}/versions/{version}')
def get_strategy_version(id: str, version: str) -> dict:
    # TODO: read from DB after persistence layer is wired.
    return {
        'strategy_id': id,
        'version_id': version,
        'weights_hash': 'w_mock_hash_v1',
        'risk_profile': 'LOW'
    }


@router.get('/tickers/{ticker}/snapshot')
def get_snapshot(ticker: str, asof: datetime, strategy_version: str) -> dict:
    snapshot = get_market_snapshot(ticker=ticker, asof=asof.isoformat())
    return {'ticker': ticker, 'strategy_version': strategy_version, 'snapshot': snapshot}


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
def graph_subtree(ticker: str, depth: int = 2, include_competitors: bool = True) -> dict:
    result = query_supply_chain_subtree(ticker=ticker, depth=depth, include_competitors=include_competitors)
    return {'ticker': ticker, 'depth': depth, **result}


@router.get('/graph/paths')
def graph_paths(entity: str, ticker: str, max_hops: int = 5) -> dict:
    result = find_impact_paths(entity=entity, ticker=ticker, max_hops=max_hops)
    exposure = compute_exposure_score(ticker=ticker, entity=entity)
    payload = {'entity': entity, 'ticker': ticker, **result}
    if not isinstance(exposure.get('error'), dict):
        payload['exposure'] = exposure
    return payload


@router.get('/memory/search')
def memory_search(ticker: str, q: str) -> dict:
    result = retrieve_memory_notes(ticker=ticker, query=q, top_k=8, time_range_days=180)
    return {'ticker': ticker, 'query': q, 'items': result.get('notes', [])}


@router.post('/memory/write', status_code=201)
def memory_write(payload: MemoryWriteRequest) -> dict:
    note = {
        'ticker': payload.ticker,
        'summary': payload.content,
        'tags': payload.tags,
    }
    result = write_memory_note(note)
    return {'ok': result.get('ok', False), 'note_id': result.get('note_id')}
