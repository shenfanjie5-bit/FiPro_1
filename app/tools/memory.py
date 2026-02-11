from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import uuid
from typing import Any

from app.tools.cache import TTLCache


MEMORY_CACHE_TTL_SECONDS = 3 * 60
_MEMORY_CACHE: TTLCache[dict[str, Any]] = TTLCache()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _runtime_db_path() -> Path:
    return Path(os.getenv('WORKFLOW_RUNTIME_DB', os.getenv('WORKFLOW_CHECKPOINT_DB', 'checkpoint.db')))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_runtime_db_path(), timeout=30)
    conn.execute(
        'create table if not exists memory_notes_runtime ('
        'note_id text primary key, '
        'ticker text not null, '
        'summary text not null, '
        'tags_json text not null, '
        'importance integer not null, '
        'links_json text not null, '
        'dedupe_key text, '
        'embedding_json text not null, '
        'created_at text not null, '
        'updated_at text not null'
        ')'
    )
    conn.execute('create index if not exists idx_memory_notes_runtime_ticker_created on memory_notes_runtime(ticker, created_at desc)')
    conn.execute('create index if not exists idx_memory_notes_runtime_dedupe on memory_notes_runtime(ticker, dedupe_key)')
    return conn


def _parse_iso_to_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_int(value: Any, default: int, *, min_value: int, max_value: int) -> int:
    try:
        output = int(value)
    except (TypeError, ValueError):
        output = default
    return max(min_value, min(max_value, output))


def _tokenize_text(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r'[\w\u4e00-\u9fff]+', text or '') if token.strip()]


def _normalize_tags(tags: Any) -> list[str]:
    if not isinstance(tags, (list, tuple, set)):
        return []
    output: list[str] = []
    for item in tags:
        value = str(item).strip()
        if value and value not in output:
            output.append(value[:40])
    return output[:20]


def _normalize_links(links: Any) -> list[str]:
    if not isinstance(links, (list, tuple, set)):
        return []
    output: list[str] = []
    for item in links:
        value = str(item).strip()
        if value and value not in output:
            output.append(value[:180])
    return output[:20]


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)


def _json_load(payload: str, fallback: Any) -> Any:
    try:
        return json.loads(payload)
    except Exception:  # noqa: BLE001
        return fallback


def _embedding(text: str, *, dim: int = 32) -> list[float]:
    vector = [0.0 for _ in range(dim)]
    for token in _tokenize_text(text):
        digest = hashlib.sha256(token.encode('utf-8')).digest()
        bucket = int.from_bytes(digest[:2], 'big') % dim
        sign = -1.0 if digest[2] % 2 else 1.0
        vector[bucket] += sign * (1.0 + (digest[3] / 255.0))
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [round(value / norm, 6) for value in vector]


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def _resolve_time_range_days(time_range: str | None, time_range_days: int | None) -> int:
    if time_range_days is not None:
        return _safe_int(time_range_days, 180, min_value=1, max_value=3650)
    if time_range:
        normalized = str(time_range).strip().lower()
        digits = ''.join(ch for ch in normalized if ch.isdigit())
        if digits:
            return _safe_int(digits, 180, min_value=1, max_value=3650)
    return 180


def _fetch_notes(ticker: str, cutoff_at: datetime) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            'select note_id, ticker, summary, tags_json, importance, links_json, dedupe_key, embedding_json, created_at '
            'from memory_notes_runtime where ticker = ? and created_at >= ? '
            'order by created_at desc limit 500',
            (ticker, cutoff_at.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    notes: list[dict[str, Any]] = []
    for row in rows:
        notes.append(
            {
                'note_id': row[0],
                'ticker': row[1],
                'summary': row[2],
                'tags': _json_load(row[3], []),
                'importance': _safe_int(row[4], 50, min_value=0, max_value=100),
                'links': _json_load(row[5], []),
                'dedupe_key': row[6],
                'embedding': _json_load(row[7], []),
                'created_at': row[8],
            }
        )
    return notes


def _score_memory_note(
    note: dict[str, Any],
    *,
    query_tokens: set[str],
    query_embedding: list[float],
    now: datetime,
) -> tuple[float, str]:
    content = f"{note.get('summary', '')} {' '.join(note.get('tags', []))} {' '.join(note.get('links', []))}"
    note_tokens = set(_tokenize_text(content))
    overlap = len(query_tokens.intersection(note_tokens))
    keyword_score = overlap / max(1, len(query_tokens))
    vector_score = max(0.0, _cosine_similarity(query_embedding, list(note.get('embedding', []))))
    importance_score = _safe_int(note.get('importance'), 50, min_value=0, max_value=100) / 100.0
    try:
        age_days = max(0.0, (now - _parse_iso_to_utc(note.get('created_at'))).total_seconds() / 86400.0)
    except Exception:  # noqa: BLE001
        age_days = 365.0
    recency_score = max(0.0, 1.0 - min(age_days, 365.0) / 365.0)
    score = round(min(1.0, 0.45 * keyword_score + 0.25 * vector_score + 0.2 * importance_score + 0.1 * recency_score), 6)
    reason = f'kw={keyword_score:.3f};vec={vector_score:.3f};importance={importance_score:.3f};recency={recency_score:.3f}'
    return score, reason


def retrieve_memory_notes(
    ticker: str,
    query: str,
    top_k: int = 5,
    time_range: str | None = None,
    time_range_days: int | None = None,
) -> dict:
    normalized_ticker = str(ticker).strip()
    if not normalized_ticker:
        return {'error': {'code': 'INVALID_ARGUMENT', 'message': 'ticker must not be blank', 'retryable': False, 'details': {}}}

    normalized_query = str(query or '').strip()
    safe_top_k = _safe_int(top_k, 5, min_value=1, max_value=50)
    range_days = _resolve_time_range_days(time_range, time_range_days)
    cutoff_at = _now_utc() - timedelta(days=range_days)
    cache_key = f'memory:{normalized_ticker}:{normalized_query}:{safe_top_k}:{range_days}'
    cached = _MEMORY_CACHE.get(cache_key)
    if cached is not None:
        return cached.value

    notes = _fetch_notes(normalized_ticker, cutoff_at)
    query_tokens = set(_tokenize_text(normalized_query))
    query_embedding = _embedding(normalized_query or normalized_ticker)
    now = _now_utc()
    ranked: list[dict[str, Any]] = []
    for note in notes:
        score, reason = _score_memory_note(
            note,
            query_tokens=query_tokens,
            query_embedding=query_embedding,
            now=now,
        )
        ranked.append(
            {
                'note_id': note['note_id'],
                'created_at': note['created_at'],
                'summary': str(note.get('summary', ''))[:600],
                'tags': _normalize_tags(note.get('tags', [])),
                'importance': _safe_int(note.get('importance'), 50, min_value=0, max_value=100),
                'links': _normalize_links(note.get('links', [])),
                'score': score,
                'reason': reason,
            }
        )
    ranked.sort(key=lambda item: (float(item.get('score', 0.0)), str(item.get('created_at', ''))), reverse=True)
    top_notes = [
        {
            'note_id': item['note_id'],
            'created_at': item['created_at'],
            'summary': item['summary'],
            'tags': item['tags'],
            'importance': item['importance'],
            'links': item['links'],
        }
        for item in ranked[:safe_top_k]
    ]
    result = {
        'notes': top_notes,
        'meta': {
            'retrieval_mode': 'HYBRID_KEYWORD_VECTOR',
            'time_range_days': range_days,
            'candidates': len(notes),
        },
    }
    _MEMORY_CACHE.set(cache_key, result, MEMORY_CACHE_TTL_SECONDS)
    return result


def write_memory_note(note: dict) -> dict:
    if not isinstance(note, dict):
        return {'error': {'code': 'INVALID_ARGUMENT', 'message': 'note must be object', 'retryable': False, 'details': {}}}

    ticker = str(note.get('ticker', '')).strip()
    summary = str(note.get('summary', '')).strip()
    if not ticker or not summary:
        return {
            'error': {
                'code': 'INVALID_ARGUMENT',
                'message': 'ticker and summary are required',
                'retryable': False,
                'details': {},
            }
        }

    tags = _normalize_tags(note.get('tags', []))
    links = _normalize_links(note.get('links', []))
    importance = _safe_int(note.get('importance', 50), 50, min_value=0, max_value=100)
    dedupe_key = str(note.get('dedupe_key', '')).strip() or None
    note_id = str(note.get('note_id', '')).strip() or f'note_{uuid.uuid4().hex[:12]}'
    created_at = _parse_iso_to_utc(note.get('created_at', _now_utc().isoformat())).isoformat()
    updated_at = _now_utc().isoformat()

    conn = _conn()
    try:
        if dedupe_key:
            row = conn.execute(
                'select note_id from memory_notes_runtime where ticker = ? and dedupe_key = ? order by created_at desc limit 1',
                (ticker, dedupe_key),
            ).fetchone()
            if row and row[0]:
                return {'ok': True, 'note_id': row[0], 'deduped': True}

        embedding_input = f"{ticker} {summary} {' '.join(tags)} {' '.join(links)}"
        embedding = _embedding(embedding_input)
        conn.execute(
            'insert into memory_notes_runtime('
            'note_id, ticker, summary, tags_json, importance, links_json, dedupe_key, embedding_json, created_at, updated_at'
            ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'on conflict(note_id) do update set '
            'ticker=excluded.ticker, '
            'summary=excluded.summary, '
            'tags_json=excluded.tags_json, '
            'importance=excluded.importance, '
            'links_json=excluded.links_json, '
            'dedupe_key=excluded.dedupe_key, '
            'embedding_json=excluded.embedding_json, '
            'created_at=excluded.created_at, '
            'updated_at=excluded.updated_at',
            (
                note_id,
                ticker,
                summary[:600],
                _json_dump(tags),
                importance,
                _json_dump(links),
                dedupe_key,
                _json_dump(embedding),
                created_at,
                updated_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _MEMORY_CACHE.clear()
    return {'ok': True, 'note_id': note_id, 'deduped': False}


def summarize_memory_rollup(ticker: str) -> dict:
    normalized_ticker = str(ticker).strip()
    if not normalized_ticker:
        return {'error': {'code': 'INVALID_ARGUMENT', 'message': 'ticker must not be blank', 'retryable': False, 'details': {}}}

    notes = _fetch_notes(normalized_ticker, _now_utc() - timedelta(days=3650))
    if not notes:
        return {'ticker': normalized_ticker, 'note_count': 0, 'summary': 'no memory notes found', 'top_tags': []}

    tag_freq: dict[str, int] = {}
    for note in notes:
        for tag in _normalize_tags(note.get('tags', [])):
            tag_freq[tag] = tag_freq.get(tag, 0) + 1
    top_tags = [item[0] for item in sorted(tag_freq.items(), key=lambda item: item[1], reverse=True)[:5]]
    latest_note_at = notes[0]['created_at']
    summary = f'{len(notes)} notes summarized; top_tags={",".join(top_tags) if top_tags else "none"}'
    return {
        'ticker': normalized_ticker,
        'note_count': len(notes),
        'latest_note_at': latest_note_at,
        'top_tags': top_tags,
        'summary': summary,
    }


def reset_memory_runtime_state() -> None:
    _MEMORY_CACHE.clear()
    conn = _conn()
    try:
        conn.execute('delete from memory_notes_runtime')
        conn.commit()
    finally:
        conn.close()
