from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SOURCE = '/Volumes/dockcase2tb/tushare_doc2_leaf_api.csv'
DEFAULT_OUTPUT_CSV = 'docs/tushare_api_history_classification.csv'
DEFAULT_OUTPUT_MD = 'docs/tushare_api_history_classification.md'


REALTIME_KEYWORDS = (
    '实时',
    '当日',
)
REALTIME_API_PREFIX = (
    'rt_',
    'realtime_',
)


MASTER_KEYWORDS = (
    '列表',
    '基本信息',
    '合约信息',
    '分类',
    '映射',
    '名录',
    '管理人',
    '经理',
    '标的',
)


HISTORICAL_HINTS = (
    '历史',
    '日线',
    '周线',
    '月线',
    '分钟',
    'tick',
    '成交',
    '指标',
    '明细',
    '统计',
    '持仓',
    '财务',
    '利润表',
    '资产负债表',
    '现金流量表',
    '分红',
    '回购',
    '解禁',
    '资金流向',
    '涨跌停',
    '龙虎榜',
    '净值',
    '规模',
    '收益率',
    '利率',
    'gdp',
    'cpi',
    'ppi',
    'pmi',
    '社融',
    '公告',
    '新闻',
    '问答',
    '票房',
    '备案',
    '行情',
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Classify Tushare APIs into historical/master/realtime groups.')
    parser.add_argument('--source', default=DEFAULT_SOURCE)
    parser.add_argument('--output-csv', default=DEFAULT_OUTPUT_CSV)
    parser.add_argument('--output-md', default=DEFAULT_OUTPUT_MD)
    return parser.parse_args()


def _text(value: str) -> str:
    return str(value or '').strip()


def _path_from_row(row: dict[str, str]) -> str:
    parts: list[str] = []
    for key in ('level1', 'level2', 'level3', 'level4'):
        value = _text(row.get(key, ''))
        if value:
            parts.append(value)
    return '/'.join(parts)


def _is_realtime(api: str, label: str) -> bool:
    api_lower = api.lower()
    if any(api_lower.startswith(prefix) for prefix in REALTIME_API_PREFIX):
        return True
    if any(keyword in label for keyword in REALTIME_KEYWORDS):
        return True
    return False


def _is_master(label: str, path: str) -> bool:
    if any(keyword in label for keyword in MASTER_KEYWORDS):
        # If label contains strong historical hints, it should still be historical.
        lowered = label.lower()
        if any(hint in lowered for hint in HISTORICAL_HINTS):
            return False
        return True

    if path.startswith('股票数据/基础数据') and ('日历' not in label):
        return True
    return False


def _is_historical(label: str, path: str, api: str) -> bool:
    if _is_realtime(api, label):
        return False
    if _is_master(label, path):
        return False
    lowered = label.lower()
    if any(hint in lowered for hint in HISTORICAL_HINTS):
        return True
    if path.startswith('股票数据/财务数据'):
        return True
    if path.startswith('股票数据/参考数据'):
        return True
    if path.startswith('股票数据/特色数据'):
        return True
    if path.startswith('股票数据/资金流向数据'):
        return True
    if path.startswith('股票数据/打板专题数据'):
        return True
    if path.startswith('指数专题'):
        return True
    if path.startswith('公募基金'):
        return True
    if path.startswith('期货数据'):
        return True
    if path.startswith('期权数据'):
        return True
    if path.startswith('债券专题'):
        return True
    if path.startswith('外汇数据'):
        return True
    if path.startswith('港股数据'):
        return True
    if path.startswith('美股数据'):
        return True
    if path.startswith('宏观经济'):
        return True
    if path.startswith('现货数据'):
        return True
    if path.startswith('大模型语料专题数据'):
        return True
    return False


def classify(api: str, label: str, path: str) -> tuple[str, str]:
    if _is_realtime(api, label):
        return 'realtime', 'label/api indicates real-time data'
    if _is_master(label, path):
        return 'master', 'label/path indicates dictionary or reference metadata'
    if _is_historical(label, path, api):
        return 'historical', 'time-series/event dataset suitable for local historical storage'
    return 'master', 'fallback to master/reference'


def main() -> int:
    args = _parse_args()
    source = Path(args.source).expanduser().resolve()
    output_csv = Path(args.output_csv).expanduser().resolve()
    output_md = Path(args.output_md).expanduser().resolve()

    items: list[dict[str, str]] = []
    with source.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            api = _text(row.get('api', ''))
            if not api:
                continue
            label = _text(row.get('label', ''))
            path = _path_from_row(row)
            group, reason = classify(api=api, label=label, path=path)
            items.append(
                {
                    'group': group,
                    'api': api,
                    'label': label,
                    'path': path,
                    'reason': reason,
                }
            )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['group', 'api', 'label', 'path', 'reason'])
        writer.writeheader()
        writer.writerows(items)

    grouped: dict[str, list[dict[str, str]]] = {'historical': [], 'master': [], 'realtime': []}
    for item in items:
        grouped.setdefault(item['group'], []).append(item)

    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')
    output_md.parent.mkdir(parents=True, exist_ok=True)
    with output_md.open('w', encoding='utf-8') as handle:
        handle.write('# Tushare 接口历史属性分类\n\n')
        handle.write(f'- 生成时间（UTC）：`{now_utc}`\n')
        handle.write(f'- 数据源：`{source}`\n')
        handle.write(f"- 总接口数：`{len(items)}`\n")
        handle.write(f"- 历史数据：`{len(grouped.get('historical', []))}`\n")
        handle.write(f"- 基础/字典：`{len(grouped.get('master', []))}`\n")
        handle.write(f"- 实时数据：`{len(grouped.get('realtime', []))}`\n\n")

        for group in ('historical', 'master', 'realtime'):
            title = {
                'historical': '历史数据接口（建议优先本地落库）',
                'master': '基础/字典接口（低频缓存即可）',
                'realtime': '实时接口（按需在线调用）',
            }[group]
            handle.write(f'## {title}\n\n')
            handle.write('| API | 名称 | 目录路径 |\n')
            handle.write('|---|---|---|\n')
            for item in grouped.get(group, []):
                label = item['label'].replace('|', '/')
                path = item['path'].replace('|', '/')
                handle.write(f"| `{item['api']}` | {label} | {path} |\n")
            handle.write('\n')

    print(f'total={len(items)}')
    print(f"historical={len(grouped.get('historical', []))}")
    print(f"master={len(grouped.get('master', []))}")
    print(f"realtime={len(grouped.get('realtime', []))}")
    print(f'output_csv={output_csv}')
    print(f'output_md={output_md}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
