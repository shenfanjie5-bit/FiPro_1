from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from app.validation.consistency import check_consistency
from app.validation.schema_validator import validate_report_schema


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_fields(report: dict[str, Any]) -> dict[str, Any]:
    decision = report.get('decision', {})
    dq = report.get('data_quality', {})
    return {
        'ticker': report.get('ticker', ''),
        'asof': report.get('asof', ''),
        'tier': report.get('tier', ''),
        'action': decision.get('action', ''),
        'overall_score': decision.get('overall_score', 0),
        'time_horizon': decision.get('time_horizon', ''),
        'data_quality_status': dq.get('status', 'OK'),
    }


def _to_jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=True, sort_keys=True, default=str))


def build_m6_rollout_drill(
    *,
    baseline_report: dict[str, Any],
    replay_report_a: dict[str, Any],
    replay_report_b: dict[str, Any],
    fault_report: dict[str, Any],
) -> dict[str, Any]:
    baseline_schema_ok, baseline_schema_errors = validate_report_schema(baseline_report)
    baseline_consistency_errors = check_consistency(baseline_report) if baseline_schema_ok else []

    replay_a_fields = _stable_fields(replay_report_a)
    replay_b_fields = _stable_fields(replay_report_b)
    replay_stable = replay_a_fields == replay_b_fields

    fault_schema_ok, fault_schema_errors = validate_report_schema(fault_report)
    fault_consistency_errors = check_consistency(fault_report) if fault_schema_ok else []
    fault_is_conservative = str(fault_report.get('decision', {}).get('action', 'WATCH')) != 'BUY'

    checks = [
        {'name': 'baseline_schema', 'pass': bool(baseline_schema_ok), 'details': baseline_schema_errors[:5]},
        {'name': 'baseline_consistency', 'pass': not baseline_consistency_errors, 'details': baseline_consistency_errors[:5]},
        {'name': 'replay_stability', 'pass': bool(replay_stable), 'details': {'a': replay_a_fields, 'b': replay_b_fields}},
        {'name': 'fault_schema', 'pass': bool(fault_schema_ok), 'details': fault_schema_errors[:5]},
        {'name': 'fault_consistency', 'pass': not fault_consistency_errors, 'details': fault_consistency_errors[:5]},
        {'name': 'fault_conservative_action', 'pass': bool(fault_is_conservative), 'details': {'action': fault_report.get('decision', {}).get('action')}},
    ]
    overall_status = 'PASS' if all(bool(item.get('pass')) for item in checks) else 'FAIL'

    return _to_jsonable(
        {
            'generated_at': _now_iso(),
            'overall_status': overall_status,
            'checks': checks,
            'artifacts': {
                'baseline_report_id': baseline_report.get('report_id', ''),
                'replay_report_id_a': replay_report_a.get('report_id', ''),
                'replay_report_id_b': replay_report_b.get('report_id', ''),
                'fault_report_id': fault_report.get('report_id', ''),
            },
        }
    )


def render_m6_rollout_markdown(report: dict[str, Any]) -> str:
    lines = [
        '# M6 Rollout Drill',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Overall Status: `{report.get('overall_status', 'UNKNOWN')}`",
        '',
        '## Drill Checks',
        '',
    ]
    for item in report.get('checks', []):
        lines.append(f"- [{ 'PASS' if item.get('pass') else 'FAIL' }] `{item.get('name', '')}`")
    lines.extend(
        [
            '',
            '## Artifact Refs',
            '',
            f"- baseline_report_id: `{report.get('artifacts', {}).get('baseline_report_id', '')}`",
            f"- replay_report_id_a: `{report.get('artifacts', {}).get('replay_report_id_a', '')}`",
            f"- replay_report_id_b: `{report.get('artifacts', {}).get('replay_report_id_b', '')}`",
            f"- fault_report_id: `{report.get('artifacts', {}).get('fault_report_id', '')}`",
        ]
    )
    return '\n'.join(lines) + '\n'
