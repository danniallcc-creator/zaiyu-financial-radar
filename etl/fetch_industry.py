#!/usr/bin/env python3
"""
fetch_industry.py — 抓取同行业对比数据

从东财 datacenter API 获取同行业公司的关键财务指标用于横向对比。
输出: data/industry/{code}_peers.json

用法:
    python etl/fetch_industry.py           # 处理所有已有数据
    python etl/fetch_industry.py 600519    # 处理指定股票
"""

import json
import os
import sys
import subprocess
import time
import glob
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
EASTMONEY_DIR = os.path.join(DATA_DIR, 'eastmoney')
INDUSTRY_DIR = os.path.join(DATA_DIR, 'industry')
OUTPUT_DIR = INDUSTRY_DIR

TOKEN = '894050c76af8597a853f5b408b759f5d'
DATACENTER_BASE = 'https://datacenter.eastmoney.com/securities/api/data/get'

# 过滤 B 股 (200xxx, 900xxx)
def is_ashare(secucode):
    """判断是否 A 股 (排除 B 股)"""
    code = secucode.split('.')[0]
    return not (code.startswith('200') or code.startswith('900'))


# ─────────────────────────────────────────────────────────────────────────────
# HTTP 工具 (使用 curl 绕过 Mac Python SSL 问题)
# ─────────────────────────────────────────────────────────────────────────────

def curl_json(url, timeout=15):
    """用 curl 请求 JSON，绕过 Mac Python SSL 证书问题"""
    try:
        result = subprocess.run(
            ['curl', '-s', '-f', '--max-time', str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 5
        )
        if result.returncode != 0:
            print(f"    curl 失败: {url[:80]}...")
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        print(f"    请求异常: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 东财 datacenter API
# ─────────────────────────────────────────────────────────────────────────────

def get_industry_code(secucode):
    """从 ORG_BASICINFO 获取股票的东财行业分类 (EM2016)"""
    url = (
        f'{DATACENTER_BASE}'
        f'?type=RPT_F10_ORG_BASICINFO'
        f'&sty=SECUCODE,SECURITY_NAME_ABBR,EM2016'
        f'&filter=(SECUCODE=%22{secucode}%22)'
        f'&p=1&ps=1&token={TOKEN}'
    )
    data = curl_json(url)
    if not data or not data.get('success'):
        return None
    items = (data.get('result') or {}).get('data', [])
    return items[0].get('EM2016') if items else None


def get_industry_peers(em2016_industry):
    """获取同行业所有 A 股代码"""
    import urllib.parse
    encoded_industry = urllib.parse.quote(em2016_industry)
    url = (
        f'{DATACENTER_BASE}'
        f'?type=RPT_F10_ORG_BASICINFO'
        f'&sty=SECUCODE,SECURITY_NAME_ABBR'
        f'&filter=(EM2016=%22{encoded_industry}%22)'
        f'&p=1&ps=100&sr=1&st=SECUCODE&token={TOKEN}'
    )
    data = curl_json(url)
    if not data or not data.get('success'):
        return []
    items = (data.get('result') or {}).get('data', [])
    return [
        {'secucode': it['SECUCODE'], 'name': it['SECURITY_NAME_ABBR']}
        for it in items
        if is_ashare(it['SECUCODE'])
    ]


def batch_fetch_financials(secucodes):
    """批量获取最新年度财务指标 (MAINFINADATA), 分批处理避免 URL 过长"""
    if not secucodes:
        return {}

    result = {}
    # 分批处理, 每批 10 只
    chunk_size = 10
    for i in range(0, len(secucodes), chunk_size):
        chunk = secucodes[i:i + chunk_size]
        codes_str = ','.join(f'%22{c}%22' for c in chunk)
        filter_str = f'(SECUCODE+in+({codes_str}))'

        fields = (
            'SECUCODE,SECURITY_NAME_ABBR,REPORT_DATE,'
            'EPSJB,BPS,ROEJQ,XSJLL,MGJYXJJE,'
            'PARENTNETPROFIT,OPERATE_INCOME_PK'
        )

        url = (
            f'{DATACENTER_BASE}'
            f'?type=RPT_F10_FINANCE_MAINFINADATA'
            f'&sty={fields}'
            f'&filter={filter_str}'
            f'&p=1&ps=100&sr=-1&st=REPORT_DATE&token={TOKEN}'
        )
        data = curl_json(url, timeout=30)
        if not data or not data.get('success'):
            print(f"    批次 {i//chunk_size + 1} 请求失败")
            continue

        items = (data.get('result') or {}).get('data', [])
        for it in items:
            sc = it['SECUCODE']
            # 只保留最新一期 (API 按 REPORT_DATE 降序)
            if sc not in result:
                result[sc] = it

        time.sleep(0.3)

    return result


def batch_fetch_quotes(secucodes):
    """
    批量获取实时行情 (PE/PB/市值)
    注意: push2 API 在本地 curl 环境返回空，仅在 Cloudflare Workers 中可用。
    本地 ETL 返回空字典，前端可通过 Worker API 补充实时数据。
    """
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def process_stock(code):
    """为单只股票生成行业对比数据"""
    print(f"\n{'='*60}")
    print(f"处理: {code}")

    # 1. 读取已有的 eastmoney 数据获取行业信息
    em_file = os.path.join(EASTMONEY_DIR, f'{code}.json')
    if not os.path.exists(em_file):
        print(f"  跳过: 无 eastmoney 数据文件")
        return None

    with open(em_file) as f:
        em_data = json.load(f)

    # 获取 SECUCODE
    profile = em_data.get('profile', {})
    secucode = profile.get('secucode', '')
    if not secucode:
        # 从代码推断
        if code.startswith('6'):
            secucode = f'{code}.SH'
        else:
            secucode = f'{code}.SZ'

    # 2. 获取行业分类
    industry_name = get_industry_code(secucode)
    if not industry_name:
        # 回退到 profile 中的行业
        industry_name = profile.get('industry', '')
    if not industry_name:
        print(f"  无法获取行业分类")
        return None

    print(f"  行业: {industry_name}")

    # 3. 获取同行业所有股票
    peers = get_industry_peers(industry_name)
    if not peers:
        print(f"  未找到同行业股票")
        return None

    # 限制最多 30 只 (按代码排序)
    peers = peers[:30]
    print(f"  同行业 A 股: {len(peers)} 只")

    # 4. 批量获取财务数据
    peer_codes = [p['secucode'] for p in peers]
    print(f"  获取财务数据...")
    financials = batch_fetch_financials(peer_codes)
    print(f"  获取到 {len(financials)} 只股票的财务数据")

    # 5. 组装对比数据 (仅使用财务数据，实时行情需前端通过 Worker 获取)
    compare_items = []
    for peer in peers:
        sc = peer['secucode']
        fin = financials.get(sc, {})
        stock_code = sc.split('.')[0]

        item = {
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
        }
        compare_items.append(item)

    # 按营收排序 (高到低)
    compare_items.sort(key=lambda x: -(x['revenue'] or 0))

    result = {
        'code': code,
        'industry': industry_name,
        'peerCount': len(compare_items),
        'fetchTime': datetime.now().isoformat(),
        'peers': compare_items,
    }

    # 7. 保存
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_file = os.path.join(OUTPUT_DIR, f'{code}_peers.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  已保存: {out_file}")
    print(f"  行业: {industry_name}, 共 {len(compare_items)} 家公司")
    return result


def main():
    codes = sys.argv[1:]
    if not codes:
        # 处理所有已有 eastmoney 数据的股票
        pattern = os.path.join(EASTMONEY_DIR, '*.json')
        codes = [
            os.path.basename(f).replace('.json', '')
            for f in sorted(glob.glob(pattern))
        ]
        print(f"找到 {len(codes)} 只股票需要处理")

    results = []
    for code in codes:
        try:
            r = process_stock(code)
            if r:
                results.append(r)
        except Exception as e:
            print(f"  错误: {e}")
        time.sleep(0.5)

    print(f"\n完成! 成功处理 {len(results)}/{len(codes)} 只股票")


if __name__ == '__main__':
    main()
