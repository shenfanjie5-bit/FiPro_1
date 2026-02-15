from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Final


DEFAULT_OPENAI_BASE_URL: Final[str] = 'https://api.openai.com/v1'
DEFAULT_RUN_MODE: Final[str] = 'LIVE'
DEFAULT_MOCK_PRIMARY_MODEL: Final[str] = 'mock-primary-v1'
DEFAULT_MOCK_SHADOW_MODEL: Final[str] = 'mock-challenger-v1'
DEFAULT_OPENAI_MODEL: Final[str] = 'gpt-4o-mini'
RUN_MODES: Final[set[str]] = {'LIVE', 'SHADOW', 'BACKTEST'}
SUPPORTED_LLM_PROVIDERS: Final[set[str]] = {'mock', 'openai', 'openai_compatible'}

_LOCK = Lock()
_OVERRIDES: dict[str, str] = {}
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE_PATH = _PROJECT_ROOT / '.env'


def _clean_text(value: Any) -> str:
    return str(value or '').strip()


@lru_cache(maxsize=1)
def _read_dotenv_values() -> dict[str, str]:
    try:
        lines = _ENV_FILE_PATH.read_text(encoding='utf-8').splitlines()
    except OSError:
        return {}

    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, raw_value = line.split('=', 1)
        key = key.strip()
        if not key:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _env_get(key: str, default: str = '') -> str:
    from_env = os.getenv(key)
    if from_env is not None:
        return from_env
    from_dotenv = _read_dotenv_values().get(key)
    if from_dotenv is not None:
        return from_dotenv
    return default


def _normalize_profile_id(value: Any, *, fallback: str = 'mock') -> str:
    raw = _clean_text(value).lower()
    normalized = re.sub(r'[^a-z0-9_]+', '_', raw).strip('_')
    return normalized or fallback


def _normalize_run_mode(value: Any, *, fallback: str = DEFAULT_RUN_MODE) -> str:
    candidate = _clean_text(value).upper() or fallback
    return candidate if candidate in RUN_MODES else fallback


def _normalize_provider(value: Any, *, fallback: str = 'mock') -> str:
    candidate = _clean_text(value).lower() or fallback
    return candidate if candidate in SUPPORTED_LLM_PROVIDERS else fallback


def _normalize_base_url(value: Any, *, fallback: str = DEFAULT_OPENAI_BASE_URL) -> str:
    candidate = _clean_text(value).rstrip('/')
    return candidate or fallback


def _normalize_model(value: Any, *, fallback: str) -> str:
    candidate = _clean_text(value)
    return candidate or fallback


def _mask_secret(secret: str) -> str:
    if not secret:
        return ''
    if len(secret) <= 8:
        return '*' * len(secret)
    return f'{secret[:4]}{"*" * (len(secret) - 8)}{secret[-4:]}'


def _collect_available_models(config: dict[str, str]) -> list[str]:
    ordered_keys = (
        'llm_primary_model',
        'llm_shadow_model',
        'llm_reviewer_model',
        'llm_shadow_reviewer_model',
    )
    values: list[str] = []
    for key in ordered_keys:
        candidate = _clean_text(config.get(key, ''))
        if not candidate or candidate.upper() == 'NONE':
            continue
        if candidate not in values:
            values.append(candidate)
        if len(values) >= 3:
            break
    return values


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _clean_text(_env_get(key, '1' if default else '0')).lower()
    return raw in {'1', 'true', 'yes', 'on'}


def _allow_runtime_connection_override() -> bool:
    return _env_bool('LLM_ALLOW_RUNTIME_CONNECTION_OVERRIDE', default=False)


def _discover_profile_ids_from_env() -> list[str]:
    from_list = _clean_text(_env_get('LLM_PROFILES', ''))
    if from_list:
        seen: list[str] = []
        for item in from_list.split(','):
            normalized = _normalize_profile_id(item, fallback='')
            if normalized and normalized not in seen:
                seen.append(normalized)
        return seen

    discovered: list[str] = []
    pattern = re.compile(r'^LLM_PROFILE_([A-Z0-9_]+)_PROVIDER$')
    all_keys = set(os.environ.keys()) | set(_read_dotenv_values().keys())
    for key in all_keys:
        matched = pattern.match(key)
        if matched is None:
            continue
        normalized = _normalize_profile_id(matched.group(1), fallback='')
        if normalized and normalized not in discovered:
            discovered.append(normalized)
    discovered.sort()
    return discovered


def _profile_model_defaults(profile_id: str, provider: str) -> tuple[str, str]:
    if provider == 'mock':
        return DEFAULT_MOCK_PRIMARY_MODEL, DEFAULT_MOCK_SHADOW_MODEL
    if profile_id == 'openclaw':
        return 'openclaw:main', 'openclaw:main'
    return DEFAULT_OPENAI_MODEL, DEFAULT_OPENAI_MODEL


def _build_profile(
    *,
    profile_id: str,
    label: str,
    provider: str,
    base_url: str,
    api_key: str,
    primary_model: str,
    reviewer_model: str,
    shadow_model: str,
    shadow_reviewer_model: str,
) -> dict[str, str]:
    normalized_id = _normalize_profile_id(profile_id)
    normalized_provider = _normalize_provider(provider, fallback='mock')
    default_primary, default_shadow = _profile_model_defaults(normalized_id, normalized_provider)
    return {
        'id': normalized_id,
        'label': _clean_text(label) or normalized_id,
        'llm_provider': normalized_provider,
        'llm_base_url': _normalize_base_url(base_url, fallback=DEFAULT_OPENAI_BASE_URL),
        'llm_api_key': _clean_text(api_key),
        'llm_primary_model': _normalize_model(primary_model, fallback=default_primary),
        'llm_reviewer_model': _normalize_model(reviewer_model, fallback='NONE'),
        'llm_shadow_model': _normalize_model(shadow_model, fallback=default_shadow),
        'llm_shadow_reviewer_model': _normalize_model(shadow_reviewer_model, fallback='NONE'),
    }


def _profile_from_env(profile_id: str) -> dict[str, str]:
    env_id = profile_id.upper()
    return _build_profile(
        profile_id=profile_id,
        label=_env_get(f'LLM_PROFILE_{env_id}_LABEL', profile_id),
        provider=_env_get(f'LLM_PROFILE_{env_id}_PROVIDER', 'mock' if profile_id == 'mock' else 'openai_compatible'),
        base_url=_env_get(f'LLM_PROFILE_{env_id}_BASE_URL', DEFAULT_OPENAI_BASE_URL),
        api_key=_env_get(f'LLM_PROFILE_{env_id}_API_KEY', ''),
        primary_model=_env_get(f'LLM_PROFILE_{env_id}_PRIMARY_MODEL', ''),
        reviewer_model=_env_get(f'LLM_PROFILE_{env_id}_REVIEWER_MODEL', 'NONE'),
        shadow_model=_env_get(f'LLM_PROFILE_{env_id}_SHADOW_MODEL', ''),
        shadow_reviewer_model=_env_get(f'LLM_PROFILE_{env_id}_SHADOW_REVIEWER_MODEL', 'NONE'),
    )


def _legacy_env_profile() -> dict[str, str]:
    provider = _normalize_provider(_env_get('LLM_PROVIDER', 'mock'))
    default_profile_id = _normalize_profile_id(_env_get('LLM_PROFILE_ID', provider or 'default'), fallback='default')
    return _build_profile(
        profile_id=default_profile_id,
        label=_env_get('LLM_PROFILE_LABEL', default_profile_id),
        provider=provider,
        base_url=_env_get('LLM_BASE_URL', DEFAULT_OPENAI_BASE_URL),
        api_key=_env_get('LLM_API_KEY', ''),
        primary_model=_env_get('LLM_PRIMARY_MODEL', DEFAULT_MOCK_PRIMARY_MODEL),
        reviewer_model=_env_get('LLM_REVIEWER_MODEL', 'NONE'),
        shadow_model=_env_get('LLM_SHADOW_MODEL', DEFAULT_MOCK_SHADOW_MODEL),
        shadow_reviewer_model=_env_get('LLM_SHADOW_REVIEWER_MODEL', 'NONE'),
    )


def _env_profiles() -> list[dict[str, str]]:
    if _legacy_env_override_enabled():
        return [_legacy_env_profile()]
    profile_ids = _discover_profile_ids_from_env()
    if not profile_ids:
        return [_legacy_env_profile()]
    return [_profile_from_env(profile_id) for profile_id in profile_ids]


def _legacy_env_override_enabled() -> bool:
    if os.getenv('LLM_PROFILES') is not None:
        return False
    legacy_keys = (
        'LLM_PROVIDER',
        'LLM_BASE_URL',
        'LLM_API_KEY',
        'LLM_PRIMARY_MODEL',
        'LLM_REVIEWER_MODEL',
        'LLM_SHADOW_MODEL',
        'LLM_SHADOW_REVIEWER_MODEL',
    )
    for key in legacy_keys:
        if os.getenv(key) is not None:
            return True
    return False


def _resolve_active_profile_id(profiles: list[dict[str, str]], preferred: Any) -> str:
    if not profiles:
        return 'default'
    normalized = _normalize_profile_id(preferred, fallback=profiles[0]['id'])
    profile_ids = {item['id'] for item in profiles}
    if normalized in profile_ids:
        return normalized
    return profiles[0]['id']


def _select_profile(profiles: list[dict[str, str]], profile_id: str) -> dict[str, str]:
    for profile in profiles:
        if profile['id'] == profile_id:
            return profile
    return profiles[0]


def _normalize_runtime(config: dict[str, str]) -> dict[str, str]:
    return {
        'default_run_mode': _normalize_run_mode(config.get('default_run_mode', DEFAULT_RUN_MODE)),
        'llm_profile_id': _normalize_profile_id(config.get('llm_profile_id', 'mock')),
        'llm_provider': _normalize_provider(config.get('llm_provider', 'mock')),
        'llm_base_url': _normalize_base_url(config.get('llm_base_url', DEFAULT_OPENAI_BASE_URL)),
        'llm_api_key': _clean_text(config.get('llm_api_key', '')),
        'llm_primary_model': _normalize_model(config.get('llm_primary_model', DEFAULT_MOCK_PRIMARY_MODEL), fallback=DEFAULT_MOCK_PRIMARY_MODEL),
        'llm_reviewer_model': _normalize_model(config.get('llm_reviewer_model', 'NONE'), fallback='NONE'),
        'llm_shadow_model': _normalize_model(config.get('llm_shadow_model', DEFAULT_MOCK_SHADOW_MODEL), fallback=DEFAULT_MOCK_SHADOW_MODEL),
        'llm_shadow_reviewer_model': _normalize_model(config.get('llm_shadow_reviewer_model', 'NONE'), fallback='NONE'),
    }


def get_runtime_config() -> dict[str, str]:
    profiles = _env_profiles()
    with _LOCK:
        overrides = dict(_OVERRIDES)
    default_run_mode = _normalize_run_mode(overrides.get('default_run_mode', _env_get('APP_DEFAULT_RUN_MODE', DEFAULT_RUN_MODE)))
    preferred_profile_id = overrides.get('llm_profile_id', _env_get('LLM_PROFILE_ID', ''))
    active_profile_id = _resolve_active_profile_id(profiles, preferred_profile_id)
    active_profile = dict(_select_profile(profiles, active_profile_id))
    merged = {
        'default_run_mode': default_run_mode,
        'llm_profile_id': active_profile_id,
        **active_profile,
    }
    for key in (
        'llm_provider',
        'llm_base_url',
        'llm_api_key',
        'llm_primary_model',
        'llm_reviewer_model',
        'llm_shadow_model',
        'llm_shadow_reviewer_model',
    ):
        if key in overrides:
            merged[key] = _clean_text(overrides.get(key, ''))
    return _normalize_runtime(merged)


def get_runtime_config_public() -> dict[str, Any]:
    config = get_runtime_config()
    api_key = config.get('llm_api_key', '')
    available_models = _collect_available_models(config)
    profiles_public = []
    for profile in _env_profiles():
        profile_models = _collect_available_models(profile)
        profile_api_key = _clean_text(profile.get('llm_api_key', ''))
        profiles_public.append(
            {
                'id': profile['id'],
                'label': profile.get('label', profile['id']),
                'llm_provider': profile['llm_provider'],
                'llm_primary_model': profile['llm_primary_model'],
                'llm_shadow_model': profile['llm_shadow_model'],
                'llm_available_models': profile_models,
                'llm_api_key_set': bool(profile_api_key),
            }
        )
    return {
        'default_run_mode': config['default_run_mode'],
        'llm_profile_id': config['llm_profile_id'],
        'llm_profiles': profiles_public,
        'llm_provider': config['llm_provider'],
        'llm_base_url': config['llm_base_url'],
        'llm_primary_model': config['llm_primary_model'],
        'llm_reviewer_model': config['llm_reviewer_model'],
        'llm_shadow_model': config['llm_shadow_model'],
        'llm_shadow_reviewer_model': config['llm_shadow_reviewer_model'],
        'llm_available_models': available_models,
        'llm_api_key_set': bool(api_key),
        'llm_api_key_masked': _mask_secret(api_key),
    }


def update_runtime_config(payload: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, str] = {}
    if 'default_run_mode' in payload and payload['default_run_mode'] is not None:
        updates['default_run_mode'] = _normalize_run_mode(payload['default_run_mode'])

    if not _allow_runtime_connection_override():
        restricted_fields = [field for field in ('llm_provider', 'llm_base_url', 'llm_api_key') if payload.get(field) is not None]
        if restricted_fields:
            blocked = ', '.join(restricted_fields)
            raise ValueError(f'runtime override disabled for fields: {blocked}')

    profiles = _env_profiles()
    profile_by_id = {item['id']: item for item in profiles}
    selected_profile_id = None
    if 'llm_profile_id' in payload and payload['llm_profile_id'] is not None:
        selected_profile_id = _resolve_active_profile_id(profiles, payload['llm_profile_id'])
        selected_profile = dict(profile_by_id[selected_profile_id])
        updates['llm_profile_id'] = selected_profile_id
        updates['llm_provider'] = selected_profile['llm_provider']
        updates['llm_base_url'] = selected_profile['llm_base_url']
        updates['llm_api_key'] = selected_profile['llm_api_key']
        updates['llm_primary_model'] = selected_profile['llm_primary_model']
        updates['llm_reviewer_model'] = selected_profile['llm_reviewer_model']
        updates['llm_shadow_model'] = selected_profile['llm_shadow_model']
        updates['llm_shadow_reviewer_model'] = selected_profile['llm_shadow_reviewer_model']

    current = get_runtime_config()
    active_profile_id = selected_profile_id or _resolve_active_profile_id(profiles, current.get('llm_profile_id', ''))
    active_profile = dict(profile_by_id.get(active_profile_id, _select_profile(profiles, active_profile_id)))
    available_models = _collect_available_models(active_profile)
    default_primary = updates.get('llm_primary_model', current.get('llm_primary_model', DEFAULT_MOCK_PRIMARY_MODEL))
    default_shadow = updates.get('llm_shadow_model', current.get('llm_shadow_model', DEFAULT_MOCK_SHADOW_MODEL))

    if 'llm_provider' in payload and payload['llm_provider'] is not None:
        updates['llm_provider'] = _normalize_provider(payload['llm_provider'])
    if 'llm_base_url' in payload and payload['llm_base_url'] is not None:
        updates['llm_base_url'] = _normalize_base_url(payload['llm_base_url'])
    if 'llm_primary_model' in payload and payload['llm_primary_model'] is not None:
        candidate = _normalize_model(payload['llm_primary_model'], fallback=default_primary)
        if available_models and candidate not in available_models:
            raise ValueError(f'llm_primary_model must be one of preset models: {available_models}')
        updates['llm_primary_model'] = candidate
    if 'llm_reviewer_model' in payload and payload['llm_reviewer_model'] is not None:
        updates['llm_reviewer_model'] = _normalize_model(payload['llm_reviewer_model'], fallback='NONE')
    if 'llm_shadow_model' in payload and payload['llm_shadow_model'] is not None:
        candidate = _normalize_model(payload['llm_shadow_model'], fallback=default_shadow)
        if available_models and candidate not in available_models:
            raise ValueError(f'llm_shadow_model must be one of preset models: {available_models}')
        updates['llm_shadow_model'] = candidate
    if 'llm_shadow_reviewer_model' in payload and payload['llm_shadow_reviewer_model'] is not None:
        updates['llm_shadow_reviewer_model'] = _normalize_model(payload['llm_shadow_reviewer_model'], fallback='NONE')
    if 'llm_api_key' in payload and payload['llm_api_key'] is not None:
        updates['llm_api_key'] = _clean_text(payload['llm_api_key'])

    if updates:
        with _LOCK:
            _OVERRIDES.update(updates)
    return get_runtime_config_public()


def reset_runtime_config_overrides() -> None:
    with _LOCK:
        _OVERRIDES.clear()
    _read_dotenv_values.cache_clear()
