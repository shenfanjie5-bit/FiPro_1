from pathlib import Path

import yaml


OPENAPI_PATH = Path('docs/OPENAPI.yaml')
HTTP_METHODS = {'get', 'post', 'put', 'patch', 'delete', 'options', 'head', 'trace'}


def load_spec() -> dict:
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding='utf-8'))


def test_generate_report_response_requires_final_report() -> None:
    spec = load_spec()
    response_schema = spec['components']['schemas']['GenerateReportResponse']
    assert 'final_report' in response_schema['required']
    assert 'report_id' in response_schema['required']


def test_generate_report_endpoint_returns_200_and_schema() -> None:
    spec = load_spec()
    responses = spec['paths']['/reports/generate']['post']['responses']
    assert '200' in responses
    schema_ref = responses['200']['content']['application/json']['schema']['$ref']
    assert schema_ref == '#/components/schemas/GenerateReportResponse'


def test_get_report_endpoint_uses_generate_report_response_schema() -> None:
    spec = load_spec()
    responses = spec['paths']['/reports/{report_id}']['get']['responses']
    schema_ref = responses['200']['content']['application/json']['schema']['$ref']
    assert schema_ref == '#/components/schemas/GenerateReportResponse'


def test_proposal_runs_openapi_has_audit_filters_and_summary_schema() -> None:
    spec = load_spec()
    params = spec['paths']['/skill-packs/proposals/runs']['get']['parameters']
    names = {item['name'] for item in params}
    assert {'skill_pack_id', 'executed', 'dry_run', 'selected_decision', 'generated_after', 'generated_before'} <= names

    response_schema_ref = (
        spec['paths']['/skill-packs/proposals/runs']['get']['responses']['200']['content']['application/json']['schema']['$ref']
    )
    assert response_schema_ref == '#/components/schemas/LlmProposalRunListResponse'

    summary_props = spec['components']['schemas']['LlmProposalRunListSummary']['properties']
    assert 'selected_decision_counts' in summary_props
    assert 'avg_selected_excess_return_delta_pct' in summary_props


def test_watchdog_run_openapi_includes_auto_rollback_controls() -> None:
    spec = load_spec()
    request_schema_ref = (
        spec['paths']['/skill-packs/champion/watchdog/run']['post']['requestBody']['content']['application/json']['schema']['$ref']
    )
    assert request_schema_ref == '#/components/schemas/ChampionWatchdogRunRequest'

    watchdog_req_props = spec['components']['schemas']['ChampionWatchdogRunRequest']['properties']
    assert 'execute_rollback_on_recommendation' in watchdog_req_props
    assert 'rollback_dry_run' in watchdog_req_props
    assert 'rollback_reason' in watchdog_req_props
    assert 'rollback_operator' in watchdog_req_props


def test_openapi_paths_are_implemented_by_runtime_routes() -> None:
    from app.main import app

    runtime_routes: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, 'methods', set())
        for method in methods:
            runtime_routes.add((method.lower(), route.path))

    spec = load_spec()
    missing_routes: list[str] = []
    for path, path_item in spec['paths'].items():
        for method in path_item.keys():
            if method not in HTTP_METHODS:
                continue
            if (method, path) not in runtime_routes:
                missing_routes.append(f'{method.upper()} {path}')

    assert not missing_routes, f'OpenAPI routes missing in runtime: {missing_routes}'
