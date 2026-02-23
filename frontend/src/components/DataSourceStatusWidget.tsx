import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

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

export function DataSourceStatusWidget() {
  const navigate = useNavigate();
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
    <div className="data-status-widget">
      <button
        type="button"
        className="data-status-button"
        onClick={() => navigate('/datasources')}
        title="查看数据源总览"
      >
        <span className={`status-light ${statusClassName(overallStatus)}`} aria-hidden="true" />
        数据状态：{overallLabel}
      </button>
    </div>
  );
}
