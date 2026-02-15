import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { AdvancedSettings } from '../components/AdvancedSettings';
import {
  acknowledgeChampionWatchdogAlert,
  closeChampionWatchdogAlert,
  getChampionHealthCheck,
  getChampionWatchdogRun,
  listChampionWatchdogAlerts,
  listChampionHealthChecks,
  listChampionWatchdogRuns,
  listChampionWatchdogTickets,
  runChampionHealthCheck,
  runChampionWatchdog
} from '../api/client';
import { DataSourceStatusWidget } from '../components/DataSourceStatusWidget';
import type {
  BatchBacktestPayload,
  ChampionWatchdogAlertItem,
  ChampionHealthCheckDetail,
  ChampionHealthCheckListItem,
  ChampionWatchdogRunDetail,
  ChampionWatchdogRunListItem,
  ChampionWatchdogTicketItem,
  Tier,
} from '../types/report';

interface FormState {
  skillPackId: string;
  championVersion: string;
  baselineVersion: string;
  ticker: string;
  market: string;
  strategyVersionId: string;
  tier: Tier;
  startDate: string;
  endDate: string;
  stepDays: number;
  asofTime: string;
  timezoneOffset: string;
  maxRuns: number;
  evaluationHorizonDays: number;
  autoRollback: boolean;
  rollbackDryRun: boolean;
  rollbackReason: string;
  operator: string;
}

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

function formatUtc(iso: string): string {
  const raw = iso.trim();
  if (!raw) {
    return '-';
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return raw;
  }
  return parsed.toLocaleString('zh-CN', { hour12: false });
}

function decisionLabel(value: string): string {
  const normalized = value.trim().toUpperCase();
  if (normalized === 'ALLOW') {
    return '通过';
  }
  if (normalized === 'BLOCK') {
    return '阻止';
  }
  if (normalized === 'PENDING_MANUAL_APPROVAL') {
    return '待人工批准';
  }
  return value || '-';
}

function statusLabel(value: string): string {
  const normalized = value.trim().toUpperCase();
  if (normalized === 'PASS') {
    return '健康';
  }
  if (normalized === 'FAIL') {
    return '异常';
  }
  return value || '-';
}

function watchdogStatusLabel(value: string): string {
  const normalized = value.trim().toUpperCase();
  if (normalized === 'PASS') {
    return '正常';
  }
  if (normalized === 'WARN') {
    return '告警';
  }
  if (normalized === 'CRITICAL') {
    return '严重';
  }
  return value || '-';
}

function alertLifecycleLabel(value: string): string {
  const normalized = value.trim().toUpperCase();
  if (normalized === 'OPEN') {
    return '待处理';
  }
  if (normalized === 'ACKED') {
    return '已确认';
  }
  if (normalized === 'CLOSED') {
    return '已关闭';
  }
  return value || '-';
}

function alertSeverityLabel(value: string): string {
  const normalized = value.trim().toLowerCase();
  if (normalized === 'critical') {
    return '严重';
  }
  if (normalized === 'warning') {
    return '告警';
  }
  if (normalized === 'info') {
    return '提示';
  }
  return value || '-';
}

function boolLabel(value: boolean): string {
  return value ? '是' : '否';
}

function toBacktestPayload(form: FormState): BatchBacktestPayload {
  const payload: BatchBacktestPayload = {
    ticker: form.ticker.trim(),
    market: form.market.trim() || 'CN_A',
    strategy_version_id: form.strategyVersionId.trim(),
    tier: form.tier,
    start_date: form.startDate.trim(),
    end_date: form.endDate.trim(),
    step_days: Number(form.stepDays),
    trading_days_only: true,
    asof_time: form.asofTime.trim(),
    timezone_offset: form.timezoneOffset.trim(),
    max_runs: Number(form.maxRuns),
    initial_capital_cny: 1_000_000
  };
  const defaultHorizonDays = Number(form.evaluationHorizonDays);
  if (Number.isFinite(defaultHorizonDays) && defaultHorizonDays >= 1) {
    payload.evaluation_horizon_days = Math.max(1, Math.min(120, Math.trunc(defaultHorizonDays)));
  }
  return payload;
}

export function ChampionHealthPage() {
  const range = defaultRange();
  const [form, setForm] = useState<FormState>({
    skillPackId: 'cn_a_core',
    championVersion: '',
    baselineVersion: '',
    ticker: '600519.SH',
    market: 'CN_A',
    strategyVersionId: 'stg_v1',
    tier: 'TIER0',
    startDate: range.start,
    endDate: range.end,
    stepDays: 1,
    asofTime: '09:30',
    timezoneOffset: '+08:00',
    maxRuns: 60,
    evaluationHorizonDays: 5,
    autoRollback: false,
    rollbackDryRun: true,
    rollbackReason: 'monitoring_gate_block',
    operator: 'monitor_engine'
  });
  const [submitting, setSubmitting] = useState(false);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [detailLoading, setDetailLoading] = useState(false);
  const [runs, setRuns] = useState<ChampionHealthCheckListItem[]>([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [detail, setDetail] = useState<ChampionHealthCheckDetail | null>(null);
  const [watchdogSubmitting, setWatchdogSubmitting] = useState(false);
  const [watchdogLoadingList, setWatchdogLoadingList] = useState(true);
  const [watchdogListError, setWatchdogListError] = useState('');
  const [watchdogDetailError, setWatchdogDetailError] = useState('');
  const [watchdogDetailLoading, setWatchdogDetailLoading] = useState(false);
  const [watchdogRuns, setWatchdogRuns] = useState<ChampionWatchdogRunListItem[]>([]);
  const [selectedWatchdogRunId, setSelectedWatchdogRunId] = useState('');
  const [watchdogDetail, setWatchdogDetail] = useState<ChampionWatchdogRunDetail | null>(null);
  const [watchdogAlertsLoading, setWatchdogAlertsLoading] = useState(true);
  const [watchdogAlertsError, setWatchdogAlertsError] = useState('');
  const [watchdogAlerts, setWatchdogAlerts] = useState<ChampionWatchdogAlertItem[]>([]);
  const [watchdogTicketsLoading, setWatchdogTicketsLoading] = useState(true);
  const [watchdogTicketsError, setWatchdogTicketsError] = useState('');
  const [watchdogTickets, setWatchdogTickets] = useState<ChampionWatchdogTicketItem[]>([]);
  const [watchdogActionBusyAlertId, setWatchdogActionBusyAlertId] = useState('');

  const canSubmit = useMemo(() => {
    return Boolean(
      form.skillPackId.trim() &&
      form.ticker.trim() &&
      form.strategyVersionId.trim() &&
      form.startDate.trim() &&
      form.endDate.trim() &&
      form.rollbackReason.trim() &&
      form.operator.trim()
    );
  }, [form]);

  async function refreshList() {
    setLoadingList(true);
    try {
      const payload = await listChampionHealthChecks(100, 0);
      const items = Array.isArray(payload.items) ? payload.items : [];
      setRuns(items);
      setListError('');
      if (!selectedRunId && items.length > 0) {
        setSelectedRunId(items[0].run_id);
      }
      if (selectedRunId && !items.some((item) => item.run_id === selectedRunId)) {
        setSelectedRunId(items[0]?.run_id || '');
      }
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '读取健康检查记录失败';
      setListError(message);
    } finally {
      setLoadingList(false);
    }
  }

  async function refreshDetail(runId: string) {
    if (!runId.trim()) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    try {
      const payload = await getChampionHealthCheck(runId);
      setDetail(payload);
      setDetailError('');
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '读取健康检查详情失败';
      setDetailError(message);
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }

  async function refreshWatchdogList() {
    setWatchdogLoadingList(true);
    try {
      const payload = await listChampionWatchdogRuns(100, 0);
      const items = Array.isArray(payload.items) ? payload.items : [];
      setWatchdogRuns(items);
      setWatchdogListError('');
      if (!selectedWatchdogRunId && items.length > 0) {
        setSelectedWatchdogRunId(items[0].run_id);
      }
      if (selectedWatchdogRunId && !items.some((item) => item.run_id === selectedWatchdogRunId)) {
        setSelectedWatchdogRunId(items[0]?.run_id || '');
      }
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '读取 Watchdog 记录失败';
      setWatchdogListError(message);
    } finally {
      setWatchdogLoadingList(false);
    }
  }

  async function refreshWatchdogDetail(runId: string) {
    if (!runId.trim()) {
      setWatchdogDetail(null);
      return;
    }
    setWatchdogDetailLoading(true);
    try {
      const payload = await getChampionWatchdogRun(runId);
      setWatchdogDetail(payload);
      setWatchdogDetailError('');
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '读取 Watchdog 详情失败';
      setWatchdogDetailError(message);
      setWatchdogDetail(null);
    } finally {
      setWatchdogDetailLoading(false);
    }
  }

  async function refreshWatchdogAlerts() {
    setWatchdogAlertsLoading(true);
    try {
      const payload = await listChampionWatchdogAlerts(200, 0, '');
      setWatchdogAlerts(Array.isArray(payload.items) ? payload.items : []);
      setWatchdogAlertsError('');
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '读取 Watchdog 告警失败';
      setWatchdogAlertsError(message);
      setWatchdogAlerts([]);
    } finally {
      setWatchdogAlertsLoading(false);
    }
  }

  async function refreshWatchdogTickets() {
    setWatchdogTicketsLoading(true);
    try {
      const payload = await listChampionWatchdogTickets(200, 0);
      setWatchdogTickets(Array.isArray(payload.items) ? payload.items : []);
      setWatchdogTicketsError('');
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '读取 Watchdog 工单失败';
      setWatchdogTicketsError(message);
      setWatchdogTickets([]);
    } finally {
      setWatchdogTicketsLoading(false);
    }
  }

  useEffect(() => {
    void refreshList();
    void refreshWatchdogList();
    void refreshWatchdogAlerts();
    void refreshWatchdogTickets();
  }, []);

  useEffect(() => {
    if (!selectedRunId.trim()) {
      return;
    }
    void refreshDetail(selectedRunId);
  }, [selectedRunId]);

  useEffect(() => {
    if (!selectedWatchdogRunId.trim()) {
      return;
    }
    void refreshWatchdogDetail(selectedWatchdogRunId);
  }, [selectedWatchdogRunId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit || submitting) {
      return;
    }
    setSubmitting(true);
    setListError('');
    setDetailError('');
    try {
      const payload = await runChampionHealthCheck({
        skill_pack_id: form.skillPackId.trim(),
        champion_version: form.championVersion.trim() || undefined,
        baseline_version: form.baselineVersion.trim() || undefined,
        auto_rollback: form.autoRollback,
        rollback_dry_run: form.rollbackDryRun,
        rollback_reason: form.rollbackReason.trim(),
        operator: form.operator.trim(),
        manual_approved: true,
        anti_overfit_evidence: {},
        backtest: toBacktestPayload(form)
      });
      setDetail(payload);
      setSelectedRunId(payload.run_id);
      await refreshList();
      await refreshWatchdogList();
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : '提交健康检查失败';
      setListError(message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRunWatchdog() {
    if (watchdogSubmitting) {
      return;
    }
    setWatchdogSubmitting(true);
    setWatchdogListError('');
    setWatchdogDetailError('');
    try {
      const payload = await runChampionWatchdog({
        run_health_check: false,
        health_check: {},
        lookback_runs: 20,
        consecutive_fail_critical: 2,
        fail_rate_warn: 0.25,
        fail_rate_critical: 0.5,
        rollback_storm_critical: 2,
        auto_create_ticket: true
      });
      setWatchdogDetail(payload);
      setSelectedWatchdogRunId(payload.run_id);
      await refreshWatchdogList();
      await refreshWatchdogAlerts();
      await refreshWatchdogTickets();
    } catch (runError) {
      const message = runError instanceof Error ? runError.message : '运行 Watchdog 失败';
      setWatchdogListError(message);
    } finally {
      setWatchdogSubmitting(false);
    }
  }

  async function handleAcknowledgeAlert(alertId: string) {
    const normalized = alertId.trim();
    if (!normalized || watchdogActionBusyAlertId) {
      return;
    }
    setWatchdogActionBusyAlertId(normalized);
    setWatchdogAlertsError('');
    try {
      await acknowledgeChampionWatchdogAlert(normalized, {
        operator: form.operator.trim() || 'monitor_engine',
        note: 'gui_ack'
      });
      await Promise.all([refreshWatchdogAlerts(), refreshWatchdogList(), refreshWatchdogTickets()]);
      if (selectedWatchdogRunId.trim()) {
        await refreshWatchdogDetail(selectedWatchdogRunId);
      }
    } catch (actionError) {
      const message = actionError instanceof Error ? actionError.message : '确认告警失败';
      setWatchdogAlertsError(message);
    } finally {
      setWatchdogActionBusyAlertId('');
    }
  }

  async function handleCloseAlert(alertId: string) {
    const normalized = alertId.trim();
    if (!normalized || watchdogActionBusyAlertId) {
      return;
    }
    setWatchdogActionBusyAlertId(normalized);
    setWatchdogAlertsError('');
    try {
      await closeChampionWatchdogAlert(normalized, {
        operator: form.operator.trim() || 'monitor_engine',
        note: 'gui_close'
      });
      await Promise.all([refreshWatchdogAlerts(), refreshWatchdogList(), refreshWatchdogTickets()]);
      if (selectedWatchdogRunId.trim()) {
        await refreshWatchdogDetail(selectedWatchdogRunId);
      }
    } catch (actionError) {
      const message = actionError instanceof Error ? actionError.message : '关闭告警失败';
      setWatchdogAlertsError(message);
    } finally {
      setWatchdogActionBusyAlertId('');
    }
  }

  const selectedDecision = String((detail?.evaluation || {}).decision || '');
  const selectedRollback = detail?.rollback_execution;
  const selectedExcessDelta = Number(((detail?.evaluation || {}).candidate_metrics as Record<string, unknown> | undefined)?.excess_return_delta_pct || 0);
  const watchdogRecommendation = (watchdogDetail?.rollback_recommendation || {}) as Record<string, unknown>;
  const watchdogShouldRollback = Boolean(watchdogRecommendation.should_rollback);
  const watchdogTargetVersion = String(watchdogRecommendation.target_version || '');
  const selectedRunAlerts = useMemo(() => {
    if (!selectedWatchdogRunId.trim()) {
      return watchdogAlerts;
    }
    return watchdogAlerts.filter((item) => String(item.run_id || '') === selectedWatchdogRunId.trim());
  }, [watchdogAlerts, selectedWatchdogRunId]);

  return (
    <main className="page-root">
      <div className="mesh" aria-hidden="true" />
      <section className="panel panel-intro">
        <p className="eyebrow">FiPro_1 图形界面</p>
        <h1>Champion 监控与回滚</h1>
        <p>对当前 champion 做健康检查（对比基线），并在触发门禁失败时执行可选回滚。</p>
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
        </div>
      </section>

      <section className="panel panel-form">
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            <span>Skill Pack ID</span>
            <input
              value={form.skillPackId}
              onChange={(event) => setForm((prev) => ({ ...prev, skillPackId: event.target.value }))}
              required
            />
          </label>
          <label>
            <span>当前 Champion（可选）</span>
            <input
              value={form.championVersion}
              onChange={(event) => setForm((prev) => ({ ...prev, championVersion: event.target.value }))}
              placeholder="留空自动读取当前 champion"
            />
          </label>
          <label>
            <span>基线版本（可选）</span>
            <input
              value={form.baselineVersion}
              onChange={(event) => setForm((prev) => ({ ...prev, baselineVersion: event.target.value }))}
              placeholder="留空自动推断"
            />
          </label>
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
          <AdvancedSettings description="后续可在这里扩展更多高级参数（当前包含默认评估窗口天数）。">
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
          </AdvancedSettings>
          <label>
            <span>时间点</span>
            <input
              value={form.asofTime}
              onChange={(event) => setForm((prev) => ({ ...prev, asofTime: event.target.value }))}
              required
            />
          </label>
          <label>
            <span>时区偏移</span>
            <input
              value={form.timezoneOffset}
              onChange={(event) => setForm((prev) => ({ ...prev, timezoneOffset: event.target.value }))}
              required
            />
          </label>
          <label>
            <span>回滚原因</span>
            <input
              value={form.rollbackReason}
              onChange={(event) => setForm((prev) => ({ ...prev, rollbackReason: event.target.value }))}
              required
            />
          </label>
          <label>
            <span>操作人</span>
            <input
              value={form.operator}
              onChange={(event) => setForm((prev) => ({ ...prev, operator: event.target.value }))}
              required
            />
          </label>
          <label className="wide checkbox-row">
            <input
              type="checkbox"
              checked={form.autoRollback}
              onChange={(event) => setForm((prev) => ({ ...prev, autoRollback: event.target.checked }))}
            />
            <span>门禁失败时自动触发回滚</span>
          </label>
          <label className="wide checkbox-row">
            <input
              type="checkbox"
              checked={form.rollbackDryRun}
              onChange={(event) => setForm((prev) => ({ ...prev, rollbackDryRun: event.target.checked }))}
            />
            <span>回滚仅演练（dry run）</span>
          </label>
          <div className="wide actions">
            <button type="submit" disabled={!canSubmit || submitting}>
              {submitting ? '检查中...' : '运行 Champion 健康检查'}
            </button>
            {listError ? <p className="error-text">{listError}</p> : null}
          </div>
        </form>
      </section>

      <section className="panel">
        <h2 className="table-title">健康检查记录</h2>
        <div className="actions-inline">
          <button type="button" className="secondary-button" onClick={() => void refreshList()}>
            刷新记录
          </button>
        </div>
        {loadingList ? <p className="helper-text">加载中...</p> : null}
        <div className="table-wrap">
          <table className="run-table">
            <thead>
              <tr>
                <th>运行 ID</th>
                <th>时间</th>
                <th>Champion</th>
                <th>Baseline</th>
                <th>健康状态</th>
                <th>决策</th>
                <th>自动回滚</th>
                <th>已执行回滚</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((item) => (
                <tr key={item.run_id} className={item.run_id === selectedRunId ? 'row-selected' : ''}>
                  <td>
                    <button
                      type="button"
                      className="table-link-button"
                      onClick={() => {
                        setSelectedRunId(item.run_id);
                      }}
                    >
                      {item.run_id}
                    </button>
                  </td>
                  <td>{formatUtc(item.generated_at)}</td>
                  <td>{item.champion_version}</td>
                  <td>{item.baseline_version}</td>
                  <td>{statusLabel(item.health_status)}</td>
                  <td>{decisionLabel(item.decision)}</td>
                  <td>{boolLabel(item.auto_rollback)}</td>
                  <td>{boolLabel(item.rollback_executed)}</td>
                </tr>
              ))}
              {!loadingList && runs.length === 0 ? (
                <tr>
                  <td colSpan={8} className="helper-text">暂无健康检查记录。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2 className="table-title">Watchdog 告警记录</h2>
        <div className="actions-inline">
          <button type="button" className="secondary-button" onClick={() => void refreshWatchdogList()}>
            刷新 Watchdog
          </button>
          <button type="button" onClick={() => void handleRunWatchdog()} disabled={watchdogSubmitting}>
            {watchdogSubmitting ? '运行中...' : '运行 Watchdog'}
          </button>
        </div>
        {watchdogLoadingList ? <p className="helper-text">加载中...</p> : null}
        {watchdogListError ? <p className="error-text">{watchdogListError}</p> : null}
        <div className="table-wrap">
          <table className="run-table">
            <thead>
              <tr>
                <th>运行 ID</th>
                <th>时间</th>
                <th>状态</th>
                <th>告警数</th>
                <th>未关闭告警</th>
                <th>最新健康</th>
                <th>建议回滚</th>
                <th>目标版本</th>
                <th>工单</th>
              </tr>
            </thead>
            <tbody>
              {watchdogRuns.map((item) => (
                <tr key={item.run_id} className={item.run_id === selectedWatchdogRunId ? 'row-selected' : ''}>
                  <td>
                    <button
                      type="button"
                      className="table-link-button"
                      onClick={() => {
                        setSelectedWatchdogRunId(item.run_id);
                      }}
                    >
                      {item.run_id}
                    </button>
                  </td>
                  <td>{formatUtc(item.generated_at)}</td>
                  <td>{watchdogStatusLabel(item.overall_status)}</td>
                  <td>{item.alert_count}</td>
                  <td>{Number(item.open_alert_count || 0)}</td>
                  <td>{statusLabel(item.latest_health_status)}</td>
                  <td>{boolLabel(item.should_rollback)}</td>
                  <td>{item.rollback_target_version || '-'}</td>
                  <td>{item.ticket_id || '-'}</td>
                </tr>
              ))}
              {!watchdogLoadingList && watchdogRuns.length === 0 ? (
                <tr>
                  <td colSpan={9} className="helper-text">暂无 Watchdog 记录。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2 className="table-title">当前 Watchdog 告警明细（可 ACK/关闭）</h2>
        <div className="actions-inline">
          <button type="button" className="secondary-button" onClick={() => void refreshWatchdogAlerts()}>
            刷新告警
          </button>
        </div>
        {watchdogAlertsLoading ? <p className="helper-text">加载中...</p> : null}
        {watchdogAlertsError ? <p className="error-text">{watchdogAlertsError}</p> : null}
        <div className="table-wrap">
          <table className="run-table">
            <thead>
              <tr>
                <th>告警 ID</th>
                <th>级别</th>
                <th>状态</th>
                <th>代码</th>
                <th>描述</th>
                <th>运行时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {selectedRunAlerts.map((item) => {
                const alertId = String(item.alert_id || '');
                const alertStatus = String(item.status || '').toUpperCase();
                const busy = watchdogActionBusyAlertId === alertId;
                return (
                  <tr key={alertId}>
                    <td>{alertId || '-'}</td>
                    <td>{alertSeverityLabel(String(item.severity || ''))}</td>
                    <td>{alertLifecycleLabel(alertStatus)}</td>
                    <td>{String(item.code || '-')}</td>
                    <td>{String(item.message || '-')}</td>
                    <td>{formatUtc(String(item.generated_at || ''))}</td>
                    <td>
                      <div className="actions-inline">
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={() => void handleAcknowledgeAlert(alertId)}
                          disabled={!alertId || busy || alertStatus === 'CLOSED'}
                        >
                          {busy ? '处理中...' : 'ACK'}
                        </button>
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={() => void handleCloseAlert(alertId)}
                          disabled={!alertId || busy || alertStatus === 'CLOSED'}
                        >
                          {busy ? '处理中...' : '关闭'}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!watchdogAlertsLoading && selectedRunAlerts.length === 0 ? (
                <tr>
                  <td colSpan={7} className="helper-text">当前选择的 Watchdog 运行没有告警。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2 className="table-title">Watchdog 自动工单</h2>
        <div className="actions-inline">
          <button type="button" className="secondary-button" onClick={() => void refreshWatchdogTickets()}>
            刷新工单
          </button>
        </div>
        {watchdogTicketsLoading ? <p className="helper-text">加载中...</p> : null}
        {watchdogTicketsError ? <p className="error-text">{watchdogTicketsError}</p> : null}
        <div className="table-wrap">
          <table className="run-table">
            <thead>
              <tr>
                <th>工单 ID</th>
                <th>时间</th>
                <th>状态</th>
                <th>级别</th>
                <th>关联运行</th>
                <th>告警数</th>
                <th>标题</th>
              </tr>
            </thead>
            <tbody>
              {watchdogTickets.map((item) => (
                <tr key={item.ticket_id}>
                  <td>{item.ticket_id}</td>
                  <td>{formatUtc(item.created_at || '')}</td>
                  <td>{item.status || '-'}</td>
                  <td>{alertSeverityLabel(item.severity || '')}</td>
                  <td>{item.run_id || '-'}</td>
                  <td>{Number(item.alert_count || 0)}</td>
                  <td>{item.title || '-'}</td>
                </tr>
              ))}
              {!watchdogTicketsLoading && watchdogTickets.length === 0 ? (
                <tr>
                  <td colSpan={7} className="helper-text">暂无 Watchdog 工单。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel panel-metrics">
        <article>
          <h2>健康状态</h2>
          <p>{statusLabel(detail?.health_status || '')}</p>
        </article>
        <article>
          <h2>门禁决策</h2>
          <p>{decisionLabel(selectedDecision)}</p>
        </article>
        <article>
          <h2>超额收益 Δ</h2>
          <p>{selectedExcessDelta.toFixed(2)}%</p>
        </article>
        <article>
          <h2>回滚执行</h2>
          <p>{boolLabel(Boolean(selectedRollback))}</p>
        </article>
        <article>
          <h2>Watchdog 状态</h2>
          <p>{watchdogStatusLabel(watchdogDetail?.overall_status || '')}</p>
        </article>
        <article>
          <h2>Watchdog 告警数</h2>
          <p>{Number(watchdogDetail?.alert_count || 0)}</p>
        </article>
        <article>
          <h2>Watchdog 建议回滚</h2>
          <p>{boolLabel(watchdogShouldRollback)}</p>
        </article>
        <article>
          <h2>Watchdog 目标版本</h2>
          <p>{watchdogTargetVersion || '-'}</p>
        </article>
      </section>

      <section className="panel panel-json">
        <h2>健康检查详情 JSON</h2>
        {detailLoading ? <p className="helper-text">详情加载中...</p> : null}
        {detailError ? <p className="error-text">{detailError}</p> : null}
        <pre>{JSON.stringify(detail ?? { hint: '选择一条健康检查记录查看详情。' }, null, 2)}</pre>
      </section>

      <section className="panel panel-json">
        <h2>Watchdog 详情 JSON</h2>
        {watchdogDetailLoading ? <p className="helper-text">详情加载中...</p> : null}
        {watchdogDetailError ? <p className="error-text">{watchdogDetailError}</p> : null}
        <pre>{JSON.stringify(watchdogDetail ?? { hint: '选择一条 Watchdog 记录查看详情。' }, null, 2)}</pre>
      </section>
    </main>
  );
}
