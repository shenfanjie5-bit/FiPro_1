from __future__ import annotations

from collections import deque
import hashlib
import json
import os
import threading
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


POLICY_VERSION = 'tool_wrapper_m6_v1'
RETRYABLE_ERROR_CODES = {'RATE_LIMITED', 'UPSTREAM_TIMEOUT', 'UPSTREAM_ERROR'}
NON_RETRYABLE_TOOLS = {'write_memory_note', 'risk_gate', 'score_signal', 'generate_price_bands'}


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _retry_policy(tool_name: str) -> dict[str, int]:
    if tool_name in NON_RETRYABLE_TOOLS:
        max_attempts = 1
    elif tool_name.startswith('llm_'):
        max_attempts = _env_int('LLM_RETRY_MAX_ATTEMPTS', 3, minimum=1, maximum=5)
    else:
        max_attempts = _env_int('TOOL_RETRY_MAX_ATTEMPTS', 3, minimum=1, maximum=5)
    return {
        'max_attempts': max_attempts,
        'backoff_ms': _env_int('TOOL_RETRY_BACKOFF_MS', 120, minimum=0, maximum=5000),
        'backoff_max_ms': _env_int('TOOL_RETRY_BACKOFF_MAX_MS', 1200, minimum=1, maximum=60000),
    }


class _SlidingWindowLimiter:
    def __init__(self, *, window_seconds: float, max_calls: int) -> None:
        self.window_seconds = max(0.001, window_seconds)
        self.max_calls = max(1, max_calls)
        self._lock = threading.Lock()
        self._calls: deque[float] = deque()

    def acquire(self, timeout_ms: int) -> tuple[bool, int]:
        start = time.perf_counter()
        timeout_seconds = max(0.0, timeout_ms / 1000.0)
        while True:
            now = time.monotonic()
            with self._lock:
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    waited_ms = int((time.perf_counter() - start) * 1000)
                    return True, waited_ms
                next_available_seconds = self.window_seconds - (now - self._calls[0])
            elapsed = time.perf_counter() - start
            remaining = timeout_seconds - elapsed
            if remaining <= 0:
                waited_ms = int((time.perf_counter() - start) * 1000)
                return False, waited_ms
            sleep_seconds = min(max(0.001, next_available_seconds), remaining, 0.05)
            time.sleep(sleep_seconds)


_RATE_LIMITER_LOCK = threading.Lock()
_RATE_LIMITERS: dict[str, _SlidingWindowLimiter] = {}


def _rate_limiter(tool_name: str) -> _SlidingWindowLimiter | None:
    max_calls = _env_int('TOOL_RATE_LIMIT_MAX_CALLS', 400, minimum=1, maximum=20000)
    window_ms = _env_int('TOOL_RATE_LIMIT_WINDOW_MS', 1000, minimum=1, maximum=60000)
    if max_calls <= 0:
        return None
    key = f'{tool_name}:{max_calls}:{window_ms}'
    with _RATE_LIMITER_LOCK:
        limiter = _RATE_LIMITERS.get(key)
        if limiter is None:
            limiter = _SlidingWindowLimiter(window_seconds=window_ms / 1000.0, max_calls=max_calls)
            _RATE_LIMITERS[key] = limiter
        return limiter


def _digest_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _error_payload(code: str, message: str, retryable: bool, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'error': {
            'code': code,
            'message': message,
            'retryable': retryable,
            'details': details or {},
        }
    }


def _extract_error(payload: Any) -> tuple[str | None, bool]:
    if not isinstance(payload, dict):
        return None, False
    err = payload.get('error')
    if not isinstance(err, dict):
        return None, False
    code = str(err.get('code', 'INTERNAL_ERROR'))
    retryable = bool(err.get('retryable', code in RETRYABLE_ERROR_CODES))
    return code, retryable


def execute_tool(tool_name: str, payload: dict[str, Any], fn: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    start = time.perf_counter()
    input_digest = _digest_payload(payload)
    policy = _retry_policy(tool_name)
    rate_limit_timeout_ms = _env_int('TOOL_RATE_LIMIT_ACQUIRE_TIMEOUT_MS', 250, minimum=0, maximum=5000)
    limiter = _rate_limiter(tool_name)

    degraded = False
    ok = False
    error_code: str | None = None
    output: dict[str, Any] = {}
    attempts = 0
    retry_wait_ms = 0
    rate_limited_wait_ms = 0
    exhausted_by_local_rate_limit = False

    for attempt in range(1, policy['max_attempts'] + 1):
        attempts = attempt
        if limiter is not None:
            acquired, waited_ms = limiter.acquire(rate_limit_timeout_ms)
            rate_limited_wait_ms += max(0, waited_ms)
            if not acquired:
                exhausted_by_local_rate_limit = True
                output = _error_payload(
                    'RATE_LIMITED',
                    'local tool rate limit exhausted',
                    True,
                    details={
                        'tool_name': tool_name,
                        'acquire_timeout_ms': rate_limit_timeout_ms,
                    },
                )
                error_code = 'RATE_LIMITED'
                break

        try:
            output = fn(**payload)
        except ToolExecutionError as exc:
            output = _error_payload(
                exc.code,
                exc.message,
                exc.retryable,
                details={**exc.details, 'tool_name': tool_name},
            )
        except TimeoutError as exc:
            output = _error_payload(
                'UPSTREAM_TIMEOUT',
                str(exc),
                True,
                details={'tool_name': tool_name},
            )
        except Exception as exc:  # noqa: BLE001
            output = _error_payload(
                'INTERNAL_ERROR',
                str(exc),
                False,
                details={'tool_name': tool_name},
            )

        error_code, retryable = _extract_error(output)
        if error_code is None:
            ok = True
            break
        ok = False
        if not retryable or attempt >= policy['max_attempts']:
            break
        backoff_ms = min(policy['backoff_max_ms'], policy['backoff_ms'] * (2 ** (attempt - 1)))
        retry_wait_ms += max(0, backoff_ms)
        if backoff_ms > 0:
            time.sleep(backoff_ms / 1000.0)

    if ok and isinstance(output, dict):
        meta = output.get('meta', {})
        if isinstance(meta, dict):
            upstream_error = meta.get('upstream_error')
            if isinstance(upstream_error, dict) and upstream_error.get('code'):
                error_code = str(upstream_error.get('code'))
                degraded = True
    elif exhausted_by_local_rate_limit and not isinstance(output.get('error', {}).get('details'), dict):
        output = _error_payload(
            'RATE_LIMITED',
            'local tool rate limit exhausted',
            True,
            details={'tool_name': tool_name},
        )

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
            'cost_est': 0.0,
            'attempts': attempts,
            'retry_count': max(0, attempts - 1),
            'retry_wait_ms': retry_wait_ms,
            'rate_limited_wait_ms': rate_limited_wait_ms,
            'policy_version': POLICY_VERSION,
        }
    }
