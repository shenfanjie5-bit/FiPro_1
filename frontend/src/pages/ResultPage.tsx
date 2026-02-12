import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { getReport } from '../api/client';
import type { ReportResponse } from '../types/report';

function extractNumber(value: unknown): string {
  return typeof value === 'number' ? value.toString() : '-';
}

function extractText(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : '-';
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
        setError('Missing report id in route.');
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
          const message = loadError instanceof Error ? loadError.message : 'Unknown load error';
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
      ? `Data quality is ${status}. Current report may include fallback or degraded dependencies.`
      : '';
  }, [dataQuality.status]);

  return (
    <main className="page-root">
      <div className="mesh" aria-hidden="true" />
      <section className="panel panel-intro">
        <p className="eyebrow">FiPro_1 GUI Seed</p>
        <h1>Report Result</h1>
        <p>Route id: <code>{reportId || 'N/A'}</code></p>
        <div className="actions-row">
          <Link className="ghost-link" to="/generate">
            Create Another Report
          </Link>
          {' · '}
          <Link className="ghost-link" to="/startup">
            Startup Configuration
          </Link>
        </div>
      </section>

      <section className="panel panel-metrics">
        <article>
          <h2>Action</h2>
          <p>{extractText(decision.action)}</p>
        </article>
        <article>
          <h2>Score</h2>
          <p>{extractNumber(decision.overall_score)}</p>
        </article>
        <article>
          <h2>Confidence</h2>
          <p>{extractNumber(decision.confidence)}</p>
        </article>
        <article>
          <h2>Data Quality</h2>
          <p>{extractText(dataQuality.status)}</p>
        </article>
      </section>

      {degradedHint ? (
        <section className="panel warning-panel">
          <p>{degradedHint}</p>
        </section>
      ) : null}

      <section className="panel panel-json">
        <h2>Raw JSON</h2>
        {loading ? <p>Loading report...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        <pre>{JSON.stringify(data ?? { report_id: reportId }, null, 2)}</pre>
      </section>
    </main>
  );
}
