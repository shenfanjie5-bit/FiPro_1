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
