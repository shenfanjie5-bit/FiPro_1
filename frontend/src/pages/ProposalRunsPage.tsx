import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { getLlmProposalRun, listLlmProposalRuns } from '../api/client';
import { DataSourceStatusWidget } from '../components/DataSourceStatusWidget';
import type { LlmProposalRunDetail, LlmProposalRunListItem } from '../types/report';

type BoolFilter = 'ALL' | 'YES' | 'NO';

interface ListFilters {
  skillPackId: string;
  selectedDecision: string;
  executed: BoolFilter;
  dryRun: BoolFilter;
}

interface ListSummary {
  executedRuns: number;
  dryRunRuns: number;
  avgExcessDelta: number;
  avgSegmentWinRate: number;
  decisionCounts: Record<string, number>;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function asItemArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => item !== null);
}

function text(value: unknown): string {
  return String(value ?? '').trim();
}

function numeric(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
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
    return '允许晋级';
  }
  if (normalized === 'PENDING_MANUAL_APPROVAL') {
    return '待人工批准';
  }
  if (normalized === 'BLOCK') {
    return '阻止晋级';
  }
  return value || '-';
}

function boolLabel(value: boolean): string {
  return value ? '是' : '否';
}

function boolFilterToOptional(value: BoolFilter): boolean | undefined {
  if (value === 'YES') {
    return true;
  }
  if (value === 'NO') {
    return false;
  }
  return undefined;
}

export function ProposalRunsPage() {
  const [filters, setFilters] = useState<ListFilters>({
    skillPackId: '',
    selectedDecision: '',
    executed: 'ALL',
    dryRun: 'ALL',
  });
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState('');
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<ListSummary>({
    executedRuns: 0,
    dryRunRuns: 0,
    avgExcessDelta: 0,
    avgSegmentWinRate: 0,
    decisionCounts: {},
  });
  const [items, setItems] = useState<LlmProposalRunListItem[]>([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [detail, setDetail] = useState<LlmProposalRunDetail | null>(null);

  async function refreshList() {
    setLoadingList(true);
    try {
      const response = await listLlmProposalRuns(100, 0, {
        skill_pack_id: filters.skillPackId.trim(),
        executed: boolFilterToOptional(filters.executed),
        dry_run: boolFilterToOptional(filters.dryRun),
        selected_decision: filters.selectedDecision.trim(),
      });
      const rows = Array.isArray(response.items) ? response.items : [];
      setItems(rows);
      setTotal(Number(response.total || 0));
      setListError('');
      const summaryPayload = asRecord(response.summary);
      const decisionCountsRaw = asRecord(summaryPayload?.selected_decision_counts);
      const decisionCounts: Record<string, number> = {};
      if (decisionCountsRaw) {
        for (const [key, value] of Object.entries(decisionCountsRaw)) {
          decisionCounts[key] = numeric(value);
        }
      }
      setSummary({
        executedRuns: numeric(summaryPayload?.executed_runs),
        dryRunRuns: numeric(summaryPayload?.dry_run_runs),
        avgExcessDelta: numeric(summaryPayload?.avg_selected_excess_return_delta_pct),
        avgSegmentWinRate: numeric(summaryPayload?.avg_selected_segment_win_rate),
        decisionCounts,
      });
      if (!selectedRunId && rows.length > 0) {
        setSelectedRunId(rows[0].run_id);
      }
      if (selectedRunId && !rows.some((item) => item.run_id === selectedRunId)) {
        setSelectedRunId(rows[0]?.run_id || '');
      }
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '读取提案运行记录失败';
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
      const payload = await getLlmProposalRun(runId);
      setDetail(payload);
      setDetailError('');
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '读取提案运行详情失败';
      setDetailError(message);
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }

  useEffect(() => {
    void refreshList();
  }, []);

  useEffect(() => {
    if (!selectedRunId.trim()) {
      setDetail(null);
      return;
    }
    void refreshDetail(selectedRunId);
  }, [selectedRunId]);

  const selectedSummary = useMemo(() => {
    const selected = asRecord(detail?.selected_candidate);
    const evaluation = asRecord(selected?.evaluation);
    const candidateMetrics = asRecord(evaluation?.candidate_metrics);
    const antiOverfit = asRecord(evaluation?.anti_overfit);
    return {
      selectedVersion: text(selected?.candidate_version),
      decision: text(evaluation?.decision),
      excessDelta: numeric(candidateMetrics?.excess_return_delta_pct),
      segmentWinRate: numeric(candidateMetrics?.segment_win_rate),
      antiOverfitPass: Boolean(antiOverfit?.pass),
    };
  }, [detail]);

  const evaluationRows = useMemo(() => {
    return asItemArray(detail?.candidate_evaluations).map((item) => {
      const evaluation = asRecord(item.evaluation);
      const candidateMetrics = asRecord(evaluation?.candidate_metrics);
      const antiOverfit = asRecord(evaluation?.anti_overfit);
      return {
        candidateVersion: text(item.candidate_version),
        status: text(item.status),
        decision: text(evaluation?.decision),
        excessDelta: numeric(candidateMetrics?.excess_return_delta_pct),
        maxDrawdownDelta: numeric(candidateMetrics?.max_drawdown_delta_pct),
        segmentWinRate: numeric(candidateMetrics?.segment_win_rate),
        antiOverfitPass: Boolean(antiOverfit?.pass),
      };
    });
  }, [detail]);

  return (
    <main className="page-root">
      <div className="mesh" aria-hidden="true" />
      <section className="panel panel-intro">
        <p className="eyebrow">FiPro_1 图形界面</p>
        <h1>提案评审</h1>
        <p>查看 LLM 提案运行记录、候选评估结果与 gate 决策明细。</p>
        <div className="actions-row">
          <DataSourceStatusWidget />
        </div>
        <div className="actions-row">
          <Link className="ghost-link" to="/startup">
            启动配置
          </Link>
          {' · '}
          <Link className="ghost-link" to="/generate">
            生成报告
          </Link>
          {' · '}
          <Link className="ghost-link" to="/backtest">
            批量回测
          </Link>
          {' · '}
          <Link className="ghost-link" to="/champion-health">
            Champion 监控
          </Link>
        </div>
      </section>

      <section className="panel">
        <h2 className="table-title">提案运行列表</h2>
        <div className="form-grid">
          <label>
            <span>Skill Pack</span>
            <input
              value={filters.skillPackId}
              onChange={(event) => setFilters((prev) => ({ ...prev, skillPackId: event.target.value }))}
              placeholder="留空表示全部"
            />
          </label>
          <label>
            <span>选中决策</span>
            <select
              value={filters.selectedDecision}
              onChange={(event) => setFilters((prev) => ({ ...prev, selectedDecision: event.target.value }))}
            >
              <option value="">全部</option>
              <option value="ALLOW">ALLOW</option>
              <option value="PENDING_MANUAL_APPROVAL">PENDING_MANUAL_APPROVAL</option>
              <option value="BLOCK">BLOCK</option>
            </select>
          </label>
          <label>
            <span>已执行晋级</span>
            <select
              value={filters.executed}
              onChange={(event) => setFilters((prev) => ({ ...prev, executed: event.target.value as BoolFilter }))}
            >
              <option value="ALL">全部</option>
              <option value="YES">是</option>
              <option value="NO">否</option>
            </select>
          </label>
          <label>
            <span>Dry Run</span>
            <select
              value={filters.dryRun}
              onChange={(event) => setFilters((prev) => ({ ...prev, dryRun: event.target.value as BoolFilter }))}
            >
              <option value="ALL">全部</option>
              <option value="YES">是</option>
              <option value="NO">否</option>
            </select>
          </label>
        </div>
        <div className="actions-inline">
          <button type="button" className="secondary-button" onClick={() => void refreshList()}>
            应用筛选并刷新
          </button>
        </div>
        {loadingList ? <p className="helper-text">加载中...</p> : null}
        {listError ? <p className="error-text">{listError}</p> : null}
        <div className="table-wrap">
          <table className="run-table">
            <thead>
              <tr>
                <th>运行 ID</th>
                <th>时间</th>
                <th>Skill Pack</th>
                <th>基线版本</th>
                <th>提案数</th>
                <th>选中版本</th>
                <th>选中决策</th>
                <th>已执行晋级</th>
                <th>Dry Run</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
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
                  <td>{item.skill_pack_id}</td>
                  <td>{item.base_version}</td>
                  <td>{item.proposal_count}</td>
                  <td>{item.selected_candidate_version || '-'}</td>
                  <td>{decisionLabel(String(item.selected_decision || ''))}</td>
                  <td>{boolLabel(item.executed)}</td>
                  <td>{boolLabel(item.dry_run)}</td>
                </tr>
              ))}
              {!loadingList && !listError && items.length === 0 ? (
                <tr>
                  <td colSpan={9} className="helper-text">暂无提案运行记录。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel panel-metrics">
        <article>
          <h2>总运行数</h2>
          <p>{total}</p>
        </article>
        <article>
          <h2>已执行晋级</h2>
          <p>{summary.executedRuns}</p>
        </article>
        <article>
          <h2>Dry Run 数</h2>
          <p>{summary.dryRunRuns}</p>
        </article>
        <article>
          <h2>平均超额收益 Δ</h2>
          <p>{summary.avgExcessDelta.toFixed(2)}%</p>
        </article>
        <article>
          <h2>平均分段胜率</h2>
          <p>{(summary.avgSegmentWinRate * 100).toFixed(2)}%</p>
        </article>
        <article>
          <h2>决策分布</h2>
          <p>
            ALLOW {Number(summary.decisionCounts.ALLOW || 0)} / PENDING {Number(summary.decisionCounts.PENDING_MANUAL_APPROVAL || 0)} / BLOCK {Number(summary.decisionCounts.BLOCK || 0)}
          </p>
        </article>
      </section>

      <section className="panel panel-metrics">
        <article>
          <h2>选中候选</h2>
          <p>{selectedSummary.selectedVersion || '-'}</p>
        </article>
        <article>
          <h2>决策</h2>
          <p>{decisionLabel(selectedSummary.decision)}</p>
        </article>
        <article>
          <h2>超额收益 Δ</h2>
          <p>{selectedSummary.excessDelta.toFixed(2)}%</p>
        </article>
        <article>
          <h2>分段胜率</h2>
          <p>{(selectedSummary.segmentWinRate * 100).toFixed(2)}%</p>
        </article>
        <article>
          <h2>稳健性通过</h2>
          <p>{boolLabel(selectedSummary.antiOverfitPass)}</p>
        </article>
      </section>

      <section className="panel">
        <h2 className="table-title">候选评估明细</h2>
        {detailLoading ? <p className="helper-text">详情加载中...</p> : null}
        {detailError ? <p className="error-text">{detailError}</p> : null}
        <div className="table-wrap">
          <table className="run-table">
            <thead>
              <tr>
                <th>候选版本</th>
                <th>状态</th>
                <th>决策</th>
                <th>超额收益 Δ</th>
                <th>回撤 Δ</th>
                <th>分段胜率</th>
                <th>稳健性通过</th>
              </tr>
            </thead>
            <tbody>
              {evaluationRows.map((item) => (
                <tr key={`${item.candidateVersion}-${item.status}`}>
                  <td>{item.candidateVersion || '-'}</td>
                  <td>{item.status || '-'}</td>
                  <td>{decisionLabel(item.decision)}</td>
                  <td>{item.excessDelta.toFixed(2)}%</td>
                  <td>{item.maxDrawdownDelta.toFixed(2)}%</td>
                  <td>{(item.segmentWinRate * 100).toFixed(2)}%</td>
                  <td>{boolLabel(item.antiOverfitPass)}</td>
                </tr>
              ))}
              {!detailLoading && !detailError && evaluationRows.length === 0 ? (
                <tr>
                  <td colSpan={7} className="helper-text">暂无候选评估数据。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel panel-json">
        <h2>运行详情 JSON</h2>
        <pre>{JSON.stringify(detail ?? { hint: '请选择一条提案运行记录查看详情。' }, null, 2)}</pre>
      </section>
    </main>
  );
}
