#!/usr/bin/env python3
"""
batch_refresh_quotes.py — 用腾讯财经 API 批量刷新所有个股实时行情

数据源: https://qt.gtimg.cn (腾讯财经行情接口)
输出:   更新 data/eastmoney/{code}.json 中的 quote 字段

用法:
    python etl/batch_refresh_quotes.py           # 刷新全部
    python etl/batch_refresh_quotes.py 600519    # 刷新指定股票
"""

import json
import os
import sys
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
EASTMONEY_DIR = os.path.join(DATA_DIR, 'eastmoney')

BATCH_SIZE = 25      # 每批股票数 (腾讯API单次最多约30只)
MAX_WORKERS = 5      # 并发数


def code_to_tencent(code: str) -> str:
    """股票代码 → 腾讯格式 (sh600519 / sz002594)"""
    if code.startswith(('6', '688', '689')):
        return f'sh{code}'
    return f'sz{code}'


def fetch_tencent_batch(codes: list) -> dict:
    """
    批量获取腾讯行情数据
    返回: {code: quote_dict}
    """
    tc_codes = ','.join(code_to_tencent(c) for c in codes)
    url = f'https://qt.gtimg.cn/q={tc_codes}'

    for attempt in range(3):
        try:
            r = subprocess.run(
                ['curl', '-s', '-f', '--max-time', '15', url,
                 '-H', 'Referer: https://finance.qq.com'],
                capture_output=True, timeout=20
            )
            if r.returncode != 0:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return {}
            # 腾讯行情 API 返回 GBK 编码
            raw_text = r.stdout.decode('gbk', errors='replace')
            break
        except Exception:
            if attempt < 2:
                time.sleep(1)
                continue
            return {}

    results = {}
    for line in raw_text.split(';'):
        line = line.strip()
        if not line or '"' not in line:
            continue
        try:
            content = line.split('"')[1]
            fields = content.split('~')
            if len(fields) < 47:
                continue

            code = fields[2]
            price = _float(fields[3])
            if not price or price <= 0:
                continue

            results[code] = {
                'code': code,
                'name': fields[1],
                'price': price,
                'change': _float(fields[31]),
                'changePct': _float(fields[32]),
                'open': _float(fields[5]),
                'high': _float(fields[33]),
                'low': _float(fields[34]),
                'prevClose': _float(fields[4]),
                'volume': _int(fields[6]),                      # 成交量(手)
                'amount': _float(fields[37]),                    # 成交额(元)
                'totalMV': (_float(fields[45]) or 0) * 1e8,     # 总市值(亿→元)
                'circMV': (_float(fields[44]) or 0) * 1e8,      # 流通市值(亿→元)
                'pe': _float(fields[39]),
                'pb': _float(fields[46]),
                'turnover': _float(fields[38]),                  # 换手率(%)
                'fetchTime': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            }
        except (IndexError, ValueError):
            continue

    return results


def _float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _int(val):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def process_batch(batch_codes: list) -> tuple:
    """处理一批股票: 获取行情 → 更新 JSON 文件"""
    quotes = fetch_tencent_batch(batch_codes)
    updated = 0
    failed = 0

    for code in batch_codes:
        quote = quotes.get(code)
        if not quote:
            failed += 1
            continue

        json_path = os.path.join(EASTMONEY_DIR, f'{code}.json')
        if not os.path.exists(json_path):
            failed += 1
            continue

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            data['quote'] = quote
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            updated += 1
        except Exception:
            failed += 1

    return updated, failed


def main():
    # 确定要处理的股票
    if len(sys.argv) > 1:
        codes = sys.argv[1:]
    else:
        codes = []
        for fname in sorted(os.listdir(EASTMONEY_DIR)):
            if fname.endswith('.json'):
                codes.append(fname[:-5])

    total = len(codes)
    print(f'共 {total} 只股票待刷新行情\n')

    # 分批
    batches = []
    for i in range(0, total, BATCH_SIZE):
        batches.append(codes[i:i + BATCH_SIZE])

    total_batches = len(batches)
    total_updated = 0
    total_failed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_batch, batch): i for i, batch in enumerate(batches)}
        for future in as_completed(futures):
            updated, failed = future.result()
            total_updated += updated
            total_failed += failed

            completed = sum(1 for f in futures if f.done())
            if completed % 5 == 0 or completed == total_batches:
                pct = completed / total_batches * 100
                print(f'  进度: {completed}/{total_batches} 批 ({pct:.0f}%) '
                      f'更新={total_updated} 失败={total_failed}')

            time.sleep(0.15)

    print(f'\n{"="*60}')
    print(f'完成: 更新={total_updated}, 失败={total_failed}, 共={total}')
    print(f'时间: {datetime.now().strftime("%H:%M:%S")}')


if __name__ == '__main__':
    main()
