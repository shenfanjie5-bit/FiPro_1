from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_m7_monthly_review(
    feedback_rows: list[dict[str, Any]],
    *,
    offline_eval_report: dict[str, Any] | None = None,
    shadow_compare_report: dict[str, Any] | None = None,
    drift_report: dict[str, Any] | None = None,
    month_label: str = '',
) -> dict[str, Any]:
    month = month_label.strip() or datetime.now(timezone.utc).strftime('%Y-%m')
    counts = {'USEFUL': 0, 'USELESS': 0, 'FALSE_POSITIVE': 0}
    bad_report_ids: list[str] = []

    for row in feedback_rows:
        label = str(row.get('feedback_label', '')).upper()
        if label in counts:
            counts[label] += 1
        if label in {'USELESS', 'FALSE_POSITIVE'}:
            report_id = str(row.get('report_id', '')).strip()
            if report_id and report_id not in bad_report_ids:
                bad_report_ids.append(report_id)

    total_feedback = max(1, sum(counts.values()))
    useless_rate = round((counts['USELESS'] + counts['FALSE_POSITIVE']) / total_feedback, 6)

    findings: list[dict[str, Any]] = []
    findings.append(
        {
            'title': 'Feedback summary',
            'details': {
                'useful': counts['USEFUL'],
                'useless': counts['USELESS'],
                'false_positive': counts['FALSE_POSITIVE'],
                'negative_feedback_rate': useless_rate,
            },
        }
    )

    if drift_report:
        findings.append(
            {
                'title': 'Drift monitor',
                'details': {
                    'overall_status': drift_report.get('overall_status', 'UNKNOWN'),
                    'alerts': len(drift_report.get('alerts', [])),
                },
            }
        )

    if shadow_compare_report:
        summary = dict(shadow_compare_report.get('summary', {}))
        findings.append(
            {
                'title': 'Shadow compare',
                'details': {
                    'paired_sample_size': summary.get('paired_sample_size', 0),
                    'decision_change_rate': summary.get('decision_change_rate', 0.0),
                    'challenger_quality_win_rate': summary.get('challenger_quality_win_rate', 0.0),
                },
            }
        )

    backlog: list[dict[str, Any]] = []

    def add_backlog(title: str, priority: str, owner: str, reason: str) -> None:
        backlog.append(
            {
                'id': f"M7-RVW-{len(backlog) + 1:02d}",
                'title': title,
                'priority': priority,
                'owner': owner,
                'reason': reason,
            }
        )

    if useless_rate >= 0.20:
        add_backlog(
            'Revise risk gate prompts and fallback wording',
            'P0',
            'Product+BE',
            f'Negative feedback rate={useless_rate:.2f} exceeds 0.20.',
        )

    if counts['FALSE_POSITIVE'] > 0:
        add_backlog(
            'Review false-positive report cases and update invalidation rules',
            'P1',
            'QA+BE',
            f'Found {counts["FALSE_POSITIVE"]} false-positive labels linked to report_id.',
        )

    if drift_report and drift_report.get('overall_status') in {'WARN', 'FAIL'}:
        add_backlog(
            'Run drift investigation and feature-slice diagnosis',
            'P0' if drift_report.get('overall_status') == 'FAIL' else 'P1',
            'Data+SRE',
            f"Drift status={drift_report.get('overall_status')} with {len(drift_report.get('alerts', []))} alerts.",
        )

    if shadow_compare_report:
        summary = dict(shadow_compare_report.get('summary', {}))
        if _safe_float(summary.get('decision_change_rate', 0.0), 0.0) > 0.35:
            add_backlog(
                'Reduce challenger decision volatility before promotion',
                'P0',
                'ML Eng',
                f"decision_change_rate={summary.get('decision_change_rate', 0.0)} exceeds 0.35.",
            )

    if offline_eval_report:
        challenger_track = dict((offline_eval_report.get('tracks') or {}).get('CHALLENGER', {}))
        schema_pass = _safe_float(challenger_track.get('schema_pass_rate', 0.0), 0.0)
        if schema_pass < 0.99:
            add_backlog(
                'Improve challenger schema stability',
                'P0',
                'ML Eng+QA',
                f'challenger schema_pass_rate={schema_pass:.4f} below 0.99.',
            )

    overall_status = 'STABLE' if not backlog else 'ACTION_REQUIRED'
    return {
        'generated_at': _now_iso(),
        'month': month,
        'overall_status': overall_status,
        'summary': {
            'feedback_total': sum(counts.values()),
            'feedback_counts': counts,
            'negative_feedback_rate': useless_rate,
            'bad_report_ids': bad_report_ids[:100],
        },
        'findings': findings,
        'backlog': backlog,
    }


def render_m7_monthly_review_markdown(report: dict[str, Any]) -> str:
    summary = report.get('summary', {})
    feedback_counts = summary.get('feedback_counts', {})
    lines = [
        '# M7 Monthly Review',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Month: `{report.get('month', '')}`",
        f"- Overall Status: `{report.get('overall_status', 'UNKNOWN')}`",
        '',
        '## Feedback Summary',
        '',
        f"- total: `{summary.get('feedback_total', 0)}`",
        f"- useful: `{feedback_counts.get('USEFUL', 0)}`",
        f"- useless: `{feedback_counts.get('USELESS', 0)}`",
        f"- false_positive: `{feedback_counts.get('FALSE_POSITIVE', 0)}`",
        f"- negative_feedback_rate: `{summary.get('negative_feedback_rate', 0)}`",
        '',
        '## Findings',
        '',
    ]

    for item in report.get('findings', []):
        lines.append(f"- {item.get('title', '')}: `{item.get('details', {})}`")

    lines.extend(['', '## Backlog Items', ''])
    backlog = report.get('backlog', [])
    if not backlog:
        lines.append('- No new backlog items.')
    else:
        lines.append('| ID | Priority | Owner | Title | Reason |')
        lines.append('|---|---|---|---|---|')
        for item in backlog:
            lines.append(
                f"| {item.get('id', '')} | {item.get('priority', '')} | {item.get('owner', '')} | "
                f"{item.get('title', '')} | {item.get('reason', '')} |"
            )

    return '\n'.join(lines) + '\n'
