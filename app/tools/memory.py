from __future__ import annotations

from datetime import datetime, timezone
import uuid


_MEMORY_STORE: list[dict] = []


# TODO: Replace with pgvector + keyword retrieval.
def retrieve_memory_notes(ticker: str, query: str, top_k: int = 5, time_range: str | None = None) -> dict:
    filtered = [x for x in _MEMORY_STORE if x['ticker'] == ticker]
    return {'notes': filtered[-top_k:]}


# TODO: Persist into Postgres and embedding store.
def write_memory_note(note: dict) -> dict:
    payload = {
        'note_id': note.get('note_id', f'note_{uuid.uuid4().hex[:10]}'),
        'ticker': note['ticker'],
        'summary': note['summary'],
        'tags': note.get('tags', []),
        'importance': note.get('importance', 50),
        'created_at': datetime.now(timezone.utc).isoformat()
    }
    _MEMORY_STORE.append(payload)
    return {'ok': True, 'note_id': payload['note_id']}


# TODO: Add long-context rollup strategy.
def summarize_memory_rollup(ticker: str) -> dict:
    notes = [x for x in _MEMORY_STORE if x['ticker'] == ticker]
    return {'ticker': ticker, 'summary': f'{len(notes)} notes in memory store'}
