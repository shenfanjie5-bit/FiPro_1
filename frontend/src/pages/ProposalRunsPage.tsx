import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { getLlmProposalRun, listLlmProposalRuns } from '../api/client';
import { DataSourceStatusWidget } from '../components/DataSourceStatusWidget';
import type { LlmProposalRunDetail, LlmProposalRunListItem } from '../types/report';

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

export function ProposalRunsPage() {
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState('');
  const [items, setItems] = useState<LlmProposalRunListItem[]>([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [detail, setDetail] = useState<LlmProposalRunDetail | null>(null);

  async function refreshList() {
    setLoadingList(true);
    try {
      const response = await listLlmProposalRuns(100, 0);
      const rows = Array.isArray(response.items) ? response.items : [];
      setItems(rows);
      setListError('');
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
        <div className="actions-inline">
          <button type="button" className="secondary-button" onClick={() => void refreshList()}>
            刷新列表
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
                  <td>{boolLabel(item.executed)}</td>
                  <td>{boolLabel(item.dry_run)}</td>
                </tr>
              ))}
              {!loadingList && !listError && items.length === 0 ? (
                <tr>
                  <td colSpan={8} className="helper-text">暂无提案运行记录。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
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
