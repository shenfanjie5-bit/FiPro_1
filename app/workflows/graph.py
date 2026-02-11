from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from app.workflows.checkpoint import get_checkpointer, get_latest_checkpoint
from app.workflows.nodes import (
    build_context,
    build_facts,
    build_features,
    draft_report_node,
    generate_price_bands_node,
    init_data_quality,
    load_strategy_config,
    mark_invalid_node,
    persist_node,
    publish_node,
    repair_report_node,
    rerank_docs_node,
    retrieve_memory_node,
    search_docs_node,
    risk_gate_node,
    score_signal_node,
    extract_events_node,
    validate_node,
)
from app.workflows.state import ResearchState


def _route_need_repair(state: ResearchState) -> Literal['persist', 'repair', 'invalid']:
    has_errors = bool(state.get('validation_errors') or state.get('consistency_errors'))
    if not has_errors:
        return 'persist'
    if state.get('repair_attempts', 0) >= state.get('max_repairs', 2):
        return 'invalid'
    return 'repair'


def _route_after_memory(state: ResearchState) -> Literal['direct_context', 'rag_chain']:
    req = state.get('request', {})
    tier = str(req.get('tier', 'TIER0'))
    config = state.get('config', {})
    policies = config.get('tier_policy', {})
    tier_cfg = policies.get(tier, {})
    rag_enabled = bool((tier_cfg.get('rag') or {}).get('enabled', False))
    budget = state.get('budget', {})
    budget_degraded = bool(budget.get('degraded', False))
    max_calls = int(budget.get('max_tool_calls', 0))
    used_calls = int(budget.get('used_tool_calls', 0))
    budget_exhausted = max_calls > 0 and used_calls >= max_calls
    if tier in ('TIER1', 'TIER2') and rag_enabled and not budget_degraded and not budget_exhausted:
        return 'rag_chain'
    return 'direct_context'


def _build_graph():
    builder = StateGraph(ResearchState)
    builder.add_node('load_strategy_config', load_strategy_config)
    builder.add_node('init_data_quality', init_data_quality)
    builder.add_node('build_facts', build_facts)
    builder.add_node('build_features', build_features)
    builder.add_node('score_signal_node', score_signal_node)
    builder.add_node('generate_price_bands_node', generate_price_bands_node)
    builder.add_node('retrieve_memory_node', retrieve_memory_node)
    builder.add_node('search_docs_node', search_docs_node)
    builder.add_node('rerank_docs_node', rerank_docs_node)
    builder.add_node('extract_events_node', extract_events_node)
    builder.add_node('build_context', build_context)
    builder.add_node('draft_report_node', draft_report_node)
    builder.add_node('risk_gate_node', risk_gate_node)
    builder.add_node('validate_node', validate_node)
    builder.add_node('repair_report_node', repair_report_node)
    builder.add_node('mark_invalid_node', mark_invalid_node)
    builder.add_node('persist_node', persist_node)
    builder.add_node('publish_node', publish_node)

    builder.add_edge(START, 'load_strategy_config')
    builder.add_edge('load_strategy_config', 'init_data_quality')
    builder.add_edge('init_data_quality', 'build_facts')
    builder.add_edge('build_facts', 'build_features')
    builder.add_edge('build_features', 'score_signal_node')
    builder.add_edge('score_signal_node', 'generate_price_bands_node')
    builder.add_edge('generate_price_bands_node', 'retrieve_memory_node')
    builder.add_conditional_edges(
        'retrieve_memory_node',
        _route_after_memory,
        {
            'direct_context': 'build_context',
            'rag_chain': 'search_docs_node',
        },
    )
    builder.add_edge('search_docs_node', 'rerank_docs_node')
    builder.add_edge('rerank_docs_node', 'extract_events_node')
    builder.add_edge('extract_events_node', 'build_context')
    builder.add_edge('build_context', 'draft_report_node')
    builder.add_edge('draft_report_node', 'risk_gate_node')
    builder.add_edge('risk_gate_node', 'validate_node')
    builder.add_conditional_edges(
        'validate_node',
        _route_need_repair,
        {
            'persist': 'persist_node',
            'repair': 'repair_report_node',
            'invalid': 'mark_invalid_node',
        },
    )
    builder.add_edge('repair_report_node', 'risk_gate_node')
    builder.add_edge('mark_invalid_node', 'persist_node')
    builder.add_edge('persist_node', 'publish_node')
    builder.add_edge('publish_node', END)
    return builder.compile(checkpointer=get_checkpointer())


RESEARCH_GRAPH = _build_graph()


def recover_research_state(thread_id: str) -> dict | None:
    return get_latest_checkpoint(thread_id)


def run_research_workflow(request_data: dict, thread_id: str) -> dict:
    state = RESEARCH_GRAPH.invoke(
        {'thread_id': thread_id, 'request': request_data},
        config={'configurable': {'thread_id': thread_id}},
    )
    return {
        'final_report': state['final_report'],
        'persist_refs': state.get('persist_refs', {}),
    }
