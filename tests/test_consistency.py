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
