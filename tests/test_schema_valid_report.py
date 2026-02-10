from app.llm.provider import LLMProvider
from app.validation.schema_validator import validate_report_schema


def test_schema_valid_report() -> None:
    provider = LLMProvider()
    report = provider.generate_report_draft(
        {
            'request': {
                'ticker': '600519.SH',
                'market': 'CN_A',
                'asof': '2026-02-10T09:30:00+08:00',
                'strategy_version_id': 'stg_v1',
                'tier': 'TIER0',
                'run_mode': 'LIVE',
            },
            'score': {'overall_score': 66, 'confidence': 0.63},
            'price_bands': [
                {
                    'band_id': 'B1',
                    'range': {'currency': 'CNY', 'min': 95, 'max': 100},
                    'score': 60,
                    'confidence': 0.58,
                    'rationale': 'mock rationale',
                    'entry_conditions': [{'type': 'TECHNICAL', 'description': 'support holds', 'priority': 'MEDIUM'}],
                    'exit_conditions': [{'type': 'RISK', 'description': 'support breaks', 'priority': 'HIGH'}],
                }
            ],
            'evidence_refs': [
                {
                    'evidence_id': 'ev_001',
                    'type': 'SNAPSHOT_FIELD',
                    'title': 'snapshot',
                    'source': 'mock',
                    'captured_at': '2026-02-10T01:30:00Z',
                    'uri': None,
                    'snippet': 'close=100',
                    'checksum': 'abc',
                }
            ],
            'data_quality': {'status': 'OK', 'missing_fields': [], 'notes': ''},
            'snapshot_ids': ['snap_001'],
            'weights_hash': 'w_mock_hash_v1',
            'tool_call_stats': {'tool_calls': 3, 'latency_ms': 120, 'cost_usd_est': 0},
        }
    )
    ok, errors = validate_report_schema(report)
    assert ok, errors
