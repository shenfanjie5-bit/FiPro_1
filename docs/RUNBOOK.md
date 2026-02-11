# RUNBOOK（M6 稳定性与风控）

## 1. 值班分级与目标
- `P0`：核心生成链路不可用，或失败率持续高于 `3%`（15 分钟）。
- `P1`：延迟/成本显著退化，或 schema pass 明显下降。
- `P2`：局部功能异常，不影响核心可用性。

SLO/SLA 参考（与 M6 面板一致）：
- `success_rate >= 0.97`
- `failure_rate <= 0.03`
- `schema_pass_rate >= 0.99`
- `latency_p95_ms <= 12000`
- `cost_p95_usd <= 2.5`

## 2. 启动与检查
- 启动依赖：`docker compose up -d`
- 启动服务：`uvicorn app.main:app --reload --port 8000`
- 健康检查：`GET /health`
- 迁移：`alembic upgrade head`
- SQL 初始化（仅引导/对照）：`psql "$DATABASE_URL" -f sql/001_init.sql`

## 3. 告警到缓解（前 15 分钟）
1. 确认告警是否连续触发（避免抖动误报）。
2. 立即执行稳定性面板：
   - `python scripts/m6_reliability_panel.py --lookback-days 1`
3. 如果失败率或 schema pass 失控，先启用保守策略：
   - 降 tier（优先限制 TIER2 深链路）。
   - 观察 `budget.degraded` 和 `degradation_matrix` 是否大范围触发。
4. 若出现上游波动（429/超时），允许重试但控制雪崩：
   - 检查 `tool_call_stats.retry_count`、`tool_retry_rate_5m`。
5. 记录事件时间线（告警时间、缓解时间、恢复时间）。

## 4. 常见故障路径

### 4.1 数据源异常（超时/限流/不可用）
- 现象：
  - `UPSTREAM_TIMEOUT` / `RATE_LIMITED` 增加。
  - `data_quality` 从 `OK` 变为 `PARTIAL/DEGRADED`。
  - `degradation_matrix.data_source` 非 `OK`。
- 处理：
  1. 确认外部 token/网络状态。
  2. 暂时接受降级（缓存/合成快照）并强制保守结论。
  3. 持续观察成功率与 schema pass 是否稳定。

### 4.2 LLM 失败或抖动
- 现象：
  - `llm_generate_report_draft` trace 出现重试/失败。
  - 回退报告 `model.primary=rule-fallback-v1`。
- 处理：
  1. 确认 `LLM_API_KEY`、上游可用性、配额。
  2. 维持 fallback 模式，避免 BUY 激进结论。
  3. 恢复后执行回放校验（见第 6 节）。

### 4.3 图谱链路不可用
- 现象：
  - `query_supply_chain_subtree` / `find_impact_paths` 报错。
  - `degradation_matrix.graph` 非 `OK`。
- 处理：
  1. 检查 `NEO4J_URI/USER/PASSWORD` 与连接状态。
  2. 临时接受无图谱保守输出（TIER2 禁止激进 BUY）。
  3. 图谱恢复后做一次 TIER2 回放验证。

### 4.4 预算超限/容量不足
- 现象：
  - `budget.degraded=true`，`degrade_reason` 出现 guardrail。
  - `tool_calls/cost` 接近或超过 tier 上限。
- 处理：
  1. 降并发或降 tier，优先保证可用性。
  2. 运行压测基线脚本评估当前容量：
     - `python scripts/m6_load_soak.py --requests 60 --concurrency 6 --tier TIER1`

## 5. 回滚与恢复
- 回滚触发条件：
  - `P0` 持续超过 30 分钟无法缓解。
  - 核心指标持续不达标且影响用户面。
- 回滚动作：
  1. 回退到上一稳定版本（代码+配置）。
  2. 保持保守输出策略，避免激进 action。
  3. 复测基础健康与生成链路。
- 恢复动作：
  1. 指标连续两个窗口恢复正常。
  2. 逐步恢复 TIER2/高开销路径。
  3. 更新事故记录与改进项。

## 6. 演练与回放
- 上线预演脚本（故障+回放）：
  - `python scripts/m6_rollout_drill.py --tier TIER1 --enforce-checks`
- 通过标准：
  - baseline schema/consistency 通过。
  - replay 稳定性通过。
  - fault 场景输出保守结论（非 BUY）。

## 7. 事故复盘模板（最少字段）
- 事件编号、日期、负责人
- 触发告警与时间线
- 用户影响范围
- 根因
- 临时缓解动作
- 永久修复动作
- 是否需要更新阈值/runbook/测试
