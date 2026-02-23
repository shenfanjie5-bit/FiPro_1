import type {
  BatchBacktestPayload,
  BacktestJobResponse,
  BatchBacktestResponse,
  ChampionHealthCheckDetail,
  ChampionHealthCheckListResponse,
  ChampionHealthCheckPayload,
  ChampionWatchdogAlertActionPayload,
  ChampionWatchdogAlertItem,
  ChampionWatchdogAlertListResponse,
  ChampionWatchdogRunDetail,
  ChampionWatchdogRunListResponse,
  ChampionWatchdogRunPayload,
  ChampionWatchdogTicketItem,
  ChampionWatchdogTicketListResponse,
  DataSourceStatusResponse,
  GenerateReportPayload,
  GenerateReportResponse,
  LlmProposalRunDetail,
  LlmProposalRunListFilters,
  LlmProposalRunListResponse,
  ReportResponse,
  RuntimeConfig,
  RuntimeConfigUpdatePayload
} from '../types/report';

const ENV_API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ?? '';
const API_BASE_URL = ENV_API_BASE_URL || (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '');
const API_TIMEOUT_MS = Number((import.meta.env.VITE_API_TIMEOUT_MS as string | undefined) ?? '45000');

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const timeoutMs = Number.isFinite(API_TIMEOUT_MS) && API_TIMEOUT_MS > 0 ? Math.trunc(API_TIMEOUT_MS) : 45000;
  const controller = init?.signal ? null : new AbortController();
  const timer = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      signal: controller?.signal ?? init?.signal
    });
  } catch (error) {
    if (timer !== null) {
      window.clearTimeout(timer);
    }
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(`请求超时（>${timeoutMs}ms）`);
    }
    if (error instanceof Error) {
      throw new Error(`网络请求失败：${error.message}`);
    }
    throw new Error('网络请求失败');
  }
  if (timer !== null) {
    window.clearTimeout(timer);
  }

  const raw = await response.text();
  let body: unknown = null;
  try {
    body = raw ? JSON.parse(raw) : null;
  } catch {
    body = raw;
  }
  if (!response.ok) {
    const detail = typeof (body as { detail?: unknown })?.detail === 'string'
      ? (body as { detail: string }).detail
      : typeof body === 'string' && body.trim()
        ? body
        : `请求失败（${response.status}）`;
    throw new Error(detail);
  }
  return (body ?? {}) as T;
}

export async function generateReport(
  payload: GenerateReportPayload,
  threadId?: string
): Promise<GenerateReportResponse> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json'
  };
  if (threadId?.trim()) {
    headers['x-thread-id'] = threadId.trim();
  }

  return request<GenerateReportResponse>('/reports/generate', {
    method: 'POST',
    headers,
    body: JSON.stringify(payload)
  });
}

export async function getReport(reportId: string): Promise<ReportResponse> {
  return request<ReportResponse>(`/reports/${encodeURIComponent(reportId)}`);
}

export async function getRuntimeConfig(): Promise<RuntimeConfig> {
  return request<RuntimeConfig>('/runtime/config');
}

export async function updateRuntimeConfig(payload: RuntimeConfigUpdatePayload): Promise<RuntimeConfig> {
  return request<RuntimeConfig>('/runtime/config', {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
}

export async function runBatchBacktest(payload: BatchBacktestPayload): Promise<BatchBacktestResponse> {
  return request<BatchBacktestResponse>('/backtests/run', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
}

export async function createBacktestJob(payload: BatchBacktestPayload): Promise<BacktestJobResponse> {
  return request<BacktestJobResponse>('/backtests/jobs', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
}

export async function getBacktestJob(jobId: string): Promise<BacktestJobResponse> {
  return request<BacktestJobResponse>(`/backtests/jobs/${encodeURIComponent(jobId)}`);
}

export async function cancelBacktestJob(jobId: string): Promise<BacktestJobResponse> {
  return request<BacktestJobResponse>(`/backtests/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST'
  });
}

export async function resumeBacktestJob(jobId: string): Promise<BacktestJobResponse> {
  return request<BacktestJobResponse>(`/backtests/jobs/${encodeURIComponent(jobId)}/resume`, {
    method: 'POST'
  });
}

export async function getDataSourceStatus(): Promise<DataSourceStatusResponse> {
  const payload = await request<unknown>('/datasources/status');
  const asRecord = (typeof payload === 'object' && payload !== null) ? payload as Record<string, unknown> : null;
  if (!asRecord || !Array.isArray(asRecord.sources) || typeof asRecord.overall_status !== 'string') {
    throw new Error('数据状态接口返回格式异常，请检查前端代理与后端服务。');
  }
  return payload as DataSourceStatusResponse;
}

export async function listLlmProposalRuns(
  limit = 50,
  offset = 0,
  filters: LlmProposalRunListFilters = {}
): Promise<LlmProposalRunListResponse> {
  const search = new URLSearchParams();
  search.set('limit', String(limit));
  search.set('offset', String(offset));
  if ((filters.skill_pack_id ?? '').trim()) {
    search.set('skill_pack_id', String(filters.skill_pack_id).trim());
  }
  if (typeof filters.executed === 'boolean') {
    search.set('executed', String(filters.executed));
  }
  if (typeof filters.dry_run === 'boolean') {
    search.set('dry_run', String(filters.dry_run));
  }
  if ((filters.selected_decision ?? '').trim()) {
    search.set('selected_decision', String(filters.selected_decision).trim());
  }
  if ((filters.generated_after ?? '').trim()) {
    search.set('generated_after', String(filters.generated_after).trim());
  }
  if ((filters.generated_before ?? '').trim()) {
    search.set('generated_before', String(filters.generated_before).trim());
  }
  return request<LlmProposalRunListResponse>(`/skill-packs/proposals/runs?${search.toString()}`);
}

export async function getLlmProposalRun(runId: string): Promise<LlmProposalRunDetail> {
  return request<LlmProposalRunDetail>(`/skill-packs/proposals/runs/${encodeURIComponent(runId)}`);
}

export async function runChampionHealthCheck(payload: ChampionHealthCheckPayload): Promise<ChampionHealthCheckDetail> {
  return request<ChampionHealthCheckDetail>('/skill-packs/champion/health-check', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
}

export async function listChampionHealthChecks(limit = 50, offset = 0): Promise<ChampionHealthCheckListResponse> {
  const search = new URLSearchParams();
  search.set('limit', String(limit));
  search.set('offset', String(offset));
  return request<ChampionHealthCheckListResponse>(`/skill-packs/champion/health-checks?${search.toString()}`);
}

export async function getChampionHealthCheck(runId: string): Promise<ChampionHealthCheckDetail> {
  return request<ChampionHealthCheckDetail>(`/skill-packs/champion/health-checks/${encodeURIComponent(runId)}`);
}

export async function runChampionWatchdog(payload: ChampionWatchdogRunPayload): Promise<ChampionWatchdogRunDetail> {
  return request<ChampionWatchdogRunDetail>('/skill-packs/champion/watchdog/run', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
}

export async function listChampionWatchdogRuns(limit = 50, offset = 0): Promise<ChampionWatchdogRunListResponse> {
  const search = new URLSearchParams();
  search.set('limit', String(limit));
  search.set('offset', String(offset));
  return request<ChampionWatchdogRunListResponse>(`/skill-packs/champion/watchdog/runs?${search.toString()}`);
}

export async function getChampionWatchdogRun(runId: string): Promise<ChampionWatchdogRunDetail> {
  return request<ChampionWatchdogRunDetail>(`/skill-packs/champion/watchdog/runs/${encodeURIComponent(runId)}`);
}

export async function listChampionWatchdogAlerts(
  limit = 100,
  offset = 0,
  status = ''
): Promise<ChampionWatchdogAlertListResponse> {
  const search = new URLSearchParams();
  search.set('limit', String(limit));
  search.set('offset', String(offset));
  if (status.trim()) {
    search.set('status', status.trim());
  }
  return request<ChampionWatchdogAlertListResponse>(`/skill-packs/champion/watchdog/alerts?${search.toString()}`);
}

export async function getChampionWatchdogAlert(alertId: string): Promise<ChampionWatchdogAlertItem> {
  return request<ChampionWatchdogAlertItem>(`/skill-packs/champion/watchdog/alerts/${encodeURIComponent(alertId)}`);
}

export async function acknowledgeChampionWatchdogAlert(
  alertId: string,
  payload: ChampionWatchdogAlertActionPayload
): Promise<ChampionWatchdogAlertItem> {
  return request<ChampionWatchdogAlertItem>(`/skill-packs/champion/watchdog/alerts/${encodeURIComponent(alertId)}/ack`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
}

export async function closeChampionWatchdogAlert(
  alertId: string,
  payload: ChampionWatchdogAlertActionPayload
): Promise<ChampionWatchdogAlertItem> {
  return request<ChampionWatchdogAlertItem>(`/skill-packs/champion/watchdog/alerts/${encodeURIComponent(alertId)}/close`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
}

export async function listChampionWatchdogTickets(limit = 100, offset = 0): Promise<ChampionWatchdogTicketListResponse> {
  const search = new URLSearchParams();
  search.set('limit', String(limit));
  search.set('offset', String(offset));
  return request<ChampionWatchdogTicketListResponse>(`/skill-packs/champion/watchdog/tickets?${search.toString()}`);
}

export async function getChampionWatchdogTicket(ticketId: string): Promise<ChampionWatchdogTicketItem> {
  return request<ChampionWatchdogTicketItem>(`/skill-packs/champion/watchdog/tickets/${encodeURIComponent(ticketId)}`);
}
