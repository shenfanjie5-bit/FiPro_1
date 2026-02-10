from pathlib import Path

import yaml


OPENAPI_PATH = Path('docs/OPENAPI.yaml')


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
