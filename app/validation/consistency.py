from __future__ import annotations


def _collect_evidence_ids(report_json: dict) -> set[str]:
    refs = report_json.get('evidence_refs', [])
    return {x.get('evidence_id') for x in refs if x.get('evidence_id')}


def check_consistency(report_json: dict) -> list[str]:
    errors: list[str] = []
    evidence_ids = _collect_evidence_ids(report_json)

    if not evidence_ids:
        errors.append('evidence_refs must contain at least 1 item')

    def _check_ids(field_name: str, items: list[dict]):
        for idx, item in enumerate(items):
            for eid in item.get('evidence_ids', []):
                if eid not in evidence_ids:
                    errors.append(f'{field_name}[{idx}] references missing evidence_id={eid}')

    _check_ids('risk_flags', report_json.get('risk_flags', []))
    _check_ids('invalidations', report_json.get('invalidations', []))
    _check_ids('key_drivers_to_watch', report_json.get('key_drivers_to_watch', []))

    for idx, band in enumerate(report_json.get('price_bands', [])):
        rng = band.get('range', {})
        if rng.get('min', 0) > rng.get('max', 0):
            errors.append(f'price_bands[{idx}] range.min must be <= range.max')

    dq = report_json.get('data_quality', {}).get('status', 'OK')
    decision = report_json.get('decision', {})
    if dq != 'OK' and decision.get('action') == 'BUY' and decision.get('confidence', 0) > 0.6:
        errors.append('BUY with high confidence is not allowed when data_quality is not OK')

    return errors
