from app.validation.consistency import check_consistency


def test_consistency_detects_missing_evidence_reference() -> None:
    report = {
        'evidence_refs': [
            {
                'evidence_id': 'ev_1',
                'type': 'SNAPSHOT_FIELD',
                'title': 'mock',
                'source': 'mock',
                'captured_at': '2026-02-10T01:30:00Z',
            }
        ],
        'risk_flags': [{'risk_id': 'r1', 'severity': 'LOW', 'description': 'x', 'evidence_ids': ['ev_missing']}],
        'invalidations': [],
        'key_drivers_to_watch': [],
        'price_bands': [],
        'data_quality': {'status': 'OK'},
        'decision': {'action': 'WATCH', 'confidence': 0.5},
    }
    errors = check_consistency(report)
    assert any('missing evidence_id=ev_missing' in err for err in errors)


def test_consistency_detects_unresolved_graph_refs() -> None:
    report = {
        'evidence_refs': [
            {
                'evidence_id': 'ev_1',
                'type': 'SNAPSHOT_FIELD',
                'title': 'mock',
                'source': 'mock',
                'captured_at': '2026-02-10T01:30:00Z',
            }
        ],
        'risk_flags': [],
        'invalidations': [],
        'key_drivers_to_watch': [
            {
                'driver_id': 'd1',
                'graph_refs': ['path_missing_001'],
                'evidence_ids': ['ev_1'],
            }
        ],
        'price_bands': [],
        'data_quality': {'status': 'OK'},
        'decision': {'action': 'WATCH', 'confidence': 0.5},
    }
    errors = check_consistency(report)
    assert any('missing graph_ref=path_missing_001' in err for err in errors)


def test_consistency_requires_agent_reasoning_when_ta_hybrid_applied() -> None:
    report = {
        'evidence_refs': [
            {
                'evidence_id': 'ev_1',
                'type': 'SNAPSHOT_FIELD',
                'title': 'market snapshot',
                'source': 'mock',
                'captured_at': '2026-02-10T01:30:00Z',
            }
        ],
        'risk_flags': [],
        'invalidations': [],
        'key_drivers_to_watch': [],
        'price_bands': [],
        'data_quality': {'status': 'OK'},
        'decision': {'action': 'WATCH', 'confidence': 0.5},
        'provenance': {
            'ta_hybrid': {
                'applied': True,
                'require_evidence_refs': True,
                'disagreement': 0.1,
            }
        },
    }
    errors = check_consistency(report)
    assert any('ta_hybrid.applied requires at least one AGENT_REASONING evidence_ref' in err for err in errors)


def test_consistency_blocks_buy_with_high_ta_disagreement() -> None:
    report = {
        'evidence_refs': [
            {
                'evidence_id': 'ev_1',
                'type': 'AGENT_REASONING',
                'title': 'ta hybrid reasoning',
                'source': 'ta_hybrid.research',
                'captured_at': '2026-02-10T01:30:00Z',
            }
        ],
        'risk_flags': [],
        'invalidations': [],
        'key_drivers_to_watch': [],
        'price_bands': [],
        'data_quality': {'status': 'OK'},
        'decision': {'action': 'BUY', 'confidence': 0.55},
        'provenance': {
            'ta_hybrid': {
                'applied': True,
                'require_evidence_refs': True,
                'disagreement': 0.92,
            }
        },
    }
    errors = check_consistency(report)
    assert any('BUY is not allowed when ta_hybrid.disagreement is above 0.75' in err for err in errors)
