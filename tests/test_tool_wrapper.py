from __future__ import annotations

from app.tools.wrapper import execute_tool


def test_execute_tool_sets_error_code_for_degraded_success() -> None:
    def degraded_tool() -> dict:
        return {
            'value': 1,
            'meta': {
                'upstream_error': {
                    'code': 'UPSTREAM_TIMEOUT',
                    'message': 'timed out',
                    'retryable': True,
                }
            },
        }

    result = execute_tool('degraded_tool', {}, degraded_tool)
    assert result['ok'] is True
    assert result['trace']['error_code'] == 'UPSTREAM_TIMEOUT'
    assert result['trace']['degraded'] is True


def test_execute_tool_marks_error_when_tool_returns_error_payload() -> None:
    def failing_tool() -> dict:
        return {'error': {'code': 'DATA_UNAVAILABLE', 'message': 'no data', 'retryable': False, 'details': {}}}

    result = execute_tool('failing_tool', {}, failing_tool)
    assert result['ok'] is False
    assert result['trace']['error_code'] == 'DATA_UNAVAILABLE'


def test_execute_tool_retries_retryable_errors() -> None:
    calls = {'count': 0}

    def flaky_tool() -> dict:
        calls['count'] += 1
        if calls['count'] < 3:
            return {
                'error': {
                    'code': 'UPSTREAM_TIMEOUT',
                    'message': 'temporary timeout',
                    'retryable': True,
                    'details': {},
                }
            }
        return {'value': 42}

    result = execute_tool('flaky_tool_retry', {}, flaky_tool)
    assert result['ok'] is True
    assert calls['count'] == 3
    assert result['trace']['attempts'] == 3
    assert result['trace']['retry_count'] == 2


def test_execute_tool_non_retryable_tool_does_not_retry() -> None:
    calls = {'count': 0}

    def always_retryable_error() -> dict:
        calls['count'] += 1
        return {
            'error': {
                'code': 'UPSTREAM_TIMEOUT',
                'message': 'temporary timeout',
                'retryable': True,
                'details': {},
            }
        }

    result = execute_tool('write_memory_note', {}, always_retryable_error)
    assert result['ok'] is False
    assert calls['count'] == 1
    assert result['trace']['attempts'] == 1


def test_execute_tool_returns_rate_limited_when_local_limiter_blocks(monkeypatch) -> None:
    monkeypatch.setenv('TOOL_RATE_LIMIT_MAX_CALLS', '1')
    monkeypatch.setenv('TOOL_RATE_LIMIT_WINDOW_MS', '10000')
    monkeypatch.setenv('TOOL_RATE_LIMIT_ACQUIRE_TIMEOUT_MS', '0')

    def ok_tool() -> dict:
        return {'value': 1}

    first = execute_tool('rate_limited_tool_test', {}, ok_tool)
    second = execute_tool('rate_limited_tool_test', {}, ok_tool)
    assert first['ok'] is True
    assert second['ok'] is False
    assert second['trace']['error_code'] == 'RATE_LIMITED'
