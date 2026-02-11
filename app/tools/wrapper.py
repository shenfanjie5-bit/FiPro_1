from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Callable


class ToolExecutionError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def _digest_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def execute_tool(tool_name: str, payload: dict[str, Any], fn: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    start = time.perf_counter()
    input_digest = _digest_payload(payload)
    degraded = False
    try:
        output = fn(**payload)
        if isinstance(output, dict) and isinstance(output.get('error'), dict):
            ok = False
            error_code = str(output['error'].get('code', 'INTERNAL_ERROR'))
        else:
            ok = True
            error_code = None
    except ToolExecutionError as exc:
        output = {
            'error': {
                'code': exc.code,
                'message': exc.message,
                'retryable': exc.retryable,
                'details': {**exc.details, 'tool_name': tool_name},
            }
        }
        ok = False
        error_code = exc.code
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

    if ok and isinstance(output, dict):
        meta = output.get('meta', {})
        if isinstance(meta, dict):
            upstream_error = meta.get('upstream_error')
            if isinstance(upstream_error, dict) and upstream_error.get('code'):
                error_code = str(upstream_error.get('code'))
                degraded = True

    latency_ms = int((time.perf_counter() - start) * 1000)
    return {
        'ok': ok,
        'output': output,
        'trace': {
            'trace_id': f"trace_{uuid.uuid4().hex[:12]}",
            'tool_name': tool_name,
            'input_digest': input_digest,
            'latency_ms': latency_ms,
            'error_code': error_code,
            'degraded': degraded,
            'cost_est': 0.0
        }
    }
