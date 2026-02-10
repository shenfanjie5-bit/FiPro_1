from __future__ import annotations

from typing import Any, TypedDict


class ResearchState(TypedDict, total=False):
    request: dict[str, Any]
    config: dict[str, Any]
    data_quality: dict[str, Any]
    snapshots: dict[str, Any]
    snapshot_ids: list[str]
    features: dict[str, Any]
    score: dict[str, Any]
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
    weights_hash: str
    tool_call_stats: dict[str, Any]
