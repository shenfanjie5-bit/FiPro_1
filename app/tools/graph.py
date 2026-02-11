from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import hashlib
import json
import os
import re
import time
from typing import Any

from neo4j import Driver, GraphDatabase

from app.tools.cache import TTLCache


GRAPH_SUBTREE_TTL_SECONDS = 7 * 24 * 60 * 60
GRAPH_PATH_TTL_SECONDS = 24 * 60 * 60
GRAPH_EXPOSURE_TTL_SECONDS = 24 * 60 * 60
NEO4J_RETRY_COOLDOWN_SECONDS = 30

POSITIVE_HINTS = ('recovery', 'improve', 'subsidy', 'support', 'drop', 'down')
NEGATIVE_HINTS = ('shortage', 'spike', 'tight', 'sanction', 'tariff', 'risk', 'cut')

RELATION_WEIGHTS: dict[str, float] = {
    'SUPPLIES': 0.78,
    'DEPENDS_ON': 0.71,
    'AFFECTED_BY': 0.67,
    'SHIPS_THROUGH': 0.58,
    'COMPETES_WITH': 0.46,
}

_GRAPH_CACHE: TTLCache[dict[str, Any]] = TTLCache()
_NEO4J_DRIVER: Driver | None = None
_NEO4J_LAST_FAILURE_TS: float = 0.0


@dataclass(frozen=True)
class TraversalPath:
    nodes: list[str]
    edges: list[dict[str, Any]]


def _json_clone(payload: Any) -> Any:
    return json.loads(json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str))


def _stable_digest(payload: Any, *, length: int = 12) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:length]


def _error(code: str, message: str, *, retryable: bool = False, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {'error': {'code': code, 'message': message, 'retryable': retryable, 'details': details or {}}}


def _safe_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        output = int(value)
    except (TypeError, ValueError):
        output = default
    return max(min_value, min(max_value, output))


def _normalize_ticker(ticker: str) -> str:
    return str(ticker or '').strip().upper()


def _normalize_entity(entity: str) -> str:
    return str(entity or '').strip()


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _with_cache_meta(result: dict[str, Any], *, cache_key: str, cache_hit: bool, ttl_seconds: int) -> dict[str, Any]:
    output = _json_clone(result)
    output.setdefault('meta', {})
    output['meta']['cache'] = {'key': cache_key, 'hit': cache_hit, 'ttl_seconds': ttl_seconds}
    output['meta']['cache_stats'] = _GRAPH_CACHE.stats()
    return output


def _neo4j_connection_timeout() -> float:
    try:
        timeout = float(os.getenv('GRAPH_NEO4J_TIMEOUT_SECONDS', '0.7'))
    except ValueError:
        timeout = 0.7
    return max(0.2, min(3.0, timeout))


def _neo4j_auth() -> tuple[str, str, str] | None:
    uri = str(os.getenv('NEO4J_URI', '')).strip()
    user = str(os.getenv('NEO4J_USER', '')).strip()
    password = str(os.getenv('NEO4J_PASSWORD', '')).strip()
    if not uri or not user or not password:
        return None
    return uri, user, password


def _neo4j_disabled() -> bool:
    raw = str(os.getenv('GRAPH_DISABLE_NEO4J', '')).strip().lower()
    return raw in {'1', 'true', 'yes'}


def _get_neo4j_driver() -> Driver | None:
    global _NEO4J_DRIVER, _NEO4J_LAST_FAILURE_TS

    if _neo4j_disabled():
        return None
    if _NEO4J_DRIVER is not None:
        return _NEO4J_DRIVER
    if time.time() - _NEO4J_LAST_FAILURE_TS < NEO4J_RETRY_COOLDOWN_SECONDS:
        return None

    auth = _neo4j_auth()
    if auth is None:
        return None

    uri, user, password = auth
    driver = None
    try:
        driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_timeout=_neo4j_connection_timeout(),
            max_connection_pool_size=2,
        )
        driver.verify_connectivity()
        _NEO4J_DRIVER = driver
        return _NEO4J_DRIVER
    except Exception:
        _NEO4J_LAST_FAILURE_TS = time.time()
        if driver is not None:
            with suppress(Exception):
                driver.close()
        return None


def _normalize_relation_weight(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.6
    return max(0.1, min(0.99, value))


def _synthetic_company_graph(
    ticker: str,
    *,
    include_competitors: bool,
    entity: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root_id = f'company:{ticker}'
    competitor_ticker = '000858.SZ' if ticker != '000858.SZ' else '600519.SH'
    competitor_id = f'company:{competitor_ticker}'

    nodes_by_id: dict[str, dict[str, Any]] = {
        root_id: {'id': root_id, 'type': 'Company', 'label': ticker},
        'industry:baijiu': {'id': 'industry:baijiu', 'type': 'Industry', 'label': 'Baijiu Industry'},
        'product:baijiu': {'id': 'product:baijiu', 'type': 'Product', 'label': 'Baijiu'},
        'commodity:sorghum': {'id': 'commodity:sorghum', 'type': 'Commodity', 'label': 'Sorghum'},
        'commodity:glass': {'id': 'commodity:glass', 'type': 'Commodity', 'label': 'Glass Packaging'},
        'region:china': {'id': 'region:china', 'type': 'Region', 'label': 'China Mainland'},
        'route:domestic_cn': {'id': 'route:domestic_cn', 'type': 'Route', 'label': 'Domestic Logistics'},
        'policy:consumption_tax': {'id': 'policy:consumption_tax', 'type': 'PolicyEvent', 'label': 'Consumption Tax Policy'},
    }
    if include_competitors:
        nodes_by_id[competitor_id] = {'id': competitor_id, 'type': 'Company', 'label': competitor_ticker}

    edges: list[dict[str, Any]] = [
        {'from': 'commodity:sorghum', 'to': 'product:baijiu', 'type': 'SUPPLIES', 'weight': 0.78},
        {'from': 'commodity:glass', 'to': 'product:baijiu', 'type': 'SUPPLIES', 'weight': 0.66},
        {'from': 'product:baijiu', 'to': root_id, 'type': 'DEPENDS_ON', 'weight': 0.74},
        {'from': 'industry:baijiu', 'to': root_id, 'type': 'AFFECTED_BY', 'weight': 0.69},
        {'from': 'policy:consumption_tax', 'to': 'industry:baijiu', 'type': 'AFFECTED_BY', 'weight': 0.62},
        {'from': 'region:china', 'to': 'route:domestic_cn', 'type': 'AFFECTED_BY', 'weight': 0.57},
        {'from': 'route:domestic_cn', 'to': root_id, 'type': 'SHIPS_THROUGH', 'weight': 0.58},
    ]
    if include_competitors:
        edges.extend(
            [
                {'from': competitor_id, 'to': root_id, 'type': 'COMPETES_WITH', 'weight': 0.46},
                {'from': root_id, 'to': competitor_id, 'type': 'COMPETES_WITH', 'weight': 0.46},
            ]
        )

    normalized_entity = _normalize_entity(entity or '')
    if normalized_entity:
        slug = re.sub(r'[^a-z0-9]+', '_', normalized_entity.lower()).strip('_')
        if not slug:
            slug = _stable_digest({'entity': normalized_entity}, length=10)
        entity_id = f'entity:{slug[:32]}'
        nodes_by_id[entity_id] = {'id': entity_id, 'type': 'Entity', 'label': normalized_entity}
        low = normalized_entity.lower()
        anchor_id = 'industry:baijiu'
        edge_type = 'AFFECTED_BY'
        if any(token in low for token in ('freight', 'logistics', 'shipping', 'port', 'route', 'transport')):
            anchor_id = 'route:domestic_cn'
            edge_type = 'SHIPS_THROUGH'
        elif any(token in low for token in ('tax', 'policy', 'tariff', 'regulation')):
            anchor_id = 'policy:consumption_tax'
            edge_type = 'AFFECTED_BY'
        elif any(token in low for token in ('grain', 'sorghum', 'corn', 'wheat', 'glass', 'commodity', 'input')):
            anchor_id = 'commodity:sorghum'
            edge_type = 'SUPPLIES'
        edges.append({'from': entity_id, 'to': anchor_id, 'type': edge_type, 'weight': 0.63})

    nodes = sorted(nodes_by_id.values(), key=lambda item: str(item['id']))
    normalized_edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        src = str(edge.get('from', '')).strip()
        dst = str(edge.get('to', '')).strip()
        rel_type = str(edge.get('type', '')).strip().upper()
        if not src or not dst or not rel_type:
            continue
        dedupe_key = (src, dst, rel_type)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized_edges.append(
            {
                'from': src,
                'to': dst,
                'type': rel_type,
                'weight': _normalize_relation_weight(edge.get('weight', RELATION_WEIGHTS.get(rel_type, 0.6))),
            }
        )
    normalized_edges.sort(key=lambda item: (str(item['from']), str(item['to']), str(item['type'])))
    return nodes, normalized_edges


def _resolve_root_node_id(nodes: list[dict[str, Any]], ticker: str) -> str | None:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        return None
    for node in nodes:
        node_id = str(node.get('id', ''))
        label = str(node.get('label', ''))
        if node_id.upper() == normalized_ticker or label.upper() == normalized_ticker:
            return node_id
        if node_id.upper().endswith(f':{normalized_ticker}'):
            return node_id
    for node in nodes:
        if str(node.get('type', '')).upper() == 'COMPANY':
            return str(node.get('id'))
    return None


def _match_node_id(nodes: list[dict[str, Any]], entity: str) -> str | None:
    target = entity.strip().lower()
    if not target:
        return None
    for node in nodes:
        node_id = str(node.get('id', '')).lower()
        label = str(node.get('label', '')).lower()
        if target == node_id or target == label:
            return str(node.get('id'))
    for node in nodes:
        node_id = str(node.get('id', '')).lower()
        label = str(node.get('label', '')).lower()
        if target in node_id or target in label:
            return str(node.get('id'))
    return None


def _adjacency(edges: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    graph: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        src = str(edge.get('from', '')).strip()
        dst = str(edge.get('to', '')).strip()
        if not src or not dst:
            continue
        payload = {
            'from': src,
            'to': dst,
            'type': str(edge.get('type', '')).strip().upper(),
            'weight': _normalize_relation_weight(edge.get('weight', 0.6)),
        }
        graph.setdefault(src, []).append(payload)
        graph.setdefault(dst, []).append(
            {
                'from': dst,
                'to': src,
                'type': payload['type'],
                'weight': payload['weight'],
            }
        )
    return graph


def _find_paths(
    *,
    start_node: str,
    target_node: str,
    edges: list[dict[str, Any]],
    max_hops: int,
    limit: int,
) -> list[TraversalPath]:
    max_steps = max(1, max_hops)
    limit_count = max(1, min(10, limit))
    graph = _adjacency(edges)
    queue: list[TraversalPath] = [TraversalPath(nodes=[start_node], edges=[])]
    out: list[TraversalPath] = []

    while queue:
        current = queue.pop(0)
        current_node = current.nodes[-1]
        if len(current.edges) >= max_steps:
            continue
        for edge in graph.get(current_node, []):
            next_node = str(edge.get('to', ''))
            if not next_node or next_node in current.nodes:
                continue
            next_path = TraversalPath(nodes=current.nodes + [next_node], edges=current.edges + [edge])
            if next_node == target_node:
                out.append(next_path)
                if len(out) >= limit_count * 3:
                    break
            else:
                queue.append(next_path)
        if len(out) >= limit_count * 3:
            break

    out.sort(
        key=lambda item: (
            len(item.edges),
            -sum(_normalize_relation_weight(edge.get('weight', 0.6)) for edge in item.edges) / max(1, len(item.edges)),
        )
    )
    return out[:limit_count]


def _infer_direction(entity: str, path_edges: list[dict[str, Any]]) -> str:
    text = entity.lower()
    has_pos = any(token in text for token in POSITIVE_HINTS)
    has_neg = any(token in text for token in NEGATIVE_HINTS)
    if has_pos and has_neg:
        return 'MIXED'
    if has_pos:
        return 'POS'
    if has_neg:
        return 'NEG'
    if any(str(edge.get('type', '')) == 'COMPETES_WITH' for edge in path_edges):
        return 'NEG'
    if any(str(edge.get('type', '')) == 'SUPPLIES' for edge in path_edges):
        return 'MIXED'
    return 'UNCERTAIN'


def _path_confidence(path_edges: list[dict[str, Any]]) -> tuple[float, float]:
    if not path_edges:
        return 0.0, 0.0
    avg_weight = sum(_normalize_relation_weight(edge.get('weight', 0.6)) for edge in path_edges) / len(path_edges)
    hop_penalty = max(0.4, 1.0 - 0.12 * max(0, len(path_edges) - 1))
    confidence = max(0.2, min(0.95, avg_weight * hop_penalty))
    return round(confidence, 3), round(avg_weight, 3)


def _build_path_payload(entity: str, path: TraversalPath) -> dict[str, Any]:
    confidence, avg_weight = _path_confidence(path.edges)
    direction = _infer_direction(entity, path.edges)
    edge_tokens = [f"{edge['from']}->{edge['to']}:{edge['type']}" for edge in path.edges]
    explanation = f"path_len={len(path.edges)} via {'/'.join(str(edge.get('type', '')) for edge in path.edges)}"
    return {
        'nodes': list(path.nodes),
        'edges': edge_tokens,
        'impact_direction': direction,
        'confidence': confidence,
        'weight': avg_weight,
        'explanation': explanation,
    }


def _subtree_by_depth(
    *,
    root_node: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    depth: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    max_depth = max(1, depth)
    node_map = {str(node.get('id')): node for node in nodes}
    graph = _adjacency(edges)
    queue: list[tuple[str, int]] = [(root_node, 0)]
    visited_depth: dict[str, int] = {root_node: 0}
    used_edges: set[tuple[str, str, str]] = set()

    while queue:
        current, current_depth = queue.pop(0)
        if current_depth >= max_depth:
            continue
        for edge in graph.get(current, []):
            next_node = str(edge.get('to', ''))
            if not next_node:
                continue
            edge_key = (str(edge.get('from', '')), str(edge.get('to', '')), str(edge.get('type', '')))
            used_edges.add(edge_key)
            next_depth = current_depth + 1
            prev_depth = visited_depth.get(next_node)
            if prev_depth is None or next_depth < prev_depth:
                visited_depth[next_node] = next_depth
                queue.append((next_node, next_depth))

    selected_nodes = [node_map[node_id] for node_id in visited_depth if node_id in node_map]
    selected_nodes.sort(key=lambda item: str(item.get('id')))

    selected_edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for src, dst, rel_type in sorted(used_edges):
        if src not in visited_depth or dst not in visited_depth:
            continue
        key = (src, dst, rel_type)
        if key in seen:
            continue
        seen.add(key)
        selected_edges.append({'from': src, 'to': dst, 'type': rel_type})
    selected_edges.sort(key=lambda item: (str(item.get('from')), str(item.get('to')), str(item.get('type'))))
    return selected_nodes, selected_edges


def _neo4j_subtree(
    *,
    ticker: str,
    depth: int,
    include_competitors: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    driver = _get_neo4j_driver()
    if driver is None:
        return None

    relations = ['SUPPLIES', 'DEPENDS_ON', 'AFFECTED_BY', 'SHIPS_THROUGH']
    if include_competitors:
        relations.append('COMPETES_WITH')
    relation_pattern = '|'.join(relations)

    node_query = (
        f"MATCH (root:Company {{ticker: $ticker}}) "
        f"OPTIONAL MATCH p=(root)-[:{relation_pattern}*1..{depth}]-(n) "
        "WITH collect(DISTINCT root) + collect(DISTINCT n) AS ns "
        "UNWIND ns AS n "
        "WITH DISTINCT n "
        "RETURN coalesce(n.id, n.ticker, n.name) AS id, "
        "head(labels(n)) AS type, "
        "coalesce(n.name, n.ticker, n.id) AS label"
    )
    edge_query = (
        f"MATCH (root:Company {{ticker: $ticker}}) "
        f"OPTIONAL MATCH p=(root)-[rels:{relation_pattern}*1..{depth}]-(n) "
        "UNWIND rels AS r "
        "WITH DISTINCT r "
        "RETURN coalesce(startNode(r).id, startNode(r).ticker, startNode(r).name) AS src, "
        "coalesce(endNode(r).id, endNode(r).ticker, endNode(r).name) AS dst, "
        "type(r) AS rel_type, "
        "coalesce(r.weight, 0.6) AS weight"
    )

    try:
        with driver.session() as session:
            node_rows = [dict(row) for row in session.run(node_query, ticker=ticker)]
            edge_rows = [dict(row) for row in session.run(edge_query, ticker=ticker)]
    except Exception:
        return None

    nodes: list[dict[str, Any]] = []
    for row in node_rows:
        node_id = str(row.get('id', '')).strip()
        if not node_id:
            continue
        nodes.append(
            {
                'id': node_id,
                'type': str(row.get('type', '')).strip() or 'Entity',
                'label': str(row.get('label', '')).strip() or node_id,
            }
        )
    if not nodes:
        return None
    nodes.sort(key=lambda item: str(item.get('id')))

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in edge_rows:
        src = str(row.get('src', '')).strip()
        dst = str(row.get('dst', '')).strip()
        rel_type = str(row.get('rel_type', '')).strip().upper()
        if not src or not dst or not rel_type:
            continue
        key = (src, dst, rel_type)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                'from': src,
                'to': dst,
                'type': rel_type,
                'weight': _normalize_relation_weight(row.get('weight', RELATION_WEIGHTS.get(rel_type, 0.6))),
            }
        )
    edges.sort(key=lambda item: (str(item.get('from')), str(item.get('to')), str(item.get('type'))))
    return nodes, edges


def query_supply_chain_subtree(ticker: str, depth: int, include_competitors: bool = True) -> dict[str, Any]:
    normalized_ticker = _normalize_ticker(ticker)
    if not normalized_ticker:
        return _error('INVALID_ARGUMENT', 'ticker must not be blank', details={'field': 'ticker'})
    safe_depth = _safe_int(depth, default=2, min_value=1, max_value=6)
    include = bool(include_competitors)

    cache_key = f'graph_subtree:{normalized_ticker}:{safe_depth}:{int(include)}'
    cached = _GRAPH_CACHE.get(cache_key)
    if cached is not None:
        return _with_cache_meta(cached.value, cache_key=cache_key, cache_hit=True, ttl_seconds=GRAPH_SUBTREE_TTL_SECONDS)

    source = 'SYNTHETIC'
    graph_data = _neo4j_subtree(ticker=normalized_ticker, depth=safe_depth, include_competitors=include)
    if graph_data is None:
        graph_data = _synthetic_company_graph(normalized_ticker, include_competitors=include)
    else:
        source = 'NEO4J'

    nodes, edges = graph_data
    root_node = _resolve_root_node_id(nodes, normalized_ticker)
    if root_node is None:
        return _error('NOT_FOUND', f'company node not found for ticker={normalized_ticker}', details={'ticker': normalized_ticker})

    subtree_nodes, subtree_edges = _subtree_by_depth(root_node=root_node, nodes=nodes, edges=edges, depth=safe_depth)
    graph_id = f"graph_{_stable_digest({'ticker': normalized_ticker, 'depth': safe_depth, 'source': source, 'nodes': [node.get('id') for node in subtree_nodes], 'edges': subtree_edges}, length=14)}"
    path_id = f"path_{_stable_digest({'graph_id': graph_id, 'root': root_node}, length=14)}"
    result = {
        'graph_id': graph_id,
        'path_id': path_id,
        'nodes': subtree_nodes,
        'edges': subtree_edges,
        'meta': {
            'source': source,
            'ticker': normalized_ticker,
            'depth': safe_depth,
            'generated_at': _now_iso(),
        },
    }
    _GRAPH_CACHE.set(cache_key, result, GRAPH_SUBTREE_TTL_SECONDS)
    return _with_cache_meta(result, cache_key=cache_key, cache_hit=False, ttl_seconds=GRAPH_SUBTREE_TTL_SECONDS)


def find_impact_paths(entity: str, ticker: str, max_hops: int = 5) -> dict[str, Any]:
    normalized_ticker = _normalize_ticker(ticker)
    normalized_entity = _normalize_entity(entity)
    if not normalized_ticker:
        return _error('INVALID_ARGUMENT', 'ticker must not be blank', details={'field': 'ticker'})
    if not normalized_entity:
        return _error('INVALID_ARGUMENT', 'entity must not be blank', details={'field': 'entity'})

    safe_max_hops = _safe_int(max_hops, default=5, min_value=1, max_value=8)
    cache_key = f'graph_paths:{normalized_ticker}:{normalized_entity.lower()}:{safe_max_hops}'
    cached = _GRAPH_CACHE.get(cache_key)
    if cached is not None:
        return _with_cache_meta(cached.value, cache_key=cache_key, cache_hit=True, ttl_seconds=GRAPH_PATH_TTL_SECONDS)

    source = 'SYNTHETIC'
    graph_data = _neo4j_subtree(ticker=normalized_ticker, depth=max(2, safe_max_hops), include_competitors=True)
    if graph_data is None:
        graph_data = _synthetic_company_graph(
            normalized_ticker,
            include_competitors=True,
            entity=normalized_entity,
        )
    else:
        source = 'NEO4J'

    nodes, edges = graph_data
    root_node = _resolve_root_node_id(nodes, normalized_ticker)
    if root_node is None:
        return _error('NOT_FOUND', f'company node not found for ticker={normalized_ticker}', details={'ticker': normalized_ticker})

    start_node = _match_node_id(nodes, normalized_entity)
    if start_node is None:
        synth_nodes, synth_edges = _synthetic_company_graph(
            normalized_ticker,
            include_competitors=True,
            entity=normalized_entity,
        )
        node_map = {str(node.get('id')): node for node in nodes}
        for node in synth_nodes:
            node_map.setdefault(str(node.get('id')), node)
        edge_map = {(str(edge.get('from')), str(edge.get('to')), str(edge.get('type'))): edge for edge in edges}
        for edge in synth_edges:
            key = (str(edge.get('from')), str(edge.get('to')), str(edge.get('type')))
            edge_map.setdefault(key, edge)
        nodes = sorted(node_map.values(), key=lambda item: str(item.get('id')))
        edges = sorted(edge_map.values(), key=lambda item: (str(item.get('from')), str(item.get('to')), str(item.get('type'))))
        start_node = _match_node_id(nodes, normalized_entity)

    if start_node is None:
        return _error('NOT_FOUND', f'entity not found in graph: {normalized_entity}', details={'entity': normalized_entity})

    found_paths = _find_paths(
        start_node=start_node,
        target_node=root_node,
        edges=edges,
        max_hops=safe_max_hops,
        limit=3,
    )
    path_payloads = [_build_path_payload(normalized_entity, item) for item in found_paths]
    path_id = f"path_{_stable_digest({'ticker': normalized_ticker, 'entity': normalized_entity, 'paths': path_payloads}, length=14)}"
    result = {
        'path_id': path_id,
        'paths': path_payloads,
        'meta': {
            'source': source,
            'ticker': normalized_ticker,
            'entity': normalized_entity,
            'max_hops': safe_max_hops,
            'generated_at': _now_iso(),
        },
    }
    _GRAPH_CACHE.set(cache_key, result, GRAPH_PATH_TTL_SECONDS)
    return _with_cache_meta(result, cache_key=cache_key, cache_hit=False, ttl_seconds=GRAPH_PATH_TTL_SECONDS)


def compute_exposure_score(ticker: str, entity: str) -> dict[str, Any]:
    normalized_ticker = _normalize_ticker(ticker)
    normalized_entity = _normalize_entity(entity)
    if not normalized_ticker:
        return _error('INVALID_ARGUMENT', 'ticker must not be blank', details={'field': 'ticker'})
    if not normalized_entity:
        return _error('INVALID_ARGUMENT', 'entity must not be blank', details={'field': 'entity'})

    cache_key = f'graph_exposure:{normalized_ticker}:{normalized_entity.lower()}'
    cached = _GRAPH_CACHE.get(cache_key)
    if cached is not None:
        return _with_cache_meta(cached.value, cache_key=cache_key, cache_hit=True, ttl_seconds=GRAPH_EXPOSURE_TTL_SECONDS)

    path_result = find_impact_paths(entity=normalized_entity, ticker=normalized_ticker, max_hops=5)
    if isinstance(path_result.get('error'), dict):
        return path_result

    paths = [path for path in path_result.get('paths', []) if isinstance(path, dict)]
    if not paths:
        result = {
            'ticker': normalized_ticker,
            'entity': normalized_entity,
            'path_id': path_result.get('path_id', ''),
            'exposure_score': 0.0,
            'explanation': 'No impact path found within configured hop limit.',
            'meta': {
                'derived_from': 'find_impact_paths',
                'path_count': 0,
                'generated_at': _now_iso(),
            },
        }
        _GRAPH_CACHE.set(cache_key, result, GRAPH_EXPOSURE_TTL_SECONDS)
        return _with_cache_meta(result, cache_key=cache_key, cache_hit=False, ttl_seconds=GRAPH_EXPOSURE_TTL_SECONDS)

    weighted_components: list[float] = []
    for item in paths:
        confidence = float(item.get('confidence', 0.0))
        avg_weight = float(item.get('weight', 0.0))
        hops = max(1, len(item.get('nodes', [])) - 1)
        hop_factor = 1.0 / hops
        weighted_components.append((0.65 * confidence + 0.35 * avg_weight) * hop_factor)
    score = min(100.0, max(0.0, round(sum(weighted_components) / len(weighted_components) * 130, 2)))
    best = max(paths, key=lambda item: (float(item.get('confidence', 0.0)), float(item.get('weight', 0.0))))
    explanation = (
        f"best_path={best.get('explanation', '')}; "
        f"confidence={best.get('confidence', 0.0)}; "
        f"weight={best.get('weight', 0.0)}; "
        f"path_count={len(paths)}"
    )
    result = {
        'ticker': normalized_ticker,
        'entity': normalized_entity,
        'path_id': path_result.get('path_id', ''),
        'exposure_score': score,
        'explanation': explanation[:280],
        'meta': {
            'derived_from': 'find_impact_paths',
            'path_count': len(paths),
            'generated_at': _now_iso(),
        },
    }
    _GRAPH_CACHE.set(cache_key, result, GRAPH_EXPOSURE_TTL_SECONDS)
    return _with_cache_meta(result, cache_key=cache_key, cache_hit=False, ttl_seconds=GRAPH_EXPOSURE_TTL_SECONDS)


def reset_graph_runtime_state() -> None:
    global _NEO4J_DRIVER, _NEO4J_LAST_FAILURE_TS

    _GRAPH_CACHE.clear()
    _NEO4J_LAST_FAILURE_TS = 0.0
    if _NEO4J_DRIVER is not None:
        with suppress(Exception):
            _NEO4J_DRIVER.close()
    _NEO4J_DRIVER = None
