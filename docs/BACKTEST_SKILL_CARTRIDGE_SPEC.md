# Backtest Skill Cartridge 标准（MVP v0.1）

## 0. 文档定位

本文件定义 FiPro_1 的“可中断回测 + LLM 决策增强 + 可插拔规则公式”最小落地标准。  
目标是先统一规范，再分阶段实现。

- 适用市场：`CN_A`（MVP），后续可扩展 `US/HK/CRYPTO`。
- 适用模式：`BACKTEST` 主，`LIVE/SHADOW` 后续复用同一决策引擎。
- 版本策略：文档优先于实现，所有实现需对齐本标准。

## 1. 设计目标与非目标

### 1.1 目标

1. 在保证效率的前提下，最大化利用 LLM 的信息理解能力。
2. 将非结构化信息（新闻/公告/问答等）转为可计算因子并进入统一指数。
3. 支持策略规则、公式、权重的“插碟式”加载与版本化管理。
4. 支持回测中断、恢复、进度可见，并可复盘每一步决策来源。
5. 允许任意因子权重降为 `0`（保留因子但禁用其影响）。

### 1.2 非目标（MVP 阶段）

1. 不做高频逐笔撮合级回测。
2. 不做无门禁的在线自动自我改策略。
3. 不做全市场资产组合优化（先单标的策略决策）。

## 2. 双层决策架构

### 2.1 角色分工

1. `LLM Analyst`：理解文本、提炼结构化事件、提出策略改进提案。
2. `Deterministic Score Engine`：将所有因子输入公式，输出标准化指数。
3. `Policy Engine`：基于指数、置信度、仓位状态给出交易动作。
4. `Risk Overlay`：强制风控，优先级高于任何模型输出。

### 2.2 原则

1. LLM 不直接改线上 champion 配置，只能提交提案。
2. 上线策略必须经过回测门禁，未达标不能晋级。
3. 所有决策必须可解释、可追溯、可复现。

## 3. 因子标准（核心）

### 3.1 因子注册协议

每个因子必须在注册表中定义，建议结构：

```json
{
  "factor_id": "price.momentum_20d",
  "domain": "price|fundamental|flow|event|macro|graph|risk",
  "enabled": true,
  "weight": 0.12,
  "value_range": [-1.0, 1.0],
  "default_value": 0.0,
  "source_refs": [
    {"source": "tushare", "endpoint": "daily"}
  ],
  "transform": "zscore_then_clip",
  "missing_policy": "use_default_and_penalty"
}
```

### 3.2 因子值统一规范

1. 所有因子值统一映射到 `[-1, 1]`。
2. 缺失值用 `default_value`，并在 `data_quality_penalty` 中反映。
3. 异常值先 winsorize 再标准化，防止单点失真。

### 3.3 权重规范（含 0 权重）

1. `weight` 允许为 `0`，表示因子暂时不参与计算。
2. 权重更新必须记录版本和变更原因。
3. 因子保留但 `weight=0` 的场景：
   - 接口刚接入，先观测稳定性。
   - 因子在当前市场阶段失效。
   - 权限/频率限制导致可用性波动。

## 4. Tushare Pro 数据扩展标准

## 4.1 已接入（现状）

当前主链路已使用（代码中已存在）：

1. `daily` / `index_daily`
2. `daily_basic`
3. `moneyflow`
4. `fina_indicator`
5. `shibor`

## 4.2 待扩展（按价值优先级）

以下接口来自当前账号可用清单与策略价值评估，分阶段接入：

| 阶段 | 目标域 | 候选接口（示例） | 默认权重策略 |
|---|---|---|---|
| P1 | 交易结构 | `adj_factor`, `limit_list_d`, `margin`, `margin_detail`, `block_trade` | 初始 `0`，稳定后逐步上调 |
| P1 | 资金与热度 | `moneyflow_ind_dc`, `moneyflow_ind_ths`, `dc_hot` | 初始小权重 |
| P2 | 财务质量 | `cashflow_vip`, `forecast_vip`, `express_vip`, `fina_audit` | 分行业逐步启用 |
| P2 | 指数与风格 | `index_weight`, `index_member_all`, `ths_daily` | 与基准风格联动 |
| P3 | 宏观与外盘 | `cn_gdp`, `cn_pmi`, `cn_ppi`, `index_global`, `fx_daily` | 先 `0` 权重影子运行 |

说明：

1. “候选接口”不等于立即生效，需经过权限、限频、稳定性验证。
2. 限频敏感接口可先离线聚合，避免在线调用卡住决策链路。

## 4.3 接口接入门禁

新增接口进入生产前必须满足：

1. 近 14 天成功率 >= 99%。
2. 限频触发率 <= 1%。
3. 字段缺失率低于预设阈值（按接口单独定义）。
4. 因子引入后离线指标不恶化（见第 8 节）。

## 5. 指数计算公式标准

### 5.1 主公式

```text
z = b + Σ(w_i * x_i)
base_score = 50 + 50 * tanh(z)
final_score = clamp(base_score - risk_penalty - dq_penalty + regime_bonus, 0, 100)
```

其中：

1. `x_i`：归一化因子值（`[-1,1]`）。
2. `w_i`：因子权重（可为 `0`）。
3. `risk_penalty`：硬风控和高风险事件惩罚。
4. `dq_penalty`：数据质量惩罚。
5. `regime_bonus`：市场状态修正项。

### 5.2 置信度公式

```text
confidence = clamp(
  c0 + c1*data_quality_score + c2*evidence_coverage
  - c3*factor_conflict - c4*staleness,
  0, 1
)
```

## 6. 动作决策标准（状态机）

### 6.1 动作集合

`BUY`, `ADD`, `HOLD`, `REDUCE`, `SELL`, `AVOID`

### 6.2 目标仓位

```text
target_pos = clamp(((final_score - 50)/50) * confidence, 0, max_position)
```

### 6.3 动作阈值（MVP 默认）

1. 无仓位：
   - `final_score >= 72 && confidence >= 0.62` -> `BUY`
   - 否则 `AVOID`
2. 有仓位：
   - `target_pos - current_pos >= 0.15` -> `ADD`
   - `|target_pos - current_pos| < 0.10` -> `HOLD`
   - `current_pos - target_pos >= 0.15` -> `REDUCE`
   - `final_score < 35` 或命中硬风控 -> `SELL`

### 6.4 风控覆盖（强制）

1. 单标的最大仓位上限。
2. 最大回撤触发减仓/清仓。
3. 连续负面事件阈值触发防守模式。
4. 数据质量降级到 `DEGRADED` 时禁止新增仓位。

## 7. LLM 输出与 Skill Cartridge 规范

### 7.1 LLM 事件抽取输出协议

LLM 必须输出结构化事件，不得直接写最终动作：

```json
{
  "event_type": "policy|earnings|supply_chain|governance|macro|other",
  "direction": -1.0,
  "severity": 0.78,
  "source_quality": 0.85,
  "model_confidence": 0.72,
  "half_life_days": 5,
  "evidence_refs": ["doc_xxx", "ann_xxx"]
}
```

事件值：

```text
event_value = direction * severity * source_quality * model_confidence * exp(-age_days/half_life_days)
```

### 7.2 Skill Cartridge 文件结构

```text
skill_packs/
  cn_a_core/
    0.1.0/
      manifest.json
      factors.json
      formula.json
      policy.json
      risk.json
      llm_mapping.json
      gate.json
      calibration.json   # 可选：参数定标模板
```

关键字段：

1. `manifest.json`
   - `skill_pack_id`, `version`, `market`, `author`, `status`
   - `derived_from_job_id`, `derived_from_champion_version`
2. `status` 仅允许：
   - `draft`
   - `candidate`
   - `champion`
   - `archived`

### 7.3 变更治理

1. LLM 只能产出 `draft/candidate`。
2. `champion` 升级必须通过门禁并落审计记录。
3. 所有参数变更必须可追溯到具体回测任务和评估报告。

## 8. 回测门禁与晋级规则

## 8.1 必选指标

1. `strategy_total_return_pct`
2. `excess_return_pct`
3. `max_drawdown`
4. `volatility`
5. `turnover`
6. `win_rate`
7. `cost_budget_violation_rate`
8. `data_quality_degraded_rate`

## 8.2 MVP 晋级门槛（candidate -> champion）

对比当前 champion，满足全部条件才可晋级：

1. `excess_return_pct` 提升 >= 1.0%
2. `max_drawdown` 不恶化超过 2.0%
3. `turnover` 不上升超过 20%
4. `data_quality_degraded_rate` 不恶化
5. 分段窗口（滚动或牛熊子区间）至少 70% 子窗口优于 champion

## 8.3 防过拟合约束

1. 固定训练窗口和验证窗口，禁止同窗调参后直接评估。
2. 关键阈值需做敏感性测试（小范围扰动仍稳定）。
3. 每次仅允许有限参数改动，避免一次性大改不可解释。

## 9. 效率与成本标准

### 9.1 在线效率

1. 在线决策优先“数值引擎 + 缓存因子”。
2. LLM 仅在以下条件触发：
   - 新重大事件进入
   - 因子冲突高
   - 置信度过低
3. 文本事件按 `ticker + doc_hash + asof_window` 缓存。

### 9.2 成本预算

沿用 tier 预算上限，并加入策略回测任务预算：

1. `TIER0`: 低成本、低深度。
2. `TIER1`: 中成本、标准深度。
3. `TIER2`: 高成本、深度复核。

预算达到阈值时：

1. 优先降级 LLM 调用频率。
2. 不降级硬风控。
3. 记录降级原因进 `degradation_matrix`。

## 10. 实施里程碑

### M1（标准落地）

1. 建立 `factor registry` 与 `skill cartridge` 解析器。
2. 固化公式与动作状态机。
3. 回测任务输出完整审计字段。

### M2（数据扩展）

1. 接入 P1 Tushare 接口，先 `weight=0` 影子运行。
2. 完成稳定性评估后逐步放量。

### M3（学习闭环）

1. LLM 提案器：生成 candidate skill 包。
2. 自动回测评估 + 门禁决策 + 晋级流程。

### M4（上线联动）

1. champion skill 在线加载。
2. 与 `LIVE/SHADOW` 统一决策引擎。

### 10.1 当前执行进度（2026-02-14）

1. 已完成：`skill_pack` 模板目录与文件结构落地。
2. 已完成：回测与工作流在运行时加载 `skill_pack`，并回传模板摘要。
3. 已完成：模板驱动评分函数（读取 `factors/formula/policy/risk`）接入 `score_signal_node`。
4. 已完成：事件因子在 RAG/公告链路后的实时回填（`event.policy_signal`/`event.governance_signal`），并在进入 LLM 前刷新评分。
5. 已完成：`candidate -> champion` 自动评估与晋级执行器（支持 `dry_run` 与 `manual_approval`）。
6. 已完成：工作流与回测支持 `champion/auto` 版本别名，优先在线加载 champion skill。
7. 已完成：晋级门禁改为“相对 champion 增量”口径（`excess_return_delta_pct`）并引入真实分段窗口胜率计算（`segment_win_rate`）。
8. 已完成：落地参数定标模板 `calibration.json` 及校验加载器/脚本，支持后续批量调参与候选版本治理。
9. 已完成：`anti_overfit` 从配置约束升级为强门禁校验（训练/验证窗口、灵敏度、参数变更上限）。
10. 已完成：提供基于 `calibration.json` 的 candidate 自动生成功能（API + 脚本）。
11. 已完成：新增 Tushare 全量接口因子注册表（默认 `weight=0`）与查询 API（`GET /factors/registry`），并在 BACKTEST 上下文注入注册表供模型使用。
12. 已完成：新增本地数据统一读取网关（`POST /local-data/batch-query`），支持 `endpoint+ticker+时间窗` 切片，并将读取摘要写入报告 `provenance.local_data_access`。
13. 已完成：candidate 生成支持“参数扰动 + endpoint 组合搜索”混合模式，可按 endpoint 生成 `weight=0` 影子候选（支持 allowlist 与组合阶数）。
14. 已完成：LIVE 模式强制绑定 champion skill（忽略显式版本）；新增 champion 手动切换/回滚能力（`POST /skill-packs/{skill_pack_id}/champion/switch`），并写入 manifest 审计字段 `champion_switch`。
15. 已完成：LLM 提案闭环（`POST /skill-packs/proposals/llm-run`）：支持 LLM 生成规则/公式候选（含 `append` 新增规则）、批量回测评估、LLM 在候选结果中选优；默认仍由 gate 决定是否可晋级。
16. 已完成：回测成本模型升级（手续费/滑点/卖出税），新增 gross/net 双口径收益、交易成本与换手统计。
17. 已完成：LLM 提案运行审计落库（`.run/llm_proposal_runs`）与查询 API（`GET /skill-packs/proposals/runs*`）。
18. 已完成：组合级回测接口（`POST /backtests/portfolio/run`），支持多标的权重分配、组合资金曲线聚合与组件回测摘要输出。
19. 已完成：promotion anti-overfit 增加稳健性门禁（`walk_forward_stability` + `bootstrap_significance`），默认纳入 gate 决策。
20. 已完成：前端提案评审页（`/proposals`），支持 run 列表、详情评估和审计 JSON 复盘。
21. 已完成：champion 健康检查与审计接口（`POST /skill-packs/champion/health-check` + `GET /skill-packs/champion/health-checks*`）。
22. 已完成：gate 失败时可触发自动回滚（支持 dry-run），并在监控页回看回滚执行结果。
23. 已完成：发布事件审计时间线（`GET /skill-packs/releases*`），统一回看晋级/切换/回滚事件。
24. 已完成：Champion Watchdog（`POST /skill-packs/champion/watchdog/run`），输出告警清单与回滚建议。
25. 已完成：Watchdog 定时脚本与调度模板（`scripts/champion_watchdog.py` + `docs/CHAMPION_WATCHDOG_SCHEDULE.md`）。

### 10.2 偏离评估与取舍（2026-02-12）

1. 当前主流程与规范已对齐，未发现阻断级偏离项。
2. 仍建议后续补充：anti-overfit 证据从“请求侧上报”升级为“系统内自动计算并落库”，减少人工填报成本。

## 11. 参数定标基线（MVP v0.1.0）

### 11.1 已定版参数

1. 因子清单：MVP 固定 10 个（`price/fundamental/flow/event/risk` 五大域），其中允许 `weight=0` 因子作为影子观测位（当前为 `flow.block_trade_net`、`event.governance_signal`）。
2. 行业差异化权重：MVP 不引入行业分桶，先统一权重；行业化放到 `v0.2+`。
3. 动作阈值：`buy_score_min=72`、`buy_confidence_min=0.62`、`add_gap_min=0.15`、`hold_gap_max=0.10`、`reduce_gap_min=0.15`、`sell_score_max=35`。
4. 风控阈值：`max_single_position=0.6`、`max_drawdown_pct=12`，`DEGRADED` 状态禁止开新仓。
5. 门禁阈值：`excess_return_delta_pct>=1.0`、`max_drawdown_delta_pct<=2.0`、`turnover_delta_pct<=20.0`、`data_quality_degraded_rate_delta_pct<=0`、`segment_win_rate>=0.7`。
6. 人工确认：`candidate -> champion` 默认必须人工确认（`manual_approval_required=true`）。

### 11.2 调参范围（执行模板）

1. 调参模板文件：`skill_packs/cn_a_core/0.1.0/calibration.json`。
2. 主要搜索空间：核心权重、买入/卖出阈值、置信度系数，保持风控硬约束冻结。
3. 单轮修改上限：最多 8 个参数（防止不可解释的大幅漂移）。
4. 数据切分：固定 `train` 与 `validation` 窗口，禁止同窗调参与评估。

## 12. 产品化细化标准

### 12.1 版本与发布

1. Skill 包状态仅允许 `draft/candidate/champion/archived`。
2. 线上默认优先加载 `champion`，无 champion 才回退到默认基线版本。
3. 晋级执行必须记录：候选版本、对比基线、门禁明细、执行人/审批状态。

### 12.2 可运维性

1. 回测任务必须支持异步运行、进度查询、取消中断、结果留存。
2. 回测输出必须包含：收益曲线、基准曲线、超额收益、动作分布、数据质量分布。
3. 所有降级路径必须进入 `degradation_matrix`，且不能绕过硬风控。

### 12.3 可扩展性

1. 新 Tushare 接口先 `weight=0` 接入影子运行，通过稳定性门禁后再上调权重。
2. 新模型接入不得改变 `LLM -> 结构化事件 -> 规则引擎动作` 的接口契约。
3. 允许通过新增 skill pack 版本引入新公式，但必须保持向后可审计、可回滚。

---

本文件是后续代码改造的唯一规范输入。  
实现前若与本文件冲突，以本文件为准，先修订文档再改代码。
