import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { generateReport, getRuntimeConfig } from '../api/client';
import { DataSourceStatusWidget } from '../components/DataSourceStatusWidget';
import type { GenerateReportPayload, RunMode, Tier } from '../types/report';

type GenerateRunMode = Exclude<RunMode, 'BACKTEST'>;

interface FormState {
  ticker: string;
  market: string;
  asofLocal: string;
  strategyVersionId: string;
  tier: Tier;
  runMode: GenerateRunMode;
  threadId: string;
}

function defaultAsofLocal(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function formToPayload(state: FormState): GenerateReportPayload {
  return {
    ticker: state.ticker.trim(),
    market: state.market.trim() || 'OTHER',
    asof: new Date(state.asofLocal).toISOString(),
    strategy_version_id: state.strategyVersionId.trim(),
    tier: state.tier,
    run_mode: state.runMode
  };
}

function normalizeGenerateRunMode(runMode: RunMode): GenerateRunMode {
  return runMode === 'SHADOW' ? 'SHADOW' : 'LIVE';
}

export function GeneratePage() {
  const navigate = useNavigate();
  const [form, setForm] = useState<FormState>({
    ticker: '600519.SH',
    market: 'CN_A',
    asofLocal: defaultAsofLocal(),
    strategyVersionId: 'stg_v1',
    tier: 'TIER0',
    runMode: 'LIVE',
    threadId: ''
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    async function loadDefaultRunMode() {
      try {
        const runtimeConfig = await getRuntimeConfig();
        if (!cancelled) {
          setForm((prev) => ({ ...prev, runMode: normalizeGenerateRunMode(runtimeConfig.default_run_mode) }));
        }
      } catch {
        // Keep local fallback when runtime config endpoint is temporarily unavailable.
      }
    }

    void loadDefaultRunMode();
    return () => {
      cancelled = true;
    };
  }, []);

  const canSubmit = useMemo(() => {
    return Boolean(form.ticker.trim() && form.strategyVersionId.trim() && form.asofLocal.trim());
  }, [form]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');

    if (!canSubmit) {
      setError('请填写完整的股票代码、分析时间和策略版本。');
      return;
    }

    const asofDate = new Date(form.asofLocal);
    if (Number.isNaN(asofDate.getTime())) {
      setError('分析时间格式无效。');
      return;
    }

    setLoading(true);
    try {
      const response = await generateReport(formToPayload(form), form.threadId);
      sessionStorage.setItem(`fipro1:report:${response.report_id}`, JSON.stringify(response));
      navigate(`/results/${response.report_id}`);
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : '提交失败（未知错误）';
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page-root">
      <div className="mesh" aria-hidden="true" />
      <section className="panel panel-intro">
        <p className="eyebrow">FiPro_1 图形界面</p>
        <h1>生成报告</h1>
        <p>调用后端 API 并提交完整参数，生成单次分析报告。</p>
        <div className="actions-row">
          <DataSourceStatusWidget />
        </div>
        <div className="actions-row">
          <Link className="ghost-link" to="/startup">
            启动配置
          </Link>
          {' · '}
          <Link className="ghost-link" to="/backtest">
            批量回测
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
              placeholder="600519.SH"
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
              <option value="HK">HK</option>
              <option value="US">US</option>
              <option value="OTHER">OTHER</option>
            </select>
          </label>

          <label>
            <span>分析时间（本地）</span>
            <input
              type="datetime-local"
              value={form.asofLocal}
              onChange={(event) => setForm((prev) => ({ ...prev, asofLocal: event.target.value }))}
              required
            />
          </label>

          <label>
            <span>策略版本</span>
            <input
              value={form.strategyVersionId}
              onChange={(event) => setForm((prev) => ({ ...prev, strategyVersionId: event.target.value }))}
              placeholder="stg_v1"
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
            <span>运行模式</span>
            <select
              value={form.runMode}
              onChange={(event) => setForm((prev) => ({ ...prev, runMode: event.target.value as GenerateRunMode }))}
            >
              <option value="LIVE">LIVE（实盘）</option>
              <option value="SHADOW">SHADOW（影子）</option>
            </select>
          </label>

          <label className="wide">
            <span>线程 ID（可选）</span>
            <input
              value={form.threadId}
              onChange={(event) => setForm((prev) => ({ ...prev, threadId: event.target.value }))}
              placeholder="例如：thread_manual_001"
            />
          </label>

          <div className="wide actions">
            <button type="submit" disabled={loading || !canSubmit}>
              {loading ? '生成中...' : '生成并打开结果'}
            </button>
            {error ? <p className="error-text">{error}</p> : null}
          </div>
        </form>
      </section>
    </main>
  );
}
