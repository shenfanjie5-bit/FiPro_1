# API_SPEC（M1 Contract-Aligned）

以 `docs/OPENAPI.yaml` 为唯一机器可读契约来源；本文件提供实现与联调视图。

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
- 返回（200）：
  - `report_id`
  - `final_report`（完整 Report 结构）

### 2.2 获取报告
- `GET /reports/{report_id}`
- 返回（200）：
  - `report_id`
  - `final_report`

### 2.3 策略版本
- `POST /strategies/{id}/versions`
- `GET /strategies/{id}/versions/{version}`

### 2.4 快照与观察清单
- `GET /tickers/{ticker}/snapshot?asof=...&strategy_version=...`
- `POST /watchlist`（add/update tier）
- `GET /watchlist`

### 2.5 图谱与记忆（MVP 可选）
- `GET /graph/subtree?ticker=...&depth=...`
- `GET /graph/paths?entity=...&ticker=...`
- `GET /memory/search?ticker=...&q=...`
- `POST /memory/write`

## 3. 对齐声明
- 若本文件与 `docs/OPENAPI.yaml` 冲突，以 `docs/OPENAPI.yaml` 为准。
