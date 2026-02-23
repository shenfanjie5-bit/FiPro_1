import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { getReport } from '../api/client';
import { DataSourceStatusWidget } from '../components/DataSourceStatusWidget';
import type { ReportResponse } from '../types/report';

function extractNumber(value: unknown): string {
  return typeof value === 'number' ? value.toString() : '-';
}

function extractText(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : '-';
}

function actionLabel(value: unknown): string {
  const action = extractText(value).toUpperCase();
  if (action === 'BUY') {
    return '买入';
  }
  if (action === 'WATCH') {
    return '观察';
  }
  if (action === 'AVOID') {
    return '回避';
  }
  return extractText(value);
}

export function ResultPage() {
  const { reportId = '' } = useParams();
  const [data, setData] = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (!reportId.trim()) {
        setError('路由中缺少 report id。');
        setLoading(false);
        return;
      }

      const cacheKey = `fipro1:report:${reportId}`;
      const cached = sessionStorage.getItem(cacheKey);
      if (cached) {
        try {
          const parsed = JSON.parse(cached) as ReportResponse;
          if (!cancelled) {
            setData(parsed);
            setLoading(false);
          }
        } catch {
          sessionStorage.removeItem(cacheKey);
        }
      }

      try {
        const fresh = await getReport(reportId);
        if (cancelled) {
          return;
        }
        setData(fresh);
        sessionStorage.setItem(cacheKey, JSON.stringify(fresh));
        setError('');
      } catch (loadError) {
        if (!cancelled) {
          const message = loadError instanceof Error ? loadError.message : '加载失败（未知错误）';
          setError(message);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [reportId]);

  const finalReport = data?.final_report ?? {};
  const decision = (finalReport.decision ?? {}) as Record<string, unknown>;
  const dataQuality = (finalReport.data_quality ?? {}) as Record<string, unknown>;

  const degradedHint = useMemo(() => {
    const status = String(dataQuality.status ?? '').toUpperCase();
    return status && status !== 'OK'
      ? `当前数据质量为 ${status}，报告可能包含回退链路或降级依赖。`
      : '';
  }, [dataQuality.status]);

  return (
    <main className="page-root">
      <div className="mesh" aria-hidden="true" />
      <section className="panel panel-intro">
        <p className="eyebrow">FiPro_1 图形界面</p>
        <h1>报告结果</h1>
        <p>路由 ID：<code>{reportId || '无'}</code></p>
        <div className="actions-row">
          <DataSourceStatusWidget />
        </div>
        <div className="actions-row">
          <Link className="ghost-link" to="/generate">
            新建报告
          </Link>
          {' · '}
          <Link className="ghost-link" to="/backtest">
            批量回测
          </Link>
          {' · '}
          <Link className="ghost-link" to="/startup">
            启动配置
          </Link>
        </div>
      </section>

      <section className="panel panel-metrics">
        <article>
          <h2>动作</h2>
          <p>{actionLabel(decision.action)}</p>
        </article>
        <article>
          <h2>评分</h2>
          <p>{extractNumber(decision.overall_score)}</p>
        </article>
        <article>
          <h2>置信度</h2>
          <p>{extractNumber(decision.confidence)}</p>
        </article>
        <article>
          <h2>数据质量</h2>
          <p>{extractText(dataQuality.status)}</p>
        </article>
      </section>

      {degradedHint ? (
        <section className="panel warning-panel">
          <p>{degradedHint}</p>
        </section>
      ) : null}

      <section className="panel panel-json">
        <h2>原始 JSON</h2>
        {loading ? <p>报告加载中...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        <pre>{JSON.stringify(data ?? { report_id: reportId }, null, 2)}</pre>
      </section>
    </main>
  );
}
