from __future__ import annotations

from typing import Any, TypedDict


class ResearchState(TypedDict, total=False):
    thread_id: str
    request: dict[str, Any]
    config: dict[str, Any]
    data_quality: dict[str, Any]
    snapshots: dict[str, Any]
    snapshot_ids: list[str]
    feature_id: str
    features: dict[str, Any]
    score_id: str
    score: dict[str, Any]
    price_band_set_id: str
    price_bands: list[dict[str, Any]]
    memory_notes: list[dict[str, Any]]
    context: dict[str, Any]
    report_draft: dict[str, Any]
    final_report: dict[str, Any]
    validation_errors: list[str]
    consistency_errors: list[str]
    tool_traces: list[dict[str, Any]]
    repair_attempts: int
    max_repairs: int
    workflow_invalid: bool
    risk_gate_hard_blocks: list[str]
    weights_hash: str
    tool_call_stats: dict[str, Any]
    persist_refs: dict[str, str]
