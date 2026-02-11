from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

import httpx

from app.tools.cache import TTLCache


EVENT_DOCS_CACHE_TTL_SECONDS = 10 * 60
_DOCS_CACHE: TTLCache[dict[str, Any]] = TTLCache()

ALLOWED_DOC_SOURCES = {'NEWS', 'FILINGS', 'REPORT', 'SOCIAL'}
EVENT_TYPES = {'COMMODITY', 'POLICY', 'GEOPOLITICS', 'LOGISTICS', 'EARNINGS', 'COMPETITION', 'OTHER'}

POSITIVE_HINTS = ('增长', '上调', '利好', '改善', '恢复', '提升', 'increase', 'beat', 'upgrade')
NEGATIVE_HINTS = ('下滑', '下调', '利空', '恶化', '收缩', '风险', 'decrease', 'miss', 'downgrade')


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


def _tokenize_text(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r'[\w\u4e00-\u9fff]+', text or '') if token.strip()]


def _normalize_queries(query: Any) -> list[str]:
    if isinstance(query, str):
        normalized = query.strip()
        return [normalized] if normalized else []
    if isinstance(query, (list, tuple, set)):
        output: list[str] = []
        for item in query:
            normalized = str(item).strip()
            if normalized and normalized not in output:
                output.append(normalized)
        return output[:6]
    return []


def _normalize_sources(sources: Any) -> list[str]:
    if sources is None:
        return sorted(ALLOWED_DOC_SOURCES)
    if isinstance(sources, str):
        candidates = [sources]
    else:
        candidates = list(sources) if isinstance(sources, (list, tuple, set)) else []
    normalized: list[str] = []
    for item in candidates:
        value = str(item).upper().strip()
        if value in ALLOWED_DOC_SOURCES and value not in normalized:
            normalized.append(value)
    return normalized or sorted(ALLOWED_DOC_SOURCES)


def _normalize_source(raw: str) -> str:
    value = str(raw or '').strip().upper()
    if value in ALLOWED_DOC_SOURCES:
        return value
    if 'FILING' in value or '公告' in value:
        return 'FILINGS'
    if 'REPORT' in value or '研报' in value:
        return 'REPORT'
    if 'SOCIAL' in value or '微博' in value:
        return 'SOCIAL'
    return 'NEWS'


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
    source = _normalize_source(str(item.get('source', 'NEWS')))
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
                'source': _normalize_source(source_name),
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
                'source': ['NEWS', 'FILINGS', 'REPORT', 'SOCIAL'][index % 4],
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


def _doc_matches_sources(doc: dict[str, Any], sources: list[str]) -> bool:
    return _normalize_source(str(doc.get('source', 'NEWS'))) in set(sources)


def search_event_docs(query: Any, asof_range: Any, top_k: int = 8, sources: list[str] | None = None) -> dict:
    start, end = _parse_asof_range(asof_range)
    normalized_queries = _normalize_queries(query)
    if not normalized_queries:
        return {'docs': [], 'meta': {'error': {'code': 'INVALID_ARGUMENT', 'message': 'query must not be blank'}}}
    normalized_sources = _normalize_sources(sources)
    safe_top_k = max(1, min(50, int(top_k)))

    cache_key = (
        f"docs:{'|'.join(normalized_queries)}:{'|'.join(normalized_sources)}:"
        f'{start.isoformat()}:{end.isoformat()}:{safe_top_k}'
    )
    cached = _DOCS_CACHE.get(cache_key)
    if cached is not None:
        return _with_cache_meta(cached.value, cache_key=cache_key, cache_hit=True)

    per_query_store_limit = max(1, min(25, safe_top_k // len(normalized_queries) + 2))
    docs: list[dict[str, Any]] = []
    db_hits = 0
    for q in normalized_queries:
        stored = _query_docs_from_store(q, start, end, per_query_store_limit)
        filtered = [doc for doc in stored if _doc_matches_sources(doc, normalized_sources)]
        docs.extend(filtered)
        db_hits += len(filtered)

    source_modes: list[str] = ['STORE'] if db_hits else []
    fallback_reasons: list[str] = []
    upstream_errors: list[dict[str, Any]] = []
    for q in normalized_queries:
        if len(docs) >= safe_top_k:
            break
        need = max(1, safe_top_k - len(docs))
        try:
            fetched_docs = _fetch_newsapi_docs(q, start, end, top_k=need)
            source_modes.append('NEWS_API')
        except Exception as exc:  # noqa: BLE001
            code = _classify_news_error(exc)
            upstream_errors.append(
                {'code': code, 'message': str(exc), 'retryable': code in {'UPSTREAM_TIMEOUT', 'RATE_LIMITED', 'UPSTREAM_ERROR'}}
            )
            fallback_reasons.append(str(exc))
            fetched_docs = _synthetic_docs(q, start, end, top_k=need)
            source_modes.append('SYNTHETIC_FALLBACK')

        normalized_fetched = [_normalize_doc(q, item) for item in fetched_docs]
        filtered_fetched = [doc for doc in normalized_fetched if _doc_matches_sources(doc, normalized_sources)]
        _ingest_docs(q, filtered_fetched)
        docs.extend(filtered_fetched)

    dedup: dict[str, dict[str, Any]] = {}
    for doc in docs:
        normalized = _normalize_doc(str(doc.get('query', normalized_queries[0])), doc)
        dedup_key = normalized.get('doc_id') or normalized.get('checksum')
        dedup[dedup_key] = normalized
    merged_docs = sorted(dedup.values(), key=lambda item: item['published_at'], reverse=True)[:safe_top_k]

    result = {
        'docs': merged_docs,
        'meta': {
            'queries': normalized_queries,
            'sources': normalized_sources,
            'source_modes': sorted(set(source_modes or ['STORE'])),
            'db_hits': db_hits,
            'fallback_reason': ' | '.join(fallback_reasons),
            'window': {'start': start.isoformat(), 'end': end.isoformat()},
            'upstream_error': upstream_errors[0] if upstream_errors else None,
        },
    }
    _DOCS_CACHE.set(cache_key, result, EVENT_DOCS_CACHE_TTL_SECONDS)
    return _with_cache_meta(result, cache_key=cache_key, cache_hit=False)


def rerank_docs(query: str, docs: list[dict], top_k: int = 5) -> dict:
    if not isinstance(docs, list):
        return {'error': {'code': 'INVALID_ARGUMENT', 'message': 'docs must be list', 'retryable': False, 'details': {}}}
    query_tokens = set(_tokenize_text(query))
    safe_top_k = max(1, min(20, int(top_k)))
    scored_docs: list[dict[str, Any]] = []
    for doc in docs:
        text = f"{doc.get('title', '')} {doc.get('snippet', '')}"
        doc_tokens = set(_tokenize_text(text))
        overlap = len(query_tokens.intersection(doc_tokens))
        keyword_score = overlap / max(1, len(query_tokens))
        contains_phrase = 1.0 if str(query).strip().lower() in text.lower() else 0.0
        published_at = doc.get('published_at')
        recency_score = 0.0
        if published_at:
            try:
                published_dt = _parse_iso_to_utc(published_at)
                age_hours = max(0.0, (_now_utc() - published_dt).total_seconds() / 3600.0)
                recency_score = max(0.0, 1.0 - min(age_hours, 24.0 * 7.0) / (24.0 * 7.0))
            except Exception:  # noqa: BLE001
                recency_score = 0.0
        source_prior = {
            'FILINGS': 0.95,
            'REPORT': 0.9,
            'NEWS': 0.8,
            'SOCIAL': 0.65,
        }.get(_normalize_source(str(doc.get('source', 'NEWS'))), 0.75)
        score = round(min(1.0, 0.5 * keyword_score + 0.2 * contains_phrase + 0.15 * recency_score + 0.15 * source_prior), 4)
        reason = f'overlap={overlap}, phrase={int(contains_phrase)}, recency={recency_score:.2f}, source_prior={source_prior:.2f}'
        ranked = dict(doc)
        ranked['rank_score'] = score
        ranked['rank_reason'] = reason
        scored_docs.append(ranked)
    scored_docs.sort(
        key=lambda item: (
            float(item.get('rank_score', 0)),
            str(item.get('published_at', '')),
            str(item.get('doc_id', '')),
        ),
        reverse=True,
    )
    top_docs = scored_docs[:safe_top_k]
    return {
        'docs': top_docs,
        'ranked_doc_ids': [str(item.get('doc_id', '')) for item in top_docs if str(item.get('doc_id', '')).strip()],
        'scores': [float(item.get('rank_score', 0.0)) for item in top_docs],
        'reasons': [str(item.get('rank_reason', '')) for item in top_docs],
    }


def extract_events_from_docs(docs: list[dict]) -> dict:
    if not isinstance(docs, list):
        return {'error': {'code': 'INVALID_ARGUMENT', 'message': 'docs must be list', 'retryable': False, 'details': {}}}

    def classify_event_type(text: str, source: str) -> str:
        lowered = text.lower()
        if '政策' in text or 'regulation' in lowered or 'policy' in lowered:
            return 'POLICY'
        if any(x in text for x in ['运价', '物流', '港口']) or 'shipping' in lowered:
            return 'LOGISTICS'
        if any(x in text for x in ['油价', '煤炭', '铜', 'commodity']) or 'commodity' in lowered:
            return 'COMMODITY'
        if any(x in text for x in ['地缘', '制裁', '冲突']) or 'geopolitics' in lowered:
            return 'GEOPOLITICS'
        if '竞争' in text or 'competition' in lowered:
            return 'COMPETITION'
        if _normalize_source(source) in {'NEWS', 'REPORT'}:
            return 'EARNINGS'
        return 'OTHER'

    def infer_direction(text: str) -> str:
        lowered = text.lower()
        pos_hits = sum(1 for hint in POSITIVE_HINTS if hint in lowered or hint in text)
        neg_hits = sum(1 for hint in NEGATIVE_HINTS if hint in lowered or hint in text)
        if pos_hits and not neg_hits:
            return 'POS'
        if neg_hits and not pos_hits:
            return 'NEG'
        if pos_hits and neg_hits:
            return 'MIXED'
        return 'UNCERTAIN'

    def extract_entities(text: str, fallback: str) -> list[str]:
        entities: list[str] = []
        for token in re.findall(r'[0-9]{6}\.(?:SH|SZ)', text.upper()):
            if token not in entities:
                entities.append(token)
        for token in re.findall(r'[\u4e00-\u9fff]{2,8}', text):
            if token not in entities:
                entities.append(token)
            if len(entities) >= 4:
                break
        if not entities and fallback:
            entities.append(fallback)
        return entities[:5]

    events: list[dict[str, Any]] = []
    for index, doc in enumerate(docs):
        doc_id = str(doc.get('doc_id', '')).strip()
        if not doc_id:
            continue
        title = str(doc.get('title', '')).strip()
        snippet = str(doc.get('snippet', '')).strip()
        source = _normalize_source(str(doc.get('source', 'NEWS')))
        text = f'{title} {snippet}'.strip()
        event_type = classify_event_type(text, source)
        if event_type not in EVENT_TYPES:
            event_type = 'OTHER'
        direction = infer_direction(text)
        keyword_density = min(1.0, len(_tokenize_text(text)) / 40.0)
        source_conf = {'FILINGS': 0.92, 'REPORT': 0.88, 'NEWS': 0.8, 'SOCIAL': 0.65}.get(source, 0.7)
        confidence = round(max(0.2, min(0.97, 0.5 * source_conf + 0.5 * keyword_density)), 4)
        summary_base = title or snippet or f'event from {doc_id}'
        summary = summary_base[:160]
        events.append(
            {
                'event_id': f"evt_{_hash_digest({'doc_id': doc_id, 'idx': index, 'type': event_type, 'direction': direction}, length=10)}",
                'type': event_type,
                'entities': extract_entities(text, fallback=source),
                'direction': direction,
                'confidence': confidence,
                'summary': summary,
                'evidence_doc_ids': [doc_id],
            }
        )
    return {'events': events}


def reset_rag_runtime_state() -> None:
    _DOCS_CACHE.clear()
