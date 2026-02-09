# TECH_DESIGN 模板（可直接按模块开工）

## 2.1 总体架构（逻辑图）

建议用“服务化 + 可插拔模型层”的结构：

```text
[UI Web]
   | (REST/GraphQL)
[API Gateway]-----------------------------+
   |                                      |
[Config Service]                          |
[Research Orchestrator/Agent Service] <---+--- (calls tools)
   |             |            |
   |         [Tool Layer / Skills]--------------------+
   |             |            |                       |
[Data Ingestion] [Feature Compute] [Graph Service] [Memory Service]
   |             |            |                       |
[Raw Data Store] [Feature Store] [Graph DB]      [Relational+Vector DB]
```

## 2.2 技术选型（建议默认一套可落地栈）

你也可以换，但先定一套“可跑起来”的。

- 后端：Python（FastAPI）或 Node（NestJS）。
- 调度：Celery / APScheduler / Airflow（MVP 可用 APScheduler）。
- 数据库（事实与配置）：PostgreSQL。
- 向量检索（记忆/RAG）：pgvector（省一个系统）。
- 缓存：Redis。
- 图数据库（产业链）：Neo4j（或先用 Postgres 图表，再迁移）。
- 前端：React + Ant Design（图谱用 Cytoscape.js / ECharts Graph）。
- 可观测：OpenTelemetry +（可选）Langfuse 类 tracing。

## 2.3 数据流与数据分层

**数据分层**
- Raw Layer：原始数据（带 source、timestamp、checksum）。
- Clean Layer：清洗标准化后（统一字段、统一时区、缺失标记）。
- Feature Layer：因子/指标（可重复计算、可回测）。
- Snapshot Layer：给 LLM 的“事实快照”（精简、结构化、可引用）。
- Report Layer：LLM 输出与决策日志（可复盘）。

**核心原则**
- LLM 不算数：指标由 Feature 层生成。
- LLM 不凭空：只允许基于 Snapshot + 可检索证据生成结论。

## 2.4 Skills / Tools 层设计（skills 落地）

把所有能力做成可调用工具，LLM 负责组合。

**工具分类**
- 数据工具：行情/财务/资金/热度/宏观/大宗/运价。
- 图谱工具：上下游路径、暴露度、关联公司。
- 记忆工具：检索、写入、滚动摘要。
- 风控工具：评分、门禁、仓位/集中度约束。
- 报告工具：生成并校验 schema。

**工具接口签名（示例，写到 `SCHEMA.md`）**
- `get_market_snapshot(ticker, asof) -> MarketSnapshot`
- `get_feature_vector(ticker, asof, strategy_version) -> FeatureVector`
- `get_hotness(sector_or_theme, asof) -> HotnessPack`
- `search_events(query, asof_range, sources, top_k) -> EventDocs[]`
- `query_supply_chain(ticker, depth) -> GraphSubtree`
- `compute_exposure(ticker, entity) -> ExposureScore`
- `retrieve_memory(ticker, query, top_k, time_range) -> MemoryNotes[]`
- `write_memory(note: MemoryNote) -> ok`
- `score_signal(features, weights) -> Score`
- `risk_gate(score, risk_profile, constraints) -> GateResult`
- `generate_report(state) -> ReportJSON`
- `validate_report(report_json) -> ValidationResult`

## 2.5 LLM 编排（Agent Workflow）

**工作流（建议固定成图/状态机）**
- Load Config（策略版本、权重、风控）。
- Build Snapshot（工具拉数据 -> 拼事实快照）。
- Retrieve Memory（召回历史结论、被证伪点、关键变量）。
- Graph Reasoning（需要时调用图谱工具）。
- Draft Report（LLM 生成结构化报告）。
- Risk Gate（确定性门禁：不通过则降级 action）。
- Reviewer（可选第二模型复核：找矛盾/缺证据/违反风控）。
- Persist（写入 Report + Memory + Trace）。
- Publish（UI 展示、触发告警）。

**任务级模型路由（支持多模型/可替换）**
- 抽取/分类：便宜快模型或本地小模型。
- 深度推理/报告生成：强模型。
- 复核/审计：另一个模型或规则引擎。
- Shadow：新模型后台跑同输入，仅记录。

## 2.6 输出 Schema（UI、落库、复盘核心）

建议定义一个“强 schema”：
- `action`: BUY/WATCH/AVOID。
- `overall_score`: 0-100（来自确定性评分 + LLM 调整/解释，但受风控门禁约束）。
- `price_bands[]`: `{min,max,score,rationale,trigger_conditions}`。
- `key_drivers_to_watch[]`: `{type,what,direction,urgency,monitor,trigger,evidence_refs}`。
- `thesis`: 核心逻辑（短）。
- `risks[]`: 风险点。
- `invalidations[]`: 哪些条件发生则逻辑失效。
- `evidence_refs[]`: 引用哪些事实快照/文档/图谱查询结果。
- `memory_update`: 写入记忆的摘要与标签。

把它写进 `SCHEMA.md`，并在后端做 JSON Schema 校验（不通过不落库或标记为 invalid）。

## 2.7 存储设计（表结构建议）

**Postgres（核心）**
- `strategy_versions`：策略版本、权重、风控参数（immutable）。
- `tickers`：标的信息。
- `daily_snapshots`：事实快照（结构化 JSON + 引用指针）。
- `reports`：LLM 输出报告（结构化 JSON + schema_version）。
- `decision_logs`：动作、分数、置信度、当日上下文引用。
- `watchlist`：重点关注/持仓标记、关注等级（tier）。
- `event_docs`：新闻/公告/宏观事件文档元数据（可选）。
- `traces`：工具调用轨迹与耗时成本（可选但强烈建议）。

**向量（pgvector）**
- `memory_embeddings`：研究笔记/报告摘要的 embedding，支持相似检索。

**图谱（Neo4j）**
- 节点：`Company`、`Product`、`Commodity`、`Industry`、`Region`、`Port`、`Route`、`PolicyEvent`。
- 边：`SUPPLIES`、`DEPENDS_ON`、`COMPETES_WITH`、`SHIPS_THROUGH`、`AFFECTED_BY`。

## 2.8 产业链图谱构建与更新

- 数据来源：你的行业分类、研报结构化、公告、手工维护。
- 更新策略：
  - 基础图谱低频更新（人工校验）。
  - 事件节点高频追加（自动抽取 + 人工抽检）。
- 图谱查询：
  - 上下游 N 层。
  - 给定事件/商品/地区的影响路径。
  - 暴露度评分（规则/统计 + 可解释）。

## 2.9 缓存与成本守门

- Snapshot 缓存：同一标的同一时点多次请求复用。
- 检索缓存：同一 query + 时间窗复用。
- 预算策略：
  - tier0（普通）：少量文档 + 单模型。
  - tier1（观察）：加图谱与更多文档。
  - tier2（重点/持仓）：多模型复核 + 更深检索。

## 2.10 安全与合规（写进 `SECURITY_COMPLIANCE.md`）

- 密钥：服务端加密存储（env/secret manager），前端不可见。
- 权限：策略配置与数据访问权限控制。
- 审计：每次生成报告记录“用过哪些源、版本、模型”。
- 风险提示：UI 明确“非投资建议”；禁止夸大收益承诺。

## 2.11 测试与质量保障

- 单测：因子计算、风控门禁、schema 校验。
- 集成测试：数据源失败/缺失/异常值的降级逻辑。
- 回放测试：给定某日快照+策略版本，应生成可复现报告。
- 评估：离线回测 + 在线“有用性”反馈闭环。
