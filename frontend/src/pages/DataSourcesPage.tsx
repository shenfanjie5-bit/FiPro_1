import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { getDataSourceStatus } from '../api/client';
import type { DataSourceStatus, DataSourceStatusResponse } from '../types/report';

function statusClassName(status: DataSourceStatus): string {
  if (status === 'COMPLETED') {
    return 'ok';
  }
  if (status === 'UPDATING') {
    return 'updating';
  }
  return 'error';
}

function formatUtc(isoUtc: string): string {
  if (!isoUtc.trim()) {
    return '-';
  }
  const parsed = new Date(isoUtc);
  if (Number.isNaN(parsed.getTime())) {
    return isoUtc;
  }
  return parsed.toLocaleString('zh-CN', { hour12: false });
}

export function DataSourcesPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [snapshot, setSnapshot] = useState<DataSourceStatusResponse | null>(null);

  async function refreshStatus() {
    try {
      const latest = await getDataSourceStatus();
      setSnapshot(latest);
      setError('');
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '读取状态失败';
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      if (cancelled) {
        return;
      }
      await refreshStatus();
    }

    void bootstrap();
    const timer = window.setInterval(() => {
      if (!cancelled) {
        void refreshStatus();
      }
    }, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const overallStatus: DataSourceStatus = useMemo(() => {
    const value = snapshot?.overall_status;
    if (value === 'COMPLETED' || value === 'UPDATING' || value === 'ERROR') {
      return value;
    }
    if (error) {
      return 'ERROR';
    }
    return loading ? 'UPDATING' : 'ERROR';
  }, [snapshot?.overall_status, error, loading]);

  const overallLabel = snapshot?.overall_label || (overallStatus === 'COMPLETED' ? '已完成' : overallStatus === 'UPDATING' ? '更新中' : '异常');

  return (
    <main className="page-root">
      <div className="mesh" aria-hidden="true" />
      <section className="panel panel-intro">
        <p className="eyebrow">FiPro_1 图形界面</p>
        <h1>数据源总览</h1>
        <p>查看各数据源是否已完成更新、是否正在更新，以及异常信息。</p>
        <div className="actions-row">
          <span className="data-status-pill">
            <span className={`status-light ${statusClassName(overallStatus)}`} aria-hidden="true" />
            总状态：{overallLabel}
          </span>
        </div>
        <div className="actions-row">
          <button type="button" className="secondary-button" onClick={() => void refreshStatus()}>
            刷新状态
          </button>
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
          <Link className="ghost-link" to="/proposals">
            提案评审
          </Link>
          {' · '}
          <Link className="ghost-link" to="/champion-health">
            Champion 监控
          </Link>
        </div>
      </section>

      <section className="panel">
        <h2 className="table-title">数据源状态明细</h2>
        {loading ? <p className="helper-text">状态加载中...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}

        <ul className="data-status-list">
          {(snapshot?.sources ?? []).map((item) => (
            <li key={item.source_id} className="data-status-item">
              {(() => {
                const meta = (item.meta && typeof item.meta === 'object') ? item.meta as Record<string, unknown> : {};
                const criticalOpen = Number(meta.critical_open_alert_count ?? 0);
                const warningOpen = Number(meta.warning_open_alert_count ?? 0);
                const openCount = Number(meta.open_alert_count ?? 0);
                const latestRunId = String(meta.latest_run_id ?? '');
                return (
                  <>
                    <div className="data-status-item-head">
                      <span className={`status-light ${statusClassName(item.status)}`} aria-hidden="true" />
                      <span className="data-status-item-name">{item.name}</span>
                      <span className="data-status-item-label">{item.label}</span>
                    </div>
                    <p className="data-status-item-message">{item.message || '-'}</p>
                    <p className="helper-text">
                      最近完成：{formatUtc(item.last_success_at_utc)} | 最近异常：{formatUtc(item.last_error_at_utc)} | 最近更新：{formatUtc(item.updated_at_utc)}
                    </p>
                    {item.source_id === 'CHAMPION_WATCHDOG' ? (
                      <p className="helper-text">
                        未关闭告警：{openCount}（严重 {criticalOpen} / 普通 {warningOpen}）{latestRunId ? ` | 最新运行：${latestRunId}` : ''}
                      </p>
                    ) : null}
                  </>
                );
              })()}
              {item.running_jobs.length > 0 ? (
                <div className="data-status-jobs">
                  <p className="helper-text">运行中任务：</p>
                  <ul>
                    {item.running_jobs.map((job) => (
                      <li key={`${item.source_id}-${job.pid}`}>
                        PID {job.pid} | {job.mode} | {job.command}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </li>
          ))}
          {!loading && !error && (snapshot?.sources?.length ?? 0) === 0 ? <li className="helper-text">暂无数据源状态。</li> : null}
        </ul>
      </section>
    </main>
  );
}
