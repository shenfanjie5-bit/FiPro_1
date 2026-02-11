#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

from neo4j import GraphDatabase


NODES = [
    ('Company', {'id': 'company:600519.SH', 'ticker': '600519.SH', 'name': 'Kweichow Moutai'}),
    ('Company', {'id': 'company:000858.SZ', 'ticker': '000858.SZ', 'name': 'Wuliangye'}),
    ('Industry', {'id': 'industry:baijiu', 'name': 'Baijiu Industry'}),
    ('Product', {'id': 'product:baijiu', 'name': 'Baijiu'}),
    ('Commodity', {'id': 'commodity:sorghum', 'name': 'Sorghum'}),
    ('Commodity', {'id': 'commodity:glass', 'name': 'Glass Packaging'}),
    ('Region', {'id': 'region:china', 'name': 'China Mainland'}),
    ('Route', {'id': 'route:domestic_cn', 'name': 'Domestic Logistics'}),
    ('PolicyEvent', {'id': 'policy:consumption_tax', 'name': 'Consumption Tax Policy'}),
]

RELATIONS = [
    ('commodity:sorghum', 'product:baijiu', 'SUPPLIES', 0.78),
    ('commodity:glass', 'product:baijiu', 'SUPPLIES', 0.66),
    ('product:baijiu', 'company:600519.SH', 'DEPENDS_ON', 0.74),
    ('industry:baijiu', 'company:600519.SH', 'AFFECTED_BY', 0.69),
    ('policy:consumption_tax', 'industry:baijiu', 'AFFECTED_BY', 0.62),
    ('region:china', 'route:domestic_cn', 'AFFECTED_BY', 0.57),
    ('route:domestic_cn', 'company:600519.SH', 'SHIPS_THROUGH', 0.58),
    ('company:000858.SZ', 'company:600519.SH', 'COMPETES_WITH', 0.46),
    ('company:600519.SH', 'company:000858.SZ', 'COMPETES_WITH', 0.46),
]


def _env(key: str, default: str = '') -> str:
    return str(os.getenv(key, default)).strip()


def _require_neo4j_settings() -> tuple[str, str, str]:
    uri = _env('NEO4J_URI', 'bolt://localhost:7687')
    user = _env('NEO4J_USER', 'neo4j')
    password = _env('NEO4J_PASSWORD')
    if not password:
        raise RuntimeError('NEO4J_PASSWORD is required')
    return uri, user, password


def _schema_queries() -> list[str]:
    return [
        'CREATE CONSTRAINT company_ticker_unique IF NOT EXISTS FOR (c:Company) REQUIRE c.ticker IS UNIQUE',
        'CREATE CONSTRAINT industry_id_unique IF NOT EXISTS FOR (n:Industry) REQUIRE n.id IS UNIQUE',
        'CREATE CONSTRAINT product_id_unique IF NOT EXISTS FOR (n:Product) REQUIRE n.id IS UNIQUE',
        'CREATE CONSTRAINT commodity_id_unique IF NOT EXISTS FOR (n:Commodity) REQUIRE n.id IS UNIQUE',
        'CREATE CONSTRAINT region_id_unique IF NOT EXISTS FOR (n:Region) REQUIRE n.id IS UNIQUE',
        'CREATE CONSTRAINT route_id_unique IF NOT EXISTS FOR (n:Route) REQUIRE n.id IS UNIQUE',
        'CREATE CONSTRAINT policy_id_unique IF NOT EXISTS FOR (n:PolicyEvent) REQUIRE n.id IS UNIQUE',
        'CREATE INDEX company_ticker_idx IF NOT EXISTS FOR (c:Company) ON (c.ticker)',
    ]


def seed_graph() -> dict[str, int]:
    uri, user, password = _require_neo4j_settings()
    driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=3)
    node_count = 0
    rel_count = 0
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            for query in _schema_queries():
                session.run(query).consume()

            for label, props in NODES:
                query = (
                    f"MERGE (n:{label} {{id: $id}}) "
                    "SET n += $props"
                )
                session.run(query, id=props['id'], props=props).consume()
                node_count += 1

            for src_id, dst_id, rel_type, weight in RELATIONS:
                query = (
                    f"MATCH (a {{id: $src_id}}), (b {{id: $dst_id}}) "
                    f"MERGE (a)-[r:{rel_type}]->(b) "
                    "SET r.weight = $weight"
                )
                session.run(query, src_id=src_id, dst_id=dst_id, weight=float(weight)).consume()
                rel_count += 1
    finally:
        driver.close()

    return {'nodes': node_count, 'relations': rel_count}


def main() -> None:
    try:
        summary = seed_graph()
    except Exception as exc:  # noqa: BLE001
        print(f'[load_graph_seed] failed: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"[load_graph_seed] done: nodes={summary['nodes']} relations={summary['relations']}")


if __name__ == '__main__':
    main()
