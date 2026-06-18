#!/usr/bin/env python3
"""
fetch_batch.py — 批量抓取 A 股核心财务数据

为沪深300成分股(按净利润Top300近似)批量抓取基本财务数据。
输出: data/eastmoney/{code}.json (精简版)

用法:
    python etl/fetch_batch.py              # 处理 hs300_codes.json 中所有股票
    python etl/fetch_batch.py --resume     # 跳过已有文件
    python etl/fetch_batch.py 600519 000858  # 处理指定股票
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

TOKEN = '894050c76af8597a853f5b408b759f5d'
DATACENTER_BASE = 'https://datacenter.eastmoney.com/securities/api/data/get'

# ─────────────────────────────────────────────────────────────────────────────
# HTTP 工具
# ─────────────────────────────────────────────────────────────────────────────

def curl_json(url, timeout=15):
    try:
        result = subprocess.run(
            ['curl', '-s', '-f', '--max-time', str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 5
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 东财 datacenter API 调用
# ─────────────────────────────────────────────────────────────────────────────

def fetch_org_info(secucode):
    """获取公司基本信息"""
    url = (
        f'{DATACENTER_BASE}?type=RPT_F10_ORG_BASICINFO'
        f'&sty=SECUCODE,SECURITY_NAME_ABBR,SECURITY_CODE,EM2016,CSRC_INDUSTRY_NAME,'
        f'ORG_NAME,CHAIRMAN,PROVINCE,TRADE_MARKET,SECURITY_TYPE'
        f'&filter=(SECUCODE=%22{secucode}%22)'
        f'&p=1&ps=1&token={TOKEN}'
    )
    data = curl_json(url)
    if not data or not data.get('success'):
        return None
    items = (data.get('result') or {}).get('data', [])
    if not items:
        return None
    it = items[0]
    return {
        'name': it.get('SECURITY_NAME_ABBR', ''),
        'code': it.get('SECURITY_CODE', secucode.split('.')[0]),
        'fullName': it.get('ORG_NAME', ''),
        'industry': it.get('EM2016', ''),
        'industryCSRC': it.get('CSRC_INDUSTRY_NAME', ''),
        'chairman': it.get('CHAIRMAN', ''),
        'province': it.get('PROVINCE', ''),
        'secucode': secucode,
    }


def fetch_main_indicators(secucode, periods=10):
    """获取主要财务指标 (最近N期)"""
    url = (
        f'{DATACENTER_BASE}?type=RPT_F10_FINANCE_MAINFINADATA'
        f'&sty=ALL'
        f'&filter=(SECUCODE=%22{secucode}%22)'
        f'&p=1&ps={periods}&sr=-1&st=REPORT_DATE&token={TOKEN}'
    )
    data = curl_json(url)
    if not data or not data.get('success'):
        return []
    return (data.get('result') or {}).get('data', [])


def fetch_income(secucode, periods=10):
    """获取利润表"""
    url = (
        f'{DATACENTER_BASE}?type=RPT_F10_FINANCE_GINCOME'
        f'&sty=ALL'
        f'&filter=(SECUCODE=%22{secucode}%22)'
        f'&p=1&ps={periods}&sr=-1&st=REPORT_DATE&token={TOKEN}'
    )
    data = curl_json(url)
    if not data or not data.get('success'):
        return []
    return (data.get('result') or {}).get('data', [])


def fetch_shareholders(secucode):
    """获取股东信息"""
    prefix = 'SH' if secucode.endswith('.SH') else 'SZ'
    code = secucode.split('.')[0]
    scode = f'{prefix}{code}'
    url = (
        f'https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={scode}'
    )
    data = curl_json(url, timeout=20)
    if not data:
        return {}
    result = {}
    # 十大股东
    sdgd = data.get('sdgd', [])
    if sdgd:
        result['top10Holders'] = [{
            'name': h.get('HOLDER_NAME', ''),
            'holdNum': h.get('HOLD_NUM'),
            'holdRatio': h.get('HOLD_NUM_RATIO'),
            'holdChange': h.get('HOLD_NUM_CHANGE'),
            'holderType': h.get('HOLDER_TYPE', ''),
        } for h in sdgd[:10]]
    # 股东数量历史
    sltb = data.get('sltb', [])
    if sltb:
        result['holderCountHistory'] = [{
            'date': h.get('END_DATE', '')[:10],
            'count': h.get('HOLDER_NUM'),
        } for h in sltb[:20]]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 数据处理
# ─────────────────────────────────────────────────────────────────────────────

def transform_indicators(raw_list):
    """转换财务指标为前端友好格式"""
    result = []
    for it in raw_list:
        result.append({
            'reportDate': (it.get('REPORT_DATE') or '')[:10],
            'eps': it.get('EPSJB'),
            'bps': it.get('BPS'),
            'roe': it.get('ROEJQ'),
            'netProfitMargin': it.get('XSJLL'),
            'revenue': it.get('OPERATE_INCOME_PK'),
            'netProfit': it.get('PARENTNETPROFIT'),
            'deductedNetProfit': it.get('KCFJCXSYJLR'),
            'revenueYoY': it.get('DJD_TOI_YOY'),
            'profitYoY': it.get('DJD_DPNP_YOY'),
            'grossMargin': it.get('XSMLL'),
            'ocfPerShare': it.get('MGJYXJJE'),
            'debtRatio': it.get('ZCFZL'),
        })
    return result


def transform_income(raw_list):
    """转换利润表"""
    result = []
    for it in raw_list:
        result.append({
            'reportDate': (it.get('REPORT_DATE') or '')[:10],
            'revenue': it.get('TOTAL_OPERATE_INCOME'),
            'operatingCost': it.get('TOTAL_OPERATE_COST'),
            'grossProfit': it.get('OPERATE_PROFIT'),
            'netProfit': it.get('PARENT_NETPROFIT'),
            'deductedNetProfit': it.get('DEDUCT_PARENT_NETPROFIT'),
            'salesExpense': it.get('SALE_EXPENSE'),
            'adminExpense': it.get('MANAGE_EXPENSE'),
            'rdExpense': it.get('RESEARCH_EXPENSE'),
            'financeExpense': it.get('FINANCE_EXPENSE'),
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def process_stock(secucode):
    """处理单只股票"""
    code = secucode.split('.')[0]
    out_file = os.path.join(EASTMONEY_DIR, f'{code}.json')

    print(f"  {code} ({secucode})...", end='', flush=True)

    # 1. 公司信息
    profile = fetch_org_info(secucode)
    if not profile:
        print(" 无公司信息, 跳过")
        return None

    # 2. 财务指标
    raw_indicators = fetch_main_indicators(secucode, periods=12)
    indicators = transform_indicators(raw_indicators)
    time.sleep(0.2)

    # 3. 利润表
    raw_income = fetch_income(secucode, periods=12)
    income = transform_income(raw_income)
    time.sleep(0.2)

    # 4. 股东 (只在没有数据时才抓, 减少请求)
    shareholder = {}
    if not os.path.exists(out_file):
        shareholder = fetch_shareholders(secucode)
        time.sleep(0.2)

    # 5. 组装
    result = {
        'code': code,
        'profile': profile,
        'financialIndicators': indicators,
        'incomeStatement': income,
        'shareholder': shareholder,
        'fetchTime': datetime.now().isoformat(),
    }

    # 如果已有完整数据, 保留其 balance/cashflow/cninfo 等字段
    if os.path.exists(out_file):
        with open(out_file) as f:
            existing = json.load(f)
        # 保留已有的完整字段
        for key in ['balanceSheet', 'cashflowStatement', 'cninfo', 'quote']:
            if key in existing and key not in result:
                result[key] = existing[key]
        # 保留已有的股东数据 (如果新抓的为空)
        if not result['shareholder'] and existing.get('shareholder'):
            result['shareholder'] = existing['shareholder']

    # 6. 保存
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f" OK ({len(indicators)}期, {profile.get('name', '?')})")
    return result


def main():
    resume = '--resume' in sys.argv
    codes = [a for a in sys.argv[1:] if not a.startswith('--')]

    if not codes:
        # 从 hs300_codes.json 读取列表
        codes_file = os.path.join(DATA_DIR, 'hs300_codes.json')
        if not os.path.exists(codes_file):
            print(f"错误: 未找到 {codes_file}")
            print("请先运行: 从 datacenter API 获取 top 300 股票列表")
            sys.exit(1)
        with open(codes_file) as f:
            data = json.load(f)
        codes = data['codes']
        print(f"从 hs300_codes.json 加载 {len(codes)} 只股票")

    total = len(codes)
    done = 0
    skipped = 0
    failed = 0

    for i, secucode in enumerate(codes):
        code = secucode.split('.')[0]
        out_file = os.path.join(EASTMONEY_DIR, f'{code}.json')

        if resume and os.path.exists(out_file):
            skipped += 1
            continue

        try:
            result = process_stock(secucode)
            if result:
                done += 1
            else:
                failed += 1
        except Exception as e:
            print(f" 错误: {e}")
            failed += 1

        # 进度报告
        if (i + 1) % 20 == 0:
            print(f"\n--- 进度: {i+1}/{total} (完成{done} 跳过{skipped} 失败{failed}) ---\n")

        # 限速: 每只股票间隔 0.5s
        time.sleep(0.3)

    print(f"\n{'='*60}")
    print(f"完成! 总计: {total}, 新处理: {done}, 跳过: {skipped}, 失败: {failed}")


if __name__ == '__main__':
    main()
