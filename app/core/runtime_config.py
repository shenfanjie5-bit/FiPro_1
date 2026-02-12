from __future__ import annotations

import os
from threading import Lock
from typing import Any, Final


DEFAULT_OPENAI_BASE_URL: Final[str] = 'https://api.openai.com/v1'
DEFAULT_RUN_MODE: Final[str] = 'LIVE'
RUN_MODES: Final[set[str]] = {'LIVE', 'SHADOW', 'BACKTEST'}
SUPPORTED_LLM_PROVIDERS: Final[set[str]] = {'mock', 'openai', 'openai_compatible'}

_LOCK = Lock()
_OVERRIDES: dict[str, str] = {}


def _clean_text(value: Any) -> str:
    return str(value or '').strip()


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


def _env_defaults() -> dict[str, str]:
    return {
        'default_run_mode': _normalize_run_mode(os.getenv('APP_DEFAULT_RUN_MODE', DEFAULT_RUN_MODE)),
        'llm_provider': _normalize_provider(os.getenv('LLM_PROVIDER', 'mock')),
        'llm_base_url': _normalize_base_url(os.getenv('LLM_BASE_URL', DEFAULT_OPENAI_BASE_URL)),
        'llm_api_key': _clean_text(os.getenv('LLM_API_KEY', '')),
        'llm_primary_model': _normalize_model(os.getenv('LLM_PRIMARY_MODEL', 'mock-primary-v1'), fallback='mock-primary-v1'),
        'llm_reviewer_model': _normalize_model(os.getenv('LLM_REVIEWER_MODEL', 'NONE'), fallback='NONE'),
        'llm_shadow_model': _normalize_model(os.getenv('LLM_SHADOW_MODEL', 'mock-challenger-v1'), fallback='mock-challenger-v1'),
        'llm_shadow_reviewer_model': _normalize_model(os.getenv('LLM_SHADOW_REVIEWER_MODEL', 'NONE'), fallback='NONE'),
    }


def get_runtime_config() -> dict[str, str]:
    merged = _env_defaults()
    with _LOCK:
        merged.update(_OVERRIDES)
    return {
        'default_run_mode': _normalize_run_mode(merged.get('default_run_mode', DEFAULT_RUN_MODE)),
        'llm_provider': _normalize_provider(merged.get('llm_provider', 'mock')),
        'llm_base_url': _normalize_base_url(merged.get('llm_base_url', DEFAULT_OPENAI_BASE_URL)),
        'llm_api_key': _clean_text(merged.get('llm_api_key', '')),
        'llm_primary_model': _normalize_model(merged.get('llm_primary_model', 'mock-primary-v1'), fallback='mock-primary-v1'),
        'llm_reviewer_model': _normalize_model(merged.get('llm_reviewer_model', 'NONE'), fallback='NONE'),
        'llm_shadow_model': _normalize_model(merged.get('llm_shadow_model', 'mock-challenger-v1'), fallback='mock-challenger-v1'),
        'llm_shadow_reviewer_model': _normalize_model(merged.get('llm_shadow_reviewer_model', 'NONE'), fallback='NONE'),
    }


def get_runtime_config_public() -> dict[str, Any]:
    config = get_runtime_config()
    api_key = config.get('llm_api_key', '')
    return {
        'default_run_mode': config['default_run_mode'],
        'llm_provider': config['llm_provider'],
        'llm_base_url': config['llm_base_url'],
        'llm_primary_model': config['llm_primary_model'],
        'llm_reviewer_model': config['llm_reviewer_model'],
        'llm_shadow_model': config['llm_shadow_model'],
        'llm_shadow_reviewer_model': config['llm_shadow_reviewer_model'],
        'llm_api_key_set': bool(api_key),
        'llm_api_key_masked': _mask_secret(api_key),
    }


def update_runtime_config(payload: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, str] = {}
    if 'default_run_mode' in payload and payload['default_run_mode'] is not None:
        updates['default_run_mode'] = _normalize_run_mode(payload['default_run_mode'])
    if 'llm_provider' in payload and payload['llm_provider'] is not None:
        updates['llm_provider'] = _normalize_provider(payload['llm_provider'])
    if 'llm_base_url' in payload and payload['llm_base_url'] is not None:
        updates['llm_base_url'] = _normalize_base_url(payload['llm_base_url'])
    if 'llm_primary_model' in payload and payload['llm_primary_model'] is not None:
        updates['llm_primary_model'] = _normalize_model(payload['llm_primary_model'], fallback='mock-primary-v1')
    if 'llm_reviewer_model' in payload and payload['llm_reviewer_model'] is not None:
        updates['llm_reviewer_model'] = _normalize_model(payload['llm_reviewer_model'], fallback='NONE')
    if 'llm_shadow_model' in payload and payload['llm_shadow_model'] is not None:
        updates['llm_shadow_model'] = _normalize_model(payload['llm_shadow_model'], fallback='mock-challenger-v1')
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
