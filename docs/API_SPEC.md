# API_SPEC（运行时对齐）

`docs/OPENAPI.yaml` 是目标机器契约来源；当前若与运行时代码不一致，以 `app/api/routes.py` 为准，并在后续同步 OpenAPI。

## 1. 通用约定
- 协议：REST JSON
- 鉴权：Bearer Token（MVP 可先 stub）
- 时间：ISO 8601（含时区）
- 错误结构：`{code,message,retryable}`

## 2. 核心接口

### 2.1 生成报告（触发工作流）
- `POST /reports/generate`
- 请求体（最小）：
  - `ticker`
  - `asof`
  - `strategy_version_id`
  - `tier`
  - `run_mode`（可选）
- 请求体（可选高级分析参数）：
  - `analysis_mode`: `BASELINE` | `TA_HYBRID` | `AUTO`
  - `ta_hybrid_mode`: `OFF` | `ANALYZE_ONLY` | `BLEND`
  - `ta_research_rounds`: `1~3`
  - `ta_risk_rounds`: `1~3`
  - `ta_llm_call_cap`: `0~20`
  - `ta_require_evidence_refs`: `bool`
- 返回（200）：
  - `report_id`
  - `final_report`（完整 Report 结构）

### 2.2 获取报告
- `GET /reports/{report_id}`
- 返回（200）：
  - `report_id`
  - `final_report`

### 2.3 运行时配置
- `GET /runtime/config`
- `PUT /runtime/config`
- 说明：
  - GUI 启动页当前仅编辑 `llm_provider` / `llm_primary_model` / `llm_shadow_model`
  - `llm_base_url` 与 `llm_api_key` 可通过 API 更新

### 2.4 批量回测
- 同步执行：
  - `POST /backtests/run`
  - `POST /backtests/portfolio/run`
- 异步任务：
  - `POST /backtests/jobs`
  - `GET /backtests/jobs/{job_id}`
  - `POST /backtests/jobs/{job_id}/cancel`
- 可选成本模型参数：
  - `transaction_fee_bps`
  - `slippage_bps`
  - `sell_tax_bps`
- 可选高级分析参数（同 `/reports/generate`）：
  - `analysis_mode`
  - `ta_hybrid_mode`
  - `ta_research_rounds`
  - `ta_risk_rounds`
  - `ta_llm_call_cap`
  - `ta_require_evidence_refs`
- 组合回测补充：
  - `portfolio: [{ticker, weight?}]`（最多 50 个标的，权重自动归一化）

### 2.5 Skill Pack（候选/晋级/回滚）
- `GET /skill-packs/{skill_pack_id}/versions`
- `POST /skill-packs/candidates/generate`
- `POST /skill-packs/proposals/llm-run`
- `GET /skill-packs/proposals/runs`
- `GET /skill-packs/proposals/runs/{run_id}`
- `POST /skill-packs/champion/health-check`
- `GET /skill-packs/champion/health-checks`
- `GET /skill-packs/champion/health-checks/{run_id}`
- `POST /skill-packs/champion/watchdog/run`
- `GET /skill-packs/champion/watchdog/runs`
- `GET /skill-packs/champion/watchdog/runs/{run_id}`
- `GET /skill-packs/champion/watchdog/alerts`
- `GET /skill-packs/champion/watchdog/alerts/{alert_id}`
- `POST /skill-packs/champion/watchdog/alerts/{alert_id}/ack`
- `POST /skill-packs/champion/watchdog/alerts/{alert_id}/close`
- `GET /skill-packs/champion/watchdog/tickets`
- `GET /skill-packs/champion/watchdog/tickets/{ticket_id}`
- `GET /skill-packs/releases`
- `GET /skill-packs/releases/{event_id}`
- `POST /skill-packs/promotions/run`
- `POST /skill-packs/{skill_pack_id}/champion/switch`
- 行为约束：
  - `LIVE` 模式运行时强制绑定 champion skill（忽略显式非 champion 版本）。
  - `promotion gate` 默认启用稳健性门禁：`walk_forward_stability`、`bootstrap_significance`。
  - champion 健康检查可在 gate 失败时触发自动回滚（支持 `rollback_dry_run`）。
  - champion watchdog 可生成告警清单与回滚建议（支持“先跑健康检查再评估”）。
  - watchdog 运行默认可自动生成工单（`auto_create_ticket=true`），告警支持 ACK/关闭生命周期。

### 2.6 数据源可见化
- `GET /datasources/status`
- `GET /factors/registry`
- `POST /local-data/batch-query`
- 数据源总览会聚合 `TUSHARE` 与 `CHAMPION_WATCHDOG`：
  - `CHAMPION_WATCHDOG` 出现未关闭 `critical` 告警时，总状态转为 `ERROR`
  - 出现未关闭 `warning` 告警时，总状态转为 `UPDATING`

### 2.7 策略版本（保留）
- `POST /strategies/{id}/versions`
- `GET /strategies/{id}/versions/{version}`

### 2.8 快照与观察清单
- `GET /tickers/{ticker}/snapshot?asof=...&strategy_version=...`
- `POST /watchlist`（add/update tier）
- `GET /watchlist`

### 2.9 图谱与记忆（MVP 可选）
- `GET /graph/subtree?ticker=...&depth=...`
- `GET /graph/paths?entity=...&ticker=...`
- `GET /memory/search?ticker=...&q=...`
- `POST /memory/write`

### 2.10 M7 在线反馈
- `POST /reports/{report_id}/feedback`
  - `feedback_label`: `USEFUL` | `USELESS` | `FALSE_POSITIVE`
  - `comment`: 可选文本
- `GET /reports/{report_id}/feedback?limit=...`

## 3. 对齐声明
- 若本文件与运行时代码冲突，以运行时代码为准。
- 若本文件与 `docs/OPENAPI.yaml` 冲突，说明 OpenAPI 待同步。
