from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import uuid
from typing import Any

import httpx

from app.core.runtime_config import SUPPORTED_LLM_PROVIDERS, get_runtime_config
from app.tools.wrapper import ToolExecutionError


def _env_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _clean_text(value: Any, *, max_len: int = 500) -> str:
    if not isinstance(value, str):
        return ''
    return ' '.join(value.split())[:max_len]


def _extract_json_payload(raw: str) -> dict[str, Any]:
    text = str(raw or '').strip()
    if not text:
        raise ValueError('empty response body')
    if text.startswith('```'):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = '\n'.join(lines[1:-1]).strip()
    if not text.startswith('{'):
        start = text.find('{')
        end = text.rfind('}')
        if start == -1 or end == -1 or end <= start:
            raise ValueError('no JSON object found')
        text = text[start : end + 1]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError('expected JSON object')
    return payload


class LLMProvider:
    """LLM provider adapter.

    Supported provider modes:
    - mock: local deterministic draft (default)
    - openai/openai_compatible: real chat completion API call
    """

    def __init__(self, primary_model: str = 'mock-primary', reviewer_model: str = 'NONE') -> None:
        self.primary_model = primary_model
        self.reviewer_model = reviewer_model
        runtime_config = get_runtime_config()
        self.provider = runtime_config['llm_provider']
        self.api_key = runtime_config['llm_api_key']
        self.base_url = runtime_config['llm_base_url']
        self.timeout_seconds = _env_float('LLM_TIMEOUT_SECONDS', 30.0, minimum=5.0, maximum=180.0)

    def generate_report_draft(self, context: dict) -> dict:
        failure_mode = str(os.getenv('LLM_FORCE_FAILURE', '')).strip().lower()
        if failure_mode in {'timeout', 'upstream_timeout'}:
            raise ToolExecutionError(
                code='UPSTREAM_TIMEOUT',
                message='forced llm timeout for drill',
                retryable=True,
                details={'provider': self.primary_model},
            )
        if failure_mode in {'rate_limit', '429'}:
            raise ToolExecutionError(
                code='RATE_LIMITED',
                message='forced llm rate limit for drill',
                retryable=True,
                details={'provider': self.primary_model},
            )
        if failure_mode in {'error', 'internal_error'}:
            raise ToolExecutionError(
                code='UPSTREAM_ERROR',
                message='forced llm upstream error for drill',
                retryable=True,
                details={'provider': self.primary_model},
            )

        baseline = self._build_baseline_report(context)
        if self.provider == 'mock':
            return baseline
        if self.provider not in SUPPORTED_LLM_PROVIDERS:
            raise ToolExecutionError(
                code='DATA_UNAVAILABLE',
                message=f'Unsupported LLM provider: {self.provider}',
                retryable=False,
                details={'provider': self.provider},
            )
        if not self.api_key:
            raise ToolExecutionError(
                code='DATA_UNAVAILABLE',
                message=f'LLM_API_KEY not configured for provider={self.provider}',
                retryable=False,
                details={'provider': self.provider},
            )

        live_analysis = self._call_openai_chat_completion(context)
        return self._merge_live_analysis(baseline, live_analysis)

    def _build_baseline_report(self, context: dict) -> dict[str, Any]:
        ticker = context['request']['ticker']
        market = context['request'].get('market', 'OTHER')
        asof = context['request']['asof']
        tier = context['request']['tier']
        strategy_version_id = context['request']['strategy_version_id']
        score = context['score']['overall_score']
        confidence = context['score']['confidence']
        proposed_action = context['score'].get('proposed_action', 'WATCH')
        price_bands = context['price_bands']
        evidence = [item for item in context.get('evidence_refs', []) if isinstance(item, dict)]
        if not evidence:
            evidence = [
                {
                    'evidence_id': 'ev_fallback_001',
                    'type': 'MANUAL_NOTE',
                    'title': 'Fallback evidence',
                    'source': 'fallback',
                    'captured_at': datetime.now(timezone.utc).isoformat(),
                    'uri': None,
                    'snippet': 'generated by baseline llm provider fallback',
                    'checksum': 'fallback',
                }
            ]
        router_policy = context.get('router_policy', 'default-v1')
        graph_refs = [str(item).strip() for item in context.get('graph_refs', []) if str(item).strip()]
        graph_evidence_ids = [
            str(ref.get('evidence_id', '')).strip()
            for ref in evidence
            if isinstance(ref, dict) and str(ref.get('type', '')).upper() == 'GRAPH_QUERY' and str(ref.get('evidence_id', '')).strip()
        ]
        skill_notes = [item for item in context.get('skill_notes', []) if isinstance(item, dict)]
        primary_evidence_ids = [str(evidence[0]['evidence_id'])]
        if graph_evidence_ids and graph_evidence_ids[0] not in primary_evidence_ids:
            primary_evidence_ids.append(graph_evidence_ids[0])
        if skill_notes:
            skill_hint = f"skill_rules={len(skill_notes)}"
            for idx, item in enumerate(skill_notes[:2], start=1):
                evidence_id = f"ev_skill_{str(item.get('skill_id', f'auto{idx}')).strip()}"
                if evidence_id not in primary_evidence_ids:
                    primary_evidence_ids.append(evidence_id)
            decision_summary = f"MVP draft with local skills applied ({skill_hint})."
        else:
            decision_summary = 'MVP mock summary based on deterministic score and available evidence.'

        return {
            'schema_version': '0.1',
            'report_id': str(uuid.uuid4()),
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'ticker': ticker,
            'market': market,
            'asof': asof,
            'strategy_version_id': strategy_version_id,
            'tier': tier,
            'decision': {
                'action': proposed_action,
                'overall_score': score,
                'confidence': confidence,
                'time_horizon': 'SWING',
                'summary': decision_summary,
            },
            'price_bands': price_bands,
            'key_drivers_to_watch': [
                {
                    'driver_id': 'drv_001',
                    'type': 'SUPPLY_DEMAND',
                    'what': 'Track demand elasticity and inventory turnover changes',
                    'direction': 'UNCERTAIN',
                    'urgency': 'MEDIUM',
                    'impact_hypothesis': 'Demand stabilization with controlled supply can support valuation.',
                    'monitor': {
                        'signals': [{'name': 'inventory_days', 'source': 'snapshot'}],
                        'triggers': [{'description': 'inventory_days rises > 15% WoW', 'severity': 'HIGH'}],
                    },
                    'evidence_ids': primary_evidence_ids,
                    'graph_refs': graph_refs[:6],
                }
            ],
            'thesis': {
                'base_case': 'Earnings and flow remain stable; trend is range-bound positive.',
                'bull_case': 'Policy and demand upside lead to stronger rerating.',
                'bear_case': 'Macro volatility and weak flows increase drawdown risk.',
                'next_steps': ['Monitor volume expansion with price breakout', 'Track policy and logistics updates'],
            },
            'risk_flags': [
                {
                    'risk_id': 'risk_001',
                    'severity': 'MEDIUM',
                    'description': 'Volatility can expand around macro headlines.',
                    'evidence_ids': [str(evidence[0]['evidence_id'])],
                }
            ],
            'invalidations': [
                {
                    'invalidation_id': 'inv_001',
                    'description': 'Break below support with expanding volume.',
                    'priority': 'HIGH',
                    'evidence_ids': [str(evidence[0]['evidence_id'])],
                }
            ],
            'evidence_refs': evidence,
            'data_quality': context['data_quality'],
            'provenance': {
                'model': {'primary': self.primary_model, 'reviewer': self.reviewer_model},
                'router_policy': router_policy,
                'snapshot_ids': context['snapshot_ids'],
                'weights_hash': context['weights_hash'],
                'run_mode': context['request'].get('run_mode', 'LIVE'),
                'tool_call_stats': context.get('tool_call_stats', {'tool_calls': 0, 'latency_ms': 0, 'cost_usd_est': 0}),
            },
            'memory_update': {
                'summary': 'Keep monitoring demand-flow balance and risk triggers.',
                'tags': [ticker, 'tier:' + tier.lower(), 'mvp'],
                'importance': 60,
                'followups': ['Recheck flows after next major event', 'Validate thesis against updated fundamentals'],
            },
        }

    def _build_openai_prompt(self, context: dict) -> str:
        request_payload = {
            'request': context.get('request', {}),
            'score': context.get('score', {}),
            'data_quality': context.get('data_quality', {}),
            'price_bands': context.get('price_bands', []),
            'event_docs': list(context.get('event_docs', []))[:3],
            'memory_notes': list(context.get('memory_notes', []))[:3],
            'skill_notes': list(context.get('skill_notes', []))[:5],
            'graph_refs': list(context.get('graph_refs', []))[:6],
            'evidence_refs': list(context.get('evidence_refs', []))[:6],
        }
        return (
            "You are an equity research assistant. "
            "Return one compact JSON object only, with keys: "
            "decision_summary, base_case, bull_case, bear_case, next_steps, "
            "driver_focus, risk_flags, invalidations, memory_summary. "
            "risk_flags is a list of {severity, description}. "
            "invalidations is a list of {priority, description}. "
            "Use concise factual wording and avoid markdown. "
            "When skill_notes are present, use them as stable decision priors unless strong evidence disproves them.\n\n"
            f"Context JSON:\n{json.dumps(request_payload, ensure_ascii=True, separators=(',', ':'))}"
        )

    def _call_openai_chat_completion(self, context: dict) -> dict[str, Any]:
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        body = {
            'model': self.primary_model,
            'temperature': 0.2,
            'response_format': {'type': 'json_object'},
            'messages': [
                {'role': 'system', 'content': 'You return strict JSON only.'},
                {'role': 'user', 'content': self._build_openai_prompt(context)},
            ],
        }
        endpoint = f'{self.base_url}/chat/completions'

        try:
            response = httpx.post(endpoint, headers=headers, json=body, timeout=self.timeout_seconds)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ToolExecutionError(
                code='UPSTREAM_TIMEOUT',
                message=f'LLM request timeout: {exc}',
                retryable=True,
                details={'provider': self.provider, 'model': self.primary_model},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = int(exc.response.status_code)
            if status_code == 429:
                raise ToolExecutionError(
                    code='RATE_LIMITED',
                    message=f'LLM provider rate limited (status={status_code})',
                    retryable=True,
                    details={'provider': self.provider, 'model': self.primary_model},
                ) from exc
            if status_code in (401, 403):
                raise ToolExecutionError(
                    code='DATA_UNAVAILABLE',
                    message=f'LLM authentication failed (status={status_code})',
                    retryable=False,
                    details={'provider': self.provider, 'model': self.primary_model},
                ) from exc
            retryable = status_code >= 500
            raise ToolExecutionError(
                code='UPSTREAM_ERROR',
                message=f'LLM provider returned status={status_code}',
                retryable=retryable,
                details={'provider': self.provider, 'model': self.primary_model},
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolExecutionError(
                code='UPSTREAM_ERROR',
                message=f'LLM provider http error: {exc}',
                retryable=True,
                details={'provider': self.provider, 'model': self.primary_model},
            ) from exc

        payload = response.json()
        choices = payload.get('choices') if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ToolExecutionError(
                code='UPSTREAM_ERROR',
                message='LLM response missing choices',
                retryable=True,
                details={'provider': self.provider, 'model': self.primary_model},
            )
        message = choices[0].get('message', {}) if isinstance(choices[0], dict) else {}
        content = message.get('content', '') if isinstance(message, dict) else ''
        if isinstance(content, list):
            text_chunks = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get('text'), str):
                    text_chunks.append(part['text'])
            content = '\n'.join(text_chunks)

        try:
            return _extract_json_payload(str(content))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ToolExecutionError(
                code='UPSTREAM_ERROR',
                message=f'LLM response is not valid JSON object: {exc}',
                retryable=True,
                details={'provider': self.provider, 'model': self.primary_model},
            ) from exc

    def _merge_live_analysis(self, baseline: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
        merged = json.loads(json.dumps(baseline, ensure_ascii=True))
        primary_evidence_id = str(merged['evidence_refs'][0]['evidence_id'])

        summary = _clean_text(live.get('decision_summary'), max_len=260)
        if summary:
            merged['decision']['summary'] = summary

        base_case = _clean_text(live.get('base_case'), max_len=320)
        bull_case = _clean_text(live.get('bull_case'), max_len=320)
        bear_case = _clean_text(live.get('bear_case'), max_len=320)
        if base_case:
            merged['thesis']['base_case'] = base_case
        if bull_case:
            merged['thesis']['bull_case'] = bull_case
        if bear_case:
            merged['thesis']['bear_case'] = bear_case

        next_steps_raw = live.get('next_steps')
        if isinstance(next_steps_raw, list):
            next_steps = [_clean_text(item, max_len=120) for item in next_steps_raw]
            next_steps = [item for item in next_steps if item]
            if next_steps:
                merged['thesis']['next_steps'] = next_steps[:5]

        driver_focus = _clean_text(live.get('driver_focus'), max_len=180)
        if driver_focus:
            merged['key_drivers_to_watch'][0]['what'] = driver_focus

        live_risks = live.get('risk_flags')
        if isinstance(live_risks, list):
            normalized_risks: list[dict[str, Any]] = []
            for idx, item in enumerate(live_risks[:4], start=1):
                if not isinstance(item, dict):
                    continue
                description = _clean_text(item.get('description'), max_len=180)
                if not description:
                    continue
                severity = str(item.get('severity', 'MEDIUM')).upper()
                if severity not in {'LOW', 'MEDIUM', 'HIGH'}:
                    severity = 'MEDIUM'
                normalized_risks.append(
                    {
                        'risk_id': f'risk_live_{idx:03d}',
                        'severity': severity,
                        'description': description,
                        'evidence_ids': [primary_evidence_id],
                    }
                )
            if normalized_risks:
                merged['risk_flags'] = normalized_risks

        live_invalidations = live.get('invalidations')
        if isinstance(live_invalidations, list):
            normalized_invalidations: list[dict[str, Any]] = []
            for idx, item in enumerate(live_invalidations[:4], start=1):
                if not isinstance(item, dict):
                    continue
                description = _clean_text(item.get('description'), max_len=180)
                if not description:
                    continue
                priority = str(item.get('priority', 'MEDIUM')).upper()
                if priority not in {'LOW', 'MEDIUM', 'HIGH'}:
                    priority = 'MEDIUM'
                normalized_invalidations.append(
                    {
                        'invalidation_id': f'inv_live_{idx:03d}',
                        'description': description,
                        'priority': priority,
                        'evidence_ids': [primary_evidence_id],
                    }
                )
            if normalized_invalidations:
                merged['invalidations'] = normalized_invalidations

        memory_summary = _clean_text(live.get('memory_summary'), max_len=180)
        if memory_summary:
            merged['memory_update']['summary'] = memory_summary
        return merged
