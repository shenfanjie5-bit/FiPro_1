from __future__ import annotations

from app.eval.m7_monthly_review import build_m7_monthly_review, render_m7_monthly_review_markdown


def test_build_m7_monthly_review_creates_backlog() -> None:
    feedback_rows = [
        {'report_id': 'r1', 'feedback_label': 'USELESS', 'comment': 'not helpful'},
        {'report_id': 'r2', 'feedback_label': 'FALSE_POSITIVE', 'comment': 'bad alert'},
        {'report_id': 'r3', 'feedback_label': 'USEFUL', 'comment': 'good'},
    ]
    shadow_compare = {'summary': {'decision_change_rate': 0.5, 'challenger_quality_win_rate': 0.4, 'paired_sample_size': 10}}
    drift_report = {'overall_status': 'FAIL', 'alerts': [{'severity': 'critical'}]}
    offline_eval = {'tracks': {'CHALLENGER': {'schema_pass_rate': 0.95}}}

    report = build_m7_monthly_review(
        feedback_rows,
        offline_eval_report=offline_eval,
        shadow_compare_report=shadow_compare,
        drift_report=drift_report,
        month_label='2026-02',
    )
    assert report['overall_status'] == 'ACTION_REQUIRED'
    assert report['backlog']


def test_render_m7_monthly_review_markdown_contains_sections() -> None:
    report = build_m7_monthly_review([], month_label='2026-02')
    markdown = render_m7_monthly_review_markdown(report)
    assert '# M7 Monthly Review' in markdown
    assert '## Feedback Summary' in markdown
    assert '## Backlog Items' in markdown
