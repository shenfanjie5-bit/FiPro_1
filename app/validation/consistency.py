from __future__ import annotations


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _collect_evidence_ids(report_json: dict) -> set[str]:
    refs = report_json.get('evidence_refs', [])
    return {x.get('evidence_id') for x in refs if x.get('evidence_id')}


def _collect_graph_artifact_ids(report_json: dict) -> set[str]:
    refs = report_json.get('evidence_refs', [])
    graph_ids: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if str(ref.get('type', '')).upper() != 'GRAPH_QUERY':
            continue
        checksum = str(ref.get('checksum', '')).strip()
        if checksum:
            graph_ids.add(checksum)
    return graph_ids


def check_consistency(report_json: dict) -> list[str]:
    errors: list[str] = []
    evidence_ids = _collect_evidence_ids(report_json)
    graph_artifact_ids = _collect_graph_artifact_ids(report_json)

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

    for idx, item in enumerate(report_json.get('key_drivers_to_watch', [])):
        if not isinstance(item, dict):
            continue
        for graph_ref in item.get('graph_refs', []):
            graph_id = str(graph_ref).strip()
            if graph_id and graph_id not in graph_artifact_ids:
                errors.append(f'key_drivers_to_watch[{idx}] references missing graph_ref={graph_id}')

    for idx, band in enumerate(report_json.get('price_bands', [])):
        rng = band.get('range', {})
        if rng.get('min', 0) > rng.get('max', 0):
            errors.append(f'price_bands[{idx}] range.min must be <= range.max')

    dq = report_json.get('data_quality', {}).get('status', 'OK')
    decision = report_json.get('decision', {})
    if dq != 'OK' and decision.get('action') == 'BUY' and decision.get('confidence', 0) > 0.6:
        errors.append('BUY with high confidence is not allowed when data_quality is not OK')

    provenance = report_json.get('provenance', {})
    ta_hybrid = provenance.get('ta_hybrid', {}) if isinstance(provenance, dict) else {}
    require_evidence_refs = bool(ta_hybrid.get('require_evidence_refs', True)) if isinstance(ta_hybrid, dict) else True
    if isinstance(ta_hybrid, dict) and bool(ta_hybrid.get('applied', False)) and require_evidence_refs:
        has_ta_reasoning = any(
            isinstance(ref, dict) and str(ref.get('type', '')).upper() == 'AGENT_REASONING'
            for ref in report_json.get('evidence_refs', [])
        )
        if not has_ta_reasoning:
            errors.append('ta_hybrid.applied requires at least one AGENT_REASONING evidence_ref')

    disagreement = _safe_float(ta_hybrid.get('disagreement'), default=0.0) if isinstance(ta_hybrid, dict) else 0.0
    if disagreement > 0.75 and str(decision.get('action', '')).upper() == 'BUY':
        errors.append('BUY is not allowed when ta_hybrid.disagreement is above 0.75')

    return errors
