# BACKLOG（Milestone Execution Plan）

## 0. 使用约定

- 状态字段：`TODO` / `IN_PROGRESS` / `DONE` / `BLOCKED`
- 优先级字段：`P0`（最高）/ `P1` / `P2`
- 分支策略：`main` 仅合并通过验收门的任务；日常开发在 `codex/mvp-implementation`
- 交付纪律：每次 `push` 必须同步更新 `docs/IMPLEMENTATION_LOG.md`，记录本次变更、验证结果与待跟进事项。

## M0：基座就绪（可运行）

目标：完成最小可运行工程，支持本地启动与健康检查。

| ID | Task | Priority | Status | Est | Depends On | Acceptance |
|---|---|---|---|---|---|---|
| M0-01 | 建立 Python 工程骨架（`app/`, `tests/`, `pyproject.toml`） | P0 | DONE | 0.5d | - | `uvicorn app.main:app` 可启动 |
| M0-02 | 新增 `docker-compose.yml`（postgres/redis/neo4j） | P0 | DONE | 0.5d | M0-01 | `docker compose up -d` 成功 |
| M0-03 | 配置管理（`.env` 加载与必填校验） | P0 | DONE | 0.5d | M0-01 | 关键 env 缺失时报错清晰 |
| M0-04 | 数据库初始化链路（执行 `sql/001_init.sql`） | P0 | DONE | 0.5d | M0-02 | 应用连库成功，表存在 |
| M0-05 | 基础接口（`/health`, `/version`） | P0 | DONE | 0.5d | M0-03 | curl 返回 200 |
| M0-06 | 结构化日志 + request_id 中间件 | P1 | DONE | 0.5d | M0-01 | 每次请求日志带 request_id |
| M0-07 | CI 基线（lint + test 占位） | P1 | DONE | 0.5d | M0-01 | PR 自动检查通过 |
| M0-08 | 重写 `README.md` 启动文档 | P0 | DONE | 0.5d | M0-02 | 新人可按文档完成启动 |

### M0 验收门

- `docker compose up -d` 可用
- `uvicorn app.main:app` 可启动
- `/health` 返回 200
- CI 绿色

## M1：合约冻结 v0.1（可扩展）

目标：锁定 Schema/API/DB 合约，避免后续反复返工。

| ID | Task | Priority | Status | Est | Depends On | Acceptance |
|---|---|---|---|---|---|---|
| M1-01 | 将 `docs/SCHEMA.md` 落地为机器可读 schema 文件 | P0 | DONE | 1d | M0 | schema 可被程序加载 |
| M1-02 | 实现 `schema_validator`（jsonschema） | P0 | DONE | 0.5d | M1-01 | 合法样例 pass，非法样例 fail |
| M1-03 | 实现 `consistency_check`（证据引用一致性等） | P0 | DONE | 1d | M1-01 | 核心规则有自动检查 |
| M1-04 | 修订 OpenAPI（`/reports/generate` 返回 `final_report`） | P0 | DONE | 0.5d | M1-01 | OpenAPI lint 通过 |
| M1-05 | 工具统一 wrapper（trace/error/cost） | P0 | DONE | 1d | M0 | 每次工具调用有 trace |
| M1-06 | DB 字段补齐（`weights_hash`/回放字段等） | P0 | DONE | 1d | M0-04 | migration 后字段齐全 |
| M1-07 | 合约测试（schema/api/db）进入 CI | P0 | DONE | 1d | M1-02,M1-03,M1-04,M1-06 | CI 自动通过 |

### M1 验收门

- Report Schema、OpenAPI、DB 三方对齐
- 合约测试在 CI 全部通过
- 破坏性变更必须版本化

## M2：TIER0 全链路（可回放）

目标：跑通最小业务闭环（mock 数据可接受），并落库可回放。

| ID | Task | Priority | Status | Est | Depends On | Acceptance |
|---|---|---|---|---|---|---|
| M2-01 | 定义 `ResearchState`（TypedDict/Pydantic） | P0 | DONE | 0.5d | M1 | 状态字段覆盖全链路 |
| M2-02 | 构建 TIER0 StateGraph 主路径 | P0 | DONE | 1d | M2-01 | 图可执行 |
| M2-03 | 实现 TIER0 tools stubs（facts/features/score/price_bands/memory） | P0 | DONE | 1d | M1-05 | 返回可追溯 ID |
| M2-04 | `draft_report` 节点（先 dummy JSON） | P0 | DONE | 0.5d | M2-03 | 生成可校验 report |
| M2-05 | `risk_gate` 节点（可覆盖 action/confidence） | P0 | DONE | 1d | M2-04 | 风控可强制降级 |
| M2-06 | `validate + repair loop`（最大 N 次） | P0 | DONE | 1d | M2-04 | 不合规能修复或标 invalid |
| M2-07 | `persist` 节点（reports/decision_logs/memory/traces） | P0 | DONE | 1d | M2-05 | 结果可查询与回放 |
| M2-08 | checkpointer 接入（thread_id + sqlite） | P0 | DONE | 0.5d | M2-02 | 同 thread 可恢复 |
| M2-09 | API 实装：`POST /reports/generate`, `GET /reports/{id}` | P0 | DONE | 0.5d | M2-07 | 返回 `{report_id, final_report}` |
| M2-10 | E2E 测试：一次 generate 全链路 | P0 | DONE | 1d | M2-09 | 自动化测试可重复通过 |

### M2 验收门

- `POST /reports/generate` 返回 `final_report`
- 输出 100% 通过 schema + consistency
- `provenance`/`evidence_refs`/`data_quality` 必填
- 指定 `thread_id` 可回放

## M3：数据层 MVP（真实数据替换）

目标：将 TIER0 mock 数据替换为最小真实数据闭环，保证“缺失可降级、结论可追溯”。

| ID | Task | Owner | Priority | Status | Est | Depends On | Acceptance |
|---|---|---|---|---|---|---|---|
| M3-01 | 数据源接入清单冻结（以 Tushare Pro 为主） | Product + Data | P0 | DONE | 0.5d | M2 | 明确 Tushare 接口映射、频率、鉴权、限流策略 |
| M3-02 | 实现 Tushare Pro market ingest adapter（替换 `get_market_snapshot` mock） | Data Eng | P0 | DONE | 1d | M3-01 | 可按 ticker+asof 拉取并生成 `snapshot_id` |
| M3-03 | 实现 event docs ingest adapter（替换 search mock 数据） | Data Eng | P0 | DONE | 1d | M3-01 | 文档可入库并可按 query/time 检索 |
| M3-04 | 实现 Tushare Pro 宏观/商品字段 ingest adapter（MVP最小字段） | Data Eng | P1 | DONE | 1d | M3-01 | tier>=1 时可返回宏观/运价核心字段 |
| M3-05 | 数据标准化与映射层（统一字段/时区/单位） | Data Eng | P0 | DONE | 1d | M3-02,M3-03,M3-04 | 所有快照字段满足内部命名与 UTC 规范 |
| M3-06 | 快照落库增强（`snapshot_id`,`type`,`data_quality_json`） | BE | P0 | DONE | 1d | M3-05 | 每次运行均可追溯到快照记录 |
| M3-07 | 质量门禁（freshness/null ratio/outlier）并接入 `data_quality` | Data Eng + BE | P0 | DONE | 1d | M3-05 | 缺失/异常会显式标记 `DEGRADED/PARTIAL` |
| M3-08 | 缓存策略落地（snapshot/search cache + TTL） | BE | P1 | DONE | 0.5d | M3-02,M3-03 | 重复请求命中缓存且可观测 |
| M3-09 | 数据源失败降级策略（超时/限流/无数据） | BE | P0 | DONE | 1d | M3-07 | 工具失败不沉默，报告保守降级 |
| M3-10 | 数据层集成测试（成功/超时/缺失/脏数据） | QA + BE | P0 | DONE | 1d | M3-09 | CI 中可稳定复现并通过 |

### M3 验收门

- `get_market_snapshot` 等核心 facts 工具不再依赖纯 mock
- 数据异常时 `data_quality` 字段准确反映，且 action/confidence 保守
- snapshot/event 文档可回溯（ID + source + captured_at）
- 集成测试覆盖主要失败场景并通过

## M4：TIER1 增强（RAG + Memory）

目标：在 TIER1 引入可解释检索增强，提升证据完整度与连续决策能力。

| ID | Task | Owner | Priority | Status | Est | Depends On | Acceptance |
|---|---|---|---|---|---|---|---|
| M4-01 | TIER1 路由参数冻结（top_k、depth、预算） | Product + BE | P0 | TODO | 0.5d | M3 | 参数写入 config，可版本化 |
| M4-02 | `search_event_docs` 实装（多 query + 时间窗） | BE | P0 | TODO | 1d | M3-03 | 返回 doc_id/source/checksum，支持 top_k |
| M4-03 | `rerank_docs` 实装（轻量模型/规则） | ML Eng | P1 | TODO | 1d | M4-02 | 重排结果可解释（score/reason） |
| M4-04 | `extract_events_from_docs` 实装（结构化事件） | ML Eng | P0 | TODO | 1d | M4-02 | 产出标准事件结构并附 evidence 关联 |
| M4-05 | 记忆检索 `retrieve_memory_notes`（pgvector + keyword） | BE | P0 | TODO | 1d | M3-06 | 能按 ticker/q/time_range 召回 |
| M4-06 | 记忆写入 `write_memory_note` 与去重策略 | BE | P0 | TODO | 0.5d | M4-05 | 每次 report 可写入并避免重复爆炸 |
| M4-07 | TIER1 context builder（融合 facts + docs + memory） | BE | P0 | TODO | 1d | M4-04,M4-05 | draft context 含可追溯 evidence_ids |
| M4-08 | evidence coverage 规则（最小证据条数/类型覆盖） | BE + QA | P0 | TODO | 0.5d | M4-07 | 不达标则触发 repair 或降级 |
| M4-09 | TIER1 E2E 测试（含缓存、预算、降级） | QA + BE | P0 | TODO | 1d | M4-08 | TIER1 全链路稳定通过并可回放 |
| M4-10 | 质量评估基线（evidence 覆盖率、引用一致率、成本/延迟） | QA + Data | P1 | TODO | 0.5d | M4-09 | 出具首版指标报表并设阈值 |

### M4 验收门

- TIER1 路径可稳定执行：search -> rerank -> extract -> memory -> report
- `evidence_refs` 覆盖充分且 `evidence_ids` 全部可解析
- memory 写入与检索形成闭环（下一次可被引用）
- 成本与延迟在预算内（按 tier1 配置）

## M5：TIER2 + 图谱（深度分析与复核）

目标：引入图谱推理与复核流程，确保高风险动作（如 BUY）有更强证据与审计可追踪。

| ID | Task | Owner | Priority | Status | Est | Depends On | Acceptance |
|---|---|---|---|---|---|---|---|
| M5-01 | TIER2 预算与路由规则冻结（graph depth/review policy） | Product + BE | P0 | TODO | 0.5d | M4 | 配置可版本化且落库 |
| M5-02 | Neo4j schema 初始化（节点/关系/索引） | Data Eng | P0 | TODO | 1d | M5-01 | 可建图并完成基本查询 |
| M5-03 | 图谱导入任务（公司/行业/商品/地区最小数据） | Data Eng | P0 | TODO | 1.5d | M5-02 | 导入后可命中至少 1 条影响路径 |
| M5-04 | 实装 `query_supply_chain_subtree` | BE | P0 | TODO | 1d | M5-02 | 返回 `graph_id/path_id` 与节点关系 |
| M5-05 | 实装 `find_impact_paths`（事件/商品 -> 标的） | BE | P0 | TODO | 1d | M5-03,M5-04 | 返回可解释路径与权重 |
| M5-06 | 实装 `compute_exposure_score`（确定性） | BE + Data | P0 | TODO | 1d | M5-05 | 同输入输出稳定，分数可解释 |
| M5-07 | reviewer 节点接入（TIER2 强制 + BUY 条件复核） | BE + ML Eng | P0 | TODO | 1d | M5-01,M5-05 | 复核意见进入报告或修复链路 |
| M5-08 | graph evidence 绑定（`graph_refs` + `evidence_refs`） | BE | P0 | TODO | 0.5d | M5-04,M5-07 | 图谱证据 ID 在报告中可追溯 |
| M5-09 | TIER2 E2E 测试（graph + review + repair） | QA + BE | P0 | TODO | 1d | M5-08 | TIER2 全链路回放成功 |
| M5-10 | 性能与预算校准（TIER2 tool_calls/cost/latency） | SRE + BE | P1 | TODO | 0.5d | M5-09 | 不超预算阈值并有告警线 |

### M5 验收门

- TIER2 路径稳定执行：graph 查询 + reviewer + validate/repair
- `BUY` 或低质量场景自动触发复核
- 报告中图谱证据可追溯（`graph_refs`/`evidence_refs` 可解析）
- TIER2 成本与延迟在预算范围内

## M6：稳定性与风控（生产化运行）

目标：建立生产级可靠性、可观测性和强风控执行，避免“可用但不可控”。

| ID | Task | Owner | Priority | Status | Est | Depends On | Acceptance |
|---|---|---|---|---|---|---|---|
| M6-01 | 预算守门器（按 tier 限 tool_calls/cost） | BE | P0 | TODO | 1d | M5 | 超预算自动降级并记录原因 |
| M6-02 | 限流与重试策略（外部数据源/LLM） | BE | P0 | TODO | 1d | M6-01 | 429/超时可控重试，不雪崩 |
| M6-03 | 降级矩阵实现（数据缺失/模型失败/图谱不可用） | BE | P0 | TODO | 1d | M6-02 | 任一依赖故障可返回保守报告 |
| M6-04 | 全链路 trace 与审计日志落库（tool/model/version） | BE + SRE | P0 | TODO | 1d | M6-01 | 每次生成可追踪来源与耗时成本 |
| M6-05 | 指标面板（成功率/延迟/失败率/成本/schema pass） | SRE | P1 | TODO | 1d | M6-04 | 仪表盘可查看近 7 天趋势 |
| M6-06 | 告警规则（失败率、延迟、成本、schema fail） | SRE | P0 | TODO | 0.5d | M6-05 | 告警触发准确，含分级策略 |
| M6-07 | runbook 补全（故障处理、回滚、恢复、升级） | SRE + BE | P1 | TODO | 0.5d | M6-06 | 值班人员可按文档完成处置 |
| M6-08 | 压测（并发 + 长跑）与容量基线 | QA + SRE | P1 | TODO | 1d | M6-03,M6-05 | 达到 SLA 并输出容量建议 |
| M6-09 | 风控门禁回归测试（边界条件与覆盖率） | QA + BE | P0 | TODO | 1d | M6-03 | risk_gate 对 LLM 有最高优先级 |
| M6-10 | 上线预演（故障演练 + 数据回放演练） | SRE + QA | P0 | TODO | 0.5d | M6-07,M6-08,M6-09 | 演练通过并记录改进项 |

### M6 验收门

- 高优先故障可在 runbook 规定时间内定位并缓解
- 风控门禁始终可覆盖 LLM 输出并可审计
- 系统在目标负载下满足 SLA（延迟/成功率/成本）
- 关键告警可用且噪声可控

## M7：评估与 Shadow（持续优化）

目标：建立可量化的质量闭环，支持模型替换与策略迭代不失控。

| ID | Task | Owner | Priority | Status | Est | Depends On | Acceptance |
|---|---|---|---|---|---|---|---|
| M7-01 | 离线回放数据集构建（按行业/行情阶段分层） | Data + QA | P0 | TODO | 1d | M6 | 数据集可重复生成并版本化 |
| M7-02 | 离线评估管道（schema/一致性/证据覆盖/成本） | QA + BE | P0 | TODO | 1d | M7-01 | 一键评估并输出指标报告 |
| M7-03 | Shadow 路由接入（challenger 不影响线上结果） | BE + ML Eng | P0 | TODO | 1d | M6-04 | 同输入双路跑通并落库对比 |
| M7-04 | Shadow 对比报表（质量/延迟/成本） | Data + QA | P1 | TODO | 1d | M7-03 | 可按模型版本查看差异 |
| M7-05 | 在线反馈回流（有用/无用/误报标签） | Product + BE | P1 | TODO | 0.5d | M7-02 | 用户反馈可关联 report_id |
| M7-06 | 漂移监控（数据漂移/行为漂移） | Data + SRE | P1 | TODO | 1d | M7-01,M7-02 | 超阈值触发告警与调查流程 |
| M7-07 | 模型切换准入规则（升版门槛与回滚策略） | ML Eng + Product | P0 | TODO | 0.5d | M7-04 | 满足门槛才允许主模型切换 |
| M7-08 | 月度评审机制（坏报告复盘 + 规则更新） | Product + QA + BE | P2 | TODO | 0.5d | M7-05,M7-06 | 形成可执行改进 backlog |

### M7 验收门

- Shadow 模式稳定运行，且不影响线上主结果
- 离线与在线评估形成闭环并可追溯到版本
- 模型切换有明确准入门槛与回滚路径
- 漂移监控可发现并推动修复
