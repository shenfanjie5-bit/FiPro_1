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
  - `make seed-m4 && make eval-m4`（本地先采样再守门）
  - 或 `python scripts/m4_quality_baseline.py --lookback-days 14`
- 产物：
  - `monitoring/dashboards/m4_quality_baseline.json`
  - `monitoring/dashboards/m4_quality_baseline.md`
- CI 接入：
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
