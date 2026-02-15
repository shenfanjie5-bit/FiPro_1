from __future__ import annotations

from datetime import datetime, timezone
import copy
from itertools import combinations
import json
from pathlib import Path
from typing import Any

from app.backtest.calibration import load_calibration_profile
from app.backtest.promotion import list_skill_pack_versions, resolve_champion_version
from app.backtest.skill_pack import clear_skill_pack_cache, load_skill_pack


_SKILL_PACK_FILES = (
    'manifest.json',
    'factors.json',
    'formula.json',
    'policy.json',
    'risk.json',
    'llm_mapping.json',
    'gate.json',
)

_LIST_SELECTOR_KEYS = (
    'factor_id',
    'rule_id',
    'param_id',
    'event_type',
    'metric',
    'name',
    'id',
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _skill_pack_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / 'skill_packs'
    return Path(root_dir)


def _semver_triplet(version: str) -> tuple[int, int, int] | None:
    tokens = str(version or '').split('.')
    if len(tokens) != 3:
        return None
    try:
        return (int(tokens[0]), int(tokens[1]), int(tokens[2]))
    except ValueError:
        return None


def _resolve_base_version(skill_pack_id: str, base_version: str, *, root_dir: str | Path | None = None) -> str:
    normalized = str(base_version or '').strip()
    if normalized and normalized.lower() not in {'champion', 'auto'}:
        return normalized
    champion = resolve_champion_version(skill_pack_id, root_dir=root_dir)
    if champion:
        return champion
    versions = list_skill_pack_versions(skill_pack_id, root_dir=root_dir)
    if versions:
        return str(versions[0].get('version', '0.1.0'))
    return '0.1.0'


def _next_versions(
    *,
    skill_pack_id: str,
    base_version: str,
    count: int,
    root_dir: str | Path | None = None,
) -> list[str]:
    triplet = _semver_triplet(base_version)
    if triplet is None:
        raise ValueError(f'base_version must be semver x.y.z for auto-generation, got: {base_version}')
    major, minor, patch = triplet

    existing_versions = list_skill_pack_versions(skill_pack_id, root_dir=root_dir)
    existing_patch = patch
    for item in existing_versions:
        parsed = _semver_triplet(str(item.get('version', '')))
        if parsed is None:
            continue
        if parsed[0] == major and parsed[1] == minor:
            existing_patch = max(existing_patch, parsed[2])

    versions: list[str] = []
    next_patch = existing_patch + 1
    while len(versions) < count:
        versions.append(f'{major}.{minor}.{next_patch}')
        next_patch += 1
    return versions


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _find_selector_index(items: list[Any], selector: str) -> int:
    if selector.isdigit():
        idx = int(selector)
        if idx < 0 or idx >= len(items):
            raise ValueError(f'list selector index out of range: {selector}')
        return idx
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        for key in _LIST_SELECTOR_KEYS:
            if str(item.get(key, '')).strip() == selector:
                return idx
        for key, value in item.items():
            if key.endswith('_id') and str(value).strip() == selector:
                return idx
    raise ValueError(f'cannot resolve list selector: {selector}')


def _parse_path(path: str) -> tuple[str, list[tuple[str, str, str]]]:
    text = str(path or '').strip()
    if not text:
        raise ValueError('path is required')
    chunks: list[str] = []
    buffer: list[str] = []
    bracket_depth = 0
    for char in text:
        if char == '.' and bracket_depth == 0:
            token = ''.join(buffer).strip()
            if not token:
                raise ValueError(f'invalid empty token in path: {path}')
            chunks.append(token)
            buffer = []
            continue
        if char == '[':
            bracket_depth += 1
        elif char == ']':
            bracket_depth = max(0, bracket_depth - 1)
        buffer.append(char)
    tail = ''.join(buffer).strip()
    if tail:
        chunks.append(tail)
    if not chunks:
        raise ValueError(f'invalid path: {path}')
    root_key = chunks[0].strip()
    if not root_key:
        raise ValueError(f'invalid path: {path}')
    ops: list[tuple[str, str, str]] = []
    for chunk in chunks[1:]:
        token = chunk.strip()
        if not token:
            raise ValueError(f'invalid empty token in path: {path}')
        if token.endswith(']') and '[' in token:
            field, selector = token[:-1].split('[', 1)
            field = field.strip()
            selector = selector.strip()
            if not field or not selector:
                raise ValueError(f'invalid list selector in path: {path}')
            ops.append(('list', field, selector))
        else:
            ops.append(('field', token, ''))
    if not ops:
        raise ValueError(f'path must include nested field: {path}')
    return root_key, ops


def _set_by_ops(document: dict[str, Any], ops: list[tuple[str, str, str]], value: Any) -> None:
    current: Any = document
    for idx, op in enumerate(ops):
        kind, token, selector = op
        is_last = idx == len(ops) - 1
        if kind == 'field':
            if not isinstance(current, dict):
                raise ValueError(f'path traversal expected object before field: {token}')
            if is_last:
                current[token] = value
                return
            if token not in current:
                raise ValueError(f'path token not found: {token}')
            current = current[token]
            continue
        if not isinstance(current, dict):
            raise ValueError(f'path traversal expected object before list selector: {token}[{selector}]')
        items = current.get(token)
        if not isinstance(items, list):
            raise ValueError(f'path token is not list: {token}')
        selected_index = _find_selector_index(items, selector)
        if is_last:
            items[selected_index] = value
            return
        current = items[selected_index]


def _append_by_ops(document: dict[str, Any], ops: list[tuple[str, str, str]], value: Any) -> None:
    current: Any = document
    for idx, op in enumerate(ops):
        kind, token, selector = op
        is_last = idx == len(ops) - 1
        if kind == 'field':
            if not isinstance(current, dict):
                raise ValueError(f'path traversal expected object before field: {token}')
            if is_last:
                target = current.get(token)
                if not isinstance(target, list):
                    raise ValueError(f'append target is not list: {token}')
                target.append(value)
                return
            if token not in current:
                raise ValueError(f'path token not found: {token}')
            current = current[token]
            continue
        if not isinstance(current, dict):
            raise ValueError(f'path traversal expected object before list selector: {token}[{selector}]')
        items = current.get(token)
        if not isinstance(items, list):
            raise ValueError(f'path token is not list: {token}')
        selected_index = _find_selector_index(items, selector)
        if is_last:
            raise ValueError('append path must target a list field')
        current = items[selected_index]


def _apply_change(document: dict[str, Any], ops: list[tuple[str, str, str]], change: dict[str, Any]) -> None:
    op = str(change.get('op', 'set')).strip().lower() or 'set'
    if op == 'set':
        _set_by_ops(document, ops, change.get('to'))
        return
    if op == 'append':
        _append_by_ops(document, ops, change.get('to'))
        return
    raise ValueError(f'unsupported change op: {op}')


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=False) + '\n', encoding='utf-8')


def _build_modification_plan(
    *,
    calibration_profile: dict[str, Any],
    max_candidates: int,
    param_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    search_space = calibration_profile.get('search_space', [])
    if not isinstance(search_space, list):
        search_space = []

    requested_ids = {str(item).strip() for item in (param_ids or []) if str(item).strip()}
    plans: list[dict[str, Any]] = []
    for item in search_space:
        if not isinstance(item, dict):
            continue
        param_id = str(item.get('param_id', '')).strip()
        if not param_id:
            continue
        if requested_ids and param_id not in requested_ids:
            continue
        param_type = str(item.get('type', '')).strip().lower()
        if param_type not in {'float', 'int'}:
            continue
        current = _safe_float(item.get('current'))
        minimum = _safe_float(item.get('min'))
        maximum = _safe_float(item.get('max'))
        step = _safe_float(item.get('step'))
        if step <= 0 or maximum < minimum:
            continue
        candidates: list[tuple[str, float]] = []
        up = current + step
        down = current - step
        if up <= maximum:
            candidates.append(('up', up))
        if down >= minimum:
            candidates.append(('down', down))
        for direction, target in candidates:
            value: Any = int(round(target)) if param_type == 'int' else round(target, 8)
            plans.append(
                {
                    'param_id': param_id,
                    'path': str(item.get('path', '')).strip(),
                    'direction': direction,
                    'from': int(round(current)) if param_type == 'int' else round(current, 8),
                    'to': value,
                    'type': param_type,
                }
            )
            if len(plans) >= max_candidates:
                return plans
    return plans


def _normalize_text(value: Any) -> str:
    return str(value or '').strip()


def _collect_factor_endpoint_rows(factors_payload: dict[str, Any]) -> list[dict[str, Any]]:
    factors = factors_payload.get('factors')
    if not isinstance(factors, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in factors:
        if not isinstance(item, dict):
            continue
        factor_id = _normalize_text(item.get('factor_id'))
        if not factor_id:
            continue

        endpoints: set[str] = set()
        refs = item.get('source_refs')
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                endpoint = _normalize_text(ref.get('endpoint'))
                if endpoint:
                    endpoints.add(endpoint)

        rows.append(
            {
                'factor_id': factor_id,
                'weight': round(_safe_float(item.get('weight', 0.0), 0.0), 8),
                'endpoints': sorted(endpoints),
            }
        )
    return rows


def _build_data_combo_plans(
    *,
    base_pack: dict[str, Any],
    max_candidates: int,
    max_endpoint_toggles: int = 1,
    endpoint_allowlist: list[str] | None = None,
) -> list[dict[str, Any]]:
    factors_payload = base_pack.get('factors')
    if not isinstance(factors_payload, dict):
        return []

    rows = _collect_factor_endpoint_rows(factors_payload)
    if not rows:
        return []

    requested_endpoints = {
        _normalize_text(item) for item in (endpoint_allowlist or []) if _normalize_text(item)
    }

    endpoint_set: set[str] = set()
    for row in rows:
        for endpoint in row['endpoints']:
            if requested_endpoints and endpoint not in requested_endpoints:
                continue
            endpoint_set.add(endpoint)
    endpoints = sorted(endpoint_set)
    if not endpoints:
        return []

    normalized_toggles = max(1, min(3, _safe_int(max_endpoint_toggles, 1)))
    plans: list[dict[str, Any]] = []
    for toggle_count in range(1, normalized_toggles + 1):
        for combo in combinations(endpoints, toggle_count):
            disabled = set(combo)
            changes: list[dict[str, Any]] = []
            affected_factor_ids: list[str] = []

            for row in rows:
                factor_id = str(row.get('factor_id', ''))
                row_endpoints = set(row.get('endpoints') or [])
                current_weight = _safe_float(row.get('weight', 0.0), 0.0)
                if not factor_id or not row_endpoints or current_weight == 0.0:
                    continue
                if not row_endpoints.issubset(disabled):
                    continue
                changes.append(
                    {
                        'path': f'factors.factors[{factor_id}].weight',
                        'factor_id': factor_id,
                        'from': round(current_weight, 8),
                        'to': 0.0,
                    }
                )
                affected_factor_ids.append(factor_id)

            if not changes:
                continue

            plan_id = f'data_combo:disable:{"+".join(combo)}'
            plans.append(
                {
                    'plan_type': 'data_combo',
                    'plan_id': plan_id,
                    'description': f'disable endpoints {",".join(combo)} by setting factor weight to 0',
                    'mode': 'disable_endpoints',
                    'endpoints': list(combo),
                    'affected_factor_ids': sorted(affected_factor_ids),
                    'changes': changes,
                }
            )
            if len(plans) >= max_candidates:
                return plans
    return plans


def _build_candidate_plans(
    *,
    calibration_profile: dict[str, Any],
    base_pack: dict[str, Any],
    max_candidates: int,
    param_ids: list[str] | None = None,
    include_param_search: bool = True,
    enable_data_combo_search: bool = False,
    max_endpoint_toggles: int = 1,
    endpoint_allowlist: list[str] | None = None,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []

    if include_param_search:
        param_plans = _build_modification_plan(
            calibration_profile=calibration_profile,
            max_candidates=max_candidates,
            param_ids=param_ids,
        )
        for item in param_plans:
            plans.append(
                {
                    'plan_type': 'param',
                    'plan_id': f'param:{item["param_id"]}:{item["direction"]}',
                    'description': f'calibration perturbation for {item["param_id"]} ({item["direction"]})',
                    'param_id': item['param_id'],
                    'path': item['path'],
                    'direction': item['direction'],
                    'from': item['from'],
                    'to': item['to'],
                    'value_type': item['type'],
                    'changes': [
                        {
                            'path': item['path'],
                            'from': item['from'],
                            'to': item['to'],
                        }
                    ],
                }
            )
            if len(plans) >= max_candidates:
                return plans

    if enable_data_combo_search and len(plans) < max_candidates:
        remaining = max_candidates - len(plans)
        plans.extend(
            _build_data_combo_plans(
                base_pack=base_pack,
                max_candidates=remaining,
                max_endpoint_toggles=max_endpoint_toggles,
                endpoint_allowlist=endpoint_allowlist,
            )
        )

    return plans[:max_candidates]


def _pack_documents(base_pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    required = ('manifest', 'factors', 'formula', 'policy', 'risk', 'llm_mapping', 'gate')
    documents: dict[str, dict[str, Any]] = {}
    for key in required:
        payload = base_pack.get(key)
        if not isinstance(payload, dict):
            raise ValueError(f'base skill pack missing section: {key}')
        documents[key] = copy.deepcopy(payload)
    return documents


def _materialize_candidate_versions(
    *,
    skill_pack_id: str,
    base_version: str,
    base_pack: dict[str, Any],
    plans: list[dict[str, Any]],
    author: str,
    dry_run: bool,
    root_dir: str | Path | None = None,
    default_job_namespace: str = 'calibration',
    default_job_profile_id: str = '',
) -> list[dict[str, Any]]:
    planned_versions = _next_versions(
        skill_pack_id=skill_pack_id,
        base_version=base_version,
        count=len(plans),
        root_dir=root_dir,
    )
    target_root = _skill_pack_root(root_dir) / skill_pack_id

    generated_items: list[dict[str, Any]] = []
    for idx, plan in enumerate(plans):
        version = planned_versions[idx]
        documents = _pack_documents(base_pack)
        changes = plan.get('changes')
        if not isinstance(changes, list) or not changes:
            raise ValueError(f'candidate plan has no changes: {plan.get("plan_id", "")}')
        for change in changes:
            if not isinstance(change, dict):
                raise ValueError('candidate plan change must be object')
            path = str(change.get('path', '')).strip()
            if not path:
                raise ValueError(f'candidate plan change path is required: {plan.get("plan_id", "")}')
            doc_key, ops = _parse_path(path)
            if doc_key not in documents:
                raise ValueError(f'unsupported path root "{doc_key}" in candidate plan {plan.get("plan_id", "")}')
            _apply_change(documents[doc_key], ops, change)

        manifest = documents['manifest']
        manifest['skill_pack_id'] = skill_pack_id
        manifest['version'] = version
        manifest['status'] = 'candidate'
        manifest['author'] = author
        manifest['updated_at'] = _now_iso()
        manifest['created_at'] = str(manifest.get('created_at') or _now_iso())
        manifest['derived_from_champion_version'] = base_version
        plan_type = str(plan.get('plan_type', 'param')).strip() or 'param'
        plan_id = str(plan.get('plan_id', '')).strip() or f'{plan_type}:{idx + 1}'
        explicit_job_origin = str(plan.get('job_origin', '')).strip()
        if explicit_job_origin:
            manifest['derived_from_job_id'] = explicit_job_origin
        elif plan_type == 'param':
            manifest['derived_from_job_id'] = (
                f'calibration:{default_job_profile_id}:{plan.get("param_id", "")}:{plan.get("direction", "")}'
            )
        else:
            namespace = str(default_job_namespace or 'calibration').strip() or 'calibration'
            manifest['derived_from_job_id'] = f'{namespace}:{default_job_profile_id}:{plan_id}'
        manifest['candidate_plan'] = {
            'plan_type': plan_type,
            'plan_id': plan_id,
            'description': str(plan.get('description', '')).strip(),
            'change_count': len(changes),
            'endpoints': [str(item).strip() for item in (plan.get('endpoints') or []) if str(item).strip()],
            'affected_factor_ids': [
                str(item).strip() for item in (plan.get('affected_factor_ids') or []) if str(item).strip()
            ],
        }

        item = {
            'version': version,
            'status': manifest['status'],
            'plan_type': plan_type,
            'plan_id': plan_id,
            'change_count': len(changes),
        }
        if plan_type == 'param':
            item.update(
                {
                    'param_id': str(plan.get('param_id', '')).strip(),
                    'path': str(plan.get('path', '')).strip(),
                    'direction': str(plan.get('direction', '')).strip(),
                    'from': plan.get('from'),
                    'to': plan.get('to'),
                }
            )
        else:
            item.update(
                {
                    'mode': str(plan.get('mode', '')).strip(),
                    'endpoints': [str(item).strip() for item in (plan.get('endpoints') or []) if str(item).strip()],
                    'affected_factor_ids': [
                        str(item).strip() for item in (plan.get('affected_factor_ids') or []) if str(item).strip()
                    ],
                }
            )
        generated_items.append(item)

        if dry_run:
            continue

        version_dir = target_root / version
        version_dir.mkdir(parents=True, exist_ok=False)
        file_payloads = {
            'manifest.json': documents['manifest'],
            'factors.json': documents['factors'],
            'formula.json': documents['formula'],
            'policy.json': documents['policy'],
            'risk.json': documents['risk'],
            'llm_mapping.json': documents['llm_mapping'],
            'gate.json': documents['gate'],
        }
        for filename in _SKILL_PACK_FILES:
            _write_json(version_dir / filename, file_payloads[filename])

    if not dry_run:
        clear_skill_pack_cache()
    return generated_items


def generate_skill_pack_candidates_from_plans(
    *,
    skill_pack_id: str = 'cn_a_core',
    base_version: str = 'champion',
    plans: list[dict[str, Any]] | None = None,
    author: str = 'auto_calibration',
    dry_run: bool = False,
    root_dir: str | Path | None = None,
    default_job_namespace: str = 'calibration',
    default_job_profile_id: str = '',
) -> dict[str, Any]:
    normalized_skill_pack_id = str(skill_pack_id or '').strip()
    if not normalized_skill_pack_id:
        raise ValueError('skill_pack_id is required')
    normalized_author = str(author or '').strip() or 'auto_calibration'
    resolved_base_version = _resolve_base_version(normalized_skill_pack_id, str(base_version or ''), root_dir=root_dir)
    normalized_plans = [item for item in (plans or []) if isinstance(item, dict)]
    if not normalized_plans:
        raise ValueError('plans must contain at least one plan')

    base_pack = load_skill_pack(
        skill_pack_id=normalized_skill_pack_id,
        version=resolved_base_version,
        root_dir=root_dir,
    )
    generated_items = _materialize_candidate_versions(
        skill_pack_id=normalized_skill_pack_id,
        base_version=resolved_base_version,
        base_pack=base_pack,
        plans=normalized_plans,
        author=normalized_author,
        dry_run=bool(dry_run),
        root_dir=root_dir,
        default_job_namespace=default_job_namespace,
        default_job_profile_id=default_job_profile_id,
    )
    return {
        'skill_pack_id': normalized_skill_pack_id,
        'base_version': resolved_base_version,
        'generated_count': len(generated_items),
        'items': generated_items,
        'dry_run': bool(dry_run),
    }


def generate_skill_pack_candidates(
    *,
    skill_pack_id: str = 'cn_a_core',
    base_version: str = 'champion',
    calibration_version: str | None = None,
    max_candidates: int = 4,
    author: str = 'auto_calibration',
    param_ids: list[str] | None = None,
    include_param_search: bool = True,
    enable_data_combo_search: bool = False,
    max_endpoint_toggles: int = 1,
    endpoint_allowlist: list[str] | None = None,
    dry_run: bool = False,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_skill_pack_id = str(skill_pack_id or '').strip()
    if not normalized_skill_pack_id:
        raise ValueError('skill_pack_id is required')
    normalized_max = max(1, min(32, _safe_int(max_candidates, 4)))
    normalized_author = str(author or '').strip() or 'auto_calibration'
    normalized_toggles = max(1, min(3, _safe_int(max_endpoint_toggles, 1)))
    normalized_allowlist = [str(item).strip() for item in (endpoint_allowlist or []) if str(item).strip()]

    resolved_base_version = _resolve_base_version(normalized_skill_pack_id, str(base_version or ''), root_dir=root_dir)
    profile_version = str(calibration_version or resolved_base_version).strip()

    try:
        calibration_profile = load_calibration_profile(
            skill_pack_id=normalized_skill_pack_id,
            version=profile_version,
            root_dir=root_dir,
        )
    except ValueError:
        if calibration_version is not None:
            raise
        fallback_profile: dict[str, Any] | None = None
        fallback_version = ''
        for item in list_skill_pack_versions(normalized_skill_pack_id, root_dir=root_dir):
            candidate_version = str(item.get('version', '')).strip()
            if not candidate_version:
                continue
            try:
                fallback_profile = load_calibration_profile(
                    skill_pack_id=normalized_skill_pack_id,
                    version=candidate_version,
                    root_dir=root_dir,
                )
                fallback_version = candidate_version
                break
            except ValueError:
                continue
        if fallback_profile is None:
            raise
        calibration_profile = fallback_profile
        profile_version = fallback_version
    profile_id = str(calibration_profile.get('profile_id', '')).strip()
    base_pack = load_skill_pack(
        skill_pack_id=normalized_skill_pack_id,
        version=resolved_base_version,
        root_dir=root_dir,
    )
    plans = _build_candidate_plans(
        calibration_profile=calibration_profile,
        base_pack=base_pack,
        max_candidates=normalized_max,
        param_ids=param_ids,
        include_param_search=bool(include_param_search),
        enable_data_combo_search=bool(enable_data_combo_search),
        max_endpoint_toggles=normalized_toggles,
        endpoint_allowlist=normalized_allowlist,
    )
    if not plans:
        return {
            'skill_pack_id': normalized_skill_pack_id,
            'base_version': resolved_base_version,
            'calibration_version': profile_version,
            'profile_id': profile_id,
            'generated_count': 0,
            'items': [],
            'dry_run': bool(dry_run),
            'reason': 'no eligible candidate modifications found (param search or data combo search)',
        }

    generated_items = _materialize_candidate_versions(
        skill_pack_id=normalized_skill_pack_id,
        base_version=resolved_base_version,
        base_pack=base_pack,
        plans=plans,
        author=normalized_author,
        dry_run=bool(dry_run),
        root_dir=root_dir,
        default_job_namespace='calibration',
        default_job_profile_id=profile_id,
    )

    return {
        'skill_pack_id': normalized_skill_pack_id,
        'base_version': resolved_base_version,
        'calibration_version': profile_version,
        'profile_id': profile_id,
        'generated_count': len(generated_items),
        'items': generated_items,
        'include_param_search': bool(include_param_search),
        'enable_data_combo_search': bool(enable_data_combo_search),
        'max_endpoint_toggles': normalized_toggles,
        'endpoint_allowlist': normalized_allowlist,
        'dry_run': bool(dry_run),
    }
