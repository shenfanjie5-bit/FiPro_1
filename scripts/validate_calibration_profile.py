from __future__ import annotations

import argparse
import json

from app.backtest.calibration import load_calibration_profile


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Validate and print FiPro calibration profile summary.')
    parser.add_argument('--skill-pack-id', default='cn_a_core')
    parser.add_argument('--version', default='0.1.0')
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    payload = load_calibration_profile(skill_pack_id=args.skill_pack_id, version=args.version)
    print(json.dumps(payload.get('_summary', {}), ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
