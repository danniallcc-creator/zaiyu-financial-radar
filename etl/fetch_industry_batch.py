#!/usr/bin/env python3
"""
fetch_industry_batch.py — 批量生成行业对比数据 (高效版)

优化: 同行业股票共享 peer 列表 + 财务数据, 避免重复请求。
输出: data/industry/{code}_peers.json (每只股票一份)

用法:
    python etl/fetch_industry_batch.py           # 处理所有 338 只
    python etl/fetch_industry_batch.py --resume   # 跳过已有
"""

import json
import os
import sys
import subprocess
import time
import urllib.parse
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
EASTMONEY_DIR = os.path.join(DATA_DIR, 'eastmoney')
INDUSTRY_DIR = os.path.join(DATA_DIR, 'industry')

TOKEN = '894050c76af8597a853f5b408b759f5d'
DC_BASE = 'https://datacenter.eastmoney.com/securities/api/data/get'


def curl_json(url, timeout=15):
    try:
        result = subprocess.run(
            ['curl', '-s', '-f', '--max-time', str(timeout), url,
             '-H', 'User-Agent: Mozilla/5.0'],
            capture_output=True, text=True, timeout=timeout + 5
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


def is_ashare(secucode):
    code = secucode.split('.')[0]
    return not (code.startswith('200') or code.startswith('900'))


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: 收集所有股票的行业信息 (从已有 JSON 文件)
# ─────────────────────────────────────────────────────────────────────────────

def collect_industries():
    """从 eastmoney JSON 文件收集所有股票的行业分类"""
    stocks = {}  # code -> {secucode, name, industry}
    for fname in sorted(os.listdir(EASTMONEY_DIR)):
        if not fname.endswith('.json'):
            continue
        code = fname.replace('.json', '')
        try:
            with open(os.path.join(EASTMONEY_DIR, fname)) as f:
                data = json.load(f)
            profile = data.get('profile', {})
            secucode = profile.get('secucode', '')
            if not secucode:
                secucode = f'{code}.SH' if code.startswith('6') else f'{code}.SZ'
            stocks[code] = {
                'secucode': secucode,
                'name': profile.get('name', ''),
                'industry': profile.get('industry', ''),
            }
        except Exception:
            continue
    return stocks


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: 按行业分组, 每组只查一次 peers + financials
# ─────────────────────────────────────────────────────────────────────────────

def get_industry_peers_cached(industry_name, cache):
    """获取同行业股票列表 (带缓存)"""
    if industry_name in cache:
        return cache[industry_name]

    encoded = urllib.parse.quote(industry_name)
    url = (
        f'{DC_BASE}?type=RPT_F10_ORG_BASICINFO'
        f'&sty=SECUCODE,SECURITY_NAME_ABBR'
        f'&filter=(EM2016=%22{encoded}%22)'
        f'&p=1&ps=100&sr=1&st=SECUCODE&token={TOKEN}'
    )
    data = curl_json(url)
    if not data or not data.get('success'):
        cache[industry_name] = []
        return []

    items = (data.get('result') or {}).get('data', [])
    peers = [
        {'secucode': it['SECUCODE'], 'name': it['SECURITY_NAME_ABBR']}
        for it in items
        if is_ashare(it['SECUCODE'])
    ][:50]  # 最多 50 只

    cache[industry_name] = peers
    time.sleep(0.2)
    return peers


def batch_fetch_financials_cached(secucodes, cache):
    """批量获取最新财务指标 (带缓存, 只查未缓存的)"""
    to_fetch = [sc for sc in secucodes if sc not in cache]
    if not to_fetch:
        return cache

    chunk_size = 10
    for i in range(0, len(to_fetch), chunk_size):
        chunk = to_fetch[i:i + chunk_size]
        codes_str = ','.join(f'%22{c}%22' for c in chunk)
        filter_str = f'(SECUCODE+in+({codes_str}))'

        fields = (
            'SECUCODE,SECURITY_NAME_ABBR,REPORT_DATE,'
            'EPSJB,BPS,ROEJQ,XSJLL,MGJYXJJE,'
            'PARENTNETPROFIT,OPERATE_INCOME_PK'
        )

        url = (
            f'{DC_BASE}?type=RPT_F10_FINANCE_MAINFINADATA'
            f'&sty={fields}'
            f'&filter={filter_str}'
            f'&p=1&ps=100&sr=-1&st=REPORT_DATE&token={TOKEN}'
        )
        data = curl_json(url, timeout=30)
        if data and data.get('success'):
            items = (data.get('result') or {}).get('data', [])
            for it in items:
                sc = it['SECUCODE']
                if sc not in cache:
                    cache[sc] = it

        time.sleep(0.2)

    return cache


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: 为每只股票生成 peers JSON
# ─────────────────────────────────────────────────────────────────────────────

def generate_peer_file(code, industry_name, peers, financials):
    """生成单只股票的行业对比文件"""
    compare_items = []
    for peer in peers:
        sc = peer['secucode']
        fin = financials.get(sc, {})
        stock_code = sc.split('.')[0]

        compare_items.append({
            'code': stock_code,
            'secucode': sc,
            'name': peer['name'],
            'isCurrent': stock_code == code,
            'eps': fin.get('EPSJB'),
            'bps': fin.get('BPS'),
            'roe': fin.get('ROEJQ'),
            'netProfitMargin': fin.get('XSJLL'),
            'revenue': fin.get('OPERATE_INCOME_PK'),
            'netProfit': fin.get('PARENTNETPROFIT'),
            'ocfPerShare': fin.get('MGJYXJJE'),
            'reportDate': fin.get('REPORT_DATE', '')[:10] if fin.get('REPORT_DATE') else '',
        })

    # 按营收降序
    compare_items.sort(key=lambda x: -(x['revenue'] or 0))

    result = {
        'code': code,
        'industry': industry_name,
        'peerCount': len(compare_items),
        'fetchTime': datetime.now().isoformat(),
        'peers': compare_items,
    }

    out_file = os.path.join(INDUSTRY_DIR, f'{code}_peers.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def main():
    resume = '--resume' in sys.argv
    os.makedirs(INDUSTRY_DIR, exist_ok=True)

    # Step 1: 收集所有股票
    print("Step 1: 收集股票行业信息...")
    stocks = collect_industries()
    print(f"  共 {len(stocks)} 只股票")

    # 按行业分组
    industry_groups = {}
    for code, info in stocks.items():
        ind = info['industry']
        if not ind:
            continue
        if ind not in industry_groups:
            industry_groups[ind] = []
        industry_groups[ind].append(code)

    print(f"  共 {len(industry_groups)} 个行业")

    # 检查 resume
    if resume:
        existing = set(f.replace('_peers.json', '') for f in os.listdir(INDUSTRY_DIR) if f.endswith('_peers.json'))
        before = len(stocks)
        stocks = {c: info for c, info in stocks.items() if c not in existing}
        print(f"  Resume: 跳过 {len(existing)} 只已有, 剩余 {len(stocks)} 只")

    # Step 2 & 3: 按行业批量处理
    peer_cache = {}
    fin_cache = {}
    done = 0
    failed = 0
    total = len(stocks)

    # 按行业排序处理, 同行业连续处理以最大化缓存命中
    stocks_by_industry = {}
    for code, info in stocks.items():
        ind = info['industry']
        if ind not in stocks_by_industry:
            stocks_by_industry[ind] = []
        stocks_by_industry[ind].append(code)

    processed = 0
    for ind_name, codes in sorted(stocks_by_industry.items(), key=lambda x: x[0] or ''):
        if not ind_name:
            continue

        # 获取同行业 peers
        peers = get_industry_peers_cached(ind_name, peer_cache)
        if not peers:
            failed += len(codes)
            processed += len(codes)
            continue

        # 获取财务数据
        peer_codes = [p['secucode'] for p in peers]
        batch_fetch_financials_cached(peer_codes, fin_cache)

        # 为每只股票生成文件
        for code in codes:
            processed += 1
            try:
                result = generate_peer_file(code, ind_name, peers, fin_cache)
                done += 1
            except Exception as e:
                print(f"  {code} 失败: {e}")
                failed += 1

            if processed % 50 == 0:
                print(f"  进度: {processed}/{total} (成功:{done} 失败:{failed} 行业缓存:{len(peer_cache)})")

    print(f"\n{'='*60}")
    print(f"完成! 总计:{total} 成功:{done} 失败:{failed}")
    print(f"行业缓存: {len(peer_cache)} 个行业, 财务缓存: {len(fin_cache)} 只股票")


if __name__ == '__main__':
    main()
