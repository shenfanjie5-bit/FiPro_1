# 机构化改造总方案（FiPro_1）

更新时间：2026-02-14

## 1. 目标定义

将 FiPro_1 从“可运行的策略回测与报告系统”升级为“可审计、可版本化、可持续优化”的机构化研究与策略生产平台。

核心目标：

1. 回测与实盘彻底解耦：回测用于产出策略技能与公式，实盘只消费已发布 champion。
2. 大模型最大化参与：LLM 负责提案、比较、解释；最终上线由门禁与审计链路决定。
3. 工程可治理：每次策略变更有证据、可复现、可回滚、可监控。

## 2. 目标架构（机构化）

1. 数据层：多源数据接口 + 本地历史库 + 增量调度 + 数据质量状态。
2. 特征层：统一因子注册、标准化映射、缺失与异常惩罚。
3. 策略层：`skill_pack`（factors/formula/policy/risk/llm_mapping/gate）版本化。
4. 回测层：批量回测、成本模型、窗口化评估、过拟合防线。
5. 提案层：LLM 生成候选策略提案并在候选结果中选优。
6. 晋级层：candidate/champion 门禁、人工确认、发布审计、回滚。
7. 观测层：运行状态、失败原因、成本/收益/稳定性指标仪表化。

## 3. 工作流拆分与验收

### W1. 策略治理基线（已完成）

1. `skill_pack` 目录规范、版本状态、champion 切换/回滚。
2. `promotion gate` + `anti_overfit` 强门禁。
3. 验收：candidate 不能绕过 gate 直接成为 champion。

### W2. 全量接口可见化（已完成）

1. Tushare 因子注册表、接口分类与本地路径映射。
2. `GET /factors/registry` + `POST /local-data/batch-query`。
3. 验收：回测上下文能看到接口目录与本地切片摘要。

### W3. LLM 提案闭环（已完成）

1. LLM 提案（可 `set/append` 规则、公式、权重）。
2. 候选生成、批量回测、gate 评估、LLM 选优。
3. 接口：`POST /skill-packs/proposals/llm-run`。
4. 验收：LLM 只能产出提案，最终结果仍受 gate 与人工确认约束。

### W4. 回测交易成本模型（本次完成）

1. 引入费用、滑点、卖出税三段式成本模型。
2. 输出 gross/net 两套收益口径与交易成本、换手指标。
3. 验收：summary 中可同时看到 gross/net 与总成本。

### W5. 提案运行审计（本次完成）

1. 每次提案运行落审计文件（run 级别完整记录）。
2. 查询接口：`GET /skill-packs/proposals/runs`、`GET /skill-packs/proposals/runs/{run_id}`。
3. 验收：可回看任一 run 的输入、候选、评估、选优、执行结果。

### W6. 组合级回测与稳健性（本次完成）

1. 多标的组合回测（资金分配、风险预算、持仓约束）。
2. Walk-forward / regime split / bootstrap 显著性检验。
3. 验收：晋级必须通过跨窗口稳定性和统计显著性约束。

### W7. 生产化发布与监控（进行中）

1. 发布流水线：candidate -> review -> approve -> champion。
2. 监控项：收益偏移、成本偏移、数据质量退化、模型漂移。
3. 验收：异常可自动告警并支持一键回滚。

本次新增：

1. champion 健康检查运行与审计：`POST /skill-packs/champion/health-check` + `GET /skill-packs/champion/health-checks*`。
2. 健康检查 gate 失败后的自动回滚执行器（支持 dry-run）。
3. GUI 新增 Champion 监控页（健康检查发起 + 审计回看）。
4. 发布事件审计时间线：`GET /skill-packs/releases*`（覆盖晋级/手动切换/回滚）。
5. Champion Watchdog：告警阈值评估 + 回滚建议（`POST /skill-packs/champion/watchdog/run`）。
6. Watchdog 定时执行脚本与调度说明（macOS/Win11）。
7. Watchdog 告警生命周期：ACK / 关闭（`/skill-packs/champion/watchdog/alerts*`）。
8. Watchdog 自动工单输出与查询（`/skill-packs/champion/watchdog/tickets*`）。

## 4. 里程碑与状态

### M0-M2（已完成）

1. Skill cartridge 基线
2. 全量接口可见化与本地读取网关
3. 提案与晋级门禁基础能力

### M3（已完成）

1. 回测成本模型机构化（已完成）
2. 提案 run 审计可观测（已完成）
3. 前端提案评审页（已完成）

### M4（已完成）

1. 生产发布流水线与监控收敛（已完成）
2. 监控告警与自动回滚联动（已完成）

## 5. 默认决策（无需额外拍板）

1. 成本模型默认值：
   - `CN_A`: fee 5bps, slippage 8bps, sell tax 10bps
   - 其他市场按保守默认值
2. LLM 提案仍是“提案身份”，不具备直接改 champion 权限。
3. 审计保留：
   - 本地文件 `.run/llm_proposal_runs/*.json`
   - 默认最多保留最近 1000 条运行记录。

## 6. 关键衡量指标（机构化 KPI）

1. 策略有效性：`excess_return_pct`、`segment_win_rate`、`max_drawdown`。
2. 稳定性：跨窗口通过率、`data_quality_degraded_rate`、失败率。
3. 成本：`total_trade_cost_cny`、`avg_turnover`、推理成本与延迟。
4. 治理：提案可追溯率、回滚成功率、审计完整率。

## 7. 执行约束

1. 不做无门禁自动升版。
2. 不在实盘链路中在线学习改公式。
3. 所有重要参数改动必须具备可复盘证据与审计记录。
