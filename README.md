# FiPro_1 使用手册

FiPro_1 是一个面向研究与回测场景的 Agentic Research Workbench。  
当前仓库已经包含：

- FastAPI 后端（工作流编排、报告生成、持久化、反馈）
- React 前端 GUI（启动配置页、生成页、回测页、提案评审页、Champion 监控页、数据源总览页、结果页）
- 运行时大模型配置（页面按 `.env` 预置方案选择 provider/主模型/影子模型，连接参数自动联动）
- 多级降级与数据质量标注（数据源或 LLM 不可用时可继续产出）
- BACKTEST 自动沉淀 skills（后续决策可复用本地经验）
- 数据源状态总览（红/黄/绿灯 + 数据源明细）

---

## 1. 项目能力概览

- 研究报告生成：`POST /reports/generate`
- 批量回测：`POST /backtests/run`
- 组合回测：`POST /backtests/portfolio/run`
- Candidate 生成：`POST /skill-packs/candidates/generate`
- LLM 提案回测闭环：`POST /skill-packs/proposals/llm-run`
- LLM 提案运行查询：`GET /skill-packs/proposals/runs` / `GET /skill-packs/proposals/runs/{run_id}`
- Champion 健康检查：`POST /skill-packs/champion/health-check`
- Champion 健康检查查询：`GET /skill-packs/champion/health-checks` / `GET /skill-packs/champion/health-checks/{run_id}`
- Champion Watchdog：`POST /skill-packs/champion/watchdog/run`
- Champion Watchdog 查询：`GET /skill-packs/champion/watchdog/runs` / `GET /skill-packs/champion/watchdog/runs/{run_id}`
- Champion Watchdog 告警闭环：`GET /skill-packs/champion/watchdog/alerts*` / `POST /skill-packs/champion/watchdog/alerts/{alert_id}/ack` / `POST /skill-packs/champion/watchdog/alerts/{alert_id}/close`
- Champion Watchdog 自动工单：`GET /skill-packs/champion/watchdog/tickets*`
- 发布审计查询：`GET /skill-packs/releases` / `GET /skill-packs/releases/{event_id}`
- Champion 切换/回滚：`POST /skill-packs/{skill_pack_id}/champion/switch`
- 报告查询：`GET /reports/{report_id}`
- 报告反馈：`POST /reports/{report_id}/feedback`
- 运行时配置：
  - `GET /runtime/config`
  - `PUT /runtime/config`
- 数据源状态：
  - `GET /datasources/status`
- 因子接口注册表（Tushare 全量目录）：
  - `GET /factors/registry`
- 本地数据统一读取网关（endpoint+ticker+时间窗）：
  - `POST /local-data/batch-query`
- GUI 路由：
  - `/startup` 启动配置页
  - `/generate` 生成页
  - `/backtest` 批量回测页
  - `/proposals` 提案评审页
  - `/champion-health` Champion 监控页
  - `/datasources` 数据源总览页
  - `/results/:reportId` 结果页
- 兼容旧版内置 HTML 页：`/gui`

### 1.1 策略演进标准文档

为支持“LLM 提案 + 规则公式可插拔 + 回测门禁晋级”，新增标准文档：

- `docs/BACKTEST_SKILL_CARTRIDGE_SPEC.md`

该文档定义了：

- 双层决策架构（LLM Analyst + Deterministic Engine）
- 因子与权重标准（含“权重可为 0”）
- Tushare 多接口扩展与接入门禁
- Skill Cartridge 文件结构与版本治理
- Candidate/Champion 晋级规则与评估门槛
- 机构化改造总方案：`docs/INSTITUTIONAL_TRANSFORMATION_PLAN.md`

参数定标执行模板（机器可读）：

- `skill_packs/cn_a_core/0.1.0/calibration.json`
- 校验命令：`python scripts/validate_calibration_profile.py --skill-pack-id cn_a_core --version 0.1.0`

---

## 2. 运行模式与核心概念

### Run Mode

- `LIVE`: 常规生产模式
- `SHADOW`: 影子模式（用于 challenger 观察）
- `BACKTEST`: 回测模式，会触发技能沉淀

### Tier

- `TIER0`: 成本低、链路短
- `TIER1`: 增加 RAG/图查询等增强流程
- `TIER2`: 更深度流程（当前以渐进增强为主）

### Data Quality

- `OK`: 数据完整可用
- `PARTIAL`: 部分字段缺失/部分链路降级
- `DEGRADED`: 上游不可用或关键链路降级，报告可能为保守 fallback

---

## 3. 系统架构（简版）

- 后端入口：`app/main.py`
- API 路由：`app/api/routes.py`
- 工作流图：`app/workflows/graph.py`
- 工作流节点：`app/workflows/nodes.py`
- LLM 适配：`app/llm/provider.py`
- 运行时配置：`app/core/runtime_config.py`
- 数据源工具：`app/tools/facts.py`
- 技能存储工具：`app/tools/skills.py`
- 前端入口：`frontend/src/main.tsx`

---

## 4. 环境要求

- Python 3.11+
- Node.js 18+（推荐 20+）
- Docker + Docker Compose（推荐用于本地依赖服务）
- 可选：`uv`（用于统一运行命令）

---

## 5. 快速启动（推荐）

### 5.1 准备环境变量

```bash
cp .env.example .env
```

最少需要保证以下字段可用（不能为空）：

- `DATABASE_URL`
- `REDIS_URL`
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`

`.env.example` 已给出一套本地默认值。

### 5.2 启动依赖服务

```bash
docker compose up -d
```

会启动：

- Postgres (pgvector): `localhost:5432`
- Redis: `localhost:6379`
- Neo4j: `localhost:7687` / `localhost:7474`

### 5.3 安装 Python 依赖

方式 A（pip）：

```bash
python -m pip install -U pip
python -m pip install -e .
```

方式 B（uv）：

```bash
uv sync
```

### 5.4 数据库迁移

```bash
alembic upgrade head
```

### 5.5 启动后端

```bash
uvicorn app.main:app --reload --port 8000
```

### 5.6 启动前端

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

### 5.7 打开页面

- 前端 GUI: <http://127.0.0.1:5173>
- 后端健康检查: <http://127.0.0.1:8000/health>
- 后端内置 HTML: <http://127.0.0.1:8000/gui>

---

## 6. GUI 使用流程

### Step 1: 启动配置页 `/startup`

可配置：

- `LLM Provider`（来自 `.env` 预置方案，如 `mock` / `openclaw`）
- 主模型（LIVE）与影子模型（SHADOW）

说明：

- Provider 与模型候选都来自后端返回的 `llm_profiles` 预置项（环境变量驱动）。
- 选择不同 Provider 方案时，`provider/base_url/api_key` 会自动切换，无需在 GUI 单独编辑。
- 模型候选最多展示 3 个非空模型。
- 启动页不再编辑 `default_run_mode`，默认运行模式由后端运行时配置决定。

保存后会调用 `PUT /runtime/config`，配置立即生效（进程级内存覆盖）。

页面顶部提供“数据状态”总按钮：

- 绿灯：全部数据源已完成更新
- 黄灯：至少一个数据源正在更新
- 红灯：存在异常

点击按钮可查看每个数据源明细（当前已接入：`TUSHARE数据`、`Champion监控`）。

### Step 2: 生成页 `/generate`

填写：

- `ticker`
- `market`
- `asof`
- `strategy_version_id`
- `tier`
- `run_mode`（仅 `LIVE` / `SHADOW`）
- `thread_id`（可选）

点击 `Generate and Open Result` 后跳转结果页。

### Step 2.5: 批量回测页 `/backtest`

填写：

- `ticker` / `market` / `strategy_version_id` / `tier`
- `start_date` / `end_date`
- `step_days`（采样步长）
- `trading_days_only`（跳过周末，并对非周末停市日做交易日校验）
- `asof_time` / `timezone_offset`
- `max_runs`（批次上限）
- `evaluation_horizon_days`（前瞻收益评估窗口）
- 成本模型参数（可选）：
  - `transaction_fee_bps`
  - `slippage_bps`
  - `sell_tax_bps`

点击 `Run Batch Backtest` 后可看到：

- 批次汇总（成功/失败、动作分布、均值指标、命中率）
- 固定初始资金 `¥1,000,000` 下的策略最终收益率
- 同期基准（按 market 自动选择）收益率与超额收益
- 策略 vs 基准 折线图（Equity Curve）
- 每个回测点的明细（report_id、action、DQ、forward return、错误信息）

### Step 2.6: 提案评审页 `/proposals`

可查看：

- LLM 提案运行列表（`run_id`、时间、基线版本、选中候选版本、是否执行）
- 单次运行详情（候选评估结果、gate 决策、稳健性门禁结果）
- 原始运行 JSON（便于复盘与审计）

### Step 2.7: Champion 监控页 `/champion-health`

可执行：

- 运行 champion 健康检查（当前 champion vs baseline）
- 触发可选自动回滚（支持 dry-run 演练）

可查看：

- 历史健康检查列表（健康状态、gate 决策、是否触发回滚）
- 单次检查详情（回测摘要、门禁明细、回滚执行信息）
- Watchdog 告警记录（状态、告警数、回滚建议）
- Watchdog 告警处理（ACK/关闭）
- Watchdog 自动工单列表（按运行自动生成）

### Step 3: 结果页 `/results/:reportId`

- 展示核心指标（action/score/confidence/data_quality）
- 展示完整 JSON
- 支持按 `report_id` 回查

---

## 7. API 使用示例

### 7.1 健康检查

```bash
curl http://127.0.0.1:8000/health
```

### 7.2 查看运行时配置

```bash
curl http://127.0.0.1:8000/runtime/config
```

### 7.2.1 查看数据源状态

```bash
curl http://127.0.0.1:8000/datasources/status
```

### 7.2.2 查看全量接口因子注册表

```bash
curl "http://127.0.0.1:8000/factors/registry?limit=200&offset=0"
```

### 7.2.3 查询本地数据切片（统一网关）

```bash
curl -X POST http://127.0.0.1:8000/local-data/batch-query \
  -H "Content-Type: application/json" \
  -d '{
    "endpoints": ["daily", "trade_cal"],
    "ticker": "600519.SH",
    "start_date": "20260101",
    "end_date": "20260212",
    "limit_per_endpoint": 10,
    "max_endpoints": 16,
    "order": "desc",
    "include_rows": true
  }'
```

### 7.3 更新运行时配置

```bash
curl -X PUT http://127.0.0.1:8000/runtime/config \
  -H "Content-Type: application/json" \
  -d '{
    "default_run_mode": "LIVE",
    "llm_provider": "openai",
    "llm_base_url": "https://api.openai.com/v1",
    "llm_primary_model": "gpt-4o-mini",
    "llm_reviewer_model": "NONE",
    "llm_shadow_model": "gpt-4o-mini",
    "llm_shadow_reviewer_model": "NONE",
    "llm_api_key": "sk-xxxx"
  }'
```

### 7.4 生成报告

```bash
curl -X POST http://127.0.0.1:8000/reports/generate \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "600519.SH",
    "market": "CN_A",
    "asof": "2026-02-10T09:30:00+08:00",
    "strategy_version_id": "stg_v1",
    "tier": "TIER0",
    "run_mode": "LIVE"
  }'
```

说明：

- `run_mode` 现在是可选字段。
- 如果不传，后端使用 `/runtime/config` 中的 `default_run_mode`。

### 7.5 查询报告

```bash
curl http://127.0.0.1:8000/reports/<report_id>
```

### 7.6 批量回测

```bash
curl -X POST http://127.0.0.1:8000/backtests/run \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "600519.SH",
    "market": "CN_A",
    "strategy_version_id": "stg_v1",
    "tier": "TIER0",
    "start_date": "2026-01-01",
    "end_date": "2026-01-31",
    "step_days": 1,
    "trading_days_only": true,
    "asof_time": "09:30",
    "timezone_offset": "+08:00",
    "max_runs": 60,
    "evaluation_horizon_days": 5,
    "initial_capital_cny": 1000000
  }'
```

说明：

- 批量接口会强制按 `BACKTEST` 模式运行每个点位。
- 返回值包含 `summary`（聚合指标）和 `runs`（逐点结果）。
- `summary` 额外包含：
  - `strategy_total_return_pct`
  - `strategy_gross_total_return_pct`
  - `benchmark_total_return_pct`
  - `excess_return_pct`
  - `strategy_final_capital_cny` / `benchmark_final_capital_cny`
  - `total_trade_cost_cny` / `avg_turnover`
- 返回值中 `equity_curve` 提供前端折线图数据。

### 7.6.1 组合回测（多标的）

```bash
curl -X POST http://127.0.0.1:8000/backtests/portfolio/run \
  -H "Content-Type: application/json" \
  -d '{
    "market": "CN_A",
    "strategy_version_id": "stg_v1",
    "tier": "TIER0",
    "start_date": "2026-01-01",
    "end_date": "2026-01-31",
    "step_days": 1,
    "trading_days_only": true,
    "asof_time": "09:30",
    "timezone_offset": "+08:00",
    "max_runs": 60,
    "evaluation_horizon_days": 5,
    "initial_capital_cny": 1000000,
    "portfolio": [
      {"ticker": "600519.SH", "weight": 0.6},
      {"ticker": "000001.SZ", "weight": 0.4}
    ]
  }'
```

说明：

- `portfolio` 支持最多 50 个标的，权重会自动归一化。
- 返回包含组合汇总收益、组合资金曲线，以及各组件回测摘要。

### 7.7 提交反馈

```bash
curl -X POST http://127.0.0.1:8000/reports/<report_id>/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "feedback_label": "USEFUL",
    "comment": "clear and actionable"
  }'
```

### 7.8 Skill Pack 晋级评估与执行

先查看版本与当前 champion：

```bash
curl http://127.0.0.1:8000/skill-packs/cn_a_core/versions
```

执行 candidate vs champion 回测评估，并尝试晋级：

```bash
curl -X POST http://127.0.0.1:8000/skill-packs/promotions/run \
  -H "Content-Type: application/json" \
  -d '{
    "skill_pack_id": "cn_a_core",
    "candidate_version": "0.1.0",
    "champion_version": "0.0.1",
    "execute": true,
    "dry_run": true,
    "manual_approved": false,
    "anti_overfit_evidence": {
      "train_window": {"start_date": "2018-01-01", "end_date": "2023-12-31"},
      "validation_window": {"start_date": "2024-01-01", "end_date": "2025-12-31"},
      "sensitivity": {"scenario_count": 8, "pass_rate": 0.82, "min_pass_rate": 0.70},
      "param_change_count": 3
    },
    "backtest": {
      "ticker": "600519.SH",
      "market": "CN_A",
      "strategy_version_id": "stg_v1",
      "tier": "TIER0",
      "start_date": "2026-01-01",
      "end_date": "2026-01-31",
      "step_days": 1,
      "trading_days_only": true,
      "asof_time": "09:30",
      "timezone_offset": "+08:00",
      "max_runs": 60,
      "evaluation_horizon_days": 5
    }
  }'
```

说明：

- `dry_run=true` 时只评估不改状态。
- 当 `gate.json` 配置 `manual_approval_required=true` 且 `manual_approved=false`，决策会是 `PENDING_MANUAL_APPROVAL`。
- 当 `gate.json.anti_overfit` 开启强门禁时，`anti_overfit_evidence` 缺失或不达标会直接 `BLOCK`。
- 默认 anti-overfit 还包含稳健性门禁：`walk_forward_stability` 与 `bootstrap_significance`。

### 7.9 按定标模板自动生成 Candidate 版本

先预览将要生成哪些 candidate（不写文件）：

```bash
curl -X POST http://127.0.0.1:8000/skill-packs/candidates/generate \
  -H "Content-Type: application/json" \
  -d '{
    "skill_pack_id": "cn_a_core",
    "base_version": "champion",
    "calibration_version": "0.1.0",
    "max_candidates": 4,
    "author": "auto_calibration",
    "dry_run": true
  }'
```

确认后执行写入：

```bash
python scripts/generate_skill_pack_candidates.py \
  --skill-pack-id cn_a_core \
  --base-version champion \
  --calibration-version 0.1.0 \
  --max-candidates 4
```

### 7.10 手动切换/回滚 Champion

```bash
curl -X POST http://127.0.0.1:8000/skill-packs/cn_a_core/champion/switch \
  -H "Content-Type: application/json" \
  -d '{
    "target_version": "0.0.1",
    "reason": "rollback_after_regression",
    "operator": "qa_user",
    "dry_run": false
  }'
```

说明：

- 该接口用于手动切换 champion（含回滚场景）。
- 变更会写入目标版本与旧 champion 的 `manifest.json` 审计字段 `champion_switch`。
- 同时会落发布事件审计（`release_event_id`），可通过发布审计查询接口回看。

### 7.11 LLM 提案 + 候选回测 + 选优（闭环）

该接口会按以下顺序执行：

1. 读取 base skill pack（默认 champion）
2. 调用 LLM 生成若干提案（规则/公式/权重变更）
3. 落地 candidate 版本
4. 跑 base 与 candidate 回测
5. 走 promotion gate 评估
6. 由 LLM 在候选评估结果中选出推荐 candidate（仍是提案，不会绕过门禁）

```bash
curl -X POST http://127.0.0.1:8000/skill-packs/proposals/llm-run \
  -H "Content-Type: application/json" \
  -d '{
    "skill_pack_id": "cn_a_core",
    "base_version": "champion",
    "proposal_count": 2,
    "author": "llm_proposer",
    "execute": false,
    "manual_approved": false,
    "anti_overfit_evidence": {
      "train_window": {"start_date": "2018-01-01", "end_date": "2023-12-31"},
      "validation_window": {"start_date": "2024-01-01", "end_date": "2025-12-31"},
      "sensitivity": {"scenario_count": 8, "pass_rate": 0.82, "min_pass_rate": 0.70}
    },
    "backtest": {
      "ticker": "600519.SH",
      "market": "CN_A",
      "strategy_version_id": "stg_v1",
      "tier": "TIER0",
      "start_date": "2026-01-01",
      "end_date": "2026-01-31",
      "step_days": 1,
      "trading_days_only": true,
      "asof_time": "09:30",
      "timezone_offset": "+08:00",
      "max_runs": 60,
      "evaluation_horizon_days": 5
    }
  }'
```

### 7.12 查询 LLM 提案运行记录

```bash
curl "http://127.0.0.1:8000/skill-packs/proposals/runs?limit=20&offset=0"
```

```bash
curl "http://127.0.0.1:8000/skill-packs/proposals/runs/<run_id>"
```

### 7.13 Champion 健康检查与自动回滚

```bash
curl -X POST http://127.0.0.1:8000/skill-packs/champion/health-check \
  -H "Content-Type: application/json" \
  -d '{
    "skill_pack_id": "cn_a_core",
    "champion_version": "0.1.4",
    "baseline_version": "0.1.3",
    "auto_rollback": true,
    "rollback_dry_run": true,
    "rollback_reason": "monitoring_gate_block",
    "operator": "monitor_engine",
    "manual_approved": true,
    "anti_overfit_evidence": {
      "train_window": {"start_date": "2018-01-01", "end_date": "2023-12-31"},
      "validation_window": {"start_date": "2024-01-01", "end_date": "2025-12-31"},
      "sensitivity": {"scenario_count": 8, "pass_rate": 0.82, "min_pass_rate": 0.70},
      "param_change_count": 3
    },
    "backtest": {
      "ticker": "600519.SH",
      "market": "CN_A",
      "strategy_version_id": "stg_v1",
      "tier": "TIER0",
      "start_date": "2026-01-01",
      "end_date": "2026-01-31",
      "step_days": 1,
      "trading_days_only": true,
      "asof_time": "09:30",
      "timezone_offset": "+08:00",
      "max_runs": 60,
      "evaluation_horizon_days": 5
    }
  }'
```

查询记录：

```bash
curl "http://127.0.0.1:8000/skill-packs/champion/health-checks?limit=20&offset=0"
curl "http://127.0.0.1:8000/skill-packs/champion/health-checks/<run_id>"
```

### 7.14 查询发布审计事件（Release Timeline）

```bash
curl "http://127.0.0.1:8000/skill-packs/releases?limit=20&offset=0"
curl "http://127.0.0.1:8000/skill-packs/releases/<event_id>"
```

### 7.15 运行 Champion Watchdog（告警清单 + 回滚建议）

仅基于历史记录评估：

```bash
curl -X POST http://127.0.0.1:8000/skill-packs/champion/watchdog/run \
  -H "Content-Type: application/json" \
  -d '{
    "run_health_check": false,
    "lookback_runs": 20,
    "consecutive_fail_critical": 2,
    "fail_rate_warn": 0.25,
    "fail_rate_critical": 0.5,
    "rollback_storm_critical": 2,
    "auto_create_ticket": true
  }'
```

先跑一次健康检查再评估：

```bash
curl -X POST http://127.0.0.1:8000/skill-packs/champion/watchdog/run \
  -H "Content-Type: application/json" \
  -d '{
    "run_health_check": true,
    "health_check": {
      "skill_pack_id": "cn_a_core",
      "champion_version": "0.1.4",
      "baseline_version": "0.1.3",
      "auto_rollback": false,
      "rollback_dry_run": true,
      "rollback_reason": "monitoring_gate_block",
      "operator": "monitor_engine",
      "manual_approved": true,
      "anti_overfit_evidence": {},
      "backtest": {
        "ticker": "600519.SH",
        "market": "CN_A",
        "strategy_version_id": "stg_v1",
        "tier": "TIER0",
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "step_days": 1,
        "trading_days_only": true,
        "asof_time": "09:30",
        "timezone_offset": "+08:00",
        "max_runs": 60,
        "evaluation_horizon_days": 5
      }
    }
  }'
```

查询 Watchdog 运行记录：

```bash
curl "http://127.0.0.1:8000/skill-packs/champion/watchdog/runs?limit=20&offset=0"
curl "http://127.0.0.1:8000/skill-packs/champion/watchdog/runs/<run_id>"
```

查询 Watchdog 告警（支持 `status=OPEN|ACKED|CLOSED`）：

```bash
curl "http://127.0.0.1:8000/skill-packs/champion/watchdog/alerts?limit=50&offset=0&status=OPEN"
curl "http://127.0.0.1:8000/skill-packs/champion/watchdog/alerts/<alert_id>"
```

ACK / 关闭告警：

```bash
curl -X POST "http://127.0.0.1:8000/skill-packs/champion/watchdog/alerts/<alert_id>/ack" \
  -H "Content-Type: application/json" \
  -d '{"operator":"monitor_engine","note":"ack"}'

curl -X POST "http://127.0.0.1:8000/skill-packs/champion/watchdog/alerts/<alert_id>/close" \
  -H "Content-Type: application/json" \
  -d '{"operator":"monitor_engine","note":"close"}'
```

查询 Watchdog 自动工单：

```bash
curl "http://127.0.0.1:8000/skill-packs/champion/watchdog/tickets?limit=50&offset=0"
curl "http://127.0.0.1:8000/skill-packs/champion/watchdog/tickets/<ticket_id>"
```

### 7.16 Watchdog 定时脚本（建议接入调度）

脚本路径：

- `/Users/fanjie/Documents/github/FiPro_1/scripts/champion_watchdog.py`

示例：

```bash
/Users/fanjie/Documents/github/FiPro_1/.venv/bin/python \
  /Users/fanjie/Documents/github/FiPro_1/scripts/champion_watchdog.py \
  --lookback-runs 20 \
  --consecutive-fail-critical 2 \
  --fail-rate-warn 0.25 \
  --fail-rate-critical 0.5 \
  --rollback-storm-critical 2 \
  --output-json /Users/fanjie/Documents/github/FiPro_1/monitoring/dashboards/champion_watchdog.json \
  --output-md /Users/fanjie/Documents/github/FiPro_1/monitoring/dashboards/champion_watchdog.md
```

---

## 8. LLM 配置说明

### Provider

- `mock`: 本地可控生成，不调用外部模型
- `openai`: 调用 OpenAI 风格 `/chat/completions`
- `openai_compatible`: 调用兼容 OpenAI 接口的网关

### 关键参数

- `LLM_PROFILES`（例如：`mock,openclaw`）
- `LLM_PROFILE_ID`（默认选中的方案）
- `LLM_PROFILE_<NAME>_LABEL`
- `LLM_PROFILE_<NAME>_PROVIDER`
- `LLM_PROFILE_<NAME>_BASE_URL`
- `LLM_PROFILE_<NAME>_API_KEY`
- `LLM_PROFILE_<NAME>_PRIMARY_MODEL`
- `LLM_PROFILE_<NAME>_REVIEWER_MODEL`
- `LLM_PROFILE_<NAME>_SHADOW_MODEL`
- `LLM_PROFILE_<NAME>_SHADOW_REVIEWER_MODEL`
- `OPENCLAW_SESSION_NAMESPACE`（OpenClaw 会话隔离命名空间，可选，默认 `fipro1`）
- `OPENCLAW_AGENT_ID`（显式指定 OpenClaw agent，可选；不填则由模型名推断）
- `OPENCLAW_SESSION_KEY`（强制固定会话 key，可选）
- `LLM_ALLOW_RUNTIME_CONNECTION_OVERRIDE`（默认 `false`；关闭时运行时只允许选预置 profile/model，不允许改 `provider/base_url/api_key`）

运行可靠性（可选）：

- `BACKTEST_JOBS_STATE_FILE`（回测任务状态落盘文件，默认 `.run/backtest_jobs_state.json`）
- `WORKFLOW_CHECKPOINT_AUTO_MAINTAIN`（默认 `true`，启动时自动维护 checkpoint DB）
- `WORKFLOW_CHECKPOINT_COMPACT_THRESHOLD_MB`（默认 `512`，超过阈值触发 checkpoint 压缩）
- `VITE_API_TIMEOUT_MS`（前端 API 请求超时，默认 `45000` 毫秒）

兼容单配置（可选）：

- `LLM_PROVIDER`
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_PRIMARY_MODEL`
- `LLM_REVIEWER_MODEL`
- `LLM_SHADOW_MODEL`
- `LLM_SHADOW_REVIEWER_MODEL`

### 重要行为

- 非 `mock` provider 且没有 API key 时，LLM 调用会失败并触发降级路径。
- 页面配置优先于环境变量（同一进程内即时生效）。
- 当 provider=OpenClaw（OpenAI 兼容）时，请求会自动附加 `x-openclaw-agent-id` 与 `x-openclaw-session-key`，用于会话隔离，避免不同运行互相污染。
- 运行时配置接口默认禁止覆盖 `llm_provider/llm_base_url/llm_api_key`，仅允许选预置方案与预置模型，减少错配风险。

---

## 9. 数据源与“显示不一致”的原因

当 Tushare token 无效、接口失败或超时时，系统会启用 synthetic fallback 数据，并在报告中留下数据质量信息：

- `data_quality.status` 可能变为 `PARTIAL` 或 `DEGRADED`
- `data_quality.notes` 会带上上游错误原因
- `provenance` / `evidence_refs` 会反映 fallback 来源

这就是“页面数据显示与真实行情不一致”的主要原因。

---

## 9.1 Tushare 历史库增量更新（项目内置）

全量历史下载完成后，建议改为增量模式，不再重复全量拉取。

脚本：

- `/Users/fanjie/Documents/github/FiPro_1/scripts/tushare_incremental_update.py`

默认策略：

- 每天按“昨日窗口”增量更新交易类接口
- 参考交易日历（`trade_cal`）判断：若昨日非交易日，交易类接口可跳过
- 新闻/语料相关接口（如 `news` / `research_report` / `npr`）周末也会更新
- 每 3 个交易日做一次完整性检查（是否缺最后交易日数据）

运行示例：

```bash
/Users/fanjie/Documents/github/FiPro_1/.venv/bin/python \
  /Users/fanjie/Documents/github/FiPro_1/scripts/tushare_incremental_update.py \
  --root /Volumes/dockcase2tb/database_all
```

产物：

- 最近一次运行摘要：`/Volumes/dockcase2tb/database_all/_meta/manifests/tushare_incremental_last_run.json`
- 增量状态：`/Volumes/dockcase2tb/database_all/_meta/checkpoints/tushare_incremental_state.json`
- 完整性检查：`/Volumes/dockcase2tb/database_all/_meta/qa/incremental_completeness_YYYYMMDD.csv`

---

## 9.2 定时任务（12:00 / 23:00）

本机 macOS 已安装 `launchd` 任务：

- `/Users/fanjie/Library/LaunchAgents/com.fipro1.tushare.incremental.plist`
- Label：`com.fipro1.tushare.incremental`
- 触发时间：每天 `12:00` 与 `23:00`

查看状态：

```bash
launchctl list | rg com.fipro1.tushare.incremental
launchctl print gui/$(id -u)/com.fipro1.tushare.incremental
```

更多（含 Win11 任务计划）见：

- `/Users/fanjie/Documents/github/FiPro_1/docs/TUSHARE_INCREMENTAL_SCHEDULE.md`

---

## 10. Skills（回测经验沉淀）机制

当前策略：

- 工作流在构建上下文时会检索本地 skills
- `BACKTEST` 模式下，报告会自动蒸馏并写入 `skills_runtime`
- 后续决策可将这些 skill 作为稳定先验输入给模型

默认存储位置：

- `WORKFLOW_RUNTIME_DB` 或 `WORKFLOW_CHECKPOINT_DB`
- 默认文件：`checkpoint.db`

### 10.1 Skill Pack 可执行模板（规则/公式/门禁）

已内置第一版可执行模板：

- `skill_packs/cn_a_core/0.1.0/manifest.json`
- `skill_packs/cn_a_core/0.1.0/factors.json`
- `skill_packs/cn_a_core/0.1.0/formula.json`
- `skill_packs/cn_a_core/0.1.0/policy.json`
- `skill_packs/cn_a_core/0.1.0/risk.json`
- `skill_packs/cn_a_core/0.1.0/llm_mapping.json`
- `skill_packs/cn_a_core/0.1.0/gate.json`

可直接运行校验命令：

```bash
uv run python scripts/validate_skill_pack.py --skill-pack-id cn_a_core --version 0.1.0
```

回测接口支持携带并加载模板：

- `skill_pack_id`（默认 `cn_a_core`）
- `skill_pack_version`（默认 `champion`，若无 champion 则回退 `0.1.0`）
- 当前工作流评分节点已优先采用模板中的 `factors/formula/policy/risk` 进行计算。
- `LIVE` 模式下会强制绑定 champion（忽略显式传入的非 champion 版本）。

---

## 11. 测试与构建

### 后端测试

```bash
uv run pytest
```

或：

```bash
pytest
```

### 前端构建验证

```bash
npm --prefix frontend run build
```

---

## 12. 评估与运维命令

常用命令（见 `Makefile`）：

```bash
make up
make run
make test
make migrate
make eval-m6
make eval-m7
```

M7 分阶段命令：

```bash
make eval-m7-dataset
make eval-m7-offline
make eval-m7-shadow
make eval-m7-drift
make eval-m7-gate
make eval-m7-review
```

---

## 13. 常见问题排查

### 13.1 启动时报 `Missing required settings`

检查 `.env` 是否已配置且包含必填项：

- `DATABASE_URL`
- `REDIS_URL`
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`

### 13.2 前端页面打开但接口报错

检查：

- 后端是否在 `127.0.0.1:8000` 运行
- Vite 代理是否生效（`frontend/vite.config.ts`）
- 是否跨域或 base url 配置错误（`VITE_API_BASE_URL`）

### 13.3 LLM 无法调用

检查：

- provider 是否是 `mock`
- 非 `mock` 时是否设置了 API key
- base URL 是否以 `/v1` 结尾且可达
- 模型名是否正确

### 13.4 报告看起来“太保守”

通常意味着进入了降级/fallback路径，请看：

- `data_quality.status`
- `data_quality.notes`
- `risk_flags`

---

## 14. 目录速览

```text
app/
  api/                 # FastAPI 路由
  core/                # 配置、日志、运行时配置
  db/                  # 数据库模型与迁移
  llm/                 # 模型 provider 适配
  tools/               # 数据源、图查询、记忆、skills、wrapper
  workflows/           # LangGraph 工作流
frontend/
  src/pages/           # Startup / Generate / Backtest / DataSources / Result 页面
docs/                  # 设计、规范、OpenAPI、Runbook
scripts/               # 评估与运维脚本
tests/                 # 自动化测试
```

---

## 15. 桌面端（Win11 / macOS）状态

- `desktop/` 目录当前为预留骨架
- 计划采用 Tauri 做跨平台桌面壳
- 当前可先使用 Web GUI（功能已可用）

---

## 16. 合同与规范文件

- OpenAPI: `docs/OPENAPI.yaml`
- 报告 Schema: `app/schemas/report.schema.json`
- 请求 Schema: `app/schemas/request.schema.json`
- 配置规范: `docs/CONFIG_SPEC.md`
- Runbook: `docs/RUNBOOK.md`

注：若运行时代码新增了端点但 OpenAPI 尚未同步，请以运行时代码为准。
