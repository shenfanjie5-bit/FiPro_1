import type {
  GenerateReportPayload,
  GenerateReportResponse,
  ReportResponse,
  RuntimeConfig,
  RuntimeConfigUpdatePayload
} from '../types/report';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ?? '';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  const body = (await response.json()) as unknown;
  if (!response.ok) {
    const detail = typeof (body as { detail?: unknown })?.detail === 'string'
      ? (body as { detail: string }).detail
      : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return body as T;
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
