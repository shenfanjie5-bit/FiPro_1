from __future__ import annotations

from typing import Any, TypedDict


class ResearchState(TypedDict, total=False):
    thread_id: str
    request: dict[str, Any]
    config: dict[str, Any]
    skill_pack: dict[str, Any]
    budget: dict[str, Any]
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
    skill_notes: list[dict[str, Any]]
    doc_queries: list[str]
    doc_candidates: list[dict[str, Any]]
    ranked_docs: list[dict[str, Any]]
    ranked_doc_ids: list[str]
    extracted_events: list[dict[str, Any]]
    event_docs: list[dict[str, Any]]
    graph_subtree: dict[str, Any]
    impact_paths: list[dict[str, Any]]
    exposure_scores: list[dict[str, Any]]
    graph_refs: list[str]
    local_data: dict[str, Any]
    evidence_coverage: dict[str, Any]
    reviewer_notes: list[str]
    context: dict[str, Any]
    ta_hybrid_state: dict[str, Any]
    ta_hybrid_views: dict[str, Any]
    ta_hybrid_signal: dict[str, Any]
    ta_hybrid_evidence_refs: list[dict[str, Any]]
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
    degradation_matrix: dict[str, Any]
    persist_refs: dict[str, str]
