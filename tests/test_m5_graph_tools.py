from __future__ import annotations

from app.tools.graph import compute_exposure_score, find_impact_paths, query_supply_chain_subtree, reset_graph_runtime_state


def test_query_supply_chain_subtree_returns_stable_graph(monkeypatch) -> None:
    monkeypatch.setenv('GRAPH_DISABLE_NEO4J', '1')
    reset_graph_runtime_state()

    first = query_supply_chain_subtree(ticker='600519.SH', depth=3, include_competitors=True)
    second = query_supply_chain_subtree(ticker='600519.SH', depth=3, include_competitors=True)

    assert first['graph_id'] == second['graph_id']
    assert first['path_id'] == second['path_id']
    assert first['nodes']
    assert first['edges']
    assert any(str(node.get('label', '')).upper() == '600519.SH' for node in first['nodes'])


def test_find_impact_paths_returns_explainable_paths(monkeypatch) -> None:
    monkeypatch.setenv('GRAPH_DISABLE_NEO4J', '1')
    reset_graph_runtime_state()

    result = find_impact_paths(entity='sorghum price spike', ticker='600519.SH', max_hops=5)
    assert result['path_id']
    assert result['paths']
    top_path = result['paths'][0]
    assert top_path['nodes']
    assert top_path['edges']
    assert top_path['impact_direction'] in {'POS', 'NEG', 'MIXED', 'UNCERTAIN'}
    assert 0 <= float(top_path['confidence']) <= 1
    assert 0 <= float(top_path['weight']) <= 1
    assert top_path['explanation']


def test_compute_exposure_score_is_deterministic(monkeypatch) -> None:
    monkeypatch.setenv('GRAPH_DISABLE_NEO4J', '1')
    reset_graph_runtime_state()

    first = compute_exposure_score(ticker='600519.SH', entity='shipping disruption')
    second = compute_exposure_score(ticker='600519.SH', entity='shipping disruption')

    assert first['exposure_score'] == second['exposure_score']
    assert first['path_id'] == second['path_id']
    assert first['explanation']
