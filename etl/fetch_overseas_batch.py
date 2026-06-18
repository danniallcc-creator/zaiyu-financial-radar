#!/usr/bin/env python3
"""
fetch_overseas_batch.py — 批量抓取所有 A 股主营构成地区分拆数据

数据源: Eastmoney datacenter v1 API — RPT_F10_FN_MAINOP (MAINOP_TYPE=3)
输出:   data/overseas_raw/all_mainop.json

用法:
    python etl/fetch_overseas_batch.py             # 全量抓取
    python etl/fetch_overseas_batch.py --resume    # 断点续传
"""

import json
import os
import sys
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ─── 常量 ───────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
RAW_DIR = os.path.join(DATA_DIR, 'overseas_raw')
CLASSIFY_FILE = os.path.join(DATA_DIR, 'industry_classify.json')
OUTPUT_FILE = os.path.join(RAW_DIR, 'all_mainop.json')

TOKEN = '894050c76af8597a853f5b408b759f5d'
DC_V1 = 'https://datacenter.eastmoney.com/securities/api/data/v1/get'

BATCH_SIZE = 20      # 每批股票数
MAX_WORKERS = 10     # 并发数
PAGE_SIZE = 500      # 每页条数
MAX_PAGES = 10       # 最大翻页数

os.makedirs(RAW_DIR, exist_ok=True)


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def secucode(code: str) -> str:
    """股票代码 → SECUCODE 格式"""
    if code.startswith('6'):
        return f'{code}.SH'
    elif code.startswith(('0', '3')):
        return f'{code}.SZ'
    elif code.startswith(('8', '4')):
        return f'{code}.BJ'
    return f'{code}.SH'


def curl_json(url: str, timeout: int = 25) -> dict:
    """通过 curl 获取 JSON"""
    for attempt in range(3):
        try:
            r = subprocess.run(
                ['curl', '-s', '-f', '--max-time', str(timeout), url,
                 '-H', 'User-Agent: Mozilla/5.0'],
                capture_output=True, text=True, timeout=timeout + 5
            )
            if r.returncode != 0:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return {}
            return json.loads(r.stdout)
        except Exception:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {}
    return {}


def fetch_batch_chunk(secucodes: list) -> list:
    """
    抓取一批股票的地区分拆数据 (MAINOP_TYPE=3)
    支持自动翻页
    """
    # %22 is URL-encoded double quote, required by Eastmoney API
    codes_str = ','.join(f'%22{sc}%22' for sc in secucodes)
    all_items = []

    for page in range(1, MAX_PAGES + 1):
        url = (
            f'{DC_V1}?reportName=RPT_F10_FN_MAINOP'
            f'&columns=SECUCODE,SECURITY_NAME_ABBR,REPORT_DATE,MAINOP_TYPE,'
            f'ITEM_NAME,MAIN_BUSINESS_INCOME,MBI_RATIO,'
            f'MAIN_BUSINESS_COST,MBC_RATIO,'
            f'MAIN_BUSINESS_RPOFIT,MBR_RATIO,'
            f'GROSS_RPOFIT_RATIO,RANK'
            f'&filter=(SECUCODE+in+({codes_str}))(MAINOP_TYPE=%223%22)'
            f'&pageNumber={page}&pageSize={PAGE_SIZE}'
            f'&sortTypes=-1,-1&sortColumns=REPORT_DATE,RANK'
            f'&source=HSF10&client=PC&token={TOKEN}'
        )
        resp = curl_json(url)
        result = resp.get('result') or {}
        data = result.get('data') or []
        if not data:
            break

        all_items.extend(data)

        # 检查是否还有下一页
        pages = result.get('pages', 1)
        if page >= pages:
            break

        time.sleep(0.2)

    return all_items


# ─── 主流程 ──────────────────────────────────────────────────────────────────

def main():
    resume = '--resume' in sys.argv

    # 读取已分类股票列表 + eastmoney profile 中的股票（HS300 等）
    all_codes = set()

    if os.path.exists(CLASSIFY_FILE):
        with open(CLASSIFY_FILE, 'r', encoding='utf-8') as f:
            stocks = json.load(f)
        for s in stocks:
            all_codes.add(s['code'])
        print(f'  classify: {len(stocks)} 只')

    # 补充 eastmoney profile 中的股票（HS300 等未出现在 classify 中的）
    em_dir = os.path.join(DATA_DIR, 'eastmoney')
    if os.path.isdir(em_dir):
        added = 0
        for fname in os.listdir(em_dir):
            if fname.endswith('.json'):
                code = fname[:-5]
                if code not in all_codes:
                    all_codes.add(code)
                    added += 1
        if added:
            print(f'  eastmoney 补充: {added} 只')

    print(f'共 {len(all_codes)} 只股票')

    # 检查断点续传
    existing = {}
    if resume and os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            existing = json.load(f)
        existing_codes = set(item.get('SECUCODE', '') for item in existing.get('items', []))
        print(f'已有 {len(existing_codes)} 只股票数据，断点续传')
    else:
        existing_codes = set()

    # 生成 SECUCODE 列表，过滤已抓取的
    code_list = []
    for code in sorted(all_codes):
        sc = secucode(code)
        if sc not in existing_codes:
            code_list.append(sc)

    if not code_list:
        print('所有股票数据已存在，无需重新抓取')
        return

    print(f'待抓取: {len(code_list)} 只, 分为 {(len(code_list) + BATCH_SIZE - 1) // BATCH_SIZE} 批')

    # 分批
    chunks = []
    for i in range(0, len(code_list), BATCH_SIZE):
        chunks.append(code_list[i:i + BATCH_SIZE])

    # 并发抓取
    all_items = list(existing.get('items', []))
    done = 0
    failed = 0

    def process_chunk(chunk):
        nonlocal done, failed
        try:
            items = fetch_batch_chunk(chunk)
            done += len(chunk)
            if items:
                return items
            else:
                failed += len(chunk)
                return []
        except Exception as e:
            failed += len(chunk)
            return []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_chunk, chunk): i for i, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            items = future.result()
            if items:
                all_items.extend(items)

            # 进度报告
            total_batches = len(chunks)
            completed = sum(1 for f in futures if f.done())
            if completed % 10 == 0 or completed == total_batches:
                pct = completed / total_batches * 100
                print(f'  进度: {completed}/{total_batches} 批 ({pct:.0f}%)')

    # 保存
    output = {
        'generatedAt': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'stockCount': len(set(it.get('SECUCODE', '') for it in all_items)),
        'itemCount': len(all_items),
        'items': all_items,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False)

    unique_stocks = output['stockCount']
    print(f'\n{"="*60}')
    print(f'完成! 共 {unique_stocks} 只股票, {len(all_items)} 条记录')
    print(f'保存至: {OUTPUT_FILE}')
    print(f'文件大小: {os.path.getsize(OUTPUT_FILE) / 1024 / 1024:.1f} MB')


if __name__ == '__main__':
    main()
