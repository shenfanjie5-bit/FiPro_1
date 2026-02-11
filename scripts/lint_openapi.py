#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from openapi_spec_validator import validate_spec
import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description='Lint OpenAPI specification file.')
    parser.add_argument('spec_path', nargs='?', default='docs/OPENAPI.yaml')
    args = parser.parse_args()

    spec_path = Path(args.spec_path)
    if not spec_path.exists():
        print(f'openapi_lint=FAILED reason=missing_file path={spec_path}')
        sys.exit(1)

    try:
        spec = yaml.safe_load(spec_path.read_text(encoding='utf-8'))
        validate_spec(spec)
    except Exception as exc:  # noqa: BLE001
        print(f'openapi_lint=FAILED path={spec_path}')
        print(f'error={exc}')
        sys.exit(1)

    print(f'openapi_lint=PASS path={spec_path}')


if __name__ == '__main__':
    main()
