from __future__ import annotations


# TODO: Replace with real Neo4j traversal.
def query_supply_chain_subtree(ticker: str, depth: int) -> dict:
    return {
        'graph_id': f'graph_{ticker}_{depth}',
        'path_id': f'path_{ticker}_{depth}_001',
        'nodes': [{'id': ticker, 'label': 'Company'}],
        'edges': []
    }


# TODO: Replace with path search in graph DB.
def find_impact_paths(entity: str, ticker: str) -> dict:
    return {
        'path_id': f'impact_{entity}_{ticker}',
        'paths': [
            {
                'from': entity,
                'to': ticker,
                'weight': 0.4,
                'explanation': 'Mock path from entity to ticker'
            }
        ]
    }


# TODO: Replace with deterministic exposure scoring rules.
def compute_exposure_score(ticker: str, entity: str) -> dict:
    return {'ticker': ticker, 'entity': entity, 'exposure_score': 42}
