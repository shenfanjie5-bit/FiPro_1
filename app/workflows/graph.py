from __future__ import annotations

from app.workflows.checkpoint import save_checkpoint
from app.workflows.nodes import (
    build_context,
    build_facts,
    build_features,
    draft_report_node,
    generate_price_bands_node,
    init_data_quality,
    load_strategy_config,
    persist_node,
    publish_node,
    repair_report_node,
    retrieve_memory_node,
    risk_gate_node,
    score_signal_node,
    validate_node,
)


def run_research_workflow(request_data: dict, thread_id: str) -> dict:
    """Run MVP workflow.

    TODO:
    - Replace linear runner with full LangGraph StateGraph + conditional edges.
    - Add reviewer path for TIER2.
    """
    state: dict = {'request': request_data}

    for step_name, step_fn in [
        ('load_strategy_config', load_strategy_config),
        ('init_data_quality', init_data_quality),
        ('build_facts', build_facts),
        ('build_features', build_features),
        ('score_signal_node', score_signal_node),
        ('generate_price_bands_node', generate_price_bands_node),
        ('retrieve_memory_node', retrieve_memory_node),
        ('build_context', build_context),
        ('draft_report_node', draft_report_node),
        ('risk_gate_node', risk_gate_node),
    ]:
        state = step_fn(state)
        save_checkpoint(thread_id, step_name, state)

    state = validate_node(state)
    save_checkpoint(thread_id, 'validate_node', state)

    while (state.get('validation_errors') or state.get('consistency_errors')) and state['repair_attempts'] < state['max_repairs']:
        state = repair_report_node(state)
        state = risk_gate_node(state)
        state = validate_node(state)
        save_checkpoint(thread_id, f"repair_loop_{state['repair_attempts']}", state)

    state = persist_node(state)
    state = publish_node(state)
    save_checkpoint(thread_id, 'publish_node', state)

    return {'final_report': state['final_report']}
