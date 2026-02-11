from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from typing import Any

from app.eval.m4_baseline import build_m4_quality_baseline, load_report_samples
from app.workflows.graph import run_research_workflow
from app.workflows.persistence import get_report


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return {}


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding='utf-8')


def _extract_report_ids_from_baseline(baseline_report: dict[str, Any], *, limit: int) -> list[str]:
    report_ids: list[str] = []
    for key in ('tier1_low_coverage_reports', 'raw_tier1_low_coverage_reports'):
        rows = baseline_report.get(key, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            report_id = str(row.get('report_id', '')).strip()
            if report_id and report_id not in report_ids:
                report_ids.append(report_id)
            if len(report_ids) >= max(1, int(limit)):
                break
        if len(report_ids) >= max(1, int(limit)):
            break
    return report_ids


def _build_request_from_report(report_json: dict[str, Any], *, run_mode_strategy: str) -> dict[str, Any] | None:
    ticker = str(report_json.get('ticker', '')).strip()
    market = str(report_json.get('market', 'OTHER')).strip() or 'OTHER'
    asof = str(report_json.get('asof', '')).strip()
    strategy_version_id = str(report_json.get('strategy_version_id', '')).strip()
    tier = str(report_json.get('tier', '')).strip()
    provenance = report_json.get('provenance', {}) if isinstance(report_json.get('provenance', {}), dict) else {}
    original_run_mode = str(provenance.get('run_mode', report_json.get('run_mode', 'LIVE'))).strip() or 'LIVE'

    if not ticker or not asof or not strategy_version_id or tier not in {'TIER0', 'TIER1', 'TIER2'}:
        return None

    mode_strategy = str(run_mode_strategy or 'same').strip().lower()
    if mode_strategy == 'same':
        run_mode = original_run_mode
    elif mode_strategy == 'live':
        run_mode = 'LIVE'
    elif mode_strategy == 'backtest':
        run_mode = 'BACKTEST'
    elif mode_strategy == 'shadow':
        run_mode = 'SHADOW'
    else:
        run_mode = original_run_mode

    return {
        'ticker': ticker,
        'market': market,
        'asof': asof,
        'strategy_version_id': strategy_version_id,
        'tier': tier,
        'run_mode': run_mode,
    }


def replay_single_report(report_id: str, *, run_mode_strategy: str = 'same') -> dict[str, Any]:
    current = get_report(report_id)
    if not current:
        return {'source_report_id': report_id, 'status': 'SKIPPED', 'reason': 'report_not_found'}

    request = _build_request_from_report(current, run_mode_strategy=run_mode_strategy)
    if not request:
        return {'source_report_id': report_id, 'status': 'SKIPPED', 'reason': 'invalid_request_payload'}

    replay_thread_id = f"replay_cov_{request['tier'].lower()}_{uuid.uuid4().hex[:8]}"
    try:
        result = run_research_workflow(request_data=request, thread_id=replay_thread_id)
    except Exception as exc:  # noqa: BLE001
        return {'source_report_id': report_id, 'status': 'FAILED', 'reason': str(exc), 'thread_id': replay_thread_id}

    replayed = result.get('final_report', {}) if isinstance(result, dict) else {}
    new_report_id = str(replayed.get('report_id', '')).strip()
    evidence_count = len(replayed.get('evidence_refs', [])) if isinstance(replayed.get('evidence_refs', []), list) else 0
    return {
        'source_report_id': report_id,
        'status': 'REPLAYED',
        'thread_id': replay_thread_id,
        'new_report_id': new_report_id,
        'tier': str(replayed.get('tier', request.get('tier', ''))),
        'run_mode': str(replayed.get('provenance', {}).get('run_mode', request.get('run_mode', ''))),
        'evidence_refs': evidence_count,
    }


def replay_tier1_low_coverage_reports(
    *,
    lookback_days: int,
    batch_size: int,
    max_rounds: int,
    run_mode_strategy: str = 'same',
    baseline_json_path: str | None = None,
) -> dict[str, Any]:
    rounds: list[dict[str, Any]] = []
    initial_samples = load_report_samples(lookback_days=lookback_days)
    initial_baseline = build_m4_quality_baseline(initial_samples, lookback_days=lookback_days)

    attempted_source_ids: set[str] = set()
    final_baseline = initial_baseline
    for round_index in range(1, max(1, int(max_rounds)) + 1):
        current_samples = load_report_samples(lookback_days=lookback_days)
        current_baseline = build_m4_quality_baseline(current_samples, lookback_days=lookback_days)
        final_baseline = current_baseline
        if current_baseline.get('overall_status') == 'PASS':
            break

        if baseline_json_path and round_index == 1:
            external_baseline = _read_json_file(Path(baseline_json_path))
            candidates = _extract_report_ids_from_baseline(external_baseline, limit=batch_size)
            if not candidates:
                candidates = _extract_report_ids_from_baseline(current_baseline, limit=batch_size)
        else:
            candidates = _extract_report_ids_from_baseline(current_baseline, limit=batch_size)
        candidates = [report_id for report_id in candidates if report_id not in attempted_source_ids]
        if not candidates:
            rounds.append(
                {
                    'round': round_index,
                    'status_before': current_baseline.get('overall_status'),
                    'candidate_count': 0,
                    'replayed_count': 0,
                    'items': [],
                    'note': 'no_remaining_candidates',
                }
            )
            break

        items: list[dict[str, Any]] = []
        for source_report_id in candidates:
            attempted_source_ids.add(source_report_id)
            replay_result = replay_single_report(source_report_id, run_mode_strategy=run_mode_strategy)
            items.append(replay_result)

        replayed_count = len([item for item in items if item.get('status') == 'REPLAYED'])
        rounds.append(
            {
                'round': round_index,
                'status_before': current_baseline.get('overall_status'),
                'candidate_count': len(candidates),
                'replayed_count': replayed_count,
                'items': items,
            }
        )

    final_samples = load_report_samples(lookback_days=lookback_days)
    final_baseline = build_m4_quality_baseline(final_samples, lookback_days=lookback_days)

    replayed_total = sum(round_item.get('replayed_count', 0) for round_item in rounds)
    initial_failed_checks = [item.get('metric', '') for item in initial_baseline.get('threshold_checks', []) if not item.get('pass')]
    final_failed_checks = [item.get('metric', '') for item in final_baseline.get('threshold_checks', []) if not item.get('pass')]
    return {
        'generated_at': _now_utc_iso(),
        'window': {'lookback_days': int(lookback_days)},
        'strategy': {
            'batch_size': int(batch_size),
            'max_rounds': int(max_rounds),
            'run_mode_strategy': str(run_mode_strategy),
        },
        'initial_status': initial_baseline.get('overall_status'),
        'final_status': final_baseline.get('overall_status'),
        'initial_overall': initial_baseline.get('overall', {}),
        'final_overall': final_baseline.get('overall', {}),
        'initial_tier1_low_coverage_count': len(initial_baseline.get('tier1_low_coverage_reports', [])),
        'final_tier1_low_coverage_count': len(final_baseline.get('tier1_low_coverage_reports', [])),
        'initial_raw_tier1_low_coverage_count': len(initial_baseline.get('raw_tier1_low_coverage_reports', [])),
        'final_raw_tier1_low_coverage_count': len(final_baseline.get('raw_tier1_low_coverage_reports', [])),
        'initial_failed_checks': initial_failed_checks,
        'final_failed_checks': final_failed_checks,
        'replayed_total': replayed_total,
        'rounds': rounds,
        'baseline_before': initial_baseline,
        'baseline_after': final_baseline,
    }


def render_low_coverage_replay_markdown(report: dict[str, Any]) -> str:
    lines = [
        '# TIER1 Low Coverage Replay Repair',
        '',
        f"- Generated At: `{report.get('generated_at', '')}`",
        f"- Initial Status: `{report.get('initial_status', 'UNKNOWN')}`",
        f"- Final Status: `{report.get('final_status', 'UNKNOWN')}`",
        f"- Replayed Total: `{report.get('replayed_total', 0)}`",
        f"- Initial TIER1 Low Coverage Count: `{report.get('initial_tier1_low_coverage_count', 0)}`",
        f"- Final TIER1 Low Coverage Count: `{report.get('final_tier1_low_coverage_count', 0)}`",
        f"- Initial RAW TIER1 Low Coverage Count: `{report.get('initial_raw_tier1_low_coverage_count', 0)}`",
        f"- Final RAW TIER1 Low Coverage Count: `{report.get('final_raw_tier1_low_coverage_count', 0)}`",
        f"- Initial Failed Checks: `{','.join(report.get('initial_failed_checks', [])) or 'none'}`",
        f"- Final Failed Checks: `{','.join(report.get('final_failed_checks', [])) or 'none'}`",
        '',
        '## Rounds',
        '',
        '| Round | Status Before | Candidates | Replayed |',
        '|---|---|---:|---:|',
    ]
    for round_item in report.get('rounds', []):
        lines.append(
            f"| {round_item.get('round', 0)} | {round_item.get('status_before', '')} | "
            f"{round_item.get('candidate_count', 0)} | {round_item.get('replayed_count', 0)} |"
        )

    lines.extend(['', '## Replay Mapping', '', '| Source Report ID | New Report ID | Status |'])
    lines.append('|---|---|---|')
    for round_item in report.get('rounds', []):
        for item in round_item.get('items', []):
            lines.append(
                f"| {item.get('source_report_id', '')} | {item.get('new_report_id', '')} | {item.get('status', '')} |"
            )
    return '\n'.join(lines) + '\n'


def write_replay_artifacts(report: dict[str, Any], *, output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    _write_json_file(output_json, report)
    output_md.write_text(render_low_coverage_replay_markdown(report), encoding='utf-8')
