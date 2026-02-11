# DATA_DICTIONARY（泛用框架）

## 1. 数据源目录（Source Registry）
| source_code | source_name | domain | owner | auth_type | refresh_freq | sla_latency | fallback |
|---|---|---|---|---|---|---|---|
| SRC_MARKET_001 | 待定行情源 | market | 待定 | api_key | 1m~5m | <=5m | 上次快照+降级标记 |
| SRC_EVENT_001 | 待定事件源 | event | 待定 | api_key | 5m~15m | <=15m | 降低置信度 |
| SRC_MACRO_001 | 待定宏观源 | macro | 待定 | api_key/public | 15m~1h | <=60m | 标记 PARTIAL |

## 2. 字段字典模板（Field Dictionary）
| field_path | layer | type | unit | nullable | business_meaning | source_of_truth | sample | quality_rule |
|---|---|---|---|---|---|---|---|---|
| snapshot.market.close | snapshot | number | currency | N | 最新价 | 行情源 | 100.25 | >0 |
| snapshot.market.volatility_20d | snapshot | number | pct | Y | 20日波动率 | 行情源 | 0.18 | 0<=x<=1 |
| feature.hotness_score | feature | integer | score | Y | 热度评分 | 特征计算 | 65 | 0<=x<=100 |

## 3. 统计口径模板（Metric Definitions）
| metric_name | formula | granularity | window | filters | owner | validation |
|---|---|---|---|---|---|---|
| hotness_score | 标准化(成交额增速, 搜索热度, 资金流) | ticker/day | 20d | 去除停牌 | 待定 | 范围0-100 |
| exposure_score | 图路径权重加总并归一化 | ticker/event | 30d | 仅有效路径 | 待定 | 范围0-100 |

## 4. 刷新与时效（Freshness & SLA）
| layer | refresh_mode | expected_latency | hard_timeout | stale_policy |
|---|---|---|---|---|
| raw | schedule/pull | <= 5m | 10m | 标记 stale=true |
| snapshot | workflow-on-demand | <= 2m | 5m | 回退最近可用快照 |
| report | on request | <= 12s(TIER0) | 30s | 触发降级路径 |

## 5. 质量规则（Data Quality Rules）
- 完整性：关键字段缺失率阈值（如 `close` 缺失率 < 1%）。
- 一致性：跨源相同字段偏差阈值（如价格偏差 < 0.5%）。
- 有效性：数值范围校验（例如波动率在 [0,1]）。
- 新鲜度：超过 SLA 自动标记 `DEGRADED` 或 `PARTIAL`。
- 可追溯：每条快照必须记录 `source/source_id/captured_at/checksum`。

## 6. 变更管理（Schema & Source Changes）
- 新增字段：先在本文件登记，再改 `SCHEMA.md` 与代码实现。
- 字段弃用：至少保留一个版本窗口，并在 changelog 标注。
- 源切换：必须先做并行比对（shadow）并记录偏差报告。

## 7. 待你确认（后续补充）
- 确认正式数据源供应商与授权方式。
- 确认关键指标的最终公式与业务口径。
- 确认各层 SLA 与降级阈值。
