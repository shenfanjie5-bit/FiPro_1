from __future__ import annotations

from app.backtest import llm_proposals as proposal_module


def _base_pack() -> dict:
    return {
        'summary': {'skill_pack_id': 'cn_a_core', 'version': '0.1.0'},
        'gate': {
            'manual_approval_required': False,
            'promotion_rule': {'all_of': []},
            'anti_overfit': {
                'require_train_validation_split': False,
                'require_threshold_sensitivity_check': False,
                'max_param_changes_per_iteration': 8,
            },
        },
        'factors': {'factors': [{'factor_id': 'price.momentum_20d', 'weight': 0.2, 'enabled': True}]},
        'formula': {},
        'policy': {'thresholds': {}},
        'risk': {},
        'llm_mapping': {},
    }


def test_run_llm_skill_pack_proposal_cycle_dry_run(monkeypatch) -> None:
    class FakeLLMProvider:
        def generate_skill_pack_candidate_plans(self, *, skill_pack, calibration_profile, proposal_count):  # noqa: ARG002
            return [
                {
                    'proposal_id': 'prop_01',
                    'description': 'test proposal',
                    'changes': [
                        {'op': 'set', 'path': 'factors.factors[price.momentum_20d].weight', 'to': 0.25},
                    ],
                }
            ]

    monkeypatch.setattr(proposal_module, 'resolve_champion_version', lambda skill_pack_id, root_dir=None: '0.1.0')  # noqa: ARG005
    monkeypatch.setattr(proposal_module, 'load_skill_pack', lambda skill_pack_id, version, root_dir=None: _base_pack())  # noqa: ARG005
    monkeypatch.setattr(
        proposal_module,
        'load_calibration_profile',
        lambda skill_pack_id, version, root_dir=None: {'search_space': []},  # noqa: ARG005
    )
    monkeypatch.setattr(proposal_module, 'LLMProvider', FakeLLMProvider)
    monkeypatch.setattr(
        proposal_module,
        'generate_skill_pack_candidates_from_plans',
        lambda **kwargs: {  # noqa: ARG005
            'skill_pack_id': 'cn_a_core',
            'base_version': '0.1.0',
            'generated_count': 1,
            'items': [{'version': '0.1.1', 'plan_id': 'prop_01'}],
            'dry_run': True,
        },
    )

    called = {'count': 0}

    def fake_run_batch_backtest(*args, **kwargs):  # noqa: ANN002, ANN003
        called['count'] += 1
        return {}

    monkeypatch.setattr(proposal_module, 'run_batch_backtest', fake_run_batch_backtest)

    result = proposal_module.run_llm_skill_pack_proposal_cycle(
        backtest_payload={
            'ticker': '600519.SH',
            'market': 'CN_A',
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER0',
            'start_date': '2026-02-01',
            'end_date': '2026-02-10',
        },
        runner=lambda request_data, thread_id: {},  # noqa: ARG005
        snapshot_loader=lambda ticker, asof: {},  # noqa: ARG005
        benchmark_loader=lambda ticker, asof: {},  # noqa: ARG005
        dry_run=True,
    )

    assert result['dry_run'] is True
    assert result['items'][0]['version'] == '0.1.1'
    assert called['count'] == 0


def test_run_llm_skill_pack_proposal_cycle_selects_llm_pick(monkeypatch) -> None:
    class FakeLLMProvider:
        def generate_skill_pack_candidate_plans(self, *, skill_pack, calibration_profile, proposal_count):  # noqa: ARG002
            return [
                {
                    'proposal_id': 'prop_01',
                    'description': 'test proposal 1',
                    'changes': [
                        {'op': 'set', 'path': 'factors.factors[price.momentum_20d].weight', 'to': 0.25},
                    ],
                },
                {
                    'proposal_id': 'prop_02',
                    'description': 'test proposal 2',
                    'changes': [
                        {'op': 'set', 'path': 'factors.factors[price.momentum_20d].weight', 'to': 0.15},
                    ],
                },
            ][:proposal_count]

        def select_best_skill_pack_proposal(self, *, proposal_evaluations, default_candidate_version):  # noqa: ARG002
            return {'candidate_version': '0.1.2', 'rationale': 'llm picks proposal 2'}

    monkeypatch.setattr(proposal_module, 'resolve_champion_version', lambda skill_pack_id, root_dir=None: '0.1.0')  # noqa: ARG005
    monkeypatch.setattr(proposal_module, 'load_skill_pack', lambda skill_pack_id, version, root_dir=None: _base_pack())  # noqa: ARG005
    monkeypatch.setattr(
        proposal_module,
        'load_calibration_profile',
        lambda skill_pack_id, version, root_dir=None: {'search_space': []},  # noqa: ARG005
    )
    monkeypatch.setattr(proposal_module, 'LLMProvider', FakeLLMProvider)
    monkeypatch.setattr(
        proposal_module,
        'generate_skill_pack_candidates_from_plans',
        lambda **kwargs: {  # noqa: ARG005
            'skill_pack_id': 'cn_a_core',
            'base_version': '0.1.0',
            'generated_count': 2,
            'items': [
                {'version': '0.1.1', 'plan_id': 'prop_01'},
                {'version': '0.1.2', 'plan_id': 'prop_02'},
            ],
            'dry_run': False,
        },
    )

    def fake_run_batch_backtest(payload, **kwargs):  # noqa: ANN001, ARG001
        version = payload.get('skill_pack_version')
        if version == '0.1.0':
            return {'batch_id': 'bt_base', 'summary': {'excess_return_pct': 1.0}}
        if version == '0.1.1':
            return {'batch_id': 'bt_01', 'summary': {'excess_return_pct': 1.2}}
        return {'batch_id': 'bt_02', 'summary': {'excess_return_pct': 1.4}}

    monkeypatch.setattr(proposal_module, 'run_batch_backtest', fake_run_batch_backtest)

    def fake_evaluate(**kwargs):  # noqa: ANN003
        candidate_version = kwargs.get('candidate_version')
        delta = 0.2 if candidate_version == '0.1.1' else 0.4
        return {
            'decision': 'ALLOW',
            'candidate_metrics': {
                'excess_return_delta_pct': delta,
                'max_drawdown_delta_pct': 0.1,
            },
            'champion_metrics': {},
            'failed_checks': [],
        }

    monkeypatch.setattr(proposal_module, 'evaluate_skill_pack_promotion', fake_evaluate)

    result = proposal_module.run_llm_skill_pack_proposal_cycle(
        backtest_payload={
            'ticker': '600519.SH',
            'market': 'CN_A',
            'strategy_version_id': 'stg_v1',
            'tier': 'TIER0',
            'start_date': '2026-02-01',
            'end_date': '2026-02-10',
        },
        runner=lambda request_data, thread_id: {},  # noqa: ARG005
        snapshot_loader=lambda ticker, asof: {},  # noqa: ARG005
        benchmark_loader=lambda ticker, asof: {},  # noqa: ARG005
        dry_run=False,
    )

    assert result['selected_candidate']['candidate_version'] == '0.1.2'
    assert result['selected_candidate']['llm_selector']['rationale'] == 'llm picks proposal 2'


def test_llm_proposal_run_persistence_and_query(tmp_path) -> None:
    payload = {
        'run_id': 'run_demo_009',
        'generated_at': '2026-02-14T00:00:00+00:00',
        'skill_pack_id': 'cn_a_core',
        'base_version': '0.1.0',
        'proposal_count': 2,
        'dry_run': False,
        'selected_candidate': {'candidate_version': '0.1.2'},
        'execution': {'executed': False},
    }
    proposal_module._persist_llm_proposal_run(payload, root_dir=tmp_path)  # noqa: SLF001

    listed = proposal_module.list_llm_proposal_runs(limit=10, offset=0, root_dir=tmp_path)
    assert listed['total'] == 1
    assert listed['items'][0]['run_id'] == 'run_demo_009'
    assert listed['items'][0]['selected_candidate_version'] == '0.1.2'

    fetched = proposal_module.get_llm_proposal_run('run_demo_009', root_dir=tmp_path)
    assert fetched is not None
    assert fetched['run_id'] == 'run_demo_009'
