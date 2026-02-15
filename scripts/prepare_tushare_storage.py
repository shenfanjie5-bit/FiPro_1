from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path


DEFAULT_ROOT = '/Volumes/dockcase2tb/database_all'
DEFAULT_CATALOG = '/Volumes/dockcase2tb/tushare_doc2_leaf_api.csv'


@dataclass
class ApiPathRow:
    api: str
    label: str
    raw_parts: list[str]
    normalized_parts: list[str]
    raw_relative_path: str
    normalized_relative_path: str
    exists: bool
    reason: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Validate and materialize Tushare API directory mapping under a local storage root.'
    )
    parser.add_argument('--root', default=DEFAULT_ROOT, help='Data root directory')
    parser.add_argument('--catalog', default=DEFAULT_CATALOG, help='CSV containing level1..level4,label,api')
    parser.add_argument('--output-csv', default='docs/tushare_api_storage_map.csv')
    parser.add_argument('--output-json', default='docs/tushare_api_storage_map.json')
    parser.add_argument('--create-missing', action='store_true', help='Create missing normalized directories')
    parser.add_argument('--create-meta', action='store_true', help='Create _meta/checkpoints,_meta/manifests,_meta/qa')
    return parser.parse_args()


def _sanitize_part(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    # "/" is path separator and cannot be used as folder name; keep semantics with full-width slash.
    text = text.replace('/', '／')
    # Keep simple cross-platform safety for a few noisy characters.
    text = text.replace(':', '：').replace('*', '＊').replace('?', '？')
    text = text.replace('"', "'").replace('<', '＜').replace('>', '＞').replace('|', '｜')
    return text.strip()


def _collect_rows(root: Path, catalog: Path, create_missing: bool) -> tuple[list[ApiPathRow], int]:
    rows: list[ApiPathRow] = []
    created = 0
    with catalog.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            api = str(row.get('api') or '').strip()
            if not api:
                continue
            label = str(row.get('label') or '').strip()
            raw_parts = [str(row.get(key) or '').strip() for key in ('level1', 'level2', 'level3', 'level4')]
            raw_parts = [item for item in raw_parts if item]
            normalized_parts = [_sanitize_part(item) for item in raw_parts]
            normalized_parts = [item for item in normalized_parts if item]

            raw_relative_path = '/'.join(raw_parts)
            normalized_relative_path = '/'.join(normalized_parts)

            abs_dir = root.joinpath(*normalized_parts)
            exists = abs_dir.is_dir()
            reason = ''
            if raw_relative_path != normalized_relative_path:
                reason = 'normalized_for_filesystem'
            if (not exists) and create_missing:
                abs_dir.mkdir(parents=True, exist_ok=True)
                exists = True
                created += 1
                reason = f'{reason};created' if reason else 'created'
            if (not exists) and (not reason):
                reason = 'missing'

            rows.append(
                ApiPathRow(
                    api=api,
                    label=label,
                    raw_parts=raw_parts,
                    normalized_parts=normalized_parts,
                    raw_relative_path=raw_relative_path,
                    normalized_relative_path=normalized_relative_path,
                    exists=exists,
                    reason=reason,
                )
            )
    return rows, created


def _write_outputs(rows: list[ApiPathRow], output_csv: Path, output_json: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                'api',
                'label',
                'raw_relative_path',
                'normalized_relative_path',
                'exists',
                'reason',
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    'api': row.api,
                    'label': row.label,
                    'raw_relative_path': row.raw_relative_path,
                    'normalized_relative_path': row.normalized_relative_path,
                    'exists': '1' if row.exists else '0',
                    'reason': row.reason,
                }
            )

    payload = {
        'api_map': [
            {
                'api': row.api,
                'label': row.label,
                'relative_path': row.normalized_relative_path,
                'exists': row.exists,
                'reason': row.reason,
            }
            for row in rows
        ]
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _ensure_meta_dirs(root: Path) -> list[str]:
    created: list[str] = []
    for rel in ('_meta/checkpoints', '_meta/manifests', '_meta/qa'):
        p = root / rel
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created.append(str(p))
    return created


def main() -> int:
    args = _parse_args()
    root = Path(args.root).expanduser().resolve()
    catalog = Path(args.catalog).expanduser().resolve()
    output_csv = Path(args.output_csv).expanduser().resolve()
    output_json = Path(args.output_json).expanduser().resolve()

    rows, created_dirs = _collect_rows(root, catalog, create_missing=bool(args.create_missing))
    _write_outputs(rows, output_csv, output_json)

    created_meta = _ensure_meta_dirs(root) if bool(args.create_meta) else []

    total = len(rows)
    exists = sum(1 for row in rows if row.exists)
    normalized = sum(1 for row in rows if row.raw_relative_path != row.normalized_relative_path)
    missing = total - exists

    print(f'root={root}')
    print(f'catalog={catalog}')
    print(f'total_api={total}')
    print(f'exists={exists}')
    print(f'missing={missing}')
    print(f'normalized_path_count={normalized}')
    print(f'created_dirs={created_dirs}')
    print(f'created_meta_dirs={len(created_meta)}')
    print(f'output_csv={output_csv}')
    print(f'output_json={output_json}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
