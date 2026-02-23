export type Tier = 'TIER0' | 'TIER1' | 'TIER2';
export type RunMode = 'LIVE' | 'SHADOW' | 'BACKTEST';
export type LlmProvider = 'mock' | 'openai' | 'openai_compatible';

export interface RuntimeLlmProfile {
  id: string;
  label: string;
  llm_provider: LlmProvider;
  llm_primary_model: string;
  llm_shadow_model: string;
  llm_available_models: string[];
  llm_api_key_set: boolean;
}

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
  llm_profile_id: string;
  llm_profiles: RuntimeLlmProfile[];
  llm_base_url: string;
  llm_primary_model: string;
  llm_reviewer_model: string;
  llm_shadow_model: string;
  llm_shadow_reviewer_model: string;
  llm_available_models: string[];
  llm_api_key_set: boolean;
  llm_api_key_masked: string;
}

export interface RuntimeConfigUpdatePayload {
  default_run_mode?: RunMode;
  llm_profile_id?: string;
  llm_provider?: LlmProvider;
  llm_base_url?: string;
  llm_primary_model?: string;
  llm_reviewer_model?: string;
  llm_shadow_model?: string;
  llm_shadow_reviewer_model?: string;
  llm_api_key?: string;
}

export interface BatchBacktestPayload {
  ticker: string;
  market: string;
  strategy_version_id: string;
  tier: Tier;
  start_date: string;
  end_date: string;
  step_days: number;
  trading_days_only: boolean;
  asof_time: string;
  timezone_offset: string;
  max_runs: number;
  evaluation_horizon_days?: number;
  benchmark_ticker?: string;
  initial_capital_cny?: number;
  thread_prefix?: string;
}

export interface BatchBacktestRunItem {
  index: number;
  asof: string;
  thread_id: string;
  status: 'COMPLETED' | 'FAILED';
  report_id: string;
  action: string;
  overall_score: number;
  confidence: number;
  data_quality_status: string;
  fallback: boolean;
  model_primary: string;
  tool_calls: number;
  cost_usd_est: number;
  latency_ms: number;
  wall_time_ms: number;
  skill_note_id: string;
  ticker_price: number | null;
  benchmark_ticker: string;
  benchmark_price: number | null;
  evaluation_horizon_days_used?: number;
  evaluation_horizon_source?: string;
  forward_return_pct: number | null;
  forward_return_note: string;
  error?: string;
}

export interface EquityCurvePoint {
  asof: string;
  capital_cny: number;
  nav: number;
  step_return_pct: number;
}

export interface BatchBacktestResponse {
  batch_id: string;
  started_at: string;
  finished_at: string;
  request: Record<string, unknown>;
  summary: {
    total_runs: number;
    completed_runs: number;
    failed_runs: number;
    cancelled: boolean;
    interrupted?: boolean;
    interruption_reason?: string;
    interrupted_at?: string;
    resumable?: boolean;
    processed_points: number;
    remaining_points: number;
    skipped_non_trading_runs: number;
    skipped_non_trading_dates: string[];
    action_counts: Record<string, number>;
    data_quality_counts: Record<string, number>;
    fallback_runs: number;
    avg_score: number;
    avg_confidence: number;
    avg_cost_usd_est: number;
    avg_latency_ms: number;
    avg_wall_time_ms: number;
    avg_evaluation_horizon_days?: number;
    median_evaluation_horizon_days?: number;
    min_evaluation_horizon_days?: number;
    max_evaluation_horizon_days?: number;
    evaluation_horizon_source_counts?: Record<string, number>;
    evaluated_forward_runs: number;
    avg_forward_return_pct: number;
    median_forward_return_pct: number;
    buy_signal_count: number;
    buy_hit_rate: number;
    avoid_signal_count: number;
    avoid_hit_rate: number;
    directional_signal_count: number;
    directional_hit_rate: number;
    initial_capital_cny: number;
    strategy_final_capital_cny: number;
    strategy_total_return_pct: number;
    benchmark_ticker: string;
    benchmark_final_capital_cny: number;
    benchmark_total_return_pct: number;
    excess_return_pct: number;
  };
  equity_curve: {
    base_currency: string;
    strategy: EquityCurvePoint[];
    benchmark: EquityCurvePoint[];
    benchmark_ticker: string;
  };
  resume_state?: Record<string, unknown> | null;
  runs: BatchBacktestRunItem[];
}

export type BacktestJobStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'CANCELLING'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'FAILED';

export interface BacktestJobProgress {
  generated_points: number;
  processed_points: number;
  completed_runs: number;
  failed_runs: number;
  skipped_non_trading_runs: number;
  current_date: string;
  last_outcome: string;
}

export interface BacktestJobResponse {
  job_id: string;
  status: BacktestJobStatus;
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at: string;
  cancel_requested: boolean;
  progress: BacktestJobProgress;
  result: BatchBacktestResponse | null;
  error: string;
  resumable?: boolean;
  resumed_from_job_id?: string;
}

export type DataSourceStatus = 'COMPLETED' | 'UPDATING' | 'ERROR';

export interface DataSourceStatusJob {
  pid: number;
  mode: string;
  command: string;
}

export interface DataSourceStatusItem {
  source_id: string;
  name: string;
  status: DataSourceStatus;
  label: string;
  message: string;
  updated_at_utc: string;
  last_success_at_utc: string;
  last_error_at_utc: string;
  running_jobs: DataSourceStatusJob[];
  meta?: Record<string, unknown>;
}

export interface DataSourceStatusResponse {
  generated_at_utc: string;
  overall_status: DataSourceStatus;
  overall_label: string;
  sources: DataSourceStatusItem[];
}

export interface LlmProposalRunListItem {
  run_id: string;
  generated_at: string;
  skill_pack_id: string;
  base_version: string;
  proposal_count: number;
  dry_run: boolean;
  selected_candidate_version: string;
  selected_decision?: string;
  selected_excess_return_delta_pct?: number;
  selected_segment_win_rate?: number;
  executed: boolean;
}

export interface LlmProposalRunListFilters {
  skill_pack_id?: string;
  executed?: boolean;
  dry_run?: boolean;
  selected_decision?: string;
  generated_after?: string;
  generated_before?: string;
}

export interface LlmProposalRunListResponse {
  total: number;
  limit: number;
  offset: number;
  summary?: {
    executed_runs?: number;
    dry_run_runs?: number;
    selected_decision_counts?: Record<string, number>;
    avg_selected_excess_return_delta_pct?: number;
    avg_selected_segment_win_rate?: number;
  };
  items: LlmProposalRunListItem[];
}

export interface LlmProposalRunDetail {
  run_id: string;
  generated_at?: string;
  skill_pack_id?: string;
  base_version?: string;
  proposal_count?: number;
  dry_run?: boolean;
  baseline_backtest?: {
    batch_id?: string;
    summary?: Record<string, unknown>;
  };
  candidate_generation?: Record<string, unknown>;
  candidate_evaluations?: Array<Record<string, unknown>>;
  selected_candidate?: Record<string, unknown>;
  execution?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface ChampionHealthCheckPayload {
  skill_pack_id: string;
  champion_version?: string;
  baseline_version?: string;
  auto_rollback: boolean;
  rollback_dry_run: boolean;
  rollback_reason: string;
  operator: string;
  manual_approved: boolean;
  anti_overfit_evidence?: Record<string, unknown>;
  backtest: BatchBacktestPayload;
}

export interface ChampionHealthCheckListItem {
  run_id: string;
  generated_at: string;
  skill_pack_id: string;
  champion_version: string;
  baseline_version: string;
  health_status: string;
  decision: string;
  auto_rollback: boolean;
  rollback_executed: boolean;
}

export interface ChampionHealthCheckListResponse {
  total: number;
  limit: number;
  offset: number;
  items: ChampionHealthCheckListItem[];
}

export interface ChampionHealthCheckDetail {
  run_id: string;
  generated_at: string;
  skill_pack_id: string;
  champion_version: string;
  baseline_version: string;
  health_status: string;
  evaluation: Record<string, unknown>;
  rollback_execution: Record<string, unknown> | null;
  champion_backtest: {
    batch_id?: string;
    request?: Record<string, unknown>;
    summary?: Record<string, unknown>;
  };
  baseline_backtest: {
    batch_id?: string;
    request?: Record<string, unknown>;
    summary?: Record<string, unknown>;
  };
  [key: string]: unknown;
}

export interface ChampionWatchdogRunPayload {
  run_health_check: boolean;
  health_check?: Record<string, unknown>;
  lookback_runs: number;
  consecutive_fail_critical: number;
  fail_rate_warn: number;
  fail_rate_critical: number;
  rollback_storm_critical: number;
  execute_rollback_on_recommendation: boolean;
  rollback_dry_run: boolean;
  rollback_reason: string;
  rollback_operator: string;
  auto_create_ticket: boolean;
}

export interface ChampionWatchdogRunListItem {
  run_id: string;
  generated_at: string;
  overall_status: string;
  alert_count: number;
  open_alert_count?: number;
  critical_open_alert_count?: number;
  warning_open_alert_count?: number;
  latest_health_status: string;
  latest_decision: string;
  should_rollback: boolean;
  rollback_target_version: string;
  rollback_executed?: boolean;
  rollback_release_event_id?: string;
  ticket_id?: string;
}

export interface ChampionWatchdogRunListResponse {
  total: number;
  limit: number;
  offset: number;
  items: ChampionWatchdogRunListItem[];
}

export interface ChampionWatchdogRunDetail {
  run_id: string;
  generated_at: string;
  overall_status: string;
  alert_count: number;
  open_alert_count?: number;
  critical_open_alert_count?: number;
  warning_open_alert_count?: number;
  thresholds: Record<string, unknown>;
  summary: Record<string, unknown>;
  alerts: ChampionWatchdogAlertItem[];
  rollback_recommendation: Record<string, unknown>;
  rollback_execution?: Record<string, unknown> | null;
  executed_health_check: Record<string, unknown> | null;
  ticket?: ChampionWatchdogTicketItem | null;
  alert_state_summary?: Record<string, number>;
  [key: string]: unknown;
}

export interface ChampionWatchdogAlertItem {
  alert_id: string;
  run_id: string;
  generated_at: string;
  overall_status?: string;
  severity: string;
  code: string;
  message: string;
  status: 'OPEN' | 'ACKED' | 'CLOSED' | string;
  acknowledged_at?: string;
  acknowledged_by?: string;
  ack_note?: string;
  closed_at?: string;
  closed_by?: string;
  close_note?: string;
  updated_at?: string;
}

export interface ChampionWatchdogAlertListResponse {
  total: number;
  limit: number;
  offset: number;
  status_filter?: string;
  summary?: Record<string, number>;
  items: ChampionWatchdogAlertItem[];
}

export interface ChampionWatchdogAlertActionPayload {
  operator: string;
  note: string;
}

export interface ChampionWatchdogTicketItem {
  ticket_id: string;
  created_at: string;
  status: string;
  severity: string;
  title: string;
  run_id?: string;
  alert_count?: number;
  alert_ids?: string[];
}

export interface ChampionWatchdogTicketListResponse {
  total: number;
  limit: number;
  offset: number;
  items: ChampionWatchdogTicketItem[];
}
