from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


_ALLOWED_PARAM_TYPES = {'float', 'int', 'bool', 'enum'}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _skill_pack_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / 'skill_packs'
    return Path(root_dir)


def _calibration_path(skill_pack_id: str, version: str, *, root_dir: str | Path | None = None) -> Path:
    return _skill_pack_root(root_dir) / skill_pack_id / version / 'calibration.json'


def _require_text(payload: dict[str, Any], key: str, *, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}.{key} must be non-empty string')
    return value.strip()


def _require_number(payload: dict[str, Any], key: str, *, label: str) -> float:
    value = payload.get(key)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label}.{key} must be number') from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ValueError(f'calibration profile not found: {path}') from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f'invalid calibration json: {path}: {exc}') from exc
    if not isinstance(payload, dict):
        raise ValueError(f'calibration profile must be object json: {path}')
    return payload


def _validate_param(item: dict[str, Any], *, idx: int) -> str:
    label = f'search_space[{idx}]'
    _require_text(item, 'param_id', label=label)
    _require_text(item, 'path', label=label)
    param_type = _require_text(item, 'type', label=label).lower()
    if param_type not in _ALLOWED_PARAM_TYPES:
        raise ValueError(f'{label}.type must be one of {sorted(_ALLOWED_PARAM_TYPES)}')
    if param_type in {'float', 'int'}:
        current = _require_number(item, 'current', label=label)
        minimum = _require_number(item, 'min', label=label)
        maximum = _require_number(item, 'max', label=label)
        step = _require_number(item, 'step', label=label)
        if maximum < minimum:
            raise ValueError(f'{label}.max must be >= min')
        if step <= 0:
            raise ValueError(f'{label}.step must be > 0')
        if current < minimum or current > maximum:
            raise ValueError(f'{label}.current must be within [min, max]')
    if param_type == 'enum':
        candidates = item.get('candidates')
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f'{label}.candidates must be non-empty list for enum')
    return param_type


def summarize_calibration_profile(profile: dict[str, Any]) -> dict[str, Any]:
    search_space = profile.get('search_space')
    if not isinstance(search_space, list):
        search_space = []
    numeric_params = 0
    for item in search_space:
        if not isinstance(item, dict):
            continue
        if str(item.get('type', '')).lower() in {'float', 'int'}:
            numeric_params += 1
    return {
        'profile_id': str(profile.get('profile_id', '')),
        'skill_pack_id': str(profile.get('skill_pack_id', '')),
        'skill_pack_version': str(profile.get('skill_pack_version', '')),
        'search_space_count': len(search_space),
        'numeric_param_count': numeric_params,
        'frozen_count': len(profile.get('frozen', [])) if isinstance(profile.get('frozen', []), list) else 0,
        'primary_metric': str((profile.get('objective') or {}).get('primary', '')),
    }


def load_calibration_profile(
    skill_pack_id: str = 'cn_a_core',
    version: str = '0.1.0',
    *,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_skill_pack_id = str(skill_pack_id or '').strip()
    normalized_version = str(version or '').strip()
    if not normalized_skill_pack_id:
        raise ValueError('skill_pack_id is required')
    if not normalized_version:
        raise ValueError('version is required')

    payload = _read_json(_calibration_path(normalized_skill_pack_id, normalized_version, root_dir=root_dir))
    profile_id = _require_text(payload, 'profile_id', label='calibration')
    profile_skill_pack_id = _require_text(payload, 'skill_pack_id', label='calibration')
    profile_version = _require_text(payload, 'skill_pack_version', label='calibration')
    if profile_skill_pack_id != normalized_skill_pack_id:
        raise ValueError(
            f'calibration.skill_pack_id={profile_skill_pack_id} does not match requested {normalized_skill_pack_id}'
        )
    if profile_version != normalized_version:
        raise ValueError(f'calibration.skill_pack_version={profile_version} does not match requested {normalized_version}')

    objective = payload.get('objective')
    if not isinstance(objective, dict):
        raise ValueError('calibration.objective must be object')
    _require_text(objective, 'primary', label='calibration.objective')

    search_space = payload.get('search_space')
    if not isinstance(search_space, list) or not search_space:
        raise ValueError('calibration.search_space must be non-empty list')
    for idx, item in enumerate(search_space, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'calibration.search_space[{idx}] must be object')
        _validate_param(item, idx=idx)

    execution = payload.get('execution')
    if not isinstance(execution, dict):
        raise ValueError('calibration.execution must be object')

    normalized = copy.deepcopy(payload)
    normalized['_summary'] = summarize_calibration_profile(normalized)
    normalized['_summary']['profile_id'] = profile_id
    return normalized
