from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


_SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schemas' / 'report.schema.json'


def _load_schema() -> dict:
    with _SCHEMA_PATH.open('r', encoding='utf-8') as f:
        return json.load(f)


def validate_report_schema(report_json: dict) -> tuple[bool, list[str]]:
    schema = _load_schema()
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(report_json), key=lambda e: e.path)
    if not errors:
        return True, []
    return False, [f"{'.'.join([str(x) for x in err.absolute_path]) or '<root>'}: {err.message}" for err in errors]
