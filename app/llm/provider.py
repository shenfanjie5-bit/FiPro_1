from datetime import datetime, timezone
import os
import uuid

from app.tools.wrapper import ToolExecutionError


class LLMProvider:
    """LLM provider adapter.

    TODO:
    - Replace mock draft with real structured output call.
    - Add reviewer model path.
    """

    def __init__(self, primary_model: str = 'mock-primary', reviewer_model: str = 'NONE') -> None:
        self.primary_model = primary_model
        self.reviewer_model = reviewer_model

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

        ticker = context['request']['ticker']
        market = context['request'].get('market', 'OTHER')
        asof = context['request']['asof']
        tier = context['request']['tier']
        strategy_version_id = context['request']['strategy_version_id']
        score = context['score']['overall_score']
        confidence = context['score']['confidence']
        proposed_action = context['score'].get('proposed_action', 'WATCH')
        price_bands = context['price_bands']
        evidence = context['evidence_refs']
        router_policy = context.get('router_policy', 'default-v1')
        graph_refs = [str(item).strip() for item in context.get('graph_refs', []) if str(item).strip()]
        graph_evidence_ids = [
            str(ref.get('evidence_id', '')).strip()
            for ref in evidence
            if isinstance(ref, dict) and str(ref.get('type', '')).upper() == 'GRAPH_QUERY' and str(ref.get('evidence_id', '')).strip()
        ]
        primary_evidence_ids = [evidence[0]['evidence_id']]
        if graph_evidence_ids and graph_evidence_ids[0] not in primary_evidence_ids:
            primary_evidence_ids.append(graph_evidence_ids[0])

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
                'summary': 'MVP mock summary based on deterministic score and available evidence.'
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
                        'triggers': [{'description': 'inventory_days rises > 15% WoW', 'severity': 'HIGH'}]
                    },
                    'evidence_ids': primary_evidence_ids,
                    'graph_refs': graph_refs[:6]
                }
            ],
            'thesis': {
                'base_case': 'Earnings and flow remain stable; trend is range-bound positive.',
                'bull_case': 'Policy and demand upside lead to stronger rerating.',
                'bear_case': 'Macro volatility and weak flows increase drawdown risk.',
                'next_steps': ['Monitor volume expansion with price breakout', 'Track policy and logistics updates']
            },
            'risk_flags': [
                {
                    'risk_id': 'risk_001',
                    'severity': 'MEDIUM',
                    'description': 'Volatility can expand around macro headlines.',
                    'evidence_ids': [evidence[0]['evidence_id']]
                }
            ],
            'invalidations': [
                {
                    'invalidation_id': 'inv_001',
                    'description': 'Break below support with expanding volume.',
                    'priority': 'HIGH',
                    'evidence_ids': [evidence[0]['evidence_id']]
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
                'tool_call_stats': context.get('tool_call_stats', {'tool_calls': 0, 'latency_ms': 0, 'cost_usd_est': 0})
            },
            'memory_update': {
                'summary': 'Keep monitoring demand-flow balance and risk triggers.',
                'tags': [ticker, 'tier:' + tier.lower(), 'mvp'],
                'importance': 60,
                'followups': ['Recheck flows after next major event', 'Validate thesis against updated fundamentals']
            }
        }
