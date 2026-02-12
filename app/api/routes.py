from datetime import date, datetime, timezone
import uuid

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.backtest import (
    cancel_backtest_job,
    evaluate_skill_pack_promotion,
    execute_skill_pack_promotion,
    get_backtest_job,
    list_skill_pack_versions,
    load_promotion_gate,
    resolve_champion_version,
    run_batch_backtest,
    submit_backtest_job,
)
from app.core.runtime_config import get_runtime_config, get_runtime_config_public, update_runtime_config
from app.tools.facts import get_index_market_snapshot, get_market_snapshot
from app.tools.graph import compute_exposure_score, find_impact_paths, query_supply_chain_subtree
from app.tools.memory import retrieve_memory_notes, write_memory_note
from app.workflows.graph import run_research_workflow
from app.workflows.persistence import (
    get_report as get_persisted_report,
    list_report_feedback,
    save_report_feedback,
)

router = APIRouter()

REPORT_STORE: dict[str, dict] = {}
WATCHLIST_STORE: dict[str, dict] = {}

GUI_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FiPro_1 图形界面</title>
  <style>
    :root {
      --bg-a: #0b1e2f;
      --bg-b: #142f45;
      --card: rgba(255, 255, 255, 0.92);
      --ink: #11212d;
      --muted: #476173;
      --accent: #0e7490;
      --accent-2: #155e75;
      --danger: #b91c1c;
      --ring: rgba(14, 116, 144, 0.25);
      --radius: 14px;
      --shadow: 0 12px 30px rgba(8, 28, 43, 0.25);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(80rem 30rem at 85% -10%, rgba(190, 242, 100, 0.14), transparent 58%),
        radial-gradient(70rem 40rem at -5% 95%, rgba(125, 211, 252, 0.18), transparent 60%),
        linear-gradient(140deg, var(--bg-a), var(--bg-b));
      padding: 40px 18px;
      display: grid;
      place-items: start center;
    }

    .shell {
      width: min(980px, 100%);
      background: var(--card);
      border-radius: calc(var(--radius) + 4px);
      box-shadow: var(--shadow);
      overflow: hidden;
      border: 1px solid rgba(17, 33, 45, 0.08);
    }

    .topbar {
      padding: 20px 24px;
      background: linear-gradient(120deg, #082f49, #0f766e);
      color: #ecfeff;
      display: flex;
      gap: 16px;
      align-items: baseline;
      justify-content: space-between;
      flex-wrap: wrap;
    }

    .title {
      margin: 0;
      font-size: 1.35rem;
      font-weight: 650;
      letter-spacing: 0.01em;
    }

    .subtitle {
      margin: 4px 0 0;
      color: #c7f9ff;
      font-size: 0.92rem;
    }

    .body {
      padding: 22px;
      display: grid;
      gap: 18px;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }

    .field {
      display: grid;
      gap: 6px;
    }

    label {
      font-size: 0.86rem;
      color: var(--muted);
      font-weight: 560;
      letter-spacing: 0.01em;
    }

    input, select, button {
      font: inherit;
    }

    input, select {
      width: 100%;
      border: 1px solid rgba(17, 33, 45, 0.16);
      border-radius: 10px;
      padding: 10px 12px;
      background: #fff;
      color: var(--ink);
      transition: border-color 0.18s, box-shadow 0.18s, transform 0.06s;
    }

    input:focus, select:focus {
      border-color: var(--accent);
      outline: none;
      box-shadow: 0 0 0 4px var(--ring);
    }

    .actions {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }

    button {
      border: 0;
      border-radius: 10px;
      padding: 11px 16px;
      font-weight: 620;
      letter-spacing: 0.01em;
      cursor: pointer;
      background: linear-gradient(120deg, var(--accent), var(--accent-2));
      color: #ecfeff;
      transition: transform 0.08s ease, filter 0.16s ease;
    }

    button:hover {
      filter: brightness(1.06);
    }

    button:active {
      transform: translateY(1px);
    }

    button:disabled {
      cursor: wait;
      filter: grayscale(0.25);
      opacity: 0.8;
    }

    .status {
      font-size: 0.9rem;
      color: var(--muted);
      min-height: 1.2rem;
    }

    .status.error {
      color: var(--danger);
    }

    .result {
      margin: 0;
      border-radius: 12px;
      border: 1px solid rgba(17, 33, 45, 0.14);
      background: #06141e;
      color: #d8f6ff;
      padding: 14px;
      font-family: "IBM Plex Mono", "SFMono-Regular", Menlo, monospace;
      font-size: 0.84rem;
      line-height: 1.45;
      white-space: pre-wrap;
      min-height: 220px;
      max-height: 55vh;
      overflow: auto;
    }

    @media (max-width: 760px) {
      .grid {
        grid-template-columns: 1fr;
      }
      .topbar {
        padding: 18px;
      }
      .body {
        padding: 16px;
      }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <h1 class="title">FiPro_1 最小界面</h1>
        <p class="subtitle">填写参数，生成报告，并查看 JSON 响应。</p>
      </div>
    </header>

    <section class="body">
      <form id="report-form" autocomplete="off">
        <div class="grid">
          <div class="field">
            <label for="ticker">股票代码</label>
            <input id="ticker" name="ticker" value="600519.SH" required>
          </div>

          <div class="field">
            <label for="market">市场</label>
            <select id="market" name="market">
              <option value="CN_A" selected>CN_A</option>
              <option value="US">US</option>
              <option value="HK">HK</option>
              <option value="OTHER">OTHER</option>
            </select>
          </div>

          <div class="field">
            <label for="asof">分析时间（本地）</label>
            <input id="asof" name="asof" type="datetime-local" required>
          </div>

          <div class="field">
            <label for="strategy_version_id">策略版本 ID</label>
            <input id="strategy_version_id" name="strategy_version_id" value="stg_v1" required>
          </div>

          <div class="field">
            <label for="tier">分层</label>
            <select id="tier" name="tier">
              <option value="TIER0" selected>TIER0</option>
              <option value="TIER1">TIER1</option>
              <option value="TIER2">TIER2</option>
            </select>
          </div>

          <div class="field">
            <label for="run_mode">运行模式</label>
            <select id="run_mode" name="run_mode">
              <option value="LIVE" selected>LIVE（实盘）</option>
              <option value="SHADOW">SHADOW（影子）</option>
              <option value="BACKTEST">BACKTEST（回测）</option>
            </select>
          </div>

          <div class="field" style="grid-column: 1 / -1;">
            <label for="thread_id">线程 ID（可选）</label>
            <input id="thread_id" name="thread_id" placeholder="自定义线程 ID">
          </div>
        </div>

        <div class="actions" style="margin-top: 16px;">
          <button id="submit-btn" type="submit">生成报告</button>
          <div id="status" class="status"></div>
        </div>
      </form>

      <pre id="result" class="result">{
  "hint": "提交表单后会调用 POST /reports/generate"
}</pre>
    </section>
  </main>

  <script>
    const form = document.getElementById("report-form");
    const submitBtn = document.getElementById("submit-btn");
    const statusEl = document.getElementById("status");
    const resultEl = document.getElementById("result");
    const asofInput = document.getElementById("asof");

    function setStatus(message, isError) {
      statusEl.textContent = message || "";
      statusEl.classList.toggle("error", Boolean(isError));
    }

    function seedAsofNow() {
      const now = new Date();
      const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
      asofInput.value = local.toISOString().slice(0, 16);
    }

    seedAsofNow();

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setStatus("请求发送中...", false);
      submitBtn.disabled = true;

      const formData = new FormData(form);
      const asofLocal = String(formData.get("asof") || "").trim();
      const asofDate = new Date(asofLocal);
      if (!asofLocal || Number.isNaN(asofDate.getTime())) {
        setStatus("分析时间格式无效。", true);
        submitBtn.disabled = false;
        return;
      }

      const payload = {
        ticker: String(formData.get("ticker") || "").trim(),
        market: String(formData.get("market") || "OTHER"),
        asof: asofDate.toISOString(),
        strategy_version_id: String(formData.get("strategy_version_id") || "").trim(),
        tier: String(formData.get("tier") || "TIER0"),
        run_mode: String(formData.get("run_mode") || "LIVE")
      };

      const threadId = String(formData.get("thread_id") || "").trim();
      const headers = { "Content-Type": "application/json" };
      if (threadId) {
        headers["x-thread-id"] = threadId;
      }

      try {
        const response = await fetch("/reports/generate", {
          method: "POST",
          headers,
          body: JSON.stringify(payload)
        });
        const raw = await response.text();
        let body = null;
        try {
          body = raw ? JSON.parse(raw) : null;
        } catch (_) {
          body = raw;
        }
        if (!response.ok) {
          const detail = typeof body?.detail === "string"
            ? body.detail
            : (typeof body === "string" && body.trim() ? body : `请求失败（${response.status}）`);
          throw new Error(detail);
        }
        if (body && typeof body === "object") {
          resultEl.textContent = JSON.stringify(body, null, 2);
        } else {
          resultEl.textContent = String(body || "");
        }
        setStatus("报告生成成功。", false);
      } catch (error) {
        const message = error instanceof Error ? error.message : "未知错误";
        setStatus(message, true);
      } finally {
        submitBtn.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


class GenerateReportRequest(BaseModel):
    ticker: str
    market: str = 'OTHER'
    asof: datetime
    strategy_version_id: str
    tier: str = Field(pattern='^(TIER0|TIER1|TIER2)$')
    run_mode: str | None = Field(default=None, pattern='^(LIVE|SHADOW|BACKTEST)$')


class BatchBacktestRequest(BaseModel):
    ticker: str
    market: str = Field(default='OTHER', pattern='^(CN_A|US|HK|CRYPTO|OTHER)$')
    strategy_version_id: str
    tier: str = Field(pattern='^(TIER0|TIER1|TIER2)$')
    skill_pack_id: str = Field(default='cn_a_core', min_length=1, max_length=64)
    skill_pack_version: str = Field(default='0.1.0', min_length=1, max_length=32)
    start_date: date
    end_date: date
    step_days: int = Field(default=1, ge=1, le=30)
    trading_days_only: bool = True
    asof_time: str = Field(default='09:30', pattern=r'^\d{2}:\d{2}(:\d{2})?$')
    timezone_offset: str = Field(default='+08:00', pattern=r'^[+-]\d{2}:\d{2}$')
    max_runs: int = Field(default=60, ge=1, le=500)
    evaluation_horizon_days: int = Field(default=5, ge=1, le=120)
    benchmark_ticker: str | None = None
    initial_capital_cny: float = Field(default=1000000, gt=0)
    thread_prefix: str | None = None


class SkillPackPromotionRunRequest(BaseModel):
    skill_pack_id: str = Field(default='cn_a_core', min_length=1, max_length=64)
    candidate_version: str = Field(min_length=1, max_length=32)
    champion_version: str | None = Field(default=None, min_length=1, max_length=32)
    execute: bool = False
    dry_run: bool = True
    manual_approved: bool = False
    backtest: BatchBacktestRequest


class RuntimeConfigUpdateRequest(BaseModel):
    default_run_mode: str | None = Field(default=None, pattern='^(LIVE|SHADOW|BACKTEST)$')
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_primary_model: str | None = None
    llm_reviewer_model: str | None = None
    llm_shadow_model: str | None = None
    llm_shadow_reviewer_model: str | None = None
    llm_api_key: str | None = None


class CreateStrategyRequest(BaseModel):
    name: str


class MemoryWriteRequest(BaseModel):
    ticker: str
    content: str
    tags: list[str] = Field(default_factory=list)


class ReportFeedbackRequest(BaseModel):
    feedback_label: str = Field(pattern='^(USEFUL|USELESS|FALSE_POSITIVE)$')
    comment: str = ''


@router.get('/health')
def health() -> dict:
    return {'status': 'ok'}


@router.get('/', include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url='/gui')


@router.get('/gui', response_class=HTMLResponse, include_in_schema=False)
def gui() -> HTMLResponse:
    return HTMLResponse(content=GUI_HTML)


@router.get('/version')
def version() -> dict:
    return {'version': '0.1.0'}


@router.get('/runtime/config')
def get_runtime_config_api() -> dict:
    return get_runtime_config_public()


@router.put('/runtime/config')
def update_runtime_config_api(payload: RuntimeConfigUpdateRequest) -> dict:
    return update_runtime_config(payload.model_dump(exclude_none=True))


@router.post('/reports/generate')
def generate_report(payload: GenerateReportRequest, x_thread_id: str | None = Header(default=None)) -> dict:
    request_data = payload.model_dump(mode='json', exclude_none=True)
    if 'run_mode' not in request_data:
        request_data['run_mode'] = get_runtime_config()['default_run_mode']
    thread_id = x_thread_id or f"thread_{payload.ticker}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    result = run_research_workflow(request_data=request_data, thread_id=thread_id)
    report_id = result['final_report']['report_id']
    REPORT_STORE[report_id] = result['final_report']

    return {'report_id': report_id, 'final_report': result['final_report']}


@router.post('/backtests/run')
def batch_backtest(payload: BatchBacktestRequest) -> dict:
    try:
        return run_batch_backtest(
            payload.model_dump(mode='json', exclude_none=True),
            runner=run_research_workflow,
            snapshot_loader=get_market_snapshot,
            benchmark_loader=get_index_market_snapshot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/backtests/jobs', status_code=202)
def create_backtest_job(payload: BatchBacktestRequest) -> dict:
    return submit_backtest_job(
        payload.model_dump(mode='json', exclude_none=True),
        runner=run_research_workflow,
        snapshot_loader=get_market_snapshot,
        benchmark_loader=get_index_market_snapshot,
    )


@router.get('/backtests/jobs/{job_id}')
def fetch_backtest_job(job_id: str) -> dict:
    job = get_backtest_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail='backtest job not found')
    return job


@router.post('/backtests/jobs/{job_id}/cancel')
def cancel_backtest_job_api(job_id: str) -> dict:
    job = cancel_backtest_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail='backtest job not found')
    return job


@router.get('/skill-packs/{skill_pack_id}/versions')
def get_skill_pack_versions(skill_pack_id: str) -> dict:
    versions = list_skill_pack_versions(skill_pack_id)
    return {
        'skill_pack_id': skill_pack_id,
        'champion_version': resolve_champion_version(skill_pack_id) or '',
        'items': versions,
    }


@router.post('/skill-packs/promotions/run')
def run_skill_pack_promotion(payload: SkillPackPromotionRunRequest) -> dict:
    skill_pack_id = payload.skill_pack_id.strip()
    candidate_version = payload.candidate_version.strip()
    champion_version = payload.champion_version.strip() if payload.champion_version else resolve_champion_version(skill_pack_id)
    if champion_version and champion_version == candidate_version:
        raise HTTPException(status_code=422, detail='candidate_version must be different from champion_version')

    base_backtest_payload = payload.backtest.model_dump(mode='json', exclude_none=True)
    candidate_backtest_payload = {
        **base_backtest_payload,
        'skill_pack_id': skill_pack_id,
        'skill_pack_version': candidate_version,
    }
    try:
        candidate_result = run_batch_backtest(
            candidate_backtest_payload,
            runner=run_research_workflow,
            snapshot_loader=get_market_snapshot,
            benchmark_loader=get_index_market_snapshot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f'candidate backtest failed: {exc}') from exc

    champion_result = None
    if champion_version:
        champion_backtest_payload = {
            **base_backtest_payload,
            'skill_pack_id': skill_pack_id,
            'skill_pack_version': champion_version,
        }
        try:
            champion_result = run_batch_backtest(
                champion_backtest_payload,
                runner=run_research_workflow,
                snapshot_loader=get_market_snapshot,
                benchmark_loader=get_index_market_snapshot,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f'champion backtest failed: {exc}') from exc

    try:
        gate_config = load_promotion_gate(skill_pack_id, candidate_version)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f'load promotion gate failed: {exc}') from exc

    evaluation = evaluate_skill_pack_promotion(
        candidate_backtest_result=candidate_result,
        champion_backtest_result=champion_result,
        gate_config=gate_config,
        candidate_version=candidate_version,
        champion_version=champion_version,
        manual_approved=payload.manual_approved,
    )

    execution = None
    if payload.execute:
        try:
            execution = execute_skill_pack_promotion(
                skill_pack_id=skill_pack_id,
                candidate_version=candidate_version,
                evaluation=evaluation,
                champion_version=champion_version,
                dry_run=payload.dry_run,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f'promotion execute failed: {exc}') from exc

    return {
        'skill_pack_id': skill_pack_id,
        'candidate_version': candidate_version,
        'champion_version': champion_version or '',
        'evaluation': evaluation,
        'execution': execution,
        'candidate_backtest': {
            'batch_id': candidate_result.get('batch_id', ''),
            'request': candidate_result.get('request', {}),
            'summary': candidate_result.get('summary', {}),
        },
        'champion_backtest': (
            {
                'batch_id': champion_result.get('batch_id', ''),
                'request': champion_result.get('request', {}),
                'summary': champion_result.get('summary', {}),
            }
            if isinstance(champion_result, dict)
            else None
        ),
    }


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


@router.post('/reports/{report_id}/feedback', status_code=201)
def submit_report_feedback(report_id: str, payload: ReportFeedbackRequest) -> dict:
    report = REPORT_STORE.get(report_id)
    if report is None:
        report = get_persisted_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail='report not found')
    saved = save_report_feedback(
        report_id=report_id,
        feedback_label=payload.feedback_label,
        comment=payload.comment,
    )
    return {'report_id': report_id, **saved}


@router.get('/reports/{report_id}/feedback')
def get_report_feedback(report_id: str, limit: int = 50) -> dict:
    rows = list_report_feedback(report_id=report_id, limit=max(1, min(500, int(limit))))
    return {'report_id': report_id, 'items': rows}


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
