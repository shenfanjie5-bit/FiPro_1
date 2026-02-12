# FiPro_1 使用手册

FiPro_1 是一个面向研究与回测场景的 Agentic Research Workbench。  
当前仓库已经包含：

- FastAPI 后端（工作流编排、报告生成、持久化、反馈）
- React 前端 GUI（启动配置页、生成页、结果页）
- 运行时大模型配置（页面可改 provider/base_url/model/api_key）
- 多级降级与数据质量标注（数据源或 LLM 不可用时可继续产出）
- BACKTEST 自动沉淀 skills（后续决策可复用本地经验）

---

## 1. 项目能力概览

- 研究报告生成：`POST /reports/generate`
- 批量回测：`POST /backtests/run`
- 报告查询：`GET /reports/{report_id}`
- 报告反馈：`POST /reports/{report_id}/feedback`
- 运行时配置：
  - `GET /runtime/config`
  - `PUT /runtime/config`
- GUI 路由：
  - `/startup` 启动配置页
  - `/generate` 生成页
  - `/backtest` 批量回测页
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

- 默认 `Run Mode`
- `LLM Provider` (`mock` / `openai` / `openai_compatible`)
- `LLM Base URL`
- 主模型与影子模型名称
- API Key（后端仅返回 masked 状态）

保存后会调用 `PUT /runtime/config`，配置立即生效（进程级内存覆盖）。

### Step 2: 生成页 `/generate`

填写：

- `ticker`
- `market`
- `asof`
- `strategy_version_id`
- `tier`
- `run_mode`（可覆盖默认值）
- `thread_id`（可选）

点击 `Generate and Open Result` 后跳转结果页。

### Step 2.5: 批量回测页 `/backtest`

填写：

- `ticker` / `market` / `strategy_version_id` / `tier`
- `start_date` / `end_date`
- `step_days`（采样步长）
- `trading_days_only`（是否跳过周末）
- `asof_time` / `timezone_offset`
- `max_runs`（批次上限）
- `evaluation_horizon_days`（前瞻收益评估窗口）

点击 `Run Batch Backtest` 后可看到：

- 批次汇总（成功/失败、动作分布、均值指标、命中率）
- 固定初始资金 `¥1,000,000` 下的策略最终收益率
- 同期基准（按 market 自动选择）收益率与超额收益
- 策略 vs 基准 折线图（Equity Curve）
- 每个回测点的明细（report_id、action、DQ、forward return、错误信息）

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
  - `benchmark_total_return_pct`
  - `excess_return_pct`
  - `strategy_final_capital_cny` / `benchmark_final_capital_cny`
- 返回值中 `equity_curve` 提供前端折线图数据。

### 7.7 提交反馈

```bash
curl -X POST http://127.0.0.1:8000/reports/<report_id>/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "feedback_label": "USEFUL",
    "comment": "clear and actionable"
  }'
```

---

## 8. LLM 配置说明

### Provider

- `mock`: 本地可控生成，不调用外部模型
- `openai`: 调用 OpenAI 风格 `/chat/completions`
- `openai_compatible`: 调用兼容 OpenAI 接口的网关

### 关键参数

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

---

## 9. 数据源与“显示不一致”的原因

当 Tushare token 无效、接口失败或超时时，系统会启用 synthetic fallback 数据，并在报告中留下数据质量信息：

- `data_quality.status` 可能变为 `PARTIAL` 或 `DEGRADED`
- `data_quality.notes` 会带上上游错误原因
- `provenance` / `evidence_refs` 会反映 fallback 来源

这就是“页面数据显示与真实行情不一致”的主要原因。

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
- `skill_pack_version`（默认 `0.1.0`）
- 当前工作流评分节点已优先采用模板中的 `factors/formula/policy/risk` 进行计算。

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
  src/pages/           # Startup / Generate / Result 页面
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
