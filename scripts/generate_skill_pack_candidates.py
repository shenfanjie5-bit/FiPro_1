from __future__ import annotations

import argparse
import json

from app.backtest.candidates import generate_skill_pack_candidates


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Generate candidate skill pack versions from calibration profile.')
    parser.add_argument('--skill-pack-id', default='cn_a_core')
    parser.add_argument('--base-version', default='champion')
    parser.add_argument('--calibration-version', default='')
    parser.add_argument('--max-candidates', type=int, default=4)
    parser.add_argument('--author', default='auto_calibration')
    parser.add_argument('--param-id', action='append', default=[])
    parser.add_argument('--data-combo-only', action='store_true')
    parser.add_argument('--enable-data-combo-search', action='store_true')
    parser.add_argument('--max-endpoint-toggles', type=int, default=1)
    parser.add_argument('--endpoint', action='append', default=[])
    parser.add_argument('--dry-run', action='store_true')
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    result = generate_skill_pack_candidates(
        skill_pack_id=str(args.skill_pack_id),
        base_version=str(args.base_version),
        calibration_version=(str(args.calibration_version).strip() or None),
        max_candidates=max(1, int(args.max_candidates)),
        author=str(args.author),
        param_ids=[str(item).strip() for item in args.param_id if str(item).strip()],
        include_param_search=not bool(args.data_combo_only),
        enable_data_combo_search=bool(args.enable_data_combo_search),
        max_endpoint_toggles=max(1, min(3, int(args.max_endpoint_toggles))),
        endpoint_allowlist=[str(item).strip() for item in args.endpoint if str(item).strip()],
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
