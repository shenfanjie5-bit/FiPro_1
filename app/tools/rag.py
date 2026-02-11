from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import httpx

from app.tools.cache import TTLCache


EVENT_DOCS_CACHE_TTL_SECONDS = 10 * 60
_DOCS_CACHE: TTLCache[dict[str, Any]] = TTLCache()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_to_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _runtime_db_path() -> Path:
    return Path(os.getenv('WORKFLOW_RUNTIME_DB', os.getenv('WORKFLOW_CHECKPOINT_DB', 'checkpoint.db')))


def _hash_digest(payload: Any, length: int = 16) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:length]


def _classify_news_error(exc: Exception) -> str:
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return 'UPSTREAM_TIMEOUT'
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return 'RATE_LIMITED'
        return 'UPSTREAM_ERROR'
    message = str(exc).upper()
    if 'DATA_UNAVAILABLE' in message:
        return 'DATA_UNAVAILABLE'
    return 'UPSTREAM_ERROR'


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_runtime_db_path(), timeout=30)
    conn.execute(
        'create table if not exists event_docs ('
        'doc_id text primary key, '
        'query text not null, '
        'title text not null, '
        'source text not null, '
        'published_at text not null, '
        'captured_at text not null, '
        'uri text, '
        'snippet text not null, '
        'checksum text not null, '
        'created_at text not null'
        ')'
    )
    conn.execute('create index if not exists idx_event_docs_query_published on event_docs(query, published_at desc)')
    return conn


def _parse_asof_range(asof_range: Any) -> tuple[datetime, datetime]:
    now = _now_utc()
    if asof_range is None:
        return now - timedelta(days=7), now
    if isinstance(asof_range, str):
        end = _parse_iso_to_utc(asof_range)
        return end - timedelta(days=7), end
    if isinstance(asof_range, dict):
        start_raw = asof_range.get('start')
        end_raw = asof_range.get('end')
        if start_raw and end_raw:
            start = _parse_iso_to_utc(start_raw)
            end = _parse_iso_to_utc(end_raw)
            if start > end:
                start, end = end, start
            return start, end
        if end_raw:
            end = _parse_iso_to_utc(end_raw)
            return end - timedelta(days=7), end
    return now - timedelta(days=7), now


def _normalize_doc(query: str, item: dict[str, Any]) -> dict[str, Any]:
    published_at = _parse_iso_to_utc(item.get('published_at', _now_utc().isoformat())).isoformat()
    title = str(item.get('title', '')).strip() or 'Untitled document'
    source = str(item.get('source', '')).strip() or 'UNKNOWN'
    snippet = str(item.get('snippet', '')).strip() or title[:120]
    uri = item.get('uri')
    checksum = item.get('checksum') or _hash_digest(
        {'query': query, 'title': title, 'source': source, 'published_at': published_at, 'snippet': snippet},
        length=20,
    )
    doc_id = item.get('doc_id') or f"doc_{_hash_digest({'query': query, 'checksum': checksum}, length=12)}"
    return {
        'doc_id': doc_id,
        'query': query,
        'title': title,
        'source': source,
        'published_at': published_at,
        'captured_at': _parse_iso_to_utc(item.get('captured_at', _now_utc().isoformat())).isoformat(),
        'uri': uri,
        'snippet': snippet,
        'checksum': checksum,
    }


def _ingest_docs(query: str, docs: list[dict[str, Any]]) -> None:
    if not docs:
        return
    now = _now_utc().isoformat()
    conn = _conn()
    try:
        for doc in docs:
            normalized = _normalize_doc(query, doc)
            conn.execute(
                'insert into event_docs('
                'doc_id, query, title, source, published_at, captured_at, uri, snippet, checksum, created_at'
                ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                'on conflict(doc_id) do update set '
                'query=excluded.query, '
                'title=excluded.title, '
                'source=excluded.source, '
                'published_at=excluded.published_at, '
                'captured_at=excluded.captured_at, '
                'uri=excluded.uri, '
                'snippet=excluded.snippet, '
                'checksum=excluded.checksum, '
                'created_at=excluded.created_at',
                (
                    normalized['doc_id'],
                    normalized['query'],
                    normalized['title'],
                    normalized['source'],
                    normalized['published_at'],
                    normalized['captured_at'],
                    normalized['uri'],
                    normalized['snippet'],
                    normalized['checksum'],
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _query_docs_from_store(query: str, start: datetime, end: datetime, top_k: int) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            'select doc_id, title, source, published_at, captured_at, uri, snippet, checksum '
            'from event_docs where query = ? and published_at >= ? and published_at <= ? '
            'order by published_at desc limit ?',
            (query, start.isoformat(), end.isoformat(), top_k),
        ).fetchall()
    finally:
        conn.close()
    docs: list[dict[str, Any]] = []
    for row in rows:
        docs.append(
            {
                'doc_id': row[0],
                'title': row[1],
                'source': row[2],
                'published_at': row[3],
                'captured_at': row[4],
                'uri': row[5],
                'snippet': row[6],
                'checksum': row[7],
            }
        )
    return docs


def _fetch_newsapi_docs(query: str, start: datetime, end: datetime, top_k: int) -> list[dict[str, Any]]:
    api_key = os.getenv('NEWS_DATA_API_KEY', '').strip()
    if not api_key:
        raise ValueError('DATA_UNAVAILABLE: NEWS_DATA_API_KEY not configured')

    response = httpx.get(
        'https://newsapi.org/v2/everything',
        params={
            'q': query,
            'from': start.isoformat(),
            'to': end.isoformat(),
            'sortBy': 'publishedAt',
            'language': 'zh',
            'pageSize': max(1, min(50, top_k)),
        },
        headers={'X-Api-Key': api_key},
        timeout=float(os.getenv('DATASOURCE_TIMEOUT_SECONDS', '6')),
    )
    response.raise_for_status()
    payload = response.json()
    articles = payload.get('articles', [])
    docs: list[dict[str, Any]] = []
    for article in articles:
        title = str(article.get('title', '')).strip()
        if not title:
            continue
        published_at = article.get('publishedAt') or _now_utc().isoformat()
        snippet = str(article.get('description') or article.get('content') or title)[:240]
        source_name = str((article.get('source') or {}).get('name') or 'NEWS')
        docs.append(
            {
                'title': title,
                'source': source_name,
                'published_at': published_at,
                'captured_at': _now_utc().isoformat(),
                'uri': article.get('url'),
                'snippet': snippet,
            }
        )
    return docs


def _synthetic_docs(query: str, start: datetime, end: datetime, top_k: int) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    total = max(1, min(6, top_k))
    span_seconds = max(1, int((end - start).total_seconds()))
    for index in range(total):
        seed = _hash_digest({'query': query, 'index': index, 'start': start.isoformat(), 'end': end.isoformat()}, length=12)
        offset = int(seed, 16) % span_seconds
        published = (start + timedelta(seconds=offset)).isoformat()
        docs.append(
            {
                'title': f'{query} event update {index + 1}',
                'source': 'SYNTHETIC_FALLBACK',
                'published_at': published,
                'captured_at': _now_utc().isoformat(),
                'uri': None,
                'snippet': f'Synthetic event document for query={query} generated for degraded mode.',
            }
        )
    return docs


def _with_cache_meta(result: dict[str, Any], cache_key: str, cache_hit: bool) -> dict[str, Any]:
    output = json.loads(json.dumps(result, sort_keys=True, ensure_ascii=True, default=str))
    output.setdefault('meta', {})
    output['meta']['cache'] = {'key': cache_key, 'hit': cache_hit, 'ttl_seconds': EVENT_DOCS_CACHE_TTL_SECONDS}
    output['meta']['cache_stats'] = _DOCS_CACHE.stats()
    return output


def search_event_docs(query: str, asof_range: Any, top_k: int = 8) -> dict:
    start, end = _parse_asof_range(asof_range)
    normalized_query = str(query).strip()
    if not normalized_query:
        return {'docs': [], 'meta': {'error': {'code': 'INVALID_ARGUMENT', 'message': 'query must not be blank'}}}

    cache_key = f'docs:{normalized_query}:{start.isoformat()}:{end.isoformat()}:{top_k}'
    cached = _DOCS_CACHE.get(cache_key)
    if cached is not None:
        return _with_cache_meta(cached.value, cache_key=cache_key, cache_hit=True)

    db_docs = _query_docs_from_store(normalized_query, start, end, top_k)
    docs = list(db_docs)
    source_mode = 'STORE'
    fallback_reason = ''
    fallback_error: dict[str, Any] | None = None

    if len(docs) < top_k:
        fetched_docs: list[dict[str, Any]]
        try:
            fetched_docs = _fetch_newsapi_docs(normalized_query, start, end, top_k=top_k - len(docs))
            source_mode = 'NEWS_API'
        except Exception as exc:  # noqa: BLE001
            fallback_reason = str(exc)
            code = _classify_news_error(exc)
            fallback_error = {'code': code, 'message': str(exc), 'retryable': code in {'UPSTREAM_TIMEOUT', 'RATE_LIMITED', 'UPSTREAM_ERROR'}}
            fetched_docs = _synthetic_docs(normalized_query, start, end, top_k=top_k - len(docs))
            source_mode = 'SYNTHETIC_FALLBACK'

        normalized_fetched = [_normalize_doc(normalized_query, item) for item in fetched_docs]
        _ingest_docs(normalized_query, normalized_fetched)
        docs.extend(normalized_fetched)

    dedup: dict[str, dict[str, Any]] = {}
    for doc in docs:
        normalized = _normalize_doc(normalized_query, doc)
        dedup[normalized['doc_id']] = normalized
    merged_docs = sorted(dedup.values(), key=lambda item: item['published_at'], reverse=True)[:top_k]

    result = {
        'docs': merged_docs,
        'meta': {
            'source_mode': source_mode,
            'db_hits': len(db_docs),
            'fallback_reason': fallback_reason,
            'window': {'start': start.isoformat(), 'end': end.isoformat()},
            'upstream_error': fallback_error,
        },
    }
    _DOCS_CACHE.set(cache_key, result, EVENT_DOCS_CACHE_TTL_SECONDS)
    return _with_cache_meta(result, cache_key=cache_key, cache_hit=False)


def rerank_docs(query: str, docs: list[dict], top_k: int = 5) -> dict:
    query_tokens = {token for token in query.lower().split() if token}
    scored_docs: list[dict[str, Any]] = []
    for doc in docs:
        text = f"{doc.get('title', '')} {doc.get('snippet', '')}".lower()
        overlap = sum(1 for token in query_tokens if token in text)
        score = round(min(1.0, 0.4 + overlap * 0.2), 4)
        ranked = dict(doc)
        ranked['rank_score'] = score
        scored_docs.append(ranked)
    scored_docs.sort(key=lambda item: (item.get('rank_score', 0), item.get('published_at', '')), reverse=True)
    return {'docs': scored_docs[:top_k]}


def extract_events_from_docs(docs: list[dict]) -> dict:
    events = []
    for index, doc in enumerate(docs):
        source = str(doc.get('source', '')).upper()
        event_type = 'OTHER'
        if 'POLICY' in source:
            event_type = 'POLICY'
        elif 'NEWS' in source or 'REPORT' in source:
            event_type = 'EARNINGS'
        events.append(
            {
                'event_id': f"evt_{_hash_digest({'doc_id': doc.get('doc_id'), 'idx': index}, length=10)}",
                'doc_id': doc.get('doc_id', ''),
                'type': event_type,
                'direction': 'MIXED',
                'summary': f"Derived event from {doc.get('doc_id', 'unknown_doc')}",
            }
        )
    return {'events': events}


def reset_rag_runtime_state() -> None:
    _DOCS_CACHE.clear()
