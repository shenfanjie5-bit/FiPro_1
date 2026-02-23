# 接口全量可用化实施清单（Backtest / Live 解耦）

## 0. 目标对齐（已确认）

### Backtest 目标
1. 产出可复用、可版本化、可晋级的高收益策略 `skills`（同时满足风控与稳定性约束）。
2. 确定“使用哪些数据、权重如何配置、如何组合进公式”，形成可审计推荐值链路。

### Live 目标
1. 复用已发布的 `skills + 推荐值公式链路`，做高效股票推荐。
2. 不在 Live 链路里做无门禁的在线自我改策略。

## 1. 四步实施路线

### Step 1: 全量接口因子注册与可见化（本次落地）
1. 基于 `docs/tushare_api_history_classification.csv` 建立去重后的 Tushare 接口注册表。
2. 每个接口生成标准注册项（`factor_id/endpoint/domain/local_path_hint`）。
3. 默认权重 `weight_default=0.0`，满足“先接入、后启权重”。
4. 提供 API：`GET /factors/registry`。
5. 回测上下文注入注册表，使 LLM 在 BACKTEST 时可见可用接口目录。
6. 当前状态：已完成。

### Step 2: 本地数据统一读取网关
1. 实现按 `endpoint + ticker + 时间窗` 读取本地 CSV 的统一查询层。
2. 输出标准化数据切片（包含字段、时效、缺失、质量标记）。
3. 将读取痕迹写入审计链路，支持复盘“某次决策用了哪些数据”。
4. 当前状态（MVP）：已完成核心能力。
  - API：`POST /local-data/batch-query`
  - BACKTEST 上下文预加载本地切片并写入 `tool_traces`
  - 报告审计：`provenance.local_data_access` / `provenance.local_data_slices`

### Step 3: 回测数据组合搜索与 candidate 生成
1. 在 BACKTEST 里允许模型/搜索器选择接口子集与权重候选。
2. 产出 `candidate skill_pack`（`factors/formula/policy/risk/llm_mapping`）。
3. 支持权重可为 0 的影子因子持续观测。
4. 当前状态（MVP）：已完成基础候选生成增强。
  - 支持“参数扰动 + 数据接口组合”混合生成
  - 支持按 endpoint 生成 `weight=0` 影子候选（可设 allowlist 与组合阶数）

### Step 4: 晋级门禁与实盘联动
1. 候选版本通过门禁后晋级 champion。
2. Live 仅消费 champion（可回滚、可审计）。
3. 建立“新增接口先影子再放量”的稳定性门禁流程。
4. 当前状态（MVP）：部分完成。
  - 已完成：晋级执行器（candidate -> champion）、LIVE 强制 champion 绑定
  - 已完成：手动 champion 切换/回滚 API（带审计字段）
  - 待补：新增接口“影子观察 -> 放量”自动门禁证据校验

## 2. 验收口径（Step 1）
1. `/factors/registry` 能返回去重后的全量接口目录（当前历史+基础分组）。
2. 每条目录项包含：
   - `factor_id`
   - `endpoint`
   - `group`
   - `domain`
   - `local_path_hint`
   - `weight_default=0.0`
3. BACKTEST 上下文包含 `factor_registry`，便于后续数据组合推理。
