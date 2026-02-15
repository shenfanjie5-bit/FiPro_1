from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _clean_text(value: Any, *, max_len: int = 500) -> str:
    if not isinstance(value, str):
        return ''
    return ' '.join(value.split())[:max_len]


def _normalize_horizon_days(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    if parsed < 1:
        return None
    return min(parsed, 120)


def _extract_json_payload(raw: str) -> dict[str, Any]:
    text = str(raw or '').strip()
    if not text:
        raise ValueError('empty response body')
    if text.startswith('```'):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = '\n'.join(lines[1:-1]).strip()
    if text.startswith('{'):
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError('expected JSON object')
        return payload

    # Some providers prepend chain-of-thought or prose containing braces.
    # Scan all balanced-object candidates and return the first valid JSON object.
    starts = [idx for idx, ch in enumerate(text) if ch == '{']
    if not starts:
        raise ValueError('no JSON object found')
    for start in starts:
        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(text)):
            ch = text[end]
            if escaped:
                escaped = False
                continue
            if ch == '\\':
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start : end + 1]
                    try:
                        payload = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(payload, dict):
                        return payload
                    break
                if depth < 0:
                    break
    raise ValueError('no valid JSON object found in response')


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
        self.profile_id = str(runtime_config.get('llm_profile_id', '')).strip().lower()
        self.api_key = runtime_config['llm_api_key']
        self.base_url = runtime_config['llm_base_url']
        self._isolation_namespace = _clean_text(
            os.getenv('OPENCLAW_SESSION_NAMESPACE', os.getenv('LLM_OPENCLAW_SESSION_NAMESPACE', 'fipro1')),
            max_len=64,
        ) or 'fipro1'
        self._default_session_seed = uuid.uuid4().hex[:12]
        self.timeout_seconds = _env_float('LLM_TIMEOUT_SECONDS', 30.0, minimum=5.0, maximum=180.0)

    def _ensure_live_provider_ready(self) -> None:
        if self.provider not in SUPPORTED_LLM_PROVIDERS:
            raise ToolExecutionError(
                code='DATA_UNAVAILABLE',
                message=f'Unsupported LLM provider: {self.provider}',
                retryable=False,
                details={'provider': self.provider},
            )
        if self.provider != 'mock' and not self.api_key:
            raise ToolExecutionError(
                code='DATA_UNAVAILABLE',
                message=f'LLM_API_KEY not configured for provider={self.provider}',
                retryable=False,
                details={'provider': self.provider},
            )

    def _is_openclaw_mode(self) -> bool:
        if self.provider != 'openai_compatible':
            return False
        model = str(self.primary_model or '').strip().lower()
        if model.startswith('openclaw:') or model.startswith('agent:'):
            return True
        if self.profile_id == 'openclaw':
            return True
        return 'openclaw' in str(self.base_url or '').strip().lower()

    def _resolve_openclaw_agent_id(self) -> str:
        override = _clean_text(os.getenv('OPENCLAW_AGENT_ID', os.getenv('LLM_OPENCLAW_AGENT_ID', '')), max_len=64)
        if override:
            return override
        model = str(self.primary_model or '').strip()
        lowered = model.lower()
        if lowered.startswith('openclaw:') and len(model.split(':', 1)) == 2:
            candidate = model.split(':', 1)[1].strip()
            if candidate:
                return candidate
        if lowered.startswith('agent:') and len(model.split(':', 1)) == 2:
            candidate = model.split(':', 1)[1].strip()
            if candidate:
                return candidate
        return 'main'

    def _build_openclaw_session_key(self, call_context: dict[str, Any] | None = None) -> str:
        context = call_context if isinstance(call_context, dict) else {}
        explicit = _clean_text(
            context.get('openclaw_session_key') or os.getenv('OPENCLAW_SESSION_KEY', os.getenv('LLM_OPENCLAW_SESSION_KEY', '')),
            max_len=128,
        )
        if explicit:
            return explicit
        seed_payload = {
            'thread_id': _clean_text(context.get('thread_id'), max_len=80),
            'run_id': _clean_text(context.get('run_id'), max_len=80),
            'stage': _clean_text(context.get('stage'), max_len=48),
            'ticker': _clean_text(context.get('ticker'), max_len=32),
            'asof': _clean_text(context.get('asof'), max_len=48),
            'run_mode': _clean_text(context.get('run_mode'), max_len=24),
            'skill_pack_id': _clean_text(context.get('skill_pack_id'), max_len=48),
            'proposal_count': _safe_int(context.get('proposal_count'), 0),
            'provider': self.provider,
            'profile_id': self.profile_id,
            'model': self.primary_model,
            'default_session_seed': self._default_session_seed,
        }
        digest = hashlib.sha256(json.dumps(seed_payload, ensure_ascii=True, sort_keys=True).encode('utf-8')).hexdigest()[:24]
        namespace = re.sub(r'[^a-zA-Z0-9_.:-]+', '_', self._isolation_namespace).strip('._:-') or 'fipro1'
        return f'{namespace}:{digest}'

    def _build_openclaw_headers(self, call_context: dict[str, Any] | None = None) -> dict[str, str]:
        return {
            'x-openclaw-agent-id': self._resolve_openclaw_agent_id(),
            'x-openclaw-session-key': self._build_openclaw_session_key(call_context),
        }

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
        self._ensure_live_provider_ready()

        live_analysis = self._call_openai_chat_completion(context)
        return self._merge_live_analysis(baseline, live_analysis)

    def _mock_skill_pack_candidate_plans(
        self,
        *,
        skill_pack: dict[str, Any],
        calibration_profile: dict[str, Any] | None = None,
        proposal_count: int = 2,
    ) -> list[dict[str, Any]]:
        normalized_count = max(1, min(8, _safe_int(proposal_count, 2)))
        plans: list[dict[str, Any]] = []
        search_space = calibration_profile.get('search_space', []) if isinstance(calibration_profile, dict) else []
        if isinstance(search_space, list):
            for item in search_space:
                if len(plans) >= normalized_count:
                    break
                if not isinstance(item, dict):
                    continue
                path = str(item.get('path', '')).strip()
                if not path:
                    continue
                current = _safe_float(item.get('current'), 0.0)
                step = _safe_float(item.get('step'), 0.0)
                minimum = _safe_float(item.get('min'), current - step)
                maximum = _safe_float(item.get('max'), current + step)
                if step <= 0:
                    continue
                target = current + step
                if target > maximum:
                    target = current - step
                target = max(minimum, min(maximum, target))
                plan_index = len(plans) + 1
                plans.append(
                    {
                        'proposal_id': f'mock_prop_{plan_index:02d}',
                        'description': f'mock calibration adjustment for {str(item.get("param_id", "")).strip() or path}',
                        'changes': [
                            {
                                'op': 'set',
                                'path': path,
                                'to': round(target, 8),
                            }
                        ],
                    }
                )

        if len(plans) < normalized_count:
            factor_items = (
                ((skill_pack.get('factors') or {}).get('factors') or [])
                if isinstance(skill_pack, dict)
                else []
            )
            if not isinstance(factor_items, list):
                factor_items = []
            for factor in factor_items:
                if len(plans) >= normalized_count:
                    break
                if not isinstance(factor, dict):
                    continue
                factor_id = str(factor.get('factor_id', '')).strip()
                if not factor_id:
                    continue
                current_weight = _safe_float(factor.get('weight'), 0.0)
                target_weight = round(max(-1.0, min(1.0, current_weight + 0.02)), 8)
                plan_index = len(plans) + 1
                plans.append(
                    {
                        'proposal_id': f'mock_prop_{plan_index:02d}',
                        'description': f'mock weight update for {factor_id}',
                        'changes': [
                            {
                                'op': 'set',
                                'path': f'factors.factors[{factor_id}].weight',
                                'to': target_weight,
                            }
                        ],
                    }
                )

        if len(plans) < normalized_count:
            plan_index = len(plans) + 1
            plans.append(
                {
                    'proposal_id': f'mock_prop_{plan_index:02d}',
                    'description': 'mock add conservative policy rule',
                    'changes': [
                        {
                            'op': 'append',
                            'path': 'policy.rules',
                            'to': {
                                'rule_id': f'llm_mock_reduce_guard_{plan_index:02d}',
                                'scope': 'has_position',
                                'when': 'final_score < 40 && confidence < 0.58',
                                'action': 'REDUCE',
                                'priority': 66,
                            },
                        }
                    ],
                }
            )
        return plans[:normalized_count]

    def _build_skill_pack_proposal_prompt(
        self,
        *,
        skill_pack: dict[str, Any],
        calibration_profile: dict[str, Any] | None = None,
        proposal_count: int,
    ) -> str:
        factors = []
        raw_factors = ((skill_pack.get('factors') or {}).get('factors') or []) if isinstance(skill_pack, dict) else []
        if isinstance(raw_factors, list):
            for item in raw_factors[:24]:
                if not isinstance(item, dict):
                    continue
                factor_id = str(item.get('factor_id', '')).strip()
                if not factor_id:
                    continue
                factors.append(
                    {
                        'factor_id': factor_id,
                        'weight': _safe_float(item.get('weight'), 0.0),
                        'enabled': bool(item.get('enabled', True)),
                    }
                )
        thresholds = {}
        policy_payload = skill_pack.get('policy') if isinstance(skill_pack, dict) else {}
        if isinstance(policy_payload, dict):
            raw_thresholds = policy_payload.get('thresholds')
            if isinstance(raw_thresholds, dict):
                thresholds = raw_thresholds
        search_space = []
        if isinstance(calibration_profile, dict):
            raw_search_space = calibration_profile.get('search_space')
            if isinstance(raw_search_space, list):
                for item in raw_search_space[:40]:
                    if not isinstance(item, dict):
                        continue
                    search_space.append(
                        {
                            'param_id': str(item.get('param_id', '')).strip(),
                            'path': str(item.get('path', '')).strip(),
                            'type': str(item.get('type', '')).strip(),
                            'current': item.get('current'),
                            'min': item.get('min'),
                            'max': item.get('max'),
                            'step': item.get('step'),
                        }
                    )

        payload = {
            'skill_pack_summary': skill_pack.get('summary', {}),
            'factors': factors,
            'policy_thresholds': thresholds,
            'calibration_search_space': search_space,
            'proposal_count': int(max(1, min(8, _safe_int(proposal_count, 2)))),
        }
        return (
            'You are a quantitative strategy proposer. '
            'Produce strict JSON only with shape: '
            '{"proposals":[{"proposal_id":"...","description":"...","changes":[{"op":"set|append","path":"...","to":...}]}]}. '
            'Allowed roots in change.path: factors, formula, policy, risk, llm_mapping. '
            'Do not modify manifest or gate. '
            'For append, path must point to a list field such as policy.rules or risk.penalty_rules. '
            'Provide exactly proposal_count proposals when feasible.\n\n'
            f'Context JSON:\n{json.dumps(payload, ensure_ascii=True, separators=(",", ":"))}'
        )

    def generate_skill_pack_candidate_plans(
        self,
        *,
        skill_pack: dict[str, Any],
        calibration_profile: dict[str, Any] | None = None,
        proposal_count: int = 2,
    ) -> list[dict[str, Any]]:
        normalized_count = max(1, min(8, _safe_int(proposal_count, 2)))
        if self.provider == 'mock':
            return self._mock_skill_pack_candidate_plans(
                skill_pack=skill_pack,
                calibration_profile=calibration_profile,
                proposal_count=normalized_count,
            )
        self._ensure_live_provider_ready()
        payload = self._call_openai_chat_json(
            prompt=self._build_skill_pack_proposal_prompt(
                skill_pack=skill_pack,
                calibration_profile=calibration_profile,
                proposal_count=normalized_count,
            ),
            temperature=0.15,
            call_context={
                'stage': 'llm_proposal_generate',
                'skill_pack_id': _clean_text(((skill_pack.get('summary') or {}).get('skill_pack_id')) if isinstance(skill_pack, dict) else '', max_len=64),
                'proposal_count': normalized_count,
            },
        )
        proposals = payload.get('proposals')
        if not isinstance(proposals, list):
            raise ToolExecutionError(
                code='UPSTREAM_ERROR',
                message='LLM proposal payload missing proposals list',
                retryable=True,
                details={'provider': self.provider, 'model': self.primary_model},
            )
        normalized: list[dict[str, Any]] = []
        for idx, item in enumerate(proposals, start=1):
            if len(normalized) >= normalized_count:
                break
            if not isinstance(item, dict):
                continue
            changes = item.get('changes')
            if not isinstance(changes, list) or not changes:
                continue
            clean_changes: list[dict[str, Any]] = []
            for change in changes:
                if not isinstance(change, dict):
                    continue
                path = str(change.get('path', '')).strip()
                if not path:
                    continue
                op = str(change.get('op', 'set')).strip().lower() or 'set'
                if op not in {'set', 'append'}:
                    op = 'set'
                clean_changes.append({'op': op, 'path': path, 'to': change.get('to')})
            if not clean_changes:
                continue
            normalized.append(
                {
                    'proposal_id': _clean_text(str(item.get('proposal_id', '')).strip() or f'llm_prop_{idx:02d}', max_len=64),
                    'description': _clean_text(item.get('description', ''), max_len=280),
                    'changes': clean_changes,
                }
            )
        if not normalized:
            raise ToolExecutionError(
                code='UPSTREAM_ERROR',
                message='LLM returned no valid proposals',
                retryable=True,
                details={'provider': self.provider, 'model': self.primary_model},
            )
        return normalized[:normalized_count]

    def select_best_skill_pack_proposal(
        self,
        *,
        proposal_evaluations: list[dict[str, Any]],
        default_candidate_version: str,
    ) -> dict[str, Any]:
        valid_versions = {
            str(item.get('candidate_version', '')).strip()
            for item in proposal_evaluations
            if isinstance(item, dict) and str(item.get('candidate_version', '')).strip()
        }
        default_version = str(default_candidate_version or '').strip()
        if not default_version:
            default_version = next(iter(valid_versions), '')

        if self.provider == 'mock' or not valid_versions:
            return {
                'candidate_version': default_version,
                'rationale': 'mock/deterministic selector used',
            }

        self._ensure_live_provider_ready()
        prompt_payload = {
            'default_candidate_version': default_version,
            'items': proposal_evaluations,
        }
        payload = self._call_openai_chat_json(
            prompt=(
                'You are a strategy review assistant. '
                'Choose one candidate based on backtest evaluation and gate checks. '
                'Return strict JSON only with keys: candidate_version, rationale.'
                f'\n\nContext JSON:\n{json.dumps(prompt_payload, ensure_ascii=True, separators=(",", ":"))}'
            ),
            temperature=0.0,
            call_context={
                'stage': 'llm_proposal_select',
                'run_id': _clean_text(default_version, max_len=64),
            },
        )
        selected = str(payload.get('candidate_version', '')).strip()
        if selected not in valid_versions:
            selected = default_version
        return {
            'candidate_version': selected,
            'rationale': _clean_text(payload.get('rationale', ''), max_len=300),
        }

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
            'factor_registry': context.get('factor_registry', {}),
            'local_data': context.get('local_data', {}),
        }
        return (
            "You are an equity research assistant. "
            "Return one compact JSON object only, with keys: "
            "decision_summary, time_horizon, evaluation_horizon_days, base_case, bull_case, bear_case, next_steps, "
            "driver_focus, risk_flags, invalidations, memory_summary. "
            "risk_flags is a list of {severity, description}. "
            "invalidations is a list of {priority, description}. "
            "Use concise factual wording and avoid markdown. "
            "When skill_notes are present, use them as stable decision priors unless strong evidence disproves them. "
            "When factor_registry is present, use listed Tushare endpoints as available data options for reasoning.\n\n"
            f"Context JSON:\n{json.dumps(request_payload, ensure_ascii=True, separators=(',', ':'))}"
        )

    def _call_openai_chat_json(
        self,
        *,
        prompt: str,
        temperature: float = 0.2,
        system_prompt: str = 'You return strict JSON only.',
        call_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        if self._is_openclaw_mode():
            headers.update(self._build_openclaw_headers(call_context))
        body = {
            'model': self.primary_model,
            'temperature': float(max(0.0, min(1.0, temperature))),
            'response_format': {'type': 'json_object'},
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': prompt},
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

    def _call_openai_chat_completion(self, context: dict) -> dict[str, Any]:
        request = context.get('request', {}) if isinstance(context.get('request', {}), dict) else {}
        return self._call_openai_chat_json(
            prompt=self._build_openai_prompt(context),
            temperature=0.2,
            call_context={
                'stage': 'report_draft',
                'thread_id': _clean_text(context.get('thread_id'), max_len=80),
                'ticker': _clean_text(request.get('ticker'), max_len=32),
                'asof': _clean_text(request.get('asof'), max_len=48),
                'run_mode': _clean_text(request.get('run_mode'), max_len=24),
            },
        )

    def _merge_live_analysis(self, baseline: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
        merged = json.loads(json.dumps(baseline, ensure_ascii=True))
        primary_evidence_id = str(merged['evidence_refs'][0]['evidence_id'])

        summary = _clean_text(live.get('decision_summary'), max_len=260)
        if summary:
            merged['decision']['summary'] = summary
        time_horizon = _clean_text(live.get('time_horizon'), max_len=32).upper().replace('-', '_').replace(' ', '_')
        if time_horizon:
            merged['decision']['time_horizon'] = time_horizon
        evaluation_horizon_days = _normalize_horizon_days(live.get('evaluation_horizon_days'))
        if evaluation_horizon_days is not None:
            merged['decision']['evaluation_horizon_days'] = evaluation_horizon_days

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

    def _mock_ta_hybrid_view(
        self,
        *,
        stage: str,
        role: str,
        ta_input: dict[str, Any],
        upstream: dict[str, Any] | None = None,
        round_idx: int = 1,
    ) -> dict[str, Any]:
        upstream_payload = upstream if isinstance(upstream, dict) else {}
        directional_base = _clamp(_safe_float(ta_input.get('directional_bias_base'), 0.0), -1.0, 1.0)
        risk_base = _clamp(_safe_float(ta_input.get('risk_bias_base'), 0.0), -1.0, 1.0)
        conviction_base = _clamp(_safe_float(ta_input.get('conviction_base'), 0.0), 0.0, 1.0)
        disagreement_base = _clamp(_safe_float(ta_input.get('disagreement_base'), 0.0), 0.0, 1.0)
        horizon_hint = _normalize_horizon_days(ta_input.get('horizon_days_hint'))
        if horizon_hint is None:
            horizon_hint = 5

        directional = directional_base
        risk_bias = risk_base
        conviction = conviction_base
        disagreement = disagreement_base
        stance = 'NEUTRAL'
        role_key = f'{stage}:{role}'
        if role_key == 'research:bull':
            directional = _clamp(directional_base + 0.12, -1.0, 1.0)
            conviction = _clamp(conviction_base + 0.08, 0.0, 1.0)
            stance = 'BULLISH'
        elif role_key == 'research:bear':
            directional = _clamp(directional_base - 0.12, -1.0, 1.0)
            risk_bias = _clamp(risk_base + 0.08, -1.0, 1.0)
            conviction = _clamp(conviction_base - 0.06, 0.0, 1.0)
            stance = 'BEARISH'
        elif role_key == 'research_judge:judge':
            bull = upstream_payload.get('bull', {}) if isinstance(upstream_payload.get('bull', {}), dict) else {}
            bear = upstream_payload.get('bear', {}) if isinstance(upstream_payload.get('bear', {}), dict) else {}
            directional = _clamp(
                (_safe_float(bull.get('directional_bias'), directional_base) + _safe_float(bear.get('directional_bias'), directional_base))
                / 2.0,
                -1.0,
                1.0,
            )
            disagreement = _clamp(
                abs(_safe_float(bull.get('directional_bias'), directional_base) - _safe_float(bear.get('directional_bias'), directional_base))
                / 2.0,
                0.0,
                1.0,
            )
            conviction = _clamp(conviction_base - (0.12 * disagreement), 0.0, 1.0)
            stance = 'BULL_LEAN' if directional > 0.1 else ('BEAR_LEAN' if directional < -0.1 else 'NEUTRAL')
        elif role_key == 'risk:aggressive':
            risk_bias = _clamp(risk_base - 0.12, -1.0, 1.0)
            conviction = _clamp(conviction_base + 0.08, 0.0, 1.0)
            stance = 'RISK_ON'
        elif role_key == 'risk:conservative':
            risk_bias = _clamp(risk_base + 0.12, -1.0, 1.0)
            conviction = _clamp(conviction_base - 0.08, 0.0, 1.0)
            stance = 'RISK_OFF'
        elif role_key == 'risk:neutral':
            stance = 'BALANCED'
        elif role_key == 'risk_judge:judge':
            aggr = upstream_payload.get('risk_aggressive', {}) if isinstance(upstream_payload.get('risk_aggressive', {}), dict) else {}
            cons = upstream_payload.get('risk_conservative', {}) if isinstance(upstream_payload.get('risk_conservative', {}), dict) else {}
            neu = upstream_payload.get('risk_neutral', {}) if isinstance(upstream_payload.get('risk_neutral', {}), dict) else {}
            risk_bias = _clamp(
                (
                    _safe_float(aggr.get('risk_bias'), risk_base)
                    + _safe_float(cons.get('risk_bias'), risk_base)
                    + _safe_float(neu.get('risk_bias'), risk_base)
                )
                / 3.0,
                -1.0,
                1.0,
            )
            conviction = _clamp(
                (
                    _safe_float(aggr.get('conviction'), conviction_base)
                    + _safe_float(cons.get('conviction'), conviction_base)
                    + _safe_float(neu.get('conviction'), conviction_base)
                )
                / 3.0,
                0.0,
                1.0,
            )
            stance = 'CAUTIOUS' if risk_bias > 0.2 else ('OPEN' if risk_bias < -0.2 else 'NEUTRAL')

        summary = (
            f'{stage}.{role} round={max(1, int(round_idx))}: '
            f'directional_bias={directional:.3f}, risk_bias={risk_bias:.3f}, '
            f'conviction={conviction:.3f}, disagreement={disagreement:.3f}, horizon={horizon_hint}d.'
        )
        return {
            'summary': summary,
            'stance': stance,
            'directional_bias': directional,
            'risk_bias': risk_bias,
            'conviction': conviction,
            'disagreement': disagreement,
            'horizon_days_hint': horizon_hint,
            'rationale_points': [
                f'round={max(1, int(round_idx))}',
                f'policy_signal={_safe_float(ta_input.get("policy_signal"), 0.0):.3f}',
                f'governance_signal={_safe_float(ta_input.get("governance_signal"), 0.0):.3f}',
            ],
        }

    def _build_ta_hybrid_prompt(
        self,
        *,
        stage: str,
        role: str,
        ta_input: dict[str, Any],
        upstream: dict[str, Any] | None = None,
        round_idx: int = 1,
    ) -> str:
        upstream_payload = upstream if isinstance(upstream, dict) else {}
        payload = {
            'stage': stage,
            'role': role,
            'round_idx': max(1, int(round_idx)),
            'ta_input': ta_input,
            'upstream': upstream_payload,
            'constraints': {
                'directional_bias_range': [-1, 1],
                'risk_bias_range': [-1, 1],
                'conviction_range': [0, 1],
                'disagreement_range': [0, 1],
                'horizon_days_hint_range': [1, 120],
            },
        }
        return (
            'You are one node in a multi-agent TA hybrid workflow. '
            'Return strict JSON only with keys: '
            'summary, stance, directional_bias, risk_bias, conviction, disagreement, horizon_days_hint, rationale_points. '
            'Do not output final trading action, target_position, or order quantity. '
            'summary must be concise and factual. rationale_points must be an array of short strings.\n\n'
            f'Context JSON:\n{json.dumps(payload, ensure_ascii=True, separators=(",", ":"))}'
        )

    def _normalize_ta_hybrid_view(
        self,
        *,
        payload: dict[str, Any],
        stage: str,
        role: str,
        ta_input: dict[str, Any],
    ) -> dict[str, Any]:
        baseline = self._mock_ta_hybrid_view(stage=stage, role=role, ta_input=ta_input, upstream=None, round_idx=1)
        summary = _clean_text(payload.get('summary'), max_len=320) or str(baseline.get('summary', ''))
        stance = _clean_text(payload.get('stance'), max_len=32).upper() or str(baseline.get('stance', 'NEUTRAL')).upper()
        directional_bias = _clamp(
            _safe_float(payload.get('directional_bias'), _safe_float(baseline.get('directional_bias'), 0.0)),
            -1.0,
            1.0,
        )
        risk_bias = _clamp(
            _safe_float(payload.get('risk_bias'), _safe_float(baseline.get('risk_bias'), 0.0)),
            -1.0,
            1.0,
        )
        conviction = _clamp(
            _safe_float(payload.get('conviction'), _safe_float(baseline.get('conviction'), 0.0)),
            0.0,
            1.0,
        )
        disagreement = _clamp(
            _safe_float(payload.get('disagreement'), _safe_float(baseline.get('disagreement'), 0.0)),
            0.0,
            1.0,
        )
        horizon_days_hint = _normalize_horizon_days(payload.get('horizon_days_hint'))
        if horizon_days_hint is None:
            horizon_days_hint = _normalize_horizon_days(ta_input.get('horizon_days_hint'))
        if horizon_days_hint is None:
            horizon_days_hint = int(_safe_float(baseline.get('horizon_days_hint'), 5))
        rationale_points_raw = payload.get('rationale_points')
        rationale_points = []
        if isinstance(rationale_points_raw, list):
            rationale_points = [_clean_text(item, max_len=120) for item in rationale_points_raw]
            rationale_points = [item for item in rationale_points if item]
        if not rationale_points:
            rationale_points = [f'{stage}.{role} normalized fallback']
        return {
            'summary': summary,
            'stance': stance,
            'directional_bias': round(directional_bias, 6),
            'risk_bias': round(risk_bias, 6),
            'conviction': round(conviction, 6),
            'disagreement': round(disagreement, 6),
            'horizon_days_hint': int(max(1, min(120, horizon_days_hint))),
            'rationale_points': rationale_points[:6],
        }

    def generate_ta_hybrid_view(
        self,
        *,
        stage: str,
        role: str,
        ta_input: dict[str, Any],
        upstream: dict[str, Any] | None = None,
        round_idx: int = 1,
    ) -> dict[str, Any]:
        normalized_stage = _clean_text(stage, max_len=32).lower() or 'research'
        normalized_role = _clean_text(role, max_len=32).lower() or 'judge'
        if self.provider == 'mock':
            return self._mock_ta_hybrid_view(
                stage=normalized_stage,
                role=normalized_role,
                ta_input=ta_input,
                upstream=upstream,
                round_idx=round_idx,
            )
        self._ensure_live_provider_ready()
        payload = self._call_openai_chat_json(
            prompt=self._build_ta_hybrid_prompt(
                stage=normalized_stage,
                role=normalized_role,
                ta_input=ta_input,
                upstream=upstream,
                round_idx=round_idx,
            ),
            temperature=0.15,
            call_context={
                'stage': f'ta_hybrid.{normalized_stage}.{normalized_role}',
                'ticker': _clean_text(ta_input.get('ticker'), max_len=32),
                'asof': _clean_text(ta_input.get('asof'), max_len=48),
                'run_mode': _clean_text(ta_input.get('run_mode'), max_len=24),
            },
        )
        return self._normalize_ta_hybrid_view(
            payload=payload,
            stage=normalized_stage,
            role=normalized_role,
            ta_input=ta_input,
        )
