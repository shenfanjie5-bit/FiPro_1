# TOOLS_SPEC.md

- Version: 0.1
- Status: Draft
- Last Updated: 2026-02-09 (America/Los_Angeles)
- Owner: (填写你/团队)
- Audience: 后端 / Agent 编排 / 数据 / 测试 / 前端（阅读即可，不需要全懂实现细节）

> 目的：定义“工具/技能（Tools/Skills）”的稳定合约（Contract）。
> - LLM/Agent 只通过工具拿事实、做计算、查图谱、读写记忆。
> - 工具负责“确定性、可追溯、可缓存、可测试”。
> - 任何工具的变更都需要版本化（向后兼容优先）。

---

## 1. 范围与原则

### 1.1 范围
本文件定义：
- 工具名（tool_name）
- 输入/输出参数（JSON）
- 错误结构与错误码
- 幂等性、缓存建议、成本等级
- 示例请求/响应

不在本文件中定义：
- 前后端 REST/GraphQL API（见 `API_SPEC.md`）
- LLM 的 Report 输出 Schema（见 `SCHEMA.md`）
- 架构/选型/工作流编排细节（见 `TECH_DESIGN.md`）

### 1.2 核心原则（强约束）
1) **事实由工具给**：行情、财务、指标计算、图谱路径等必须由工具输出，LLM 不负责“算数”和“编数据”。  
2) **工具输出必须可追溯**：每个工具输出应包含可追溯 ID（如 snapshot_id/doc_id/path_id/note_id）。  
3) **错误要显式**：工具失败必须返回标准 error（不要返回空对象让 LLM 猜）。  
4) **可缓存/可降级**：工具应标注 Cache TTL 与 Cost Class，便于 tier 预算与降级策略。  
5) **合规与安全**：密钥不下发前端；工具层统一做权限与审计（至少 trace + 输入输出摘要）。

---

## 2. 通用约定

### 2.1 时间、时区与单位
- 所有时间字段使用 ISO 8601 且带时区：`YYYY-MM-DDTHH:mm:ss±HH:MM`
- 百分比用小数：5% → `0.05`
- `score` 如果出现，默认范围 0~100
- `confidence` 默认范围 0~1

### 2.2 命名规范
- 工具名：`snake_case`（例：`get_market_snapshot`）
- 输入参数：`snake_case`
- 输出对象字段：`snake_case`
- ID 字段：`*_id`（如 `snapshot_id`、`doc_id`）

### 2.3 标准错误响应结构（全工具通用）
```json
{
  "error": {
    "code": "INVALID_ARGUMENT",
    "message": "Human readable error message",
    "retryable": false,
    "details": { "any": "json" }
  }
}
````

### 2.4 标准错误码（全工具通用）

* `INVALID_ARGUMENT`：参数不合法（缺字段、类型错误、范围错误）
* `NOT_FOUND`：请求对象不存在（ticker/doc/note 等）
* `UNAUTHORIZED`：鉴权失败/无权限
* `RATE_LIMITED`：触发限流（建议 `retryable=true` 并给出重试建议）
* `UPSTREAM_TIMEOUT`：上游超时（建议 `retryable=true`）
* `UPSTREAM_ERROR`：上游错误（可能 retryable）
* `DATA_UNAVAILABLE`：数据源缺失或暂无数据（不一定 retryable）
* `INTERNAL_ERROR`：工具内部错误

### 2.5 幂等性（Idempotency）定义

* **Idempotent = YES**：同样输入在同一数据版本/同一 asof 下，多次调用应返回一致结果（允许 meta 中 cached 不同）。
* **Idempotent = NO**：会产生新对象/新记录（如写入记忆、生成 rollup）。

> 建议：对非幂等工具增加 `dedupe_key`（或由 orchestrator 提供）避免重复写入。

### 2.6 Cache TTL（建议）

* `0s`：不建议缓存
* `5s/30s/60s`：高频行情
* `5m/15m/1h`：舆情/宏观数据
* `1d/7d`：财务、图谱结构

### 2.7 Cost Class（成本等级）

* `LOW`：本地/DB 查询、轻计算
* `MEDIUM`：外部 API、较重计算、Embedding/Rerank
* `HIGH`：大模型推理（通常不建议把 LLM 当工具放这里；LLM 在 orchestrator 层）

---

## 3. 工具注册表（Tool Registry）

| Category      | Tool Name                              | Purpose        | Idempotent | Cache TTL | Cost    |
| ------------- | -------------------------------------- | -------------- | ---------- | --------: | ------- |
| Facts         | get_market_snapshot                    | 行情+技术快照        | YES        |     5s~1h | LOW     |
| Facts         | get_fundamentals_snapshot              | 基本面/估值快照       | YES        |        1d | LOW     |
| Facts         | get_flow_sentiment_snapshot            | 资金流/热度/情绪快照    | YES        |     1m~5m | LOW~MED |
| Facts         | get_macro_commodity_logistics_snapshot | 宏观/大宗/物流/运价快照  | YES        |    15m~1h | MED     |
| RAG           | search_event_docs                      | 检索新闻/公告/研报等文档  | YES*       |    5m~15m | MED     |
| RAG           | rerank_docs                            | 文档重排（本地小模型）    | YES        |     0s~5m | LOW~MED |
| RAG           | extract_events_from_docs               | 文档→结构化事件抽取     | YES        |     0s~5m | MED     |
| Graph         | query_supply_chain_subtree             | 产业链上下游子图       | YES        |        7d | LOW     |
| Graph         | find_impact_paths                      | 事件/商品→标的影响路径   | YES        |        1d | LOW~MED |
| Graph         | compute_exposure_score                 | 暴露度评分（确定性）     | YES        |        1d | LOW     |
| Memory        | retrieve_memory_notes                  | 记忆检索（向量+关键词）   | YES        |     0s~5m | LOW     |
| Memory        | write_memory_note                      | 写入记忆（研究笔记）     | NO         |        0s | LOW     |
| Memory        | summarize_memory_rollup                | 压缩长期记忆         | NO         |        1d | MED     |
| Deterministic | score_signal                           | 因子+权重→推荐分数     | YES        |     0s~1d | LOW     |
| Deterministic | risk_gate                              | 风控门禁裁决         | YES        |     0s~5m | LOW     |
| Deterministic | generate_price_bands                   | 价格段位生成         | YES        |     5m~1d | LOW     |
| QA            | validate_report_schema                 | JSON Schema 校验 | YES        |        0s | LOW     |
| QA            | consistency_check                      | 一致性/逻辑校验       | YES        |        0s | LOW     |

> `YES*`：对 search 类工具，严格意义上结果会随着上游变化而变化；但对固定 `asof_range` 可认为弱幂等（同一缓存窗口内一致）。

---

## 4. 工具定义（Tool Definitions）

> 每个工具统一写法：
>
> * **Purpose**
> * **Idempotency / Cache TTL / Cost**
> * **Input**
> * **Output**
> * **Errors**
> * **Notes**
> * **Example**

---

### 4.1 Facts / 行情与指标快照

#### 4.1.1 `get_market_snapshot`

**Purpose**
获取行情与技术指标事实快照（K线/收益/波动/趋势/流动性等）。LLM 不做指标计算。

**Idempotency**: YES（固定 ticker+asof+interval+lookback 且上游数据不变时）
**Cache TTL**:

* `interval=5m/1h`：5s~30s
* `interval=1d`：15m~1h
  **Cost Class**: LOW

**Input**

```json
{
  "ticker": "string",
  "asof": "date-time",
  "lookback_days": 60,
  "interval": "1d|1h|5m"
}
```

**Output**

```json
{
  "snapshot_id": "string",
  "ticker": "string",
  "asof": "date-time",
  "currency": "string",
  "last_price": 0,
  "returns": { "d1": 0, "w1": 0, "m1": 0 },
  "volatility": { "atr_14": 0, "stdev_20": 0 },
  "trend": { "ma_20": 0, "ma_60": 0, "regime": "UP|DOWN|RANGE" },
  "liquidity": { "avg_turnover_20d": 0, "spread_est": 0 },
  "data_quality": { "status": "OK|DEGRADED|PARTIAL", "missing_fields": [], "notes": "" }
}
```

**Errors**

* INVALID_ARGUMENT / NOT_FOUND / UPSTREAM_TIMEOUT / DATA_UNAVAILABLE / INTERNAL_ERROR

**Notes**

* `snapshot_id` 必须可回放（建议包含日期+ticker+时间粒度）。
* 若上游缺失部分指标，必须填 `data_quality.missing_fields`。

**Example Input**

```json
{ "ticker": "600519.SH", "asof": "2026-02-09T09:30:00-08:00", "lookback_days": 60, "interval": "1d" }
```

---

#### 4.1.2 `get_fundamentals_snapshot`

**Purpose**
获取财务/质量/估值快照（口径统一），用于低风险偏好筛选与解释。

**Idempotency**: YES
**Cache TTL**: 1d
**Cost Class**: LOW

**Input**

```json
{
  "ticker": "string",
  "asof": "date-time",
  "period": "TTM|FY|Q"
}
```

**Output**

```json
{
  "snapshot_id": "string",
  "ticker": "string",
  "asof": "date-time",
  "quality": {
    "roe": 0,
    "gross_margin": 0,
    "debt_to_assets": 0
  },
  "growth": {
    "revenue_yoy": 0,
    "profit_yoy": 0
  },
  "valuation": {
    "pe_ttm": 0,
    "pb": 0
  },
  "data_quality": { "status": "OK|DEGRADED|PARTIAL", "missing_fields": [], "notes": "" }
}
```

**Errors**

* INVALID_ARGUMENT / NOT_FOUND / DATA_UNAVAILABLE / UPSTREAM_ERROR / INTERNAL_ERROR

**Notes**

* 财务口径必须明确（TTM/FY/Q）。
* 缺失必须记录，不允许 LLM “补值”。

---

#### 4.1.3 `get_flow_sentiment_snapshot`

**Purpose**
获取资金流、热度、情绪与拥挤度快照（追热点核心输入）。

**Idempotency**: YES
**Cache TTL**: 1m~5m
**Cost Class**: LOW~MED（取决于舆情源/计算复杂度）

**Input**

```json
{
  "ticker": "string",
  "asof": "date-time",
  "window_days": 10
}
```

**Output**

```json
{
  "snapshot_id": "string",
  "ticker": "string",
  "asof": "date-time",
  "hotness": { "mentions": 0, "hot_score": 0 },
  "sentiment": { "polarity": 0, "confidence": 0 },
  "flows": { "northbound_net": 0, "main_force_net": 0 },
  "crowding": { "crowding_score": 0 },
  "data_quality": { "status": "OK|DEGRADED|PARTIAL", "missing_fields": [], "notes": "" }
}
```

**Errors**

* INVALID_ARGUMENT / NOT_FOUND / UPSTREAM_TIMEOUT / DATA_UNAVAILABLE / INTERNAL_ERROR

---

#### 4.1.4 `get_macro_commodity_logistics_snapshot`

**Purpose**
获取宏观/大宗/运价/物流/利率/汇率等外部变量快照，用于“重点关注信息”。

**Idempotency**: YES
**Cache TTL**: 15m~1h
**Cost Class**: MED

**Input**

```json
{
  "asof": "date-time",
  "entities": ["string"],
  "region": "GLOBAL|CN|US|EU|OTHER"
}
```

**Output**

```json
{
  "snapshot_id": "string",
  "asof": "date-time",
  "series": [
    { "name": "string", "value": 0, "unit": "string", "change_1w": 0 }
  ],
  "events": [
    { "event_id": "string", "type": "string", "summary": "string", "confidence": 0 }
  ],
  "data_quality": { "status": "OK|DEGRADED|PARTIAL", "missing_fields": [], "notes": "" }
}
```

**Errors**

* INVALID_ARGUMENT / UPSTREAM_TIMEOUT / UPSTREAM_ERROR / DATA_UNAVAILABLE / INTERNAL_ERROR

---

### 4.2 RAG / 文档检索与抽取

#### 4.2.1 `search_event_docs`

**Purpose**
检索新闻/公告/研报/社媒等候选文档。

**Idempotency**: YES*（同一时间窗内弱幂等）
**Cache TTL**: 5m~15m
**Cost Class**: MED

**Input**

```json
{
  "query": "string",
  "asof_range": { "start": "date-time", "end": "date-time" },
  "sources": ["NEWS", "FILINGS", "REPORT", "SOCIAL"],
  "top_k": 20
}
```

**Output**

```json
{
  "docs": [
    {
      "doc_id": "string",
      "title": "string",
      "source": "NEWS|FILINGS|REPORT|SOCIAL",
      "published_at": "date-time",
      "snippet": "string",
      "uri": "string"
    }
  ]
}
```

**Errors**

* INVALID_ARGUMENT / UPSTREAM_TIMEOUT / RATE_LIMITED / INTERNAL_ERROR

**Notes**

* `snippet` 严格短（避免把长文本塞给 LLM）。
* `uri` 可为内部对象存储地址或外链。

---

#### 4.2.2 `rerank_docs`

**Purpose**
对检索结果重排，提高喂给 LLM 的文档质量。推荐本地小模型/向量 rerank。

**Idempotency**: YES
**Cache TTL**: 0s~5m（可按 query+doc_ids 缓存）
**Cost Class**: LOW~MED

**Input**

```json
{
  "query": "string",
  "docs": [{ "doc_id": "string", "title": "string", "snippet": "string" }],
  "top_k": 8
}
```

**Output**

```json
{
  "ranked_doc_ids": ["string"],
  "scores": [0]
}
```

**Errors**

* INVALID_ARGUMENT / INTERNAL_ERROR

---

#### 4.2.3 `extract_events_from_docs`

**Purpose**
文档→结构化事件抽取（事件类型、方向、实体、置信度），减少 LLM 长文阅读负担。

**Idempotency**: YES
**Cache TTL**: 0s~5m（doc_id 列表可缓存）
**Cost Class**: MED

**Input**

```json
{
  "docs": [
    { "doc_id": "string", "title": "string", "snippet": "string", "uri": "string" }
  ]
}
```

**Output**

```json
{
  "events": [
    {
      "event_id": "string",
      "type": "COMMODITY|POLICY|GEOPOLITICS|LOGISTICS|EARNINGS|COMPETITION|OTHER",
      "entities": ["string"],
      "direction": "POS|NEG|MIXED|UNCERTAIN",
      "confidence": 0,
      "summary": "string",
      "evidence_doc_ids": ["string"]
    }
  ]
}
```

**Errors**

* INVALID_ARGUMENT / UPSTREAM_ERROR / INTERNAL_ERROR

**Notes**

* 若无法判断方向，direction=UNCERTAIN，而不是硬猜。
* `summary` 必须短、可复盘。

---

### 4.3 Graph / 产业链图谱

#### 4.3.1 `query_supply_chain_subtree`

**Purpose**
获取标的上下游 N 层子图（供 UI 展示 + LLM 推理）。

**Idempotency**: YES
**Cache TTL**: 7d（图谱结构低频变化）
**Cost Class**: LOW

**Input**

```json
{
  "ticker": "string",
  "depth": 2,
  "include_competitors": true
}
```

**Output**

```json
{
  "graph_id": "string",
  "nodes": [{ "id": "string", "type": "string", "label": "string" }],
  "edges": [{ "from": "string", "to": "string", "type": "string" }]
}
```

**Errors**

* INVALID_ARGUMENT / NOT_FOUND / DATA_UNAVAILABLE / INTERNAL_ERROR

---

#### 4.3.2 `find_impact_paths`

**Purpose**
从某个事件/商品/地区/港口/航线等实体出发，查到标的的影响路径（传导链路）。

**Idempotency**: YES
**Cache TTL**: 1d
**Cost Class**: LOW~MED

**Input**

```json
{
  "ticker": "string",
  "entity": "string",
  "max_hops": 5
}
```

**Output**

```json
{
  "path_id": "string",
  "paths": [
    {
      "nodes": ["string"],
      "edges": ["string"],
      "impact_direction": "POS|NEG|MIXED|UNCERTAIN",
      "confidence": 0
    }
  ]
}
```

**Errors**

* INVALID_ARGUMENT / NOT_FOUND / INTERNAL_ERROR

---

#### 4.3.3 `compute_exposure_score`

**Purpose**
计算标的对某变量（商品/事件/区域）的暴露度（尽量确定性：规则+统计）。

**Idempotency**: YES
**Cache TTL**: 1d
**Cost Class**: LOW

**Input**

```json
{
  "ticker": "string",
  "entity": "string"
}
```

**Output**

```json
{
  "exposure_score": 0,
  "explanation": "string"
}
```

**Errors**

* INVALID_ARGUMENT / NOT_FOUND / INTERNAL_ERROR

---

### 4.4 Memory / 记忆与回忆

#### 4.4.1 `retrieve_memory_notes`

**Purpose**
召回历史研究/决策日志摘要（向量+关键词），供 LLM “回忆思考”。

**Idempotency**: YES
**Cache TTL**: 0s~5m（可按 query+time_range 缓存）
**Cost Class**: LOW

**Input**

```json
{
  "ticker": "string",
  "query": "string",
  "top_k": 8,
  "time_range_days": 180
}
```

**Output**

```json
{
  "notes": [
    {
      "note_id": "string",
      "created_at": "date-time",
      "summary": "string",
      "tags": ["string"],
      "importance": 0,
      "links": ["string"]
    }
  ]
}
```

**Errors**

* INVALID_ARGUMENT / NOT_FOUND / INTERNAL_ERROR

---

#### 4.4.2 `write_memory_note`

**Purpose**
写入研究笔记/决策摘要，作为长期记忆素材（供后续召回）。

**Idempotency**: NO（默认会生成新 note）
**Cache TTL**: 0s
**Cost Class**: LOW

**Input**

```json
{
  "ticker": "string",
  "created_at": "date-time",
  "summary": "string",
  "tags": ["string"],
  "importance": 0,
  "links": ["string"],
  "dedupe_key": "string"
}
```

**Output**

```json
{ "ok": true, "note_id": "string" }
```

**Errors**

* INVALID_ARGUMENT / UNAUTHORIZED / INTERNAL_ERROR

**Notes**

* `dedupe_key`：建议由 orchestrator 传入（例如 `report_id`），避免同一报告重复写入。

---

#### 4.4.3 `summarize_memory_rollup`

**Purpose**
将某标的历史记忆压缩成“长期摘要”，防止记忆无限膨胀。

**Idempotency**: NO（通常生成新 rollup）
**Cache TTL**: 1d
**Cost Class**: MED（可能调用本地小模型或轻量 LLM）

**Input**

```json
{
  "ticker": "string",
  "target_max_tokens": 1200
}
```

**Output**

```json
{
  "rollup_note_id": "string",
  "rollup_summary": "string"
}
```

**Errors**

* INVALID_ARGUMENT / INTERNAL_ERROR

---

### 4.5 Deterministic / 评分与风控（强建议代码实现）

#### 4.5.1 `score_signal`

**Purpose**
将 feature_vector 与 weights 做确定性评分（可回测、可解释、可复现）。

**Idempotency**: YES
**Cache TTL**: 0s~1d（按特征快照版本缓存）
**Cost Class**: LOW

**Input**

```json
{
  "feature_vector": { "string": 0 },
  "weights": { "string": 0 },
  "normalization": "Z|MINMAX|NONE"
}
```

**Output**

```json
{
  "raw_score": 0,
  "score_0_100": 0,
  "top_contributors": [
    { "feature": "string", "weight": 0, "value": 0, "contribution": 0 }
  ]
}
```

**Errors**

* INVALID_ARGUMENT / INTERNAL_ERROR

---

#### 4.5.2 `risk_gate`

**Purpose**
硬风控门禁裁决：即便 LLM 说 BUY，也可能被强制降级为 WATCH/AVOID 或限制仓位。

**Idempotency**: YES
**Cache TTL**: 0s~5m
**Cost Class**: LOW

**Input**

```json
{
  "ticker": "string",
  "asof": "date-time",
  "proposed_action": "BUY|WATCH|AVOID",
  "score_0_100": 0,
  "risk_profile": {
    "max_volatility": 0,
    "max_drawdown": 0,
    "max_position_pct": 0,
    "max_sector_exposure": 0
  },
  "risk_metrics": {
    "volatility": 0,
    "drawdown_60d": 0,
    "liquidity_ok": true
  }
}
```

**Output**

```json
{
  "allowed": true,
  "final_action": "BUY|WATCH|AVOID",
  "position_cap_pct": 0,
  "reasons": ["string"],
  "hard_blocks": ["string"]
}
```

**Errors**

* INVALID_ARGUMENT / INTERNAL_ERROR

---

#### 4.5.3 `generate_price_bands`

**Purpose**
生成分价格段位（ATR/均线/支撑阻力等方法），作为 UI 和策略入口/退出条件的“骨架”。

**Idempotency**: YES
**Cache TTL**: 5m~1d
**Cost Class**: LOW

**Input**

```json
{
  "ticker": "string",
  "asof": "date-time",
  "method": "ATR|PIVOT|MA_BANDS",
  "band_count": 4
}
```

**Output**

```json
{
  "currency": "string",
  "bands": [
    { "band_id": "string", "min": 0, "max": 0, "label": "string" }
  ]
}
```

**Errors**

* INVALID_ARGUMENT / INTERNAL_ERROR

---

### 4.6 QA / 报告校验

#### 4.6.1 `validate_report_schema`

**Purpose**
对 LLM 输出的 Report JSON 做 JSON Schema 校验（强制）。

**Idempotency**: YES
**Cache TTL**: 0s
**Cost Class**: LOW

**Input**

```json
{
  "report_json": {}
}
```

**Output**

```json
{
  "valid": true,
  "errors": []
}
```

**Errors**

* INVALID_ARGUMENT / INTERNAL_ERROR

---

#### 4.6.2 `consistency_check`

**Purpose**
一致性/逻辑校验（规则优先，可选小模型辅助）：

* evidence_ids 是否存在
* price_bands 范围是否合法（min<=max）
* data_quality=PARTIAL 时 confidence 是否过高
* action=BUY 时 invalidations 是否足够等

**Idempotency**: YES
**Cache TTL**: 0s
**Cost Class**: LOW

**Input**

```json
{
  "report_json": {},
  "snapshot_ids": ["string"]
}
```

**Output**

```json
{
  "ok": true,
  "warnings": ["string"],
  "hard_failures": ["string"]
}
```

**Errors**

* INVALID_ARGUMENT / INTERNAL_ERROR

---

## 5. 版本化与变更策略（必须遵守）

### 5.1 兼容性规则

* 新增字段：允许（必须可选，且有默认处理）
* 删除/改名字段：禁止（除非升 major 版本且提供迁移方案）
* 枚举新增值：允许，但下游必须容错
* 输入字段的含义变更：必须升版本并在 Notes 中明确

### 5.2 Deprecation

对要废弃的工具：

* 在注册表中标记 `DEPRECATED`
* 保留至少一个稳定期（由你定义，比如 2 个版本）
* 提供替代工具名与迁移说明

---

## 6. 测试与验收建议（给工程团队用）

* 每个工具至少具备：

  * 参数校验单测（INVALID_ARGUMENT）
  * 上游失败模拟（UPSTREAM_TIMEOUT/ERROR）
  * 返回结构固定（字段/类型不漂移）
* 关键工具（facts / risk_gate / score_signal / graph）：

  * 使用固定 fixture 回放，保证可复现
* 对 search/rerank/extract：

  * 需要“黄金集”（golden set）做回归测试（避免质量悄悄变差）

---

## 7. 待扩展清单（未来工具可能加在这里）

* `get_sector_hotness_snapshot(sector, asof)`：板块级热度
* `get_peer_comparison(ticker, peers, asof)`：同业对比
* `simulate_portfolio_impact(...)`：组合级风险影响评估
* `alert_dispatch(...)`：触发器推送（邮件/钉钉/企微）

```
::contentReference[oaicite:0]{index=0}
```
