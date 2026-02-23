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


SKILL_CACHE_TTL_SECONDS = 2 * 60
_SKILL_CACHE: TTLCache[dict[str, Any]] = TTLCache()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _runtime_db_path() -> Path:
    return Path(os.getenv('WORKFLOW_RUNTIME_DB', os.getenv('WORKFLOW_CHECKPOINT_DB', 'checkpoint.db')))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_runtime_db_path(), timeout=30)
    conn.execute(
        'create table if not exists skills_runtime ('
        'skill_id text primary key, '
        'ticker text not null, '
        'title text not null, '
        'summary text not null, '
        'decision_bias text not null, '
        'confidence real not null, '
        'tags_json text not null, '
        'source_report_id text, '
        'run_mode text not null, '
        'dedupe_key text, '
        'embedding_json text not null, '
        'created_at text not null, '
        'updated_at text not null'
        ')'
    )
    conn.execute('create index if not exists idx_skills_runtime_ticker_created on skills_runtime(ticker, created_at desc)')
    conn.execute('create index if not exists idx_skills_runtime_dedupe on skills_runtime(ticker, dedupe_key)')
    return conn


def _safe_int(value: Any, default: int, *, min_value: int, max_value: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    return max(min_value, min(max_value, out))


def _safe_float(value: Any, default: float, *, min_value: float, max_value: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = default
    return max(min_value, min(max_value, out))


def _normalize_tags(tags: Any) -> list[str]:
    if not isinstance(tags, (list, tuple, set)):
        return []
    out: list[str] = []
    for item in tags:
        value = str(item).strip()
        if value and value not in out:
            out.append(value[:48])
    return out[:24]


def _normalize_bias(value: Any) -> str:
    bias = str(value or 'WATCH').strip().upper()
    if bias not in {'BUY', 'WATCH', 'AVOID'}:
        return 'WATCH'
    return bias


def _normalize_run_mode(value: Any) -> str:
    mode = str(value or 'LIVE').strip().upper()
    if mode not in {'LIVE', 'SHADOW', 'BACKTEST'}:
        return 'LIVE'
    return mode


def _tokenize_text(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r'[\w\u4e00-\u9fff]+', text or '') if token.strip()]


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


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)


def _json_load(payload: str, fallback: Any) -> Any:
    try:
        return json.loads(payload)
    except Exception:  # noqa: BLE001
        return fallback


def write_skill_note(note: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(note, dict):
        return {'error': {'code': 'INVALID_ARGUMENT', 'message': 'skill note must be object', 'retryable': False, 'details': {}}}

    ticker = str(note.get('ticker', '')).strip().upper() or 'GLOBAL'
    title = str(note.get('title', '')).strip()
    summary = str(note.get('summary', '')).strip()
    if not summary:
        return {'error': {'code': 'INVALID_ARGUMENT', 'message': 'summary is required', 'retryable': False, 'details': {}}}
    if not title:
        title = f'{ticker} distilled rule'
    title = title[:120]
    summary = summary[:800]
    decision_bias = _normalize_bias(note.get('decision_bias', 'WATCH'))
    confidence = _safe_float(note.get('confidence', 0.6), 0.6, min_value=0.0, max_value=1.0)
    tags = _normalize_tags(note.get('tags', []))
    source_report_id = str(note.get('source_report_id', '')).strip() or None
    run_mode = _normalize_run_mode(note.get('run_mode', 'LIVE'))
    dedupe_key = str(note.get('dedupe_key', '')).strip() or None
    if not dedupe_key and source_report_id:
        dedupe_key = f'{source_report_id}:{decision_bias}'
    skill_id = str(note.get('skill_id', '')).strip() or f'skill_{uuid.uuid4().hex[:12]}'
    created_at = str(note.get('created_at', _now_utc().isoformat())).strip() or _now_utc().isoformat()
    updated_at = _now_utc().isoformat()
    embedding = _embedding(f'{ticker} {title} {summary} {" ".join(tags)} {decision_bias}')

    conn = _conn()
    try:
        if dedupe_key:
            row = conn.execute(
                'select skill_id from skills_runtime where ticker = ? and dedupe_key = ? order by created_at desc limit 1',
                (ticker, dedupe_key),
            ).fetchone()
            if row and row[0]:
                return {'ok': True, 'skill_id': row[0], 'deduped': True}

        conn.execute(
            'insert into skills_runtime('
            'skill_id, ticker, title, summary, decision_bias, confidence, tags_json, source_report_id, run_mode, dedupe_key, embedding_json, created_at, updated_at'
            ') values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'on conflict(skill_id) do update set '
            'ticker=excluded.ticker, '
            'title=excluded.title, '
            'summary=excluded.summary, '
            'decision_bias=excluded.decision_bias, '
            'confidence=excluded.confidence, '
            'tags_json=excluded.tags_json, '
            'source_report_id=excluded.source_report_id, '
            'run_mode=excluded.run_mode, '
            'dedupe_key=excluded.dedupe_key, '
            'embedding_json=excluded.embedding_json, '
            'created_at=excluded.created_at, '
            'updated_at=excluded.updated_at',
            (
                skill_id,
                ticker,
                title,
                summary,
                decision_bias,
                confidence,
                _json_dump(tags),
                source_report_id,
                run_mode,
                dedupe_key,
                _json_dump(embedding),
                created_at,
                updated_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _SKILL_CACHE.clear()
    return {'ok': True, 'skill_id': skill_id, 'deduped': False}


def _fetch_skills(ticker: str, cutoff_at: datetime, include_global: bool) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        if include_global:
            rows = conn.execute(
                'select skill_id, ticker, title, summary, decision_bias, confidence, tags_json, source_report_id, run_mode, embedding_json, created_at '
                'from skills_runtime where (ticker = ? or ticker = ?) and created_at >= ? '
                'order by created_at desc limit 500',
                (ticker, 'GLOBAL', cutoff_at.isoformat()),
            ).fetchall()
        else:
            rows = conn.execute(
                'select skill_id, ticker, title, summary, decision_bias, confidence, tags_json, source_report_id, run_mode, embedding_json, created_at '
                'from skills_runtime where ticker = ? and created_at >= ? '
                'order by created_at desc limit 500',
                (ticker, cutoff_at.isoformat()),
            ).fetchall()
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                'skill_id': row[0],
                'ticker': row[1],
                'title': row[2],
                'summary': row[3],
                'decision_bias': row[4],
                'confidence': _safe_float(row[5], 0.5, min_value=0.0, max_value=1.0),
                'tags': _json_load(row[6], []),
                'source_report_id': row[7],
                'run_mode': row[8],
                'embedding': _json_load(row[9], []),
                'created_at': row[10],
            }
        )
    return out


def retrieve_skill_notes(
    ticker: str,
    query: str,
    top_k: int = 5,
    *,
    include_global: bool = True,
    lookback_days: int = 365,
) -> dict[str, Any]:
    normalized_ticker = str(ticker).strip().upper() or 'GLOBAL'
    normalized_query = str(query or '').strip()
    safe_top_k = _safe_int(top_k, 5, min_value=1, max_value=20)
    safe_lookback_days = _safe_int(lookback_days, 365, min_value=1, max_value=3650)

    cache_key = f'skill:{normalized_ticker}:{normalized_query}:{safe_top_k}:{safe_lookback_days}:{int(include_global)}'
    cached = _SKILL_CACHE.get(cache_key)
    if cached is not None:
        return cached.value

    cutoff_at = _now_utc() - timedelta(days=safe_lookback_days)
    skills = _fetch_skills(normalized_ticker, cutoff_at, include_global=include_global)
    query_tokens = set(_tokenize_text(normalized_query or normalized_ticker))
    query_embedding = _embedding(normalized_query or normalized_ticker)
    now = _now_utc()
    ranked: list[dict[str, Any]] = []
    for item in skills:
        content = f"{item.get('title', '')} {item.get('summary', '')} {' '.join(item.get('tags', []))}"
        item_tokens = set(_tokenize_text(content))
        overlap = len(query_tokens.intersection(item_tokens))
        keyword_score = overlap / max(1, len(query_tokens))
        vector_score = max(0.0, _cosine_similarity(query_embedding, list(item.get('embedding', []))))
        confidence_score = _safe_float(item.get('confidence', 0.5), 0.5, min_value=0.0, max_value=1.0)
        try:
            created_at = datetime.fromisoformat(str(item.get('created_at', '')).replace('Z', '+00:00'))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - created_at.astimezone(timezone.utc)).total_seconds() / 86400.0)
        except Exception:  # noqa: BLE001
            age_days = 365.0
        recency_score = max(0.0, 1.0 - min(age_days, 365.0) / 365.0)
        score = round(min(1.0, 0.4 * keyword_score + 0.3 * vector_score + 0.2 * confidence_score + 0.1 * recency_score), 6)
        ranked.append(
            {
                **item,
                'score': score,
            }
        )

    ranked.sort(key=lambda x: (float(x.get('score', 0.0)), str(x.get('created_at', ''))), reverse=True)
    top = [
        {
            'skill_id': item['skill_id'],
            'ticker': item['ticker'],
            'title': item['title'],
            'summary': item['summary'],
            'decision_bias': item['decision_bias'],
            'confidence': item['confidence'],
            'tags': _normalize_tags(item.get('tags', [])),
            'source_report_id': item.get('source_report_id'),
            'run_mode': item.get('run_mode', 'LIVE'),
            'created_at': item.get('created_at'),
        }
        for item in ranked[:safe_top_k]
    ]
    result = {
        'skills': top,
        'meta': {
            'retrieval_mode': 'HYBRID_KEYWORD_VECTOR',
            'lookback_days': safe_lookback_days,
            'candidates': len(skills),
            'include_global': bool(include_global),
        },
    }
    _SKILL_CACHE.set(cache_key, result, SKILL_CACHE_TTL_SECONDS)
    return result


def write_skill_from_report(report: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {'error': {'code': 'INVALID_ARGUMENT', 'message': 'report must be object', 'retryable': False, 'details': {}}}
    ticker = str(report.get('ticker', '')).strip().upper() or 'GLOBAL'
    decision = report.get('decision', {}) if isinstance(report.get('decision', {}), dict) else {}
    thesis = report.get('thesis', {}) if isinstance(report.get('thesis', {}), dict) else {}
    risk_flags = report.get('risk_flags', []) if isinstance(report.get('risk_flags', []), list) else []
    invalidations = report.get('invalidations', []) if isinstance(report.get('invalidations', []), list) else []
    memory_update = report.get('memory_update', {}) if isinstance(report.get('memory_update', {}), dict) else {}
    provenance = report.get('provenance', {}) if isinstance(report.get('provenance', {}), dict) else {}

    decision_summary = str(decision.get('summary', '')).strip()
    base_case = str(thesis.get('base_case', '')).strip()
    risk_line = ''
    if risk_flags and isinstance(risk_flags[0], dict):
        risk_line = str(risk_flags[0].get('description', '')).strip()
    invalidation_line = ''
    if invalidations and isinstance(invalidations[0], dict):
        invalidation_line = str(invalidations[0].get('description', '')).strip()

    summary_parts = [part for part in [decision_summary, base_case, risk_line, invalidation_line] if part]
    summary = ' | '.join(summary_parts)[:800]
    if not summary:
        summary = 'Auto-distilled backtest rule from report outcome.'

    run_mode = _normalize_run_mode(provenance.get('run_mode', report.get('run_mode', 'LIVE')))
    tags = _normalize_tags(memory_update.get('tags', []))
    for extra in ['auto_skill', f'run_mode:{run_mode.lower()}', f"tier:{str(report.get('tier', 'TIER0')).lower()}"]:
        if extra not in tags:
            tags.append(extra)

    title = f"{ticker} {str(decision.get('action', 'WATCH')).upper()} pattern"
    return write_skill_note(
        {
            'ticker': ticker,
            'title': title,
            'summary': summary,
            'decision_bias': decision.get('action', 'WATCH'),
            'confidence': decision.get('confidence', 0.5),
            'tags': tags,
            'source_report_id': str(report.get('report_id', '')).strip() or None,
            'run_mode': run_mode,
            'dedupe_key': str(report.get('report_id', '')).strip() or None,
            'created_at': report.get('generated_at', _now_utc().isoformat()),
        }
    )


def reset_skills_runtime_state() -> None:
    _SKILL_CACHE.clear()
    conn = _conn()
    try:
        conn.execute('delete from skills_runtime')
        conn.commit()
    finally:
        conn.close()
