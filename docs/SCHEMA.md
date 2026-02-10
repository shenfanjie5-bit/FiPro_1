# SCHEMA.md

- Version: 0.1
- Status: Draft
- Last Updated: 2026-02-09 (America/Los_Angeles)
- Owner: (填写你/团队)
- Audience: 后端 / Agent 编排（LangGraph）/ 数据 / 测试 / 前端

> 目的：定义 LLM 工作流的“输入/输出/状态”强结构合约（Contract）。
> - **输入（GenerateReportRequest）**：触发一次研究/生成任务需要的参数。
> - **输出（Report）**：LLM+工具链产出的结构化报告，UI 展示与落库均依赖它。
> - **状态（AgentState）**：LangGraph 工作流各节点共享的状态对象结构（可选但强烈推荐）。
>
> 备注：Tools/Skills 的函数签名与 I/O 合约已拆分到 `TOOLS_SPEC.md`，本文件只关注“LLM 工作流的数据结构”。

---

## 0. 通用约定

### 0.1 时间与时区
- 所有时间字段使用 ISO 8601 且带时区：`YYYY-MM-DDTHH:mm:ss±HH:MM`
- 示例：`2026-02-09T09:35:00-08:00`

### 0.2 数值单位
- `pct`：百分比用小数（5% → `0.05`）
- `price`：价格金额按 `currency` 指定
- `score`：推荐指数 0~100 的整数
- `confidence`：0~1 的小数

### 0.3 ID 与可追溯性
- `*_id` 使用 UUID 或可追溯字符串（如 `snap_20260209_600519_0930`）
- 所有可引用的对象必须能被持久化或可重建：
  - `snapshot_id`（事实快照）
  - `doc_id`（文档）
  - `path_id`/`graph_id`（图谱查询）
  - `note_id`（记忆条目）

### 0.4 数据缺失与反幻觉规则（强约束）
- 若某字段缺失或不可得：
  1) 工具层必须在 `data_quality.missing_fields[]` 标记；
  2) 报告 `data_quality.status` 应降级；
  3) LLM 必须在结论里体现不确定性（降低 confidence 或 action）。
- **禁止** LLM 编造未出现在事实快照或证据中的“具体数据/事件”。

### 0.5 交叉校验（非 Schema 能力）
以下规则建议由 `consistency_check` 工具或后端校验实现：
- `PriceRange.min <= PriceRange.max`
- 当 `data_quality.status != OK` 时，限制 `decision.confidence` 上限
- `decision.action=BUY` 时，至少给出 2 条 `invalidations`
- `evidence_ids` 必须都能在 `evidence_refs[].evidence_id` 中找到

---

## 1) GenerateReportRequest（触发生成请求）JSON Schema

> 这是你后端/前端触发 LangGraph 工作流的输入对象。
> 不直接给 LLM（除非你把它当 state 的一部分）。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://yourdomain/schemas/generate-report-request.schema.json",
  "title": "GenerateReportRequest",
  "type": "object",
  "additionalProperties": false,
  "required": ["ticker", "asof", "strategy_version_id", "tier"],
  "properties": {
    "ticker": {
      "type": "string",
      "description": "标的代码（你自定义格式：600519.SH / AAPL / BTC-USD 等）",
      "minLength": 1
    },
    "market": {
      "type": "string",
      "description": "市场标识（可选）",
      "enum": ["CN_A", "US", "HK", "CRYPTO", "OTHER"]
    },
    "asof": {
      "type": "string",
      "format": "date-time",
      "description": "分析基准时刻（盘前/盘中某时刻）"
    },
    "strategy_version_id": {
      "type": "string",
      "description": "策略版本 ID（immutable：权重+风控参数）"
    },
    "tier": {
      "type": "string",
      "description": "关注等级（决定检索深度/预算）",
      "enum": ["TIER0", "TIER1", "TIER2"]
    },
    "run_mode": {
      "type": "string",
      "description": "运行模式：LIVE 为实际展示；SHADOW 为影子对比；BACKTEST 为历史回放",
      "enum": ["LIVE", "SHADOW", "BACKTEST"],
      "default": "LIVE"
    },
    "options": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "force_refresh": {
          "type": "boolean",
          "description": "是否跳过缓存强制刷新",
          "default": false
        },
        "max_tool_calls": {
          "type": "integer",
          "minimum": 1,
          "maximum": 200,
          "default": 40
        },
        "max_cost_usd": {
          "type": "number",
          "minimum": 0,
          "default": 2.0
        },
        "allowed_sources": {
          "type": "array",
          "items": { "type": "string" },
          "description": "检索/引用的数据源白名单（可选）"
        },
        "horizon": {
          "type": "string",
          "description": "策略时间跨度",
          "enum": ["INTRADAY", "SWING", "POSITION"],
          "default": "SWING"
        }
      }
    }
  }
}
````

---

## 2) Report（每日研究报告输出）JSON Schema

> 这是你 UI、落库、复盘、记忆写入的核心结构。
> **强烈建议**：LLM 的最终输出必须完全符合本 schema（不通过则不发布/不落库或标记 invalid）。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://yourdomain/schemas/report.schema.json",
  "title": "DailyStockResearchReport",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "report_id",
    "generated_at",
    "ticker",
    "asof",
    "strategy_version_id",
    "tier",
    "decision",
    "price_bands",
    "key_drivers_to_watch",
    "thesis",
    "risk_flags",
    "invalidations",
    "evidence_refs",
    "data_quality",
    "provenance",
    "memory_update"
  ],
  "properties": {
    "schema_version": {
      "type": "string",
      "const": "0.1"
    },
    "report_id": {
      "type": "string",
      "description": "报告唯一 ID（UUID 或命名规则）"
    },
    "generated_at": {
      "type": "string",
      "format": "date-time",
      "description": "报告生成时间（落库时间）"
    },
    "ticker": { "type": "string", "minLength": 1 },
    "market": {
      "type": "string",
      "enum": ["CN_A", "US", "HK", "CRYPTO", "OTHER"],
      "description": "市场标识（可选但建议输出）"
    },
    "asof": {
      "type": "string",
      "format": "date-time",
      "description": "分析基准时刻"
    },
    "strategy_version_id": { "type": "string" },
    "tier": { "type": "string", "enum": ["TIER0", "TIER1", "TIER2"] },

    "decision": { "$ref": "#/$defs/Decision" },

    "price_bands": {
      "type": "array",
      "minItems": 1,
      "maxItems": 10,
      "items": { "$ref": "#/$defs/PriceBand" }
    },

    "key_drivers_to_watch": {
      "type": "array",
      "minItems": 1,
      "maxItems": 20,
      "items": { "$ref": "#/$defs/KeyDriver" }
    },

    "thesis": { "$ref": "#/$defs/Thesis" },

    "risk_flags": {
      "type": "array",
      "minItems": 0,
      "maxItems": 20,
      "items": { "$ref": "#/$defs/RiskFlag" }
    },

    "invalidations": {
      "type": "array",
      "minItems": 1,
      "maxItems": 20,
      "items": { "$ref": "#/$defs/Invalidation" }
    },

    "evidence_refs": {
      "type": "array",
      "minItems": 1,
      "maxItems": 60,
      "items": { "$ref": "#/$defs/EvidenceRef" }
    },

    "data_quality": { "$ref": "#/$defs/DataQuality" },

    "provenance": { "$ref": "#/$defs/Provenance" },

    "memory_update": { "$ref": "#/$defs/MemoryUpdate" }
  },

  "$defs": {
    "Decision": {
      "type": "object",
      "additionalProperties": false,
      "required": ["action", "overall_score", "confidence", "time_horizon", "summary"],
      "properties": {
        "action": {
          "type": "string",
          "enum": ["BUY", "WATCH", "AVOID"],
          "description": "建议动作（最终可被风控门禁降级）"
        },
        "overall_score": {
          "type": "integer",
          "minimum": 0,
          "maximum": 100,
          "description": "综合推荐指数"
        },
        "confidence": {
          "type": "number",
          "minimum": 0,
          "maximum": 1,
          "description": "置信度（必须受 data_quality 与风控影响）"
        },
        "time_horizon": {
          "type": "string",
          "enum": ["INTRADAY", "SWING", "POSITION"],
          "description": "该建议适用的时间跨度"
        },
        "summary": {
          "type": "string",
          "minLength": 1,
          "maxLength": 240,
          "description": "一句话总结：为什么是这个 action（给 UI 用）"
        },
        "positioning_hint": {
          "type": "object",
          "additionalProperties": false,
          "description": "可选：低风险偏好的仓位提示（最终由 risk_gate 裁决）",
          "properties": {
            "max_portfolio_pct": { "type": "number", "minimum": 0, "maximum": 1 },
            "style": { "type": "string", "enum": ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] }
          }
        }
      }
    },

    "PriceBand": {
      "type": "object",
      "additionalProperties": false,
      "required": ["band_id", "range", "score", "confidence", "rationale", "entry_conditions", "exit_conditions"],
      "properties": {
        "band_id": { "type": "string", "minLength": 1, "description": "段位 ID（B1/B2...）" },
        "range": { "$ref": "#/$defs/PriceRange" },
        "score": { "type": "integer", "minimum": 0, "maximum": 100 },
        "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
        "rationale": {
          "type": "string",
          "minLength": 1,
          "maxLength": 1200,
          "description": "为什么这个价格段更合适/不合适（应引用事实与条件）"
        },
        "entry_conditions": {
          "type": "array",
          "minItems": 1,
          "maxItems": 10,
          "items": { "$ref": "#/$defs/Condition" }
        },
        "exit_conditions": {
          "type": "array",
          "minItems": 1,
          "maxItems": 10,
          "items": { "$ref": "#/$defs/Condition" }
        },
        "risk_note": {
          "type": "string",
          "maxLength": 400,
          "description": "该段位特有风险提示（可选）"
        }
      }
    },

    "PriceRange": {
      "type": "object",
      "additionalProperties": false,
      "required": ["currency", "min", "max"],
      "properties": {
        "currency": { "type": "string", "minLength": 1, "maxLength": 8 },
        "min": { "type": "number", "minimum": 0 },
        "max": { "type": "number", "minimum": 0 }
      }
    },

    "Condition": {
      "type": "object",
      "additionalProperties": false,
      "required": ["type", "description", "priority"],
      "properties": {
        "type": {
          "type": "string",
          "enum": ["TECHNICAL", "FUNDAMENTAL", "FLOW", "EVENT", "RISK", "OTHER"]
        },
        "description": { "type": "string", "minLength": 1, "maxLength": 240 },
        "priority": { "type": "string", "enum": ["HIGH", "MEDIUM", "LOW"] },
        "metric_ref": {
          "type": "string",
          "description": "可选：指向事实快照字段（例如 snapshot.market.atr_14）",
          "maxLength": 120
        },
        "threshold": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "op": { "type": "string", "enum": [">", ">=", "<", "<=", "==", "!="] },
            "value": { "type": ["number", "string", "boolean"] }
          }
        },
        "evidence_ids": {
          "type": "array",
          "items": { "type": "string" },
          "description": "可选：支持该条件的证据 ID（引用 evidence_refs）"
        }
      }
    },

    "KeyDriver": {
      "type": "object",
      "additionalProperties": false,
      "required": ["driver_id", "type", "what", "direction", "urgency", "monitor", "impact_hypothesis"],
      "properties": {
        "driver_id": { "type": "string", "minLength": 1 },
        "type": {
          "type": "string",
          "enum": [
            "GEOPOLITICS",
            "COMMODITY",
            "POLICY",
            "LOGISTICS",
            "EARNINGS",
            "COMPETITION",
            "FX_RATES",
            "RATES",
            "SUPPLY_DEMAND",
            "OTHER"
          ]
        },
        "what": {
          "type": "string",
          "minLength": 1,
          "maxLength": 200,
          "description": "需要关注的具体信息/事件/变量"
        },
        "direction": { "type": "string", "enum": ["POS", "NEG", "MIXED", "UNCERTAIN"] },
        "urgency": { "type": "string", "enum": ["HIGH", "MEDIUM", "LOW"] },
        "impact_hypothesis": {
          "type": "string",
          "minLength": 1,
          "maxLength": 600,
          "description": "该变量如何影响股价/基本面（简洁可复盘）"
        },
        "monitor": { "$ref": "#/$defs/MonitorSpec" },
        "evidence_ids": {
          "type": "array",
          "items": { "type": "string" },
          "description": "引用 evidence_refs 中的 evidence_id"
        },
        "graph_refs": {
          "type": "array",
          "items": { "type": "string" },
          "description": "可选：关联图谱查询结果 ID（如 path_id/graph_id）"
        }
      }
    },

    "MonitorSpec": {
      "type": "object",
      "additionalProperties": false,
      "required": ["signals", "triggers"],
      "properties": {
        "signals": {
          "type": "array",
          "minItems": 1,
          "maxItems": 10,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["name", "source"],
            "properties": {
              "name": { "type": "string", "minLength": 1, "maxLength": 80 },
              "source": { "type": "string", "minLength": 1, "maxLength": 80 },
              "metric_ref": { "type": "string", "maxLength": 120 }
            }
          }
        },
        "triggers": {
          "type": "array",
          "minItems": 1,
          "maxItems": 10,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["description", "severity"],
            "properties": {
              "description": { "type": "string", "minLength": 1, "maxLength": 200 },
              "severity": { "type": "string", "enum": ["HIGH", "MEDIUM", "LOW"] }
            }
          }
        }
      }
    },

    "Thesis": {
      "type": "object",
      "additionalProperties": false,
      "required": ["bull_case", "bear_case", "base_case", "next_steps"],
      "properties": {
        "base_case": {
          "type": "string",
          "minLength": 1,
          "maxLength": 900,
          "description": "基准情景（更倾向的路径）"
        },
        "bull_case": {
          "type": "string",
          "minLength": 1,
          "maxLength": 900,
          "description": "乐观情景（上涨催化）"
        },
        "bear_case": {
          "type": "string",
          "minLength": 1,
          "maxLength": 900,
          "description": "悲观情景（下行风险与触发点）"
        },
        "next_steps": {
          "type": "array",
          "minItems": 1,
          "maxItems": 12,
          "items": { "type": "string", "minLength": 1, "maxLength": 160 },
          "description": "后续要验证什么（可作为重点关注自动任务）"
        }
      }
    },

    "RiskFlag": {
      "type": "object",
      "additionalProperties": false,
      "required": ["risk_id", "severity", "description"],
      "properties": {
        "risk_id": { "type": "string", "minLength": 1 },
        "severity": { "type": "string", "enum": ["HIGH", "MEDIUM", "LOW"] },
        "description": { "type": "string", "minLength": 1, "maxLength": 240 },
        "evidence_ids": { "type": "array", "items": { "type": "string" } }
      }
    },

    "Invalidation": {
      "type": "object",
      "additionalProperties": false,
      "required": ["invalidation_id", "description", "priority"],
      "properties": {
        "invalidation_id": { "type": "string", "minLength": 1 },
        "description": { "type": "string", "minLength": 1, "maxLength": 240 },
        "priority": { "type": "string", "enum": ["HIGH", "MEDIUM", "LOW"] },
        "evidence_ids": { "type": "array", "items": { "type": "string" } }
      }
    },

    "EvidenceRef": {
      "type": "object",
      "additionalProperties": false,
      "required": ["evidence_id", "type", "title", "source", "captured_at"],
      "properties": {
        "evidence_id": { "type": "string", "minLength": 1 },
        "type": {
          "type": "string",
          "enum": ["SNAPSHOT_FIELD", "NEWS_DOC", "FILINGS", "MACRO_SERIES", "GRAPH_QUERY", "MANUAL_NOTE"]
        },
        "title": { "type": "string", "minLength": 1, "maxLength": 140 },
        "source": { "type": "string", "minLength": 1, "maxLength": 80 },
        "captured_at": { "type": "string", "format": "date-time" },
        "uri": { "type": "string", "description": "可选：文档或内部定位符" },
        "snippet": {
          "type": "string",
          "maxLength": 240,
          "description": "可选：极短摘要（避免长引用）"
        },
        "checksum": { "type": "string", "description": "可选：内容一致性校验" }
      }
    },

    "DataQuality": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "missing_fields", "notes"],
      "properties": {
        "status": { "type": "string", "enum": ["OK", "DEGRADED", "PARTIAL"] },
        "missing_fields": { "type": "array", "items": { "type": "string" } },
        "notes": { "type": "string", "maxLength": 400 }
      }
    },

    "Provenance": {
      "type": "object",
      "additionalProperties": false,
      "required": ["model", "router_policy", "snapshot_ids", "weights_hash", "run_mode"],
      "properties": {
        "model": {
          "type": "object",
          "additionalProperties": false,
          "required": ["primary", "reviewer"],
          "properties": {
            "primary": { "type": "string", "description": "主模型标识（厂商+模型名+版本）" },
            "reviewer": { "type": "string", "description": "复核模型标识（可为 NONE）" }
          }
        },
        "router_policy": { "type": "string", "description": "模型路由策略版本/名称" },
        "snapshot_ids": {
          "type": "array",
          "items": { "type": "string" },
          "description": "本次报告引用的事实快照 ID 列表"
        },
        "weights_hash": { "type": "string", "description": "权重配置哈希（确保可回放一致）" },
        "run_mode": { "type": "string", "enum": ["LIVE", "SHADOW", "BACKTEST"] },
        "tool_call_stats": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "tool_calls": { "type": "integer", "minimum": 0 },
            "latency_ms": { "type": "integer", "minimum": 0 },
            "cost_usd_est": { "type": "number", "minimum": 0 }
          }
        }
      }
    },

    "MemoryUpdate": {
      "type": "object",
      "additionalProperties": false,
      "required": ["summary", "tags", "importance", "followups"],
      "properties": {
        "summary": {
          "type": "string",
          "minLength": 1,
          "maxLength": 600,
          "description": "写入记忆库的滚动摘要（短）"
        },
        "tags": {
          "type": "array",
          "minItems": 1,
          "maxItems": 15,
          "items": { "type": "string", "minLength": 1, "maxLength": 40 }
        },
        "importance": {
          "type": "integer",
          "minimum": 0,
          "maximum": 100,
          "description": "用于召回排序/重点盯盘强度"
        },
        "followups": {
          "type": "array",
          "minItems": 1,
          "maxItems": 10,
          "items": { "type": "string", "minLength": 1, "maxLength": 180 },
          "description": "后续需要继续检索/验证的问题清单"
        }
      }
    }
  }
}
```

---

## 3) AgentState（LangGraph 工作流状态）JSON Schema（推荐）

> 说明：
>
> * 这是 LangGraph 节点之间传递的状态对象结构。
> * 目的：让每个节点“读什么/写什么”非常明确，方便调试、回放、trace、评估。
> * 你可以在 MVP 阶段只用其中子集字段；但建议保持字段名一致，避免后期迁移痛苦。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://yourdomain/schemas/agent-state.schema.json",
  "title": "AgentState",
  "type": "object",
  "additionalProperties": false,
  "required": ["request", "budget", "artifacts", "status"],
  "properties": {
    "request": {
      "description": "触发请求（原样保留，便于回放）",
      "$ref": "https://yourdomain/schemas/generate-report-request.schema.json"
    },

    "budget": {
      "type": "object",
      "additionalProperties": false,
      "required": ["max_tool_calls", "max_cost_usd", "tool_calls_used", "cost_usd_est"],
      "properties": {
        "max_tool_calls": { "type": "integer", "minimum": 1, "maximum": 200 },
        "max_cost_usd": { "type": "number", "minimum": 0 },
        "tool_calls_used": { "type": "integer", "minimum": 0 },
        "cost_usd_est": { "type": "number", "minimum": 0 }
      }
    },

    "artifacts": {
      "type": "object",
      "additionalProperties": false,
      "required": ["snapshots", "docs", "events", "graph", "memory", "scores", "gates", "drafts", "validations", "trace"],
      "properties": {
        "snapshots": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "market_snapshot": { "type": "object" },
            "fundamentals_snapshot": { "type": "object" },
            "flow_sentiment_snapshot": { "type": "object" },
            "macro_snapshot": { "type": "object" },
            "snapshot_ids": { "type": "array", "items": { "type": "string" } },
            "data_quality": { "$ref": "#/$defs/StateDataQuality" }
          }
        },

        "docs": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "raw_docs": { "type": "array", "items": { "type": "object" } },
            "ranked_doc_ids": { "type": "array", "items": { "type": "string" } }
          }
        },

        "events": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "extracted_events": { "type": "array", "items": { "type": "object" } },
            "selected_key_events": { "type": "array", "items": { "type": "object" } }
          }
        },

        "graph": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "supply_chain_subtree": { "type": "object" },
            "impact_paths": { "type": "array", "items": { "type": "object" } },
            "exposures": { "type": "array", "items": { "type": "object" } }
          }
        },

        "memory": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "retrieved_notes": { "type": "array", "items": { "type": "object" } },
            "rollup_summary": { "type": "string" }
          }
        },

        "scores": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "signal_score": { "type": "object" },
            "price_bands": { "type": "object" }
          }
        },

        "gates": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "risk_gate": { "type": "object" }
          }
        },

        "drafts": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "draft_report": { "type": "object", "description": "LLM 生成的草稿（未必合规）" },
            "final_report": {
              "type": "object",
              "description": "最终报告（应符合 Report schema）"
            }
          }
        },

        "validations": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "schema_validation": { "type": "object" },
            "consistency_validation": { "type": "object" }
          }
        },

        "trace": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "tool_calls": {
              "type": "array",
              "items": { "$ref": "#/$defs/ToolCallTrace" }
            },
            "notes": { "type": "array", "items": { "type": "string" } }
          }
        }
      }
    },

    "status": {
      "type": "object",
      "additionalProperties": false,
      "required": ["phase", "ok", "warnings", "errors"],
      "properties": {
        "phase": {
          "type": "string",
          "enum": [
            "INIT",
            "LOAD_CONFIG",
            "BUILD_SNAPSHOTS",
            "RETRIEVE_MEMORY",
            "SEARCH_DOCS",
            "GRAPH_QUERIES",
            "COMPUTE_SCORES",
            "DRAFT_REPORT",
            "RISK_GATE",
            "REVIEW_FIX",
            "VALIDATE",
            "PERSIST",
            "DONE",
            "FAILED"
          ]
        },
        "ok": { "type": "boolean" },
        "warnings": { "type": "array", "items": { "type": "string" } },
        "errors": { "type": "array", "items": { "type": "string" } }
      }
    }
  },

  "$defs": {
    "StateDataQuality": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "missing_fields"],
      "properties": {
        "status": { "type": "string", "enum": ["OK", "DEGRADED", "PARTIAL"] },
        "missing_fields": { "type": "array", "items": { "type": "string" } }
      }
    },
    "ToolCallTrace": {
      "type": "object",
      "additionalProperties": false,
      "required": ["tool_name", "started_at", "ended_at", "ok"],
      "properties": {
        "tool_name": { "type": "string" },
        "started_at": { "type": "string", "format": "date-time" },
        "ended_at": { "type": "string", "format": "date-time" },
        "ok": { "type": "boolean" },
        "input_digest": { "type": "string", "description": "输入摘要/哈希（避免记录敏感原文）" },
        "output_digest": { "type": "string", "description": "输出摘要/哈希" },
        "error_code": { "type": "string" },
        "cost_usd_est": { "type": "number", "minimum": 0 },
        "latency_ms": { "type": "integer", "minimum": 0 }
      }
    }
  }
}
```

---

## 4) 示例对象（用于单元测试/回放）

### 4.1 GenerateReportRequest 示例

```json
{
  "ticker": "600519.SH",
  "market": "CN_A",
  "asof": "2026-02-09T08:55:00-08:00",
  "strategy_version_id": "strat_v12",
  "tier": "TIER1",
  "run_mode": "LIVE",
  "options": {
    "force_refresh": false,
    "max_tool_calls": 40,
    "max_cost_usd": 2.0,
    "horizon": "SWING"
  }
}
```

### 4.2 Report 示例（结构示例，非真实数据）

> 你可直接把它作为“schema 校验通过”的 fixture。

```json
{
  "schema_version": "0.1",
  "report_id": "rpt_20260209_600519_001",
  "generated_at": "2026-02-09T08:55:12-08:00",
  "ticker": "600519.SH",
  "market": "CN_A",
  "asof": "2026-02-09T08:55:00-08:00",
  "strategy_version_id": "strat_v12",
  "tier": "TIER1",
  "decision": {
    "action": "WATCH",
    "overall_score": 68,
    "confidence": 0.62,
    "time_horizon": "SWING",
    "summary": "热点强度尚可但拥挤度偏高，低风险偏好更适合等待回撤确认",
    "positioning_hint": { "max_portfolio_pct": 0.05, "style": "CONSERVATIVE" }
  },
  "price_bands": [
    {
      "band_id": "B1",
      "range": { "currency": "CNY", "min": 1450, "max": 1480 },
      "score": 78,
      "confidence": 0.58,
      "rationale": "接近关键均线/波动带下沿，回撤后风险收益更匹配低风险偏好",
      "entry_conditions": [
        { "type": "TECHNICAL", "description": "回撤后量能止跌并出现企稳信号", "priority": "HIGH" }
      ],
      "exit_conditions": [
        { "type": "RISK", "description": "跌破关键支撑并放量，触发止损门禁", "priority": "HIGH" }
      ],
      "risk_note": "若外部冲击升级，段位逻辑可能失效"
    }
  ],
  "key_drivers_to_watch": [
    {
      "driver_id": "D1",
      "type": "COMMODITY",
      "what": "核心原材料价格短期上行",
      "direction": "NEG",
      "urgency": "MEDIUM",
      "impact_hypothesis": "成本上行可能压缩利润率，需观察是否能顺利提价传导",
      "monitor": {
        "signals": [
          { "name": "原材料价格指数", "source": "DATA_PROVIDER_X", "metric_ref": "macro.series.OIL" }
        ],
        "triggers": [
          { "description": "指数连续两周上行且涨幅>5%", "severity": "MEDIUM" }
        ]
      },
      "evidence_ids": ["ev_001"]
    }
  ],
  "thesis": {
    "base_case": "行业景气稳定、龙头护城河仍在，但短期更应关注拥挤度与回撤窗口。",
    "bull_case": "若需求超预期或提价顺利，利润端改善推动估值上修。",
    "bear_case": "若成本上行无法传导或外部预期走弱，可能出现持续回撤。",
    "next_steps": ["跟踪原材料/渠道反馈", "观察回撤后量价结构是否转强"]
  },
  "risk_flags": [
    { "risk_id": "R1", "severity": "MEDIUM", "description": "短期拥挤度偏高，回撤波动可能放大", "evidence_ids": ["ev_002"] }
  ],
  "invalidations": [
    { "invalidation_id": "I1", "description": "关键支撑失守且成交量放大", "priority": "HIGH", "evidence_ids": ["ev_003"] }
  ],
  "evidence_refs": [
    {
      "evidence_id": "ev_001",
      "type": "MACRO_SERIES",
      "title": "原材料价格指数",
      "source": "DATA_PROVIDER_X",
      "captured_at": "2026-02-09T08:40:00-08:00"
    },
    {
      "evidence_id": "ev_002",
      "type": "SNAPSHOT_FIELD",
      "title": "拥挤度指标",
      "source": "flow_20260209_600519.SH",
      "captured_at": "2026-02-09T08:45:00-08:00"
    },
    {
      "evidence_id": "ev_003",
      "type": "SNAPSHOT_FIELD",
      "title": "关键支撑/均线",
      "source": "snap_20260209_600519.SH_0930",
      "captured_at": "2026-02-09T08:45:00-08:00"
    }
  ],
  "data_quality": { "status": "OK", "missing_fields": [], "notes": "" },
  "provenance": {
    "model": { "primary": "VENDOR_X:model_strong_v3", "reviewer": "VENDOR_X:model_fast_v2" },
    "router_policy": "router_v1",
    "snapshot_ids": ["snap_20260209_600519.SH_0930", "flow_20260209_600519.SH", "macro_20260209_global"],
    "weights_hash": "w_9c1a...f",
    "run_mode": "LIVE",
    "tool_call_stats": { "tool_calls": 18, "latency_ms": 5200, "cost_usd_est": 0.42 }
  },
  "memory_update": {
    "summary": "短期偏观察：拥挤度限制，低风险偏好等待回撤企稳更佳；重点跟踪成本与回撤后量价结构。",
    "tags": ["leader", "hot-theme", "low-risk", "watch"],
    "importance": 72,
    "followups": ["若回撤到B1区间，验证量价企稳信号", "跟踪原材料两周趋势与提价传导"]
  }
}
```

---

## 5) 版本化与变更策略（必须遵守）

### 5.1 Schema 版本规则

* `schema_version` 为字符串（如 `"0.1"`）
* **向后兼容优先**：

  * 新增字段：允许（尽量 optional）
  * 删除字段/改名：禁止（除非升 major 且提供迁移）
  * 枚举加值：允许，但下游需容错

### 5.2 与工具合约的关系

* 工具 I/O 在 `TOOLS_SPEC.md` 版本化。
* Report/State 的字段一旦被 UI 或落库依赖，应避免频繁变动。
* 推荐做法：工具层可以更灵活迭代，Report schema 更保守稳定。

