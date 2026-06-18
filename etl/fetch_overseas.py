#!/usr/bin/env python3
"""
fetch_overseas.py — 批量抓取 A 股出海/海外市场数据

数据源:
  - RPT_F10_FN_MAINOP: 主营构成（按产品/地区分拆，含收入、成本、利润、毛利率）
  - RPT_F10_OP_BUSINESSANALYSIS: 管理层经营分析全文 + 未来展望

输出: data/overseas/{code}.json

用法:
    python etl/fetch_overseas.py              # 处理所有股票
    python etl/fetch_overseas.py --resume     # 跳过已有文件
    python etl/fetch_overseas.py 600519 002594  # 处理指定股票
"""

import json
import os
import sys
import subprocess
import time
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
EASTMONEY_DIR = os.path.join(DATA_DIR, 'eastmoney')
OVERSEAS_DIR = os.path.join(DATA_DIR, 'overseas')

TOKEN = '894050c76af8597a853f5b408b759f5d'
DC_V1 = 'https://datacenter.eastmoney.com/securities/api/data/v1/get'

# 主营构成: type=1 行业, type=2 产品, type=3 地区
# 经营分析: BUSINESS_REVIEW 经营回顾, FUTURE_EXPECT 未来展望

os.makedirs(OVERSEAS_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def sv(val):
    """安全数值"""
    if val is None or val == '-' or val == '':
        return None
    try:
        return float(val)
    except:
        return None


def rd(val, decimals=2):
    """安全数值并四舍五入"""
    v = sv(val)
    if v is not None:
        return round(v, decimals)
    return None


def secucode(code: str) -> str:
    """转换股票代码为 SECUCODE 格式"""
    if code.startswith('6'):
        return f'{code}.SH'
    elif code.startswith('0') or code.startswith('3'):
        return f'{code}.SZ'
    elif code.startswith('8') or code.startswith('4'):
        return f'{code}.BJ'
    elif code.startswith('A'):
        return f'{code}.SH'  # IPO 申请股默认 SH
    return f'{code}.SH'


def fetch_json(url: str, timeout: int = 20) -> dict:
    """通过 curl 获取 JSON 数据"""
    for attempt in range(3):
        try:
            result = subprocess.run(
                ['curl', '-s', '-f', '--max-time', str(timeout), url,
                 '-H', 'User-Agent: Mozilla/5.0'],
                capture_output=True, text=True, timeout=timeout + 5
            )
            if result.returncode != 0:
                if attempt < 2:
                    time.sleep(1 * (attempt + 1))
                    continue
                return {}
            return json.loads(result.stdout)
        except Exception as e:
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
                continue
            print(f"    [WARN] 请求失败: {e}")
            return {}
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# 数据抓取
# ─────────────────────────────────────────────────────────────────────────────

def fetch_segments(sc: str) -> list:
    """
    抓取主营构成数据（按产品/地区分拆）
    仅取年报（12-31），最近 5 期
    """
    url = (
        f'{DC_V1}?reportName=RPT_F10_FN_MAINOP'
        f'&columns=ALL'
        f'&filter=(SECUCODE=%22{sc}%22)'
        f'&pageNumber=1&pageSize=500'
        f'&sortTypes=-1,-1&sortColumns=REPORT_DATE,RANK'
        f'&source=HSF10&client=PC&token={TOKEN}'
    )
    resp = fetch_json(url)
    result = resp.get('result') or {}
    data = result.get('data') or []
    if not data:
        return []

    # 按 reportDate 分组
    by_date = {}
    for item in data:
        rd = (item.get('REPORT_DATE') or '')[:10]
        if not rd:
            continue
        # 仅保留年报
        if not rd.endswith('12-31'):
            continue
        mainop_type = str(item.get('MAINOP_TYPE', ''))
        entry = {
            'name': item.get('ITEM_NAME', ''),
            'income': sv(item.get('MAIN_BUSINESS_INCOME')),
            'incomeRatio': rd_val(item.get('MBI_RATIO')),
            'cost': sv(item.get('MAIN_BUSINESS_COST')),
            'costRatio': rd_val(item.get('MBC_RATIO')),
            'profit': sv(item.get('MAIN_BUSINESS_RPOFIT')),
            'profitRatio': rd_val(item.get('MBR_RATIO')),
            'grossMargin': rd_val(item.get('GROSS_RPOFIT_RATIO')),
            'rank': item.get('RANK', 99),
        }
        if rd not in by_date:
            by_date[rd] = {'reportDate': rd, 'byIndustry': [], 'byProduct': [], 'byRegion': []}
        if mainop_type == '1':
            by_date[rd]['byIndustry'].append(entry)
        elif mainop_type == '2':
            by_date[rd]['byProduct'].append(entry)
        elif mainop_type == '3':
            by_date[rd]['byRegion'].append(entry)

    # 排序并只取最近 5 期
    dates = sorted(by_date.keys(), reverse=True)[:5]
    result = []
    for d in dates:
        seg = by_date[d]
        # 按 rank 排序各分类
        seg['byIndustry'].sort(key=lambda x: x.get('rank', 99))
        seg['byProduct'].sort(key=lambda x: x.get('rank', 99))
        seg['byRegion'].sort(key=lambda x: x.get('rank', 99))
        result.append(seg)

    return result


def rd_val(val):
    """安全百分比值"""
    v = sv(val)
    if v is not None:
        return round(v * 100, 2)  # API 返回的是小数比例，转为百分比
    return None


def fetch_analysis(sc: str) -> list:
    """
    抓取管理层经营分析 + 未来展望
    仅取年报（12-31），最近 3 期
    """
    url = (
        f'{DC_V1}?reportName=RPT_F10_OP_BUSINESSANALYSIS'
        f'&columns=ALL'
        f'&filter=(SECUCODE=%22{sc}%22)'
        f'&pageNumber=1&pageSize=30'
        f'&sortTypes=-1&sortColumns=REPORT_DATE'
        f'&source=HSF10&client=PC&token={TOKEN}'
    )
    resp = fetch_json(url)
    result = resp.get('result') or {}
    data = result.get('data') or []
    if not data:
        return []

    result_list = []
    for item in data:
        rd = (item.get('REPORT_DATE') or '')[:10]
        if not rd:
            continue
        # 仅保留年报
        if not rd.endswith('12-31'):
            continue
        result_list.append({
            'reportDate': rd,
            'businessReview': item.get('BUSINESS_REVIEW', '') or '',
            'futureExpect': item.get('FUTURE_EXPECT', '') or '',
        })

    return result_list[:3]  # 最近 3 期年报


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def process_stock(code: str, name: str = '') -> bool:
    """处理单只股票"""
    out_path = os.path.join(OVERSEAS_DIR, f'{code}.json')
    sc = secucode(code)

    print(f"[{code}] {name or ''} → {sc}")

    # 1. 主营构成
    print(f"  1/2 主营构成...")
    segments = fetch_segments(sc)
    time.sleep(0.3)

    # 2. 经营分析
    print(f"  2/2 经营分析...")
    analysis = fetch_analysis(sc)
    time.sleep(0.3)

    # 构建输出
    output = {
        'code': code,
        'name': name,
        'fetchTime': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'segments': segments,
        'analysis': analysis,
    }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    seg_count = len(segments)
    ana_count = len(analysis)
    print(f"  ✓ segments={seg_count}期, analysis={ana_count}期")
    return seg_count > 0 or ana_count > 0


def main():
    args = sys.argv[1:]
    resume = '--resume' in args
    if resume:
        args.remove('--resume')

    # 确定要处理的股票列表
    if args:
        codes = args
        # 从 eastmoney 目录获取名称
        stocks = []
        for code in codes:
            em_path = os.path.join(EASTMONEY_DIR, f'{code}.json')
            name = ''
            if os.path.exists(em_path):
                with open(em_path, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                    name = d.get('name', '')
            stocks.append((code, name))
    else:
        # 从 eastmoney 目录读取所有已有股票
        stocks = []
        for fname in sorted(os.listdir(EASTMONEY_DIR)):
            if fname.endswith('.json'):
                code = fname[:-5]
                fpath = os.path.join(EASTMONEY_DIR, fname)
                with open(fpath, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                    name = d.get('name', '')
                stocks.append((code, name))

    total = len(stocks)
    success = 0
    failed = 0
    skipped = 0

    print(f"共 {total} 只股票待处理\n")

    for i, (code, name) in enumerate(stocks):
        out_path = os.path.join(OVERSEAS_DIR, f'{code}.json')

        if resume and os.path.exists(out_path):
            skipped += 1
            print(f"[{i+1}/{total}] 跳过 {code} {name} (已有)")
            continue

        print(f"\n[{i+1}/{total}] {code} {name}")
        try:
            ok = process_stock(code, name)
            if ok:
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  [ERROR] {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"完成: 成功={success}, 失败={failed}, 跳过={skipped}, 共={total}")


if __name__ == '__main__':
    main()
