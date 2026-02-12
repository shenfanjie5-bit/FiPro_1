export type Tier = 'TIER0' | 'TIER1' | 'TIER2';
export type RunMode = 'LIVE' | 'SHADOW' | 'BACKTEST';
export type LlmProvider = 'mock' | 'openai' | 'openai_compatible';

export interface GenerateReportPayload {
  ticker: string;
  market: string;
  asof: string;
  strategy_version_id: string;
  tier: Tier;
  run_mode?: RunMode;
}

export interface GenerateReportResponse {
  report_id: string;
  final_report: Record<string, unknown>;
}

export interface ReportResponse {
  report_id: string;
  final_report: Record<string, unknown>;
}

export interface RuntimeConfig {
  default_run_mode: RunMode;
  llm_provider: LlmProvider;
  llm_base_url: string;
  llm_primary_model: string;
  llm_reviewer_model: string;
  llm_shadow_model: string;
  llm_shadow_reviewer_model: string;
  llm_api_key_set: boolean;
  llm_api_key_masked: string;
}

export interface RuntimeConfigUpdatePayload {
  default_run_mode: RunMode;
  llm_provider: LlmProvider;
  llm_base_url: string;
  llm_primary_model: string;
  llm_reviewer_model: string;
  llm_shadow_model: string;
  llm_shadow_reviewer_model: string;
  llm_api_key?: string;
}
