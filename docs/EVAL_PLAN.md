# EVAL_PLAN（泛用框架）

## 1. 离线评估（Offline）
- 数据集分层：按行业、波动阶段、事件密度分层。
- 指标：schema 通过率、证据覆盖率、一致性错误率、成本、延迟。
- 基线对照：当前主模型 vs 候选模型（shadow）。

### 1.1 M4 质量基线（首版阈值）
- 数据来源：`reports` + `decision_logs`（postgres 优先，sqlite runtime 兜底）。
- 统计窗口：默认近 `14` 天（可调）。
- 关键指标与阈值：
  - `schema_pass_rate >= 1.00`
  - `citation_consistency_rate >= 0.98`
  - `evidence_coverage_pass_rate >= 0.90`
  - `latency_p95_ms <= 7000`
  - `avg_cost_usd <= 0.90`
  - `sample_size >= 10`
- Tier 成本预算参考：
  - `TIER0 avg_cost_usd <= 0.20`
  - `TIER1 avg_cost_usd <= 0.80`
  - `TIER2 avg_cost_usd <= 2.50`
- 生成命令：
  - `make eval-m4`
  - `make seed-m4 && make eval-m4`（可手动先采样）
  - 或 `python scripts/m4_quality_baseline.py --lookback-days 14 --auto-topup-samples --enforce-thresholds`
  - 样本量治理：`--auto-topup-samples` 会在 `sample_size < 10` 时自动补样并重算基线
- 产物：
  - `monitoring/dashboards/m4_quality_baseline.json`
  - `monitoring/dashboards/m4_quality_baseline.md`
- CI 接入：
  - 主 CI：`.github/workflows/ci.yml` 增加 `python scripts/lint_openapi.py docs/OPENAPI.yaml`
  - Workflow：`.github/workflows/m4-quality-baseline.yml`
  - 触发：`schedule (daily)` + `workflow_dispatch`（可在 PR 分支手动触发）
  - 守门：`--enforce-thresholds` 打开时，阈值不达标直接失败
  - 统计口径：按场景（ticker + asof + strategy_version_id + tier + run_mode）取最新报告去重，支持“重跑覆盖旧失败样本”。

### 1.2 TIER1 低覆盖回放修复
- 目的：针对 `tier1_low_coverage_reports` 批量重跑同场景报告，修复证据覆盖不足并更新基线状态。
- 脚本：
  - `python scripts/replay_tier1_low_coverage.py --lookback-days 14 --batch-size 20 --max-rounds 3 --run-mode-strategy same --update-baseline-artifacts`
  - 或 `make replay-m4-lowcov`
- 采样建议（用于 gate 稳定）：
  - `python scripts/seed_m4_baseline_samples.py --count 12 --tier-pattern TIER0,TIER1 --vary-asof`
- 产物：
  - `monitoring/dashboards/m4_low_coverage_replay.json`
  - `monitoring/dashboards/m4_low_coverage_replay.md`

### 1.3 M5 TIER2 预算校准（tool_calls/cost/latency）
- 目标：校准并守门 TIER2 的预算指标，保证图谱与复核链路不超预算。
- 数据来源：`reports` + `decision_logs`（Postgres 优先，sqlite runtime 兜底）。
- 统计口径：默认近 `14` 天，按场景最新报告去重（ticker+asof+strategy_version_id+tier+run_mode）。
- 阈值与告警线（由 tier2 budget 推导）：
  - `tool_calls_p95 <= 90`（warning line `76.5`）
  - `cost_p95_usd <= 2.50`（warning line `2.125`）
  - `latency_p95_ms <= 12000`（warning line `10200`）
  - `budget_violation_rate <= 0.05`
- 生成命令：
  - `make eval-m5`
  - 或 `python scripts/m5_tier2_calibration.py --lookback-days 14 --auto-topup-samples --enforce-thresholds`
  - 样本量治理：`--auto-topup-samples` 会在 TIER2 样本不足时自动补样并重算校准结果
- 产物：
  - `monitoring/dashboards/m5_tier2_calibration.json`
  - `monitoring/dashboards/m5_tier2_calibration.md`

### 1.4 M6 稳定性面板与上线预演
- 目标：补齐生产化稳定性指标与上线演练闭环（成功率/失败率/延迟/成本/schema pass + 压测 + 故障预演）。
- 稳定性面板脚本：
  - `python scripts/m6_reliability_panel.py --lookback-days 7 --enforce-thresholds`
  - 或 `make eval-m6`
- 压测/容量脚本：
  - `python scripts/m6_load_soak.py --requests 60 --concurrency 6 --tier TIER1`
  - 或 `make load-m6`
- 上线预演脚本（故障+回放）：
  - `python scripts/m6_rollout_drill.py --tier TIER1 --enforce-checks`
  - 或 `make drill-m6`
- 产物：
  - `monitoring/dashboards/m6_reliability_panel.json`
  - `monitoring/dashboards/m6_reliability_panel.md`
  - `monitoring/dashboards/m6_load_baseline.json`
  - `monitoring/dashboards/m6_load_baseline.md`
  - `monitoring/dashboards/m6_rollout_drill.json`
  - `monitoring/dashboards/m6_rollout_drill.md`

## 2. 在线评估（Online）
- 指标：用户有用率、报告生成成功率、SLA 命中率。
- 策略：小流量灰度 + 阈值守门。
- 终止条件：错误率或成本超阈值自动回滚。

## 3. 漂移监控（Drift）
- 数据漂移：关键特征分布偏移（PSI/KS）。
- 行为漂移：action 分布、confidence 分布异常波动。
- 告警：超过阈值触发调查与修复任务。

## 4. 评估产出
- 周报：质量、成本、延迟、失败案例复盘。
- 月报：模型替换建议与风险清单。
