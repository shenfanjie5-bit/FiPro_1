from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import html as html_lib
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request


INPUT_DEFAULT = Path('/Volumes/dockcase2tb/tushare_doc2_leaf_api.csv')


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Build Tushare API catalog from doc leaf csv (api/label + rate/notes).'
    )
    parser.add_argument('--input', default=str(INPUT_DEFAULT))
    parser.add_argument('--output-csv', default='docs/tushare_api_catalog.csv')
    parser.add_argument('--output-md', default='docs/tushare_api_catalog.md')
    parser.add_argument('--timeout', type=float, default=12.0)
    return parser.parse_args()


def _fetch_html(url: str, timeout: float, retries: int = 2) -> str:
    last_error = ''
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url=url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; FiPro_1/1.0; +https://tushare.pro/)'},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read()
            return raw.decode('utf-8', errors='ignore')
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(0.25 * (attempt + 1))

    # urllib occasionally hits SSL EOF on this host; fallback to curl for robustness.
    try:
        proc = subprocess.run(
            ['curl', '--http1.1', '-L', '-sS', '--max-time', str(int(max(3.0, timeout))), url],
            capture_output=True,
            text=False,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f'curl fallback failed to start: {exc}; urllib_error={last_error}') from exc

    if proc.returncode != 0:
        stderr = proc.stderr.decode('utf-8', errors='ignore').strip()
        raise RuntimeError(f'curl fallback failed: code={proc.returncode} stderr={stderr}; urllib_error={last_error}')
    payload = proc.stdout.decode('utf-8', errors='ignore')
    if not payload.strip():
        raise RuntimeError(f'curl fallback returned empty response; urllib_error={last_error}')
    return payload


def _html_to_lines(payload: str) -> list[str]:
    text = re.sub(r'<script[\s\S]*?</script>', '\n', payload, flags=re.IGNORECASE)
    text = re.sub(r'<style[\s\S]*?</style>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<(br|p|li|tr|h\d|div)[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|li|tr|h\d|div|td|th)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html_lib.unescape(text)
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r'\s+', ' ', raw).strip()
        if line:
            lines.append(line)
    return lines


def _extract_permission_lines(lines: list[str]) -> list[str]:
    keywords = ('权限', '积分', '调取', '每分钟', '限频', '频率', '不限频', '不限制')
    out: list[str] = []
    for line in lines:
        if any(keyword in line for keyword in keywords):
            if line not in out:
                out.append(line)
    return out


def _extract_rate_limit(note: str, fallback_lines: list[str]) -> str:
    patterns = (
        r'每分钟[^。；\n]*?次',
        r'每日[^。；\n]*?次',
        r'每天[^。；\n]*?次',
        r'(?:不限频|不限制|无限制)',
    )
    search_texts = [note] + fallback_lines
    for text in search_texts:
        candidate = text.strip()
        if not candidate:
            continue
        for pattern in patterns:
            found = re.search(pattern, candidate)
            if found:
                return found.group(0).strip()
    return '未明确（建议按账户权限实测）'


def _pick_note(permission_lines: list[str], fetch_error: str, csv_error: str, url: str) -> str:
    if permission_lines:
        base = permission_lines[0]
        if csv_error and csv_error not in base:
            return f'{base}；接口状态提示：{csv_error}'
        return base
    if csv_error:
        return f'接口状态提示：{csv_error}'
    if fetch_error:
        return f'文档抓取失败：{fetch_error}'
    if url:
        return '未提取到权限说明，可打开文档页人工确认。'
    return '缺少文档链接。'


def _normalize_text(value: str, limit: int = 220) -> str:
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if len(text) <= limit:
        return text
    return f'{text[:limit - 1]}...'


def build_catalog(input_path: Path, output_csv: Path, output_md: Path, timeout: float) -> tuple[int, int]:
    rows_out: list[dict[str, str]] = []
    total_with_api = 0

    with input_path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            api = _normalize_text(row.get('api', ''), limit=120)
            if not api:
                continue
            total_with_api += 1
            label = _normalize_text(row.get('label', ''), limit=200)
            url = _normalize_text(row.get('url', ''), limit=240)
            csv_error = _normalize_text(row.get('error', ''), limit=200)

            permission_lines: list[str] = []
            fetch_error = ''
            if url:
                try:
                    html_payload = _fetch_html(url, timeout=timeout)
                    lines = _html_to_lines(html_payload)
                    permission_lines = _extract_permission_lines(lines)
                except Exception as exc:  # noqa: BLE001
                    fetch_error = str(exc)

            note = _pick_note(permission_lines, fetch_error=fetch_error, csv_error=csv_error, url=url)
            rate_limit = _extract_rate_limit(note, permission_lines)

            rows_out.append(
                {
                    'api_name': label or '(未命名)',
                    'api': api,
                    'rate_limit': _normalize_text(rate_limit, limit=160),
                    'note': _normalize_text(note, limit=260),
                }
            )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['api_name', 'api', 'rate_limit', 'note'])
        writer.writeheader()
        writer.writerows(rows_out)

    generated_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')
    output_md.parent.mkdir(parents=True, exist_ok=True)
    with output_md.open('w', encoding='utf-8') as handle:
        handle.write('# Tushare API 清单（自动生成）\n\n')
        handle.write(f'- 生成时间（UTC）：`{generated_at}`\n')
        handle.write(f'- 数据源：`{input_path}`\n')
        handle.write(f'- 接口总数（api 非空）：`{total_with_api}`\n\n')
        handle.write('| API名称 | API接口 | 访问频率限制 | 补充说明 |\n')
        handle.write('|---|---|---|---|\n')
        for row in rows_out:
            api_name = row['api_name'].replace('|', '/')
            api = row['api'].replace('|', '/')
            rate_limit = row['rate_limit'].replace('|', '/')
            note = row['note'].replace('|', '/')
            handle.write(f'| {api_name} | `{api}` | {rate_limit} | {note} |\n')

    return total_with_api, len(rows_out)


def main() -> int:
    args = _parse_args()
    input_path = Path(args.input).expanduser().resolve()
    output_csv = Path(args.output_csv).expanduser().resolve()
    output_md = Path(args.output_md).expanduser().resolve()
    total_with_api, written = build_catalog(
        input_path=input_path,
        output_csv=output_csv,
        output_md=output_md,
        timeout=max(3.0, float(args.timeout)),
    )
    print(f'api_non_empty={total_with_api}')
    print(f'written={written}')
    print(f'output_csv={output_csv}')
    print(f'output_md={output_md}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
