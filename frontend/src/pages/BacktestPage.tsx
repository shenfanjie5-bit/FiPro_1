import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { AdvancedSettings } from '../components/AdvancedSettings';
import { cancelBacktestJob, createBacktestJob, getBacktestJob, resumeBacktestJob } from '../api/client';
import { DataSourceStatusWidget } from '../components/DataSourceStatusWidget';
import type { BatchBacktestPayload, BatchBacktestResponse, BacktestJobResponse, EquityCurvePoint, Tier } from '../types/report';

interface BacktestFormState {
  ticker: string;
  market: string;
  strategyVersionId: string;
  tier: Tier;
  startDate: string;
  endDate: string;
  stepDays: number;
  tradingDaysOnly: boolean;
  asofTime: string;
  timezoneOffset: string;
  maxRuns: number;
  evaluationHorizonDays: number;
  threadPrefix: string;
}

const LAST_BACKTEST_JOB_ID_KEY = 'fipro1:backtest:last_job_id';

function isoDate(value: Date): string {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function defaultRange(): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(end.getDate() - 30);
  return { start: isoDate(start), end: isoDate(end) };
}

function toPayload(form: BacktestFormState): BatchBacktestPayload {
  const payload: BatchBacktestPayload = {
    ticker: form.ticker.trim(),
    market: form.market.trim() || 'OTHER',
    strategy_version_id: form.strategyVersionId.trim(),
    tier: form.tier,
    start_date: form.startDate,
    end_date: form.endDate,
    step_days: Number(form.stepDays),
    trading_days_only: Boolean(form.tradingDaysOnly),
    asof_time: form.asofTime.trim(),
    timezone_offset: form.timezoneOffset.trim(),
    max_runs: Number(form.maxRuns),
    initial_capital_cny: 1_000_000
  };
  const defaultHorizonDays = Number(form.evaluationHorizonDays);
  if (Number.isFinite(defaultHorizonDays) && defaultHorizonDays >= 1) {
    payload.evaluation_horizon_days = Math.max(1, Math.min(120, Math.trunc(defaultHorizonDays)));
  }
  const prefix = form.threadPrefix.trim();
  if (prefix) {
    payload.thread_prefix = prefix;
  }
  return payload;
}

function percent(value: number): string {
  return `${value.toFixed(2)}%`;
}

function ratioPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

function cny(value: number): string {
  return `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function statusLabel(value: string): string {
  const normalized = value.trim().toUpperCase();
  if (normalized === 'COMPLETED') {
    return '已完成';
  }
  if (normalized === 'FAILED') {
    return '失败';
  }
  return value || '-';
}

function actionLabel(value: string): string {
  const normalized = value.trim().toUpperCase();
  if (normalized === 'BUY') {
    return '买入';
  }
  if (normalized === 'WATCH') {
    return '观察';
  }
  if (normalized === 'AVOID') {
    return '回避';
  }
  return value || '-';
}

function jobStatusLabel(value: string): string {
  const normalized = value.trim().toUpperCase();
  if (normalized === 'PENDING') {
    return '排队中';
  }
  if (normalized === 'RUNNING') {
    return '运行中';
  }
  if (normalized === 'CANCELLING') {
    return '取消中';
  }
  if (normalized === 'COMPLETED') {
    return '已完成';
  }
  if (normalized === 'CANCELLED') {
    return '已取消';
  }
  if (normalized === 'FAILED') {
    return '失败';
  }
  return value || '-';
}

function isJobTerminal(status: string): boolean {
  const normalized = status.trim().toUpperCase();
  return normalized === 'COMPLETED' || normalized === 'FAILED' || normalized === 'CANCELLED';
}

function isJobActive(status: string): boolean {
  return !isJobTerminal(status);
}

function isJobResumable(job: BacktestJobResponse | null): boolean {
  if (!job) {
    return false;
  }
  if (job.resumable) {
    return true;
  }
  const summary = job.result?.summary;
  return Boolean(job.status === 'FAILED' && summary?.interrupted && summary?.resumable);
}

function toPolyline(points: EquityCurvePoint[], width: number, height: number, padding: number, minValue: number, maxValue: number): string {
  if (points.length === 0) {
    return '';
  }
  const denom = Math.max(1, points.length - 1);
  const innerW = width - padding * 2;
  const innerH = height - padding * 2;
  const valueRange = Math.max(1e-9, maxValue - minValue);
  return points
    .map((point, index) => {
      const x = padding + (innerW * index) / denom;
      const y = padding + innerH - ((point.capital_cny - minValue) / valueRange) * innerH;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');
}

function EquityLineChart({
  strategy,
  benchmark,
  benchmarkTicker
}: {
  strategy: EquityCurvePoint[];
  benchmark: EquityCurvePoint[];
  benchmarkTicker: string;
}) {
  if (!strategy.length && !benchmark.length) {
    return null;
  }
  const width = 960;
  const height = 320;
  const padding = 34;
  const values = [...strategy.map((item) => item.capital_cny), ...benchmark.map((item) => item.capital_cny)];
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const strategyPoints = toPolyline(strategy, width, height, padding, minValue, maxValue);
  const benchmarkPoints = toPolyline(benchmark, width, height, padding, minValue, maxValue);

  const firstLabel = strategy[0]?.asof || benchmark[0]?.asof || '';
  const lastLabel = strategy[strategy.length - 1]?.asof || benchmark[benchmark.length - 1]?.asof || '';

  return (
    <div className="chart-wrap">
      <svg className="equity-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="策略与基准资金曲线">
        <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} stroke="#9fb6ca" strokeWidth="1.2" />
        <line x1={padding} y1={padding} x2={padding} y2={height - padding} stroke="#9fb6ca" strokeWidth="1.2" />
        {benchmarkPoints ? (
          <polyline fill="none" stroke="#f97316" strokeWidth="2.8" points={benchmarkPoints} />
        ) : null}
        {strategyPoints ? (
          <polyline fill="none" stroke="#0ea5a4" strokeWidth="2.8" points={strategyPoints} />
        ) : null}
      </svg>
      <div className="chart-legend">
        <span className="legend-item">
          <span className="legend-dot legend-dot-strategy" />
          策略净值
        </span>
        <span className="legend-item">
          <span className="legend-dot legend-dot-benchmark" />
          基准：{benchmarkTicker}
        </span>
        <span className="legend-range">{firstLabel} → {lastLabel}</span>
      </div>
    </div>
  );
}

export function BacktestPage() {
  const range = defaultRange();
  const [form, setForm] = useState<BacktestFormState>({
    ticker: '600519.SH',
    market: 'CN_A',
    strategyVersionId: 'stg_v1',
    tier: 'TIER0',
    startDate: range.start,
    endDate: range.end,
    stepDays: 1,
    tradingDaysOnly: true,
    asofTime: '09:30',
    timezoneOffset: '+08:00',
    maxRuns: 60,
    evaluationHorizonDays: 5,
    threadPrefix: ''
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<BatchBacktestResponse | null>(null);
  const [job, setJob] = useState<BacktestJobResponse | null>(null);

  const canSubmit = useMemo(() => {
    return Boolean(
      form.ticker.trim() &&
      form.strategyVersionId.trim() &&
      form.startDate.trim() &&
      form.endDate.trim() &&
      form.asofTime.trim() &&
      form.timezoneOffset.trim()
    );
  }, [form]);

  const syncJob = useCallback(async (jobId: string) => {
    const latest = await getBacktestJob(jobId);
    setJob(latest);
    if (latest.result) {
      setResult(latest.result);
    }
    if (latest.status === 'FAILED' && latest.error) {
      setError(latest.error);
    }
  }, []);

  useEffect(() => {
    const savedJobId = localStorage.getItem(LAST_BACKTEST_JOB_ID_KEY) || '';
    if (!savedJobId.trim()) {
      return;
    }
    void syncJob(savedJobId.trim()).catch(() => {
      // Ignore restore failures and allow user to start a new task.
    });
  }, [syncJob]);

  useEffect(() => {
    if (!job || isJobTerminal(job.status)) {
      return;
    }
    const timer = window.setInterval(() => {
      void syncJob(job.job_id).catch(() => {
        // Keep polling on transient network issues.
      });
    }, 1500);
    return () => {
      window.clearInterval(timer);
    };
  }, [job, syncJob]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit || submitting || (job && isJobActive(job.status))) {
      return;
    }
    setSubmitting(true);
    setError('');
    setResult(null);
    try {
      const created = await createBacktestJob(toPayload(form));
      setJob(created);
      localStorage.setItem(LAST_BACKTEST_JOB_ID_KEY, created.job_id);
      await syncJob(created.job_id);
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : '提交失败（未知错误）';
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleCancelJob() {
    if (!job || isJobTerminal(job.status)) {
      return;
    }
    try {
      const updated = await cancelBacktestJob(job.job_id);
      setJob(updated);
    } catch (cancelError) {
      const message = cancelError instanceof Error ? cancelError.message : '取消失败（未知错误）';
      setError(message);
    }
  }

  async function handleResumeJob() {
    if (!job || !isJobResumable(job) || submitting) {
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const resumed = await resumeBacktestJob(job.job_id);
      setJob(resumed);
      localStorage.setItem(LAST_BACKTEST_JOB_ID_KEY, resumed.job_id);
      await syncJob(resumed.job_id);
    } catch (resumeError) {
      const message = resumeError instanceof Error ? resumeError.message : '续跑失败（未知错误）';
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  const summary = result?.summary;
  const runs = result?.runs ?? [];
  const activeJobRunning = Boolean(job && isJobActive(job.status));
  const generatedPoints = Number(job?.progress?.generated_points ?? 0);
  const processedPoints = Number(job?.progress?.processed_points ?? 0);
  const progressPercent = generatedPoints > 0 ? Math.min(100, (processedPoints / generatedPoints) * 100) : 0;

  return (
    <main className="page-root">
      <div className="mesh" aria-hidden="true" />
      <section className="panel panel-intro">
        <p className="eyebrow">FiPro_1 图形界面</p>
        <h1>批量回测</h1>
        <p>在时间区间内按多个采样点执行回测，并查看汇总指标和每次运行明细。</p>
        <div className="actions-row">
          <DataSourceStatusWidget />
        </div>
        <div className="actions-row">
          <Link className="ghost-link" to="/generate">
            单次生成
          </Link>
          {' · '}
          <Link className="ghost-link" to="/startup">
            启动配置
          </Link>
          {' · '}
          <Link className="ghost-link" to="/proposals">
            提案评审
          </Link>
          {' · '}
          <Link className="ghost-link" to="/champion-health">
            Champion 监控
          </Link>
        </div>
      </section>

      <section className="panel panel-form">
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            <span>股票代码</span>
            <input
              value={form.ticker}
              onChange={(event) => setForm((prev) => ({ ...prev, ticker: event.target.value }))}
              required
            />
          </label>
          <label>
            <span>市场</span>
            <select
              value={form.market}
              onChange={(event) => setForm((prev) => ({ ...prev, market: event.target.value }))}
            >
              <option value="CN_A">CN_A</option>
              <option value="US">US</option>
              <option value="HK">HK</option>
              <option value="CRYPTO">CRYPTO</option>
              <option value="OTHER">OTHER</option>
            </select>
          </label>
          <label>
            <span>策略版本</span>
            <input
              value={form.strategyVersionId}
              onChange={(event) => setForm((prev) => ({ ...prev, strategyVersionId: event.target.value }))}
              required
            />
          </label>
          <label>
            <span>分层</span>
            <select
              value={form.tier}
              onChange={(event) => setForm((prev) => ({ ...prev, tier: event.target.value as Tier }))}
            >
              <option value="TIER0">TIER0</option>
              <option value="TIER1">TIER1</option>
              <option value="TIER2">TIER2</option>
            </select>
          </label>
          <label>
            <span>开始日期</span>
            <input
              type="date"
              value={form.startDate}
              onChange={(event) => setForm((prev) => ({ ...prev, startDate: event.target.value }))}
              required
            />
          </label>
          <label>
            <span>结束日期</span>
            <input
              type="date"
              value={form.endDate}
              onChange={(event) => setForm((prev) => ({ ...prev, endDate: event.target.value }))}
              required
            />
          </label>
          <label>
            <span>步长天数</span>
            <input
              type="number"
              min={1}
              max={30}
              value={form.stepDays}
              onChange={(event) => setForm((prev) => ({ ...prev, stepDays: Number(event.target.value) }))}
              required
            />
          </label>
          <label>
            <span>最大运行次数</span>
            <input
              type="number"
              min={1}
              max={500}
              value={form.maxRuns}
              onChange={(event) => setForm((prev) => ({ ...prev, maxRuns: Number(event.target.value) }))}
              required
            />
          </label>
          <label>
            <span>时间点</span>
            <input
              value={form.asofTime}
              onChange={(event) => setForm((prev) => ({ ...prev, asofTime: event.target.value }))}
              placeholder="09:30"
              required
            />
          </label>
          <label>
            <span>时区偏移</span>
            <input
              value={form.timezoneOffset}
              onChange={(event) => setForm((prev) => ({ ...prev, timezoneOffset: event.target.value }))}
              placeholder="+08:00"
              required
            />
          </label>
          <AdvancedSettings description="后续可在这里扩展更多高级参数（当前包含默认评估窗口与线程前缀）。">
            <label>
              <span>默认评估窗口天数（模型未指定时）</span>
              <input
                type="number"
                min={1}
                max={120}
                value={form.evaluationHorizonDays}
                onChange={(event) => setForm((prev) => ({ ...prev, evaluationHorizonDays: Number(event.target.value) }))}
              />
            </label>
            <label>
              <span>线程前缀（可选）</span>
              <input
                value={form.threadPrefix}
                onChange={(event) => setForm((prev) => ({ ...prev, threadPrefix: event.target.value }))}
                placeholder="例如：backtest_custom"
              />
            </label>
          </AdvancedSettings>
          <label>
            <span>初始资金</span>
            <input value="¥1,000,000.00（固定）" readOnly />
          </label>
          <label className="wide checkbox-row">
            <input
              type="checkbox"
              checked={form.tradingDaysOnly}
              onChange={(event) => setForm((prev) => ({ ...prev, tradingDaysOnly: event.target.checked }))}
            />
            <span>仅交易日（跳过周末）</span>
          </label>
          <div className="wide actions">
            <button type="submit" disabled={!canSubmit || submitting || activeJobRunning}>
              {submitting ? '提交中...' : activeJobRunning ? '任务运行中...' : '启动回测任务'}
            </button>
            {error ? <p className="error-text">{error}</p> : null}
          </div>
        </form>
      </section>

      {job ? (
        <section className="panel">
          <h2 className="table-title">任务进度</h2>
          <p>
            任务 ID：<code>{job.job_id}</code>
          </p>
          <p>
            状态：{jobStatusLabel(job.status)}
            {job.cancel_requested ? '（已请求取消）' : ''}
          </p>
          <p>
            进度：{processedPoints}/{generatedPoints}（{progressPercent.toFixed(1)}%）
          </p>
          <p>
            已完成 {job.progress.completed_runs}，失败 {job.progress.failed_runs}，跳过非交易日 {job.progress.skipped_non_trading_runs}
          </p>
          {job.progress.current_date ? <p>当前日期：{job.progress.current_date}</p> : null}
          {job.error ? <p className="error-text">{job.error}</p> : null}
          <div className="actions-inline">
            <button type="button" onClick={() => void syncJob(job.job_id)} disabled={submitting}>
              刷新进度
            </button>
            <button type="button" onClick={() => void handleResumeJob()} disabled={!isJobResumable(job) || submitting}>
              从断点继续
            </button>
            <button type="button" onClick={() => void handleCancelJob()} disabled={!isJobActive(job.status)}>
              {job.status === 'CANCELLING' ? '取消中...' : '取消任务'}
            </button>
          </div>
        </section>
      ) : null}

      {summary ? (
        <section className="panel panel-metrics">
          <article>
            <h2>总运行次数</h2>
            <p>{summary.total_runs}</p>
          </article>
          <article>
            <h2>已完成</h2>
            <p>{summary.completed_runs}</p>
          </article>
          <article>
            <h2>失败</h2>
            <p>{summary.failed_runs}</p>
          </article>
          <article>
            <h2>跳过(非交易日)</h2>
            <p>{summary.skipped_non_trading_runs}</p>
          </article>
          <article>
            <h2>已取消</h2>
            <p>{summary.cancelled ? '是' : '否'}</p>
          </article>
          <article>
            <h2>中断(可恢复)</h2>
            <p>{summary.interrupted ? '是' : '否'}</p>
          </article>
          <article>
            <h2>回退次数</h2>
            <p>{summary.fallback_runs}</p>
          </article>
          <article>
            <h2>平均分</h2>
            <p>{summary.avg_score}</p>
          </article>
          <article>
            <h2>平均置信度</h2>
            <p>{summary.avg_confidence}</p>
          </article>
          <article>
            <h2>平均评估窗</h2>
            <p>
              {typeof summary.avg_evaluation_horizon_days === 'number'
                ? `${summary.avg_evaluation_horizon_days.toFixed(2)} 天`
                : '-'}
            </p>
          </article>
          <article>
            <h2>方向命中率</h2>
            <p>{ratioPercent(summary.directional_hit_rate)}</p>
          </article>
          <article>
            <h2>平均前瞻收益</h2>
            <p>{percent(summary.avg_forward_return_pct)}</p>
          </article>
          <article>
            <h2>策略收益率</h2>
            <p>{percent(summary.strategy_total_return_pct)}</p>
          </article>
          <article>
            <h2>基准收益率</h2>
            <p>{summary.benchmark_ticker} {percent(summary.benchmark_total_return_pct)}</p>
          </article>
          <article>
            <h2>超额收益率</h2>
            <p>{percent(summary.excess_return_pct)}</p>
          </article>
          <article>
            <h2>策略期末资金</h2>
            <p>{cny(summary.strategy_final_capital_cny)}</p>
          </article>
          <article>
            <h2>基准期末资金</h2>
            <p>{cny(summary.benchmark_final_capital_cny)}</p>
          </article>
        </section>
      ) : null}

      {summary?.interrupted ? (
        <section className="panel warning-panel">
          <h2 className="table-title">回测中断</h2>
          <p>{summary.interruption_reason || '主链路重试失败，已中断本次回测。'}</p>
          <p className="helper-text">主链路恢复后可点击“从断点继续”。</p>
        </section>
      ) : null}

      {summary && summary.skipped_non_trading_dates.length > 0 ? (
        <section className="panel">
          <h2 className="table-title">跳过的非交易日</h2>
          <p>{summary.skipped_non_trading_dates.join('、')}</p>
        </section>
      ) : null}

      {result?.equity_curve ? (
        <section className="panel">
          <h2 className="table-title">资金曲线（策略对比基准）</h2>
          <EquityLineChart
            strategy={result.equity_curve.strategy}
            benchmark={result.equity_curve.benchmark}
            benchmarkTicker={result.equity_curve.benchmark_ticker}
          />
        </section>
      ) : null}

      {runs.length > 0 ? (
        <section className="panel">
          <h2 className="table-title">运行明细</h2>
          <div className="table-wrap">
            <table className="run-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>时间点</th>
                  <th>状态</th>
                  <th>动作</th>
                  <th>评分</th>
                  <th>置信度</th>
                  <th>数据质量</th>
                  <th>评估窗(天)</th>
                  <th>前瞻收益%</th>
                  <th>标的价格</th>
                  <th>基准价格</th>
                  <th>报告 ID</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((item) => (
                  <tr key={`${item.index}-${item.thread_id}`}>
                    <td>{item.index}</td>
                    <td>{item.asof}</td>
                    <td>{statusLabel(item.status)}</td>
                    <td>{actionLabel(item.action || '-')}</td>
                    <td>{item.overall_score}</td>
                    <td>{item.confidence}</td>
                    <td>{item.data_quality_status}</td>
                    <td>{item.evaluation_horizon_days_used ?? '-'}</td>
                    <td>{item.forward_return_pct ?? '-'}</td>
                    <td>{item.ticker_price ?? '-'}</td>
                    <td>{item.benchmark_price ?? '-'}</td>
                    <td>{item.report_id || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <section className="panel panel-json">
        <h2>原始 JSON</h2>
        <pre>{JSON.stringify(result ?? job ?? { hint: '启动回测任务后可查看完整响应。' }, null, 2)}</pre>
      </section>
    </main>
  );
}
