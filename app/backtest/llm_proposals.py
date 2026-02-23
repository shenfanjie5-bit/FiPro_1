from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable
import uuid

from app.backtest.batch import run_batch_backtest
from app.backtest.calibration import load_calibration_profile
from app.backtest.candidates import generate_skill_pack_candidates_from_plans
from app.backtest.promotion import (
    evaluate_skill_pack_promotion,
    execute_skill_pack_promotion,
    list_skill_pack_versions,
    resolve_champion_version,
)
from app.backtest.skill_pack import load_skill_pack
from app.llm.provider import LLMProvider
from app.tools.wrapper import ToolExecutionError


Runner = Callable[[dict[str, Any], str], dict[str, Any]]
SnapshotLoader = Callable[[str, str], dict[str, Any]]

_ALLOWED_CHANGE_ROOTS = {'factors', 'formula', 'policy', 'risk', 'llm_mapping'}
_ALLOWED_APPEND_PATHS = {
    'policy.rules',
    'risk.penalty_rules',
    'risk.hard_stops',
    'llm_mapping.mappings',
}
_MAX_STORED_PROPOSAL_RUNS = 1000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_text(value: Any) -> str:
    return str(value or '').strip()


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_base_version(skill_pack_id: str, base_version: str, *, root_dir: str | Path | None = None) -> str:
    normalized = _safe_text(base_version)
    if normalized and normalized.lower() not in {'champion', 'auto'}:
        return normalized
    champion = resolve_champion_version(skill_pack_id, root_dir=root_dir)
    if champion:
        return champion
    versions = list_skill_pack_versions(skill_pack_id, root_dir=root_dir)
    if versions:
        return str(versions[0].get('version', '0.1.0'))
    return '0.1.0'


def _change_root(path: str) -> str:
    text = _safe_text(path)
    if not text:
        return ''
    head = text.split('.', 1)[0]
    head = head.split('[', 1)[0]
    return _safe_text(head)


def _normalize_change(change: dict[str, Any]) -> dict[str, Any] | None:
    path = _safe_text(change.get('path'))
    if not path:
        return None
    root = _change_root(path)
    if root not in _ALLOWED_CHANGE_ROOTS:
        return None
    op = _safe_text(change.get('op') or 'set').lower() or 'set'
    if op not in {'set', 'append'}:
        return None
    if op == 'append' and path not in _ALLOWED_APPEND_PATHS:
        return None
    return {
        'op': op,
        'path': path,
        'to': change.get('to'),
    }


def _normalize_plans(
    raw_plans: list[dict[str, Any]],
    *,
    proposal_count: int,
    run_id: str,
) -> list[dict[str, Any]]:
    normalized_count = max(1, min(8, _safe_int(proposal_count, 2)))
    plans: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_plans, start=1):
        if len(plans) >= normalized_count:
            break
        if not isinstance(item, dict):
            continue
        raw_changes = item.get('changes')
        if not isinstance(raw_changes, list) or not raw_changes:
            continue
        changes: list[dict[str, Any]] = []
        for raw_change in raw_changes:
            if not isinstance(raw_change, dict):
                continue
            clean = _normalize_change(raw_change)
            if clean is not None:
                changes.append(clean)
        if not changes:
            continue
        proposal_id = _safe_text(item.get('proposal_id') or f'proposal_{idx:02d}')
        plans.append(
            {
                'plan_type': 'llm_proposal',
                'plan_id': proposal_id,
                'description': _safe_text(item.get('description') or f'LLM proposal {idx}'),
                'changes': changes,
                'job_origin': f'llm_proposal:{run_id}:{proposal_id}',
            }
        )
    return plans


def _count_param_changes(changes: list[dict[str, Any]]) -> int:
    return sum(
        1
        for item in changes
        if isinstance(item, dict) and _safe_text(item.get('op') or 'set').lower() == 'set'
    )


def _decision_rank(decision: str) -> int:
    normalized = _safe_text(decision).upper()
    if normalized == 'ALLOW':
        return 3
    if normalized == 'PENDING_MANUAL_APPROVAL':
        return 2
    if normalized == 'BLOCK':
        return 1
    return 0


def _sort_key(item: dict[str, Any]) -> tuple[int, float, float]:
    evaluation = item.get('evaluation') if isinstance(item.get('evaluation'), dict) else {}
    candidate_metrics = (
        evaluation.get('candidate_metrics') if isinstance(evaluation.get('candidate_metrics'), dict) else {}
    )
    decision = _decision_rank(str(evaluation.get('decision', '')))
    excess_delta = _safe_float(candidate_metrics.get('excess_return_delta_pct'), 0.0)
    mdd_delta = _safe_float(candidate_metrics.get('max_drawdown_delta_pct'), 0.0)
    return (decision, excess_delta, -mdd_delta)


def _proposal_run_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / '.run' / 'llm_proposal_runs'
    return Path(root_dir) / '.run' / 'llm_proposal_runs'


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=False) + '\n', encoding='utf-8')


def _prune_old_runs(root: Path) -> None:
    try:
        files = [item for item in root.glob('*.json') if item.is_file()]
    except FileNotFoundError:
        return
    if len(files) <= _MAX_STORED_PROPOSAL_RUNS:
        return
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in files[_MAX_STORED_PROPOSAL_RUNS:]:
        try:
            stale.unlink()
        except FileNotFoundError:
            continue


def _persist_llm_proposal_run(payload: dict[str, Any], *, root_dir: str | Path | None = None) -> None:
    run_id = _safe_text(payload.get('run_id'))
    if not run_id:
        return
    root = _proposal_run_root(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    to_save = copy.deepcopy(payload)
    to_save['persisted_at'] = _now_iso()
    _write_json(root / f'{run_id}.json', to_save)
    _prune_old_runs(root)


def get_llm_proposal_run(run_id: str, *, root_dir: str | Path | None = None) -> dict[str, Any] | None:
    normalized = _safe_text(run_id)
    if not normalized:
        return None
    path = _proposal_run_root(root_dir) / f'{normalized}.json'
    return _read_json(path)


def list_llm_proposal_runs(
    *,
    limit: int = 50,
    offset: int = 0,
    skill_pack_id: str = '',
    executed: bool | None = None,
    dry_run: bool | None = None,
    selected_decision: str = '',
    generated_after: str = '',
    generated_before: str = '',
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_limit = max(1, min(500, _safe_int(limit, 50)))
    normalized_offset = max(0, _safe_int(offset, 0))
    skill_pack_filter = _safe_text(skill_pack_id)
    decision_filter = _safe_text(selected_decision).upper()
    generated_after_dt = _parse_iso_datetime(generated_after) if _safe_text(generated_after) else None
    generated_before_dt = _parse_iso_datetime(generated_before) if _safe_text(generated_before) else None
    if _safe_text(generated_after) and generated_after_dt is None:
        raise ValueError('generated_after must be valid ISO datetime')
    if _safe_text(generated_before) and generated_before_dt is None:
        raise ValueError('generated_before must be valid ISO datetime')
    if generated_after_dt and generated_before_dt and generated_after_dt > generated_before_dt:
        raise ValueError('generated_after must be less than or equal to generated_before')

    root = _proposal_run_root(root_dir)
    if not root.exists() or not root.is_dir():
        return {
            'total': 0,
            'limit': normalized_limit,
            'offset': normalized_offset,
            'summary': {
                'executed_runs': 0,
                'dry_run_runs': 0,
                'selected_decision_counts': {},
                'avg_selected_excess_return_delta_pct': 0.0,
                'avg_selected_segment_win_rate': 0.0,
            },
            'items': [],
        }

    rows: list[dict[str, Any]] = []
    for path in root.glob('*.json'):
        payload = _read_json(path)
        if payload is None:
            continue
        run_id = _safe_text(payload.get('run_id')) or path.stem
        selected = payload.get('selected_candidate') if isinstance(payload.get('selected_candidate'), dict) else {}
        selected_evaluation = selected.get('evaluation') if isinstance(selected.get('evaluation'), dict) else {}
        selected_candidate_metrics = (
            selected_evaluation.get('candidate_metrics')
            if isinstance(selected_evaluation.get('candidate_metrics'), dict)
            else {}
        )
        execution = payload.get('execution') if isinstance(payload.get('execution'), dict) else {}
        row = {
            'run_id': run_id,
            'generated_at': _safe_text(payload.get('generated_at')),
            'skill_pack_id': _safe_text(payload.get('skill_pack_id')),
            'base_version': _safe_text(payload.get('base_version')),
            'proposal_count': _safe_int(payload.get('proposal_count'), 0),
            'dry_run': bool(payload.get('dry_run', False)),
            'selected_candidate_version': _safe_text(selected.get('candidate_version')),
            'selected_decision': _safe_text(selected_evaluation.get('decision')).upper(),
            'selected_excess_return_delta_pct': round(
                _safe_float(selected_candidate_metrics.get('excess_return_delta_pct'), 0.0),
                6,
            ),
            'selected_segment_win_rate': round(
                _safe_float(selected_candidate_metrics.get('segment_win_rate'), 0.0),
                6,
            ),
            'executed': bool(execution.get('executed', False)),
        }
        generated_at_dt = _parse_iso_datetime(row.get('generated_at'))
        if skill_pack_filter and _safe_text(row.get('skill_pack_id')) != skill_pack_filter:
            continue
        if executed is not None and bool(row.get('executed')) != bool(executed):
            continue
        if dry_run is not None and bool(row.get('dry_run')) != bool(dry_run):
            continue
        if decision_filter and _safe_text(row.get('selected_decision')).upper() != decision_filter:
            continue
        if generated_after_dt and (generated_at_dt is None or generated_at_dt < generated_after_dt):
            continue
        if generated_before_dt and (generated_at_dt is None or generated_at_dt > generated_before_dt):
            continue

        rows.append(
            row
        )
    rows.sort(key=lambda item: (_safe_text(item.get('generated_at')), _safe_text(item.get('run_id'))), reverse=True)
    total = len(rows)
    paged = rows[normalized_offset : normalized_offset + normalized_limit]
    decision_counts: dict[str, int] = {}
    excess_values: list[float] = []
    segment_values: list[float] = []
    for item in rows:
        decision = _safe_text(item.get('selected_decision')).upper()
        if decision:
            decision_counts[decision] = decision_counts.get(decision, 0) + 1
        excess_values.append(_safe_float(item.get('selected_excess_return_delta_pct'), 0.0))
        segment_values.append(_safe_float(item.get('selected_segment_win_rate'), 0.0))

    avg_excess = round(sum(excess_values) / len(excess_values), 6) if excess_values else 0.0
    avg_segment = round(sum(segment_values) / len(segment_values), 6) if segment_values else 0.0
    return {
        'total': total,
        'limit': normalized_limit,
        'offset': normalized_offset,
        'summary': {
            'executed_runs': sum(1 for item in rows if bool(item.get('executed', False))),
            'dry_run_runs': sum(1 for item in rows if bool(item.get('dry_run', False))),
            'selected_decision_counts': decision_counts,
            'avg_selected_excess_return_delta_pct': avg_excess,
            'avg_selected_segment_win_rate': avg_segment,
        },
        'items': paged,
    }


def run_llm_skill_pack_proposal_cycle(
    *,
    backtest_payload: dict[str, Any],
    runner: Runner,
    snapshot_loader: SnapshotLoader,
    benchmark_loader: SnapshotLoader,
    skill_pack_id: str = 'cn_a_core',
    base_version: str = 'champion',
    calibration_version: str | None = None,
    proposal_count: int = 2,
    author: str = 'llm_proposer',
    manual_approved: bool = False,
    anti_overfit_evidence: dict[str, Any] | None = None,
    execute: bool = False,
    dry_run: bool = False,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_skill_pack_id = _safe_text(skill_pack_id) or 'cn_a_core'
    normalized_author = _safe_text(author) or 'llm_proposer'
    normalized_proposal_count = max(1, min(8, _safe_int(proposal_count, 2)))
    if execute and dry_run:
        raise ValueError('dry_run=true cannot be used with execute=true')
    if not isinstance(backtest_payload, dict):
        raise ValueError('backtest_payload must be object')

    resolved_base_version = _resolve_base_version(
        normalized_skill_pack_id,
        base_version,
        root_dir=root_dir,
    )
    base_pack = load_skill_pack(
        skill_pack_id=normalized_skill_pack_id,
        version=resolved_base_version,
        root_dir=root_dir,
    )

    profile_version = _safe_text(calibration_version) or resolved_base_version
    calibration_profile: dict[str, Any] | None = None
    try:
        calibration_profile = load_calibration_profile(
            skill_pack_id=normalized_skill_pack_id,
            version=profile_version,
            root_dir=root_dir,
        )
    except ValueError:
        calibration_profile = None

    llm_provider = LLMProvider()
    try:
        raw_plans = llm_provider.generate_skill_pack_candidate_plans(
            skill_pack=base_pack,
            calibration_profile=calibration_profile,
            proposal_count=normalized_proposal_count,
        )
    except ToolExecutionError as exc:
        raise ValueError(f'LLM proposal generation failed: {exc}') from exc

    run_id = f'run_{uuid.uuid4().hex[:8]}'
    plans = _normalize_plans(raw_plans, proposal_count=normalized_proposal_count, run_id=run_id)
    if not plans:
        raise ValueError('LLM returned no valid proposal changes after safety normalization')

    generated = generate_skill_pack_candidates_from_plans(
        skill_pack_id=normalized_skill_pack_id,
        base_version=resolved_base_version,
        plans=plans,
        author=normalized_author,
        dry_run=bool(dry_run),
        root_dir=root_dir,
        default_job_namespace='llm_proposal',
        default_job_profile_id=run_id,
    )

    if dry_run:
        result = {
            'run_id': run_id,
            'generated_at': _now_iso(),
            'skill_pack_id': normalized_skill_pack_id,
            'base_version': resolved_base_version,
            'proposal_count': normalized_proposal_count,
            'dry_run': True,
            'items': generated.get('items', []),
            'message': 'dry_run preview only; no candidate backtest executed',
        }
        _persist_llm_proposal_run(result, root_dir=root_dir)
        return result

    baseline_payload = {
        **backtest_payload,
        'skill_pack_id': normalized_skill_pack_id,
        'skill_pack_version': resolved_base_version,
    }
    try:
        baseline_result = run_batch_backtest(
            baseline_payload,
            runner=runner,
            snapshot_loader=snapshot_loader,
            benchmark_loader=benchmark_loader,
        )
    except ValueError as exc:
        raise ValueError(f'baseline backtest failed: {exc}') from exc

    gate_config = base_pack.get('gate')
    if not isinstance(gate_config, dict):
        raise ValueError('base skill pack gate config missing')

    evaluations: list[dict[str, Any]] = []
    base_evidence = anti_overfit_evidence if isinstance(anti_overfit_evidence, dict) else {}
    plan_changes_by_plan_id = {
        _safe_text(plan.get('plan_id')): (plan.get('changes') if isinstance(plan.get('changes'), list) else [])
        for plan in plans
        if isinstance(plan, dict) and _safe_text(plan.get('plan_id'))
    }
    item_by_version = {
        _safe_text(item.get('version')): item
        for item in generated.get('items', [])
        if isinstance(item, dict) and _safe_text(item.get('version'))
    }

    for version, item in item_by_version.items():
        candidate_payload = {
            **backtest_payload,
            'skill_pack_id': normalized_skill_pack_id,
            'skill_pack_version': version,
        }
        try:
            candidate_result = run_batch_backtest(
                candidate_payload,
                runner=runner,
                snapshot_loader=snapshot_loader,
                benchmark_loader=benchmark_loader,
            )
        except ValueError as exc:
            evaluations.append(
                {
                    'candidate_version': version,
                    'proposal_id': _safe_text(item.get('plan_id')),
                    'status': 'FAILED',
                    'error': str(exc),
                }
            )
            continue

        merged_evidence = copy.deepcopy(base_evidence)
        if 'param_change_count' not in merged_evidence:
            plan_id = _safe_text(item.get('plan_id'))
            merged_evidence['param_change_count'] = _count_param_changes(plan_changes_by_plan_id.get(plan_id, []))

        evaluation = evaluate_skill_pack_promotion(
            candidate_backtest_result=candidate_result,
            champion_backtest_result=baseline_result,
            gate_config=gate_config,
            candidate_version=version,
            champion_version=resolved_base_version,
            manual_approved=bool(manual_approved),
            anti_overfit_evidence=merged_evidence,
        )
        evaluations.append(
            {
                'candidate_version': version,
                'proposal_id': _safe_text(item.get('plan_id')),
                'status': 'COMPLETED',
                'backtest_summary': candidate_result.get('summary', {}),
                'evaluation': evaluation,
            }
        )

    completed = [item for item in evaluations if _safe_text(item.get('status')).upper() == 'COMPLETED']
    if not completed:
        raise ValueError('all candidate backtests failed')
    ranked = sorted(completed, key=_sort_key, reverse=True)
    deterministic_best = ranked[0]
    deterministic_best_version = _safe_text(deterministic_best.get('candidate_version'))

    proposal_evaluations_for_llm: list[dict[str, Any]] = []
    for item in ranked:
        evaluation = item.get('evaluation', {})
        if not isinstance(evaluation, dict):
            continue
        proposal_evaluations_for_llm.append(
            {
                'candidate_version': _safe_text(item.get('candidate_version')),
                'decision': _safe_text(evaluation.get('decision')),
                'failed_checks': evaluation.get('failed_checks', []),
                'candidate_metrics': evaluation.get('candidate_metrics', {}),
                'champion_metrics': evaluation.get('champion_metrics', {}),
            }
        )

    try:
        llm_pick = llm_provider.select_best_skill_pack_proposal(
            proposal_evaluations=proposal_evaluations_for_llm,
            default_candidate_version=deterministic_best_version,
        )
    except ToolExecutionError:
        llm_pick = {
            'candidate_version': deterministic_best_version,
            'rationale': 'fallback to deterministic ranking',
        }

    selected_version = _safe_text(llm_pick.get('candidate_version')) or deterministic_best_version
    selected_item = next(
        (item for item in ranked if _safe_text(item.get('candidate_version')) == selected_version),
        deterministic_best,
    )
    selected_evaluation = selected_item.get('evaluation')
    if not isinstance(selected_evaluation, dict):
        selected_evaluation = {}

    execution = None
    if execute:
        execution = execute_skill_pack_promotion(
            skill_pack_id=normalized_skill_pack_id,
            candidate_version=selected_version,
            evaluation=selected_evaluation,
            champion_version=resolved_base_version,
            dry_run=False,
            root_dir=root_dir,
        )

    result = {
        'run_id': run_id,
        'generated_at': _now_iso(),
        'skill_pack_id': normalized_skill_pack_id,
        'base_version': resolved_base_version,
        'proposal_count': normalized_proposal_count,
        'dry_run': False,
        'baseline_backtest': {
            'batch_id': baseline_result.get('batch_id', ''),
            'summary': baseline_result.get('summary', {}),
        },
        'candidate_generation': generated,
        'candidate_evaluations': evaluations,
        'selected_candidate': {
            'candidate_version': selected_version,
            'selection_mode': 'llm_compare',
            'llm_selector': llm_pick,
            'deterministic_best_version': deterministic_best_version,
            'evaluation': selected_evaluation,
        },
        'execution': execution,
    }
    _persist_llm_proposal_run(result, root_dir=root_dir)
    return result
