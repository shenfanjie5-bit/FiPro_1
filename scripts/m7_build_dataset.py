#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.eval.m7_dataset import load_and_build_m7_offline_dataset, render_m7_dataset_markdown


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description='Build M7 stratified offline replay dataset artifact.')
    parser.add_argument('--lookback-days', type=int, default=30)
    parser.add_argument('--output-json', default='monitoring/dashboards/m7_offline_dataset.json')
    parser.add_argument('--output-md', default='monitoring/dashboards/m7_offline_dataset.md')
    parser.add_argument('--no-dedupe-latest', action='store_true')
    args = parser.parse_args()

    report = load_and_build_m7_offline_dataset(
        lookback_days=max(1, int(args.lookback_days)),
        dedupe_latest=not bool(args.no_dedupe_latest),
    )
    markdown = render_m7_dataset_markdown(report)

    output_json_path = Path(args.output_json)
    output_md_path = Path(args.output_md)
    _write_text(output_json_path, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    _write_text(output_md_path, markdown)

    print(f'generated_json={output_json_path}')
    print(f'generated_markdown={output_md_path}')
    print(f"dataset_version={report.get('dataset_version', '')}")
    print(f"effective_sample_size={report.get('effective_sample_size', 0)}")


if __name__ == '__main__':
    main()
