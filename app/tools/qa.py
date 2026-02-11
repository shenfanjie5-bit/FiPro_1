from __future__ import annotations

from app.validation.consistency import check_consistency
from app.validation.schema_validator import validate_report_schema


def validate_report_schema_tool(report_json: dict) -> dict:
    ok, errors = validate_report_schema(report_json)
    return {'ok': ok, 'errors': errors}


def consistency_check(report_json: dict) -> dict:
    errors = check_consistency(report_json)
    return {'ok': len(errors) == 0, 'errors': errors}
