from __future__ import annotations

from datetime import datetime, timezone


# TODO: Replace with real retrieval and source attribution.
def search_event_docs(query: str, asof_range: str, top_k: int = 8) -> dict:
    docs = []
    for idx in range(top_k):
        docs.append(
            {
                'doc_id': f'doc_{idx+1:03d}',
                'title': f'Mock event document {idx+1}',
                'source': 'mock_news',
                'captured_at': datetime.now(timezone.utc).isoformat(),
                'snippet': f'Mock snippet for query={query}'
            }
        )
    return {'docs': docs}


# TODO: Replace with model-based reranking.
def rerank_docs(query: str, docs: list[dict], top_k: int = 5) -> dict:
    ranked = docs[:top_k]
    for i, doc in enumerate(ranked):
        doc['rank_score'] = round(1 - i * 0.1, 3)
    return {'docs': ranked}


# TODO: Replace with structured event extraction model.
def extract_events_from_docs(docs: list[dict]) -> dict:
    events = []
    for idx, doc in enumerate(docs):
        events.append(
            {
                'event_id': f'evt_{idx+1:03d}',
                'doc_id': doc['doc_id'],
                'type': 'POLICY',
                'direction': 'MIXED',
                'summary': f"Derived event from {doc['doc_id']}"
            }
        )
    return {'events': events}
