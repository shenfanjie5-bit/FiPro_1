from __future__ import annotations

import pytest

from app.llm.provider import LLMProvider
from app.tools.wrapper import ToolExecutionError
from app.validation.schema_validator import validate_report_schema


def _context() -> dict:
    return {
        'request': {
            'ticker': '600519.SH',
            'market': 'CN_A',
            'asof': '2026-02-10T09:30:00+08:00',
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER0',
            'run_mode': 'LIVE',
        },
        'score': {'overall_score': 66, 'confidence': 0.63, 'proposed_action': 'WATCH'},
        'price_bands': [
            {
                'band_id': 'B1',
                'range': {'currency': 'CNY', 'min': 95, 'max': 100},
                'score': 60,
                'confidence': 0.58,
                'rationale': 'mock rationale',
                'entry_conditions': [{'type': 'TECHNICAL', 'description': 'support holds', 'priority': 'MEDIUM'}],
                'exit_conditions': [{'type': 'RISK', 'description': 'support breaks', 'priority': 'HIGH'}],
            }
        ],
        'evidence_refs': [
            {
                'evidence_id': 'ev_001',
                'type': 'SNAPSHOT_FIELD',
                'title': 'snapshot',
                'source': 'mock',
                'captured_at': '2026-02-10T01:30:00Z',
                'uri': None,
                'snippet': 'close=100',
                'checksum': 'abc',
            }
        ],
        'data_quality': {'status': 'OK', 'missing_fields': [], 'notes': ''},
        'snapshot_ids': ['snap_001'],
        'weights_hash': 'w_mock_hash_v1',
        'tool_call_stats': {'tool_calls': 3, 'latency_ms': 120, 'cost_usd_est': 0},
        'router_policy': 'router_m6_v1',
        'graph_refs': [],
        'event_docs': [],
        'memory_notes': [],
    }


def test_llm_provider_mock_mode_returns_schema_valid_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    provider = LLMProvider(primary_model='mock-primary-v1')

    report = provider.generate_report_draft(_context())

    ok, errors = validate_report_schema(report)
    assert ok, errors
    assert report['provenance']['model']['primary'] == 'mock-primary-v1'


def test_llm_provider_non_mock_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    provider = LLMProvider(primary_model='gpt-test')

    with pytest.raises(ToolExecutionError) as exc_info:
        provider.generate_report_draft(_context())
    assert exc_info.value.code == 'DATA_UNAVAILABLE'


def test_llm_provider_live_analysis_merges_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('LLM_API_KEY', 'sk-test')

    def fake_live_call(self: LLMProvider, context: dict) -> dict:
        assert context['request']['ticker'] == '600519.SH'
        return {
            'decision_summary': 'Live model summary from upstream.',
            'base_case': 'Cash flow stability supports a watch stance.',
            'bull_case': 'Demand upside can improve rerating momentum.',
            'bear_case': 'Macro risk can compress valuation quickly.',
            'next_steps': ['Track turnover and policy tone', 'Watch volume-price confirmation'],
            'driver_focus': 'Demand and inventory cadence',
            'risk_flags': [{'severity': 'HIGH', 'description': 'Policy uncertainty may spike volatility'}],
            'invalidations': [{'priority': 'HIGH', 'description': 'Volume breakdown below key support'}],
            'memory_summary': 'Live summary cached for next review.',
        }

    monkeypatch.setattr(LLMProvider, '_call_openai_chat_completion', fake_live_call)
    provider = LLMProvider(primary_model='gpt-test')

    report = provider.generate_report_draft(_context())
    ok, errors = validate_report_schema(report)
    assert ok, errors
    assert report['decision']['summary'] == 'Live model summary from upstream.'
    assert report['thesis']['base_case'] == 'Cash flow stability supports a watch stance.'
    assert report['provenance']['model']['primary'] == 'gpt-test'


def test_openclaw_mode_injects_isolation_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LLM_PROVIDER', 'openai_compatible')
    monkeypatch.setenv('LLM_BASE_URL', 'http://127.0.0.1:18789/v1')
    monkeypatch.setenv('LLM_API_KEY', 'gateway-token')
    monkeypatch.setenv('OPENCLAW_SESSION_NAMESPACE', 'fipro1-isolated')
    captured_headers: dict[str, str] = {}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                'choices': [
                    {
                        'message': {
                            'content': '{"decision_summary":"isolated","base_case":"b","bull_case":"u","bear_case":"d","next_steps":["n"],"driver_focus":"f","risk_flags":[],"invalidations":[],"memory_summary":"m"}'
                        }
                    }
                ]
            }

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):  # noqa: ANN001
        _ = url
        _ = json
        _ = timeout
        captured_headers.update(headers)
        return _FakeResponse()

    monkeypatch.setattr('app.llm.provider.httpx.post', fake_post)
    provider = LLMProvider(primary_model='openclaw:main')
    context = _context()
    context['thread_id'] = 'thread_isolation_001'
    report = provider.generate_report_draft(context)

    assert report['decision']['summary'] == 'isolated'
    assert captured_headers.get('x-openclaw-agent-id') == 'main'
    assert str(captured_headers.get('x-openclaw-session-key', '')).startswith('fipro1-isolated:')


def test_llm_provider_mock_ta_hybrid_view_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LLM_PROVIDER', 'mock')
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    provider = LLMProvider(primary_model='mock-primary-v1')
    ta_input = {
        'ticker': '600519.SH',
        'asof': '2026-02-10T09:30:00+08:00',
        'run_mode': 'LIVE',
        'policy_signal': 0.3,
        'governance_signal': -0.1,
        'directional_bias_base': 0.12,
        'risk_bias_base': 0.08,
        'conviction_base': 0.55,
        'disagreement_base': 0.2,
        'horizon_days_hint': 7,
    }
    view = provider.generate_ta_hybrid_view(
        stage='research',
        role='bull',
        ta_input=ta_input,
        upstream=None,
        round_idx=1,
    )
    assert view['summary']
    assert -1.0 <= view['directional_bias'] <= 1.0
    assert -1.0 <= view['risk_bias'] <= 1.0
    assert 0.0 <= view['conviction'] <= 1.0
    assert 0.0 <= view['disagreement'] <= 1.0
    assert 1 <= int(view['horizon_days_hint']) <= 120


def test_llm_provider_live_ta_hybrid_view_normalizes_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('LLM_API_KEY', 'sk-test')

    def fake_call(self: LLMProvider, *, prompt: str, temperature: float = 0.2, system_prompt: str = 'x', call_context=None):  # noqa: ANN001, ARG001
        _ = prompt
        _ = temperature
        _ = system_prompt
        _ = call_context
        return {
            'summary': 'live ta node result',
            'stance': 'bullish',
            'directional_bias': 9.0,
            'risk_bias': -9.0,
            'conviction': 9.0,
            'disagreement': -3.0,
            'horizon_days_hint': 500,
            'rationale_points': ['a', 'b'],
        }

    monkeypatch.setattr(LLMProvider, '_call_openai_chat_json', fake_call)
    provider = LLMProvider(primary_model='gpt-test')
    ta_input = {
        'ticker': '600519.SH',
        'asof': '2026-02-10T09:30:00+08:00',
        'run_mode': 'LIVE',
        'policy_signal': 0.3,
        'governance_signal': -0.1,
        'directional_bias_base': 0.12,
        'risk_bias_base': 0.08,
        'conviction_base': 0.55,
        'disagreement_base': 0.2,
        'horizon_days_hint': 7,
    }
    view = provider.generate_ta_hybrid_view(
        stage='risk_judge',
        role='judge',
        ta_input=ta_input,
        upstream={'risk_aggressive': {'risk_bias': -0.1}},
        round_idx=2,
    )
    assert view['directional_bias'] == 1.0
    assert view['risk_bias'] == -1.0
    assert view['conviction'] == 1.0
    assert view['disagreement'] == 0.0
    assert view['horizon_days_hint'] == 120
