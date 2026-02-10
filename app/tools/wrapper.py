from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable


def _digest_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def execute_tool(tool_name: str, payload: dict[str, Any], fn: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    start = time.perf_counter()
    input_digest = _digest_payload(payload)
    try:
        output = fn(**payload)
        ok = True
        error_code = None
    except Exception as exc:  # noqa: BLE001
        output = {
            'error': {
                'code': 'INTERNAL_ERROR',
                'message': str(exc),
                'retryable': False,
                'details': {'tool_name': tool_name}
            }
        }
        ok = False
        error_code = 'INTERNAL_ERROR'

    latency_ms = int((time.perf_counter() - start) * 1000)
    return {
        'ok': ok,
        'output': output,
        'trace': {
            'tool_name': tool_name,
            'input_digest': input_digest,
            'latency_ms': latency_ms,
            'error_code': error_code,
            'cost_est': 0.0
        }
    }
