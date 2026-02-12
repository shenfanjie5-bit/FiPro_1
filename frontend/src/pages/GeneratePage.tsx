import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { generateReport, getRuntimeConfig } from '../api/client';
import type { GenerateReportPayload, RunMode, Tier } from '../types/report';

interface FormState {
  ticker: string;
  market: string;
  asofLocal: string;
  strategyVersionId: string;
  tier: Tier;
  runMode: RunMode;
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
          setForm((prev) => ({ ...prev, runMode: runtimeConfig.default_run_mode }));
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
      setError('Please complete ticker, as-of time, and strategy version.');
      return;
    }

    const asofDate = new Date(form.asofLocal);
    if (Number.isNaN(asofDate.getTime())) {
      setError('Invalid as-of datetime.');
      return;
    }

    setLoading(true);
    try {
      const response = await generateReport(formToPayload(form), form.threadId);
      sessionStorage.setItem(`fipro1:report:${response.report_id}`, JSON.stringify(response));
      navigate(`/results/${response.report_id}`);
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : 'Unknown submit error';
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page-root">
      <div className="mesh" aria-hidden="true" />
      <section className="panel panel-intro">
        <p className="eyebrow">FiPro_1 GUI Seed</p>
        <h1>Generate Report</h1>
        <p>
          This first route targets the existing backend API and passes full report generation parameters.
        </p>
        <div className="actions-row">
          <Link className="ghost-link" to="/startup">
            Edit Startup Configuration
          </Link>
        </div>
      </section>

      <section className="panel panel-form">
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            <span>Ticker</span>
            <input
              value={form.ticker}
              onChange={(event) => setForm((prev) => ({ ...prev, ticker: event.target.value }))}
              placeholder="600519.SH"
              required
            />
          </label>

          <label>
            <span>Market</span>
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
            <span>As Of (Local)</span>
            <input
              type="datetime-local"
              value={form.asofLocal}
              onChange={(event) => setForm((prev) => ({ ...prev, asofLocal: event.target.value }))}
              required
            />
          </label>

          <label>
            <span>Strategy Version</span>
            <input
              value={form.strategyVersionId}
              onChange={(event) => setForm((prev) => ({ ...prev, strategyVersionId: event.target.value }))}
              placeholder="stg_v1"
              required
            />
          </label>

          <label>
            <span>Tier</span>
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
            <span>Run Mode</span>
            <select
              value={form.runMode}
              onChange={(event) => setForm((prev) => ({ ...prev, runMode: event.target.value as RunMode }))}
            >
              <option value="LIVE">LIVE</option>
              <option value="SHADOW">SHADOW</option>
              <option value="BACKTEST">BACKTEST</option>
            </select>
          </label>

          <label className="wide">
            <span>Thread ID (Optional)</span>
            <input
              value={form.threadId}
              onChange={(event) => setForm((prev) => ({ ...prev, threadId: event.target.value }))}
              placeholder="thread_manual_001"
            />
          </label>

          <div className="wide actions">
            <button type="submit" disabled={loading || !canSubmit}>
              {loading ? 'Generating...' : 'Generate and Open Result'}
            </button>
            {error ? <p className="error-text">{error}</p> : null}
          </div>
        </form>
      </section>
    </main>
  );
}
