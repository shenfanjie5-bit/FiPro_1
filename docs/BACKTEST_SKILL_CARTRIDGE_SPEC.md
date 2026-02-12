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

### 10.1 当前执行进度（2026-02-12）

1. 已完成：`skill_pack` 模板目录与文件结构落地。
2. 已完成：回测与工作流在运行时加载 `skill_pack`，并回传模板摘要。
3. 已完成：模板驱动评分函数（读取 `factors/formula/policy/risk`）接入 `score_signal_node`。
4. 已完成：事件因子在 RAG/公告链路后的实时回填（`event.policy_signal`/`event.governance_signal`），并在进入 LLM 前刷新评分。
5. 待完成：`candidate -> champion` 自动评估与晋级执行器。

## 11. 当前需要确认的参数（细化清单）

1. MVP 第一批因子清单（建议 8-12 个）。
2. 行业差异化权重是否在 MVP 就引入。
3. 风控阈值具体数值（回撤/止损/仓位）。
4. 门禁阈值是否按市场阶段动态调整。
5. `candidate -> champion` 是否必须人工确认。

---

本文件是后续代码改造的唯一规范输入。  
实现前若与本文件冲突，以本文件为准，先修订文档再改代码。
