from __future__ import annotations

from functools import lru_cache
import copy
import json
from pathlib import Path
from typing import Any


_REQUIRED_FILES = (
    'manifest.json',
    'factors.json',
    'formula.json',
    'policy.json',
    'risk.json',
    'llm_mapping.json',
    'gate.json',
)

_ALLOWED_STATUSES = {'draft', 'candidate', 'champion', 'archived'}
_ALLOWED_ACTIONS = {'BUY', 'ADD', 'HOLD', 'REDUCE', 'SELL', 'AVOID'}
_COMPONENT_FILES = tuple(name for name in _REQUIRED_FILES if name != 'manifest.json')


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _skill_pack_root(root_dir: str | Path | None = None) -> Path:
    if root_dir is None:
        return _repo_root() / 'skill_packs'
    return Path(root_dir)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ValueError(f'skill pack file missing: {path}') from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f'invalid json in {path}: {exc}') from exc
    if not isinstance(payload, dict):
        raise ValueError(f'skill pack file must be object json: {path}')
    return payload


def _require_text(payload: dict[str, Any], key: str, *, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}.{key} must be non-empty string')
    return value.strip()


def _require_list(payload: dict[str, Any], key: str, *, label: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f'{label}.{key} must be list')
    return value


def _validate_manifest(manifest: dict[str, Any], *, skill_pack_id: str, version: str) -> None:
    pack = _require_text(manifest, 'skill_pack_id', label='manifest')
    ver = _require_text(manifest, 'version', label='manifest')
    status = _require_text(manifest, 'status', label='manifest').lower()
    _require_text(manifest, 'market', label='manifest')
    _require_text(manifest, 'author', label='manifest')
    if pack != skill_pack_id:
        raise ValueError(f'manifest.skill_pack_id={pack} does not match requested {skill_pack_id}')
    if ver != version:
        raise ValueError(f'manifest.version={ver} does not match requested {version}')
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f'manifest.status must be one of {sorted(_ALLOWED_STATUSES)}')
    inputs = manifest.get('inputs')
    if not isinstance(inputs, dict):
        raise ValueError('manifest.inputs must be object')
    for name in _REQUIRED_FILES:
        expected = name
        actual = inputs.get(name.replace('.json', '_file'))
        if actual != expected:
            raise ValueError(f'manifest.inputs.{name.replace(".json", "_file")} must be "{expected}"')


def _validate_component_versions(files: dict[str, dict[str, Any]], *, expected_version: str) -> None:
    for filename in _COMPONENT_FILES:
        payload = files.get(filename)
        if not isinstance(payload, dict):
            raise ValueError(f'{filename} payload is invalid')
        label = filename.replace('.json', '')
        component_version = _require_text(payload, 'version', label=label)
        if component_version != expected_version:
            raise ValueError(
                f'{filename}.version={component_version} does not match manifest.version={expected_version}'
            )


def _validate_factors(factors_payload: dict[str, Any]) -> tuple[int, int, int]:
    factors = _require_list(factors_payload, 'factors', label='factors')
    if not factors:
        raise ValueError('factors.factors must contain at least one factor')
    seen: set[str] = set()
    enabled_count = 0
    zero_weight_count = 0
    for idx, item in enumerate(factors, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'factors.factors[{idx}] must be object')
        factor_id = _require_text(item, 'factor_id', label=f'factors.factors[{idx}]')
        if factor_id in seen:
            raise ValueError(f'duplicate factor_id: {factor_id}')
        seen.add(factor_id)
        enabled = bool(item.get('enabled', True))
        if enabled:
            enabled_count += 1
        try:
            weight = float(item.get('weight', 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f'factors.factors[{idx}].weight must be number') from exc
        if weight == 0:
            zero_weight_count += 1
    return len(factors), enabled_count, zero_weight_count


def _validate_formula(formula_payload: dict[str, Any]) -> None:
    score_formula = formula_payload.get('score_formula')
    confidence_formula = formula_payload.get('confidence_formula')
    if not isinstance(score_formula, dict):
        raise ValueError('formula.score_formula must be object')
    if not isinstance(confidence_formula, dict):
        raise ValueError('formula.confidence_formula must be object')
    score_clamp = score_formula.get('clamp')
    confidence_clamp = confidence_formula.get('clamp')
    if score_clamp != [0, 100]:
        raise ValueError('formula.score_formula.clamp must be [0, 100]')
    if confidence_clamp != [0, 1]:
        raise ValueError('formula.confidence_formula.clamp must be [0, 1]')


def _validate_policy(policy_payload: dict[str, Any]) -> None:
    actions = _require_list(policy_payload, 'actions', label='policy')
    action_set = {str(item).strip().upper() for item in actions if str(item).strip()}
    if not _ALLOWED_ACTIONS.issubset(action_set):
        raise ValueError(f'policy.actions must contain all {_ALLOWED_ACTIONS}')
    thresholds = policy_payload.get('thresholds')
    if not isinstance(thresholds, dict):
        raise ValueError('policy.thresholds must be object')
    for key in (
        'buy_score_min',
        'buy_confidence_min',
        'add_gap_min',
        'hold_gap_max',
        'reduce_gap_min',
        'sell_score_max',
    ):
        if key not in thresholds:
            raise ValueError(f'policy.thresholds.{key} is required')
    rules = _require_list(policy_payload, 'rules', label='policy')
    if not rules:
        raise ValueError('policy.rules must contain at least one rule')


def _validate_risk(risk_payload: dict[str, Any]) -> None:
    constraints = risk_payload.get('constraints')
    if not isinstance(constraints, dict):
        raise ValueError('risk.constraints must be object')
    for key in ('max_single_position', 'max_drawdown_pct'):
        if key not in constraints:
            raise ValueError(f'risk.constraints.{key} is required')


def _validate_llm_mapping(llm_payload: dict[str, Any], *, factor_ids: set[str]) -> int:
    mappings = _require_list(llm_payload, 'mappings', label='llm_mapping')
    if not mappings:
        raise ValueError('llm_mapping.mappings must contain at least one mapping')
    for idx, item in enumerate(mappings, start=1):
        if not isinstance(item, dict):
            raise ValueError(f'llm_mapping.mappings[{idx}] must be object')
        target_factor = _require_text(item, 'target_factor', label=f'llm_mapping.mappings[{idx}]')
        if target_factor not in factor_ids:
            raise ValueError(f'llm_mapping target_factor not found in factors: {target_factor}')
    return len(mappings)


def _validate_gate(gate_payload: dict[str, Any]) -> None:
    metrics = _require_list(gate_payload, 'required_metrics', label='gate')
    if not metrics:
        raise ValueError('gate.required_metrics must contain at least one metric')
    promotion = gate_payload.get('promotion_rule')
    if not isinstance(promotion, dict):
        raise ValueError('gate.promotion_rule must be object')
    checks = _require_list(promotion, 'all_of', label='gate.promotion_rule')
    if not checks:
        raise ValueError('gate.promotion_rule.all_of must contain at least one condition')


def _load_skill_pack_cached(root_dir: str, skill_pack_id: str, version: str) -> dict[str, Any]:
    pack_dir = Path(root_dir) / skill_pack_id / version
    if not pack_dir.exists():
        raise ValueError(f'skill pack directory not found: {pack_dir}')
    if not pack_dir.is_dir():
        raise ValueError(f'skill pack path is not directory: {pack_dir}')

    files: dict[str, dict[str, Any]] = {}
    for filename in _REQUIRED_FILES:
        files[filename] = _read_json(pack_dir / filename)

    manifest = files['manifest.json']
    factors_payload = files['factors.json']
    formula_payload = files['formula.json']
    policy_payload = files['policy.json']
    risk_payload = files['risk.json']
    llm_payload = files['llm_mapping.json']
    gate_payload = files['gate.json']

    _validate_manifest(manifest, skill_pack_id=skill_pack_id, version=version)
    _validate_component_versions(files, expected_version=manifest['version'])
    factor_count, enabled_factor_count, zero_weight_factor_count = _validate_factors(factors_payload)
    _validate_formula(formula_payload)
    _validate_policy(policy_payload)
    _validate_risk(risk_payload)
    factor_ids = {
        str(item.get('factor_id', '')).strip()
        for item in factors_payload.get('factors', [])
        if isinstance(item, dict) and str(item.get('factor_id', '')).strip()
    }
    llm_mapping_count = _validate_llm_mapping(llm_payload, factor_ids=factor_ids)
    _validate_gate(gate_payload)

    summary = {
        'skill_pack_id': manifest['skill_pack_id'],
        'version': manifest['version'],
        'market': manifest['market'],
        'status': manifest['status'],
        'path': str(pack_dir),
        'factor_count': factor_count,
        'enabled_factor_count': enabled_factor_count,
        'zero_weight_factor_count': zero_weight_factor_count,
        'llm_mapping_count': llm_mapping_count,
    }

    return {
        'manifest': manifest,
        'factors': factors_payload,
        'formula': formula_payload,
        'policy': policy_payload,
        'risk': risk_payload,
        'llm_mapping': llm_payload,
        'gate': gate_payload,
        'summary': summary,
    }


@lru_cache(maxsize=32)
def _cached(root_dir: str, skill_pack_id: str, version: str) -> dict[str, Any]:
    return _load_skill_pack_cached(root_dir, skill_pack_id, version)


def load_skill_pack(
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
    root = _skill_pack_root(root_dir).resolve()
    payload = _cached(str(root), normalized_skill_pack_id, normalized_version)
    return copy.deepcopy(payload)


def clear_skill_pack_cache() -> None:
    _cached.cache_clear()
