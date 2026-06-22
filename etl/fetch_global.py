#!/usr/bin/env python3
"""
fetch_global.py — 美股/港股数据抓取脚本

数据源:
  - 东财 push2 API: 股票列表 + 实时行情 + 基本面
  - 东财 datacenter API: 利润表 (RPT_USF10_FN_INCOME / RPT_HKF10_FN_INCOME)
                        资产负债表 (RPT_USF10_FN_BALANCE / RPT_HKF10_FN_BALANCE)
  - 腾讯行情 API: 实时行情 (qt.gtimg.cn)
  - SEC EDGAR: 美股现金流 (XBRL)

用法:
  python etl/fetch_global.py                          # 全量抓取 (美股+港股)
  python etl/fetch_global.py --market us              # 仅美股
  python etl/fetch_global.py --market hk              # 仅港股
  python etl/fetch_global.py --ticker AAPL MSFT       # 指定股票
  python etl/fetch_global.py --refresh-list           # 仅刷新股票列表
  python etl/fetch_global.py --resume                 # 跳过已有文件
"""

import sys
import json
import os
import time
import subprocess
import urllib.parse
import argparse
from collections import defaultdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
EASTMONEY_DIR = os.path.join(DATA_DIR, 'eastmoney')

DC_TOKEN = '894050c76af8597a853f5b408b759f5d'
DC_BASE = 'https://datacenter.eastmoney.com/securities/api/data/get'
SEC_UA = 'FinancialRadar research@financialradar.io'

# ═══════════════════════════════════════════════════════
# 行项目编码映射 (东财 datacenter US/HK F10 行项目格式)
# ═══════════════════════════════════════════════════════

US_INCOME_ITEMS = {
    '004001999': 'revenue',
    '004003999': 'operateCost',
    '004005999': 'grossProfit',
    '004007999': 'totalExpenses',
    '004007001': 'researchExpense',
    '004007002': 'saleExpense',
    '004009999': 'operateProfit',
    '004011999': 'totalProfit',
    '004013001': 'incomeTax',
    '004013999': 'netProfit',
    '004015999': 'parentNetProfit',
    '004017003': 'basicEPS',
    '004017004': 'dilutedEPS',
}

HK_INCOME_ITEMS = {
    '004001999': 'revenue',
    '004003999': 'operateCost',
    '004005999': 'grossProfit',
    '004007999': 'totalExpenses',
    '004007001': 'researchExpense',
    '004007002': 'saleExpense',
    '004009999': 'operateProfit',
    '004011999': 'totalProfit',
    '004013001': 'incomeTax',
    '004013999': 'netProfit',
    '004015999': 'parentNetProfit',
    '004017003': 'basicEPS',
    '004017004': 'dilutedEPS',
}

BALANCE_ITEMS = {
    '004001001': 'monetaryFunds',
    '004001004': 'accountsReceivable',
    '004001008': 'inventory',
    '004001014': 'otherCurrentAssets',
    '004001999': 'currentAssets',
    '004003001': 'fixedAsset',
    '004003007': 'intangibleAsset',
    '004003009': 'goodwill',
    '004003014': 'otherNonCurrentAssets',
    '004003999': 'nonCurrentAssets',
    '004005999': 'totalAssets',
    '004007001': 'accountsPayable',
    '004007007': 'shortLoan',
    '004007999': 'currentLiabilities',
    '004009005': 'longLoan',
    '004009999': 'nonCurrentLiabilities',
    '004011999': 'totalLiabilities',
    '004013999': 'parentEquity',
    '004015002': 'minorityEquity',
    '004017999': 'totalEquity',
}


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def sv(val):
    """安全数值转换"""
    if val is None or val == '-' or val == '':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def rd(val):
    """安全数值 + 四舍五入到 2 位"""
    v = sv(val)
    return round(v, 2) if v is not None else None


def detect_market(code):
    """从代码格式推断市场: us / hk / cn"""
    if code.replace('.', '').isalpha():
        return 'us'
    if len(code) == 5 and code.isdigit():
        return 'hk'
    return 'cn'


def code_to_tencent(code, market):
    """代码 → 腾讯行情格式"""
    if market == 'us':
        return f'us{code}'
    elif market == 'hk':
        return f'hk{code}'
    return f'sh{code}' if code.startswith('6') else f'sz{code}'


def filename_for(code, market):
    """生成文件名: us_AAPL.json / hk_00700.json"""
    if market in ('us', 'hk'):
        return f'{market}_{code}.json'
    return f'{code}.json'


def _curl_json(url, headers=None, timeout=15):
    """通用 curl + JSON 解析"""
    cmd = ['curl', '-s', '-f', '--max-time', str(timeout), url,
           '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36']
    if headers:
        for k, v in headers.items():
            cmd.extend(['-H', f'{k}: {v}'])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if result.returncode != 0:
        raise Exception(f'curl exit {result.returncode}')
    return json.loads(result.stdout)


# ═══════════════════════════════════════════════════════
# 行情获取
# ═══════════════════════════════════════════════════════

def parse_tencent_us_hk(fields, market):
    """
    解析腾讯行情美股/港股字段 (字段位置与 A 股不同)
    US: 71 fields | HK: 78 fields
    """
    pf = lambda v: float(v) if v else None
    code = fields[2]

    q = {
        'code': code,
        'name': fields[1],
        'price': pf(fields[3]),
        'change': pf(fields[31]),
        'changePct': pf(fields[32]),
        'open': pf(fields[5]),
        'high': pf(fields[33]),
        'low': pf(fields[34]),
        'prevClose': pf(fields[4]),
        'volume': int(float(fields[6])) if fields[6] else None,
        'amount': pf(fields[37]),
        'pe': pf(fields[39]),
        'turnover': pf(fields[38]),
        'fetchTime': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }

    if market == 'us':
        q['pb'] = pf(fields[41])
        q['totalMV'] = (pf(fields[45]) or 0) * 1e8    # 亿美元 → 美元
        q['circMV'] = (pf(fields[44]) or 0) * 1e8
        q['currency'] = 'USD'
    else:  # hk
        q['pb'] = pf(fields[47])
        q['totalMV'] = (pf(fields[45]) or 0) * 1e8    # 亿港元 → 港元
        q['circMV'] = (pf(fields[44]) or 0) * 1e8
        q['currency'] = 'HKD'

    return q


def parse_tencent_cn(fields):
    """解析 A 股行情 (兼容现有格式)"""
    pf = lambda v: float(v) if v else None
    return {
        'code': fields[2],
        'name': fields[1],
        'price': pf(fields[3]),
        'change': pf(fields[31]),
        'changePct': pf(fields[32]),
        'open': pf(fields[5]),
        'high': pf(fields[33]),
        'low': pf(fields[34]),
        'prevClose': pf(fields[4]),
        'volume': int(float(fields[6])) if fields[6] else None,
        'amount': pf(fields[37]),
        'totalMV': (pf(fields[45]) or 0) * 1e8,
        'circMV': (pf(fields[44]) or 0) * 1e8,
        'pe': pf(fields[39]),
        'pb': pf(fields[46]),
        'turnover': pf(fields[38]),
        'fetchTime': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
    }


def fetch_quote_tencent(code, market):
    """单只股票腾讯行情"""
    tc = code_to_tencent(code, market)
    url = f'https://qt.gtimg.cn/q={tc}'
    try:
        r = subprocess.run(
            ['curl', '-s', '-f', '--max-time', '10', url,
             '-H', 'Referer: https://finance.qq.com'],
            capture_output=True, timeout=15)
        if r.returncode != 0:
            return {}
        text = r.stdout.decode('gbk', errors='replace')
        content = text.split('"')[1] if '"' in text else ''
        if not content:
            return {}
        f = content.split('~')
        if market == 'us' and len(f) >= 50:
            return parse_tencent_us_hk(f, 'us')
        elif market == 'hk' and len(f) >= 50:
            return parse_tencent_us_hk(f, 'hk')
        elif len(f) >= 47:
            return parse_tencent_cn(f)
        return {}
    except Exception as e:
        print(f'    [WARN] 腾讯行情 {code}: {e}')
        return {}


def fetch_quote_push2(code, secid):
    """push2 实时行情 (备用)"""
    url = (f'https://push2.eastmoney.com/api/qt/stock/get?'
           f'secid={secid}&fields=f43,f44,f45,f46,f47,f48,f50,f55,f57,f58,'
           f'f84,f107,f116,f117,f162,f163,f167,f169,f170,f173,f183,f188,f189')
    try:
        data = _curl_json(url)
        d = data.get('data', {})
        if not d:
            return {}
        div = 100
        return {
            'code': d.get('f57', code),
            'name': d.get('f58', ''),
            'price': sv(d.get('f43', 0)) and sv(d['f43']) / div,
            'open': sv(d.get('f46', 0)) and sv(d['f46']) / div,
            'high': sv(d.get('f44', 0)) and sv(d['f44']) / div,
            'low': sv(d.get('f45', 0)) and sv(d['f45']) / div,
            'volume': sv(d.get('f47')),
            'amount': sv(d.get('f48')),
            'pe': sv(d.get('f55')),
            'pb': sv(d.get('f163', 0)) and sv(d['f163']) / div,
            'totalMV': sv(d.get('f116')),
            'circMV': sv(d.get('f117')),
            'roe': sv(d.get('f173')),
            'revenue': sv(d.get('f183')),
            'grossMargin': sv(d.get('f188')),
            'fetchTime': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        }
    except Exception:
        return {}


def fetch_tencent_batch(codes, market):
    """批量腾讯行情 (≤15 只/批)"""
    results = {}
    batches = [codes[i:i + 15] for i in range(0, len(codes), 15)]

    for batch in batches:
        tc = ','.join(code_to_tencent(c, market) for c in batch)
        url = f'https://qt.gtimg.cn/q={tc}'
        try:
            r = subprocess.run(
                ['curl', '-s', '-f', '--max-time', '15', url,
                 '-H', 'Referer: https://finance.qq.com'],
                capture_output=True, timeout=20)
            if r.returncode != 0:
                time.sleep(2)
                continue
            text = r.stdout.decode('gbk', errors='replace')
            for line in text.split(';'):
                line = line.strip()
                if not line or '"' not in line:
                    continue
                content = line.split('"')[1]
                fields = content.split('~')
                if len(fields) < 40:
                    continue
                try:
                    if market in ('us', 'hk'):
                        q = parse_tencent_us_hk(fields, market)
                    else:
                        q = parse_tencent_cn(fields)
                    if q.get('price'):
                        results[q['code']] = q
                except (IndexError, ValueError):
                    continue
        except Exception:
            pass
        time.sleep(0.3)

    return results


# ═══════════════════════════════════════════════════════
# 东财 datacenter API (行项目格式)
# ═══════════════════════════════════════════════════════

def fetch_dc(table, code, page_size=200):
    """
    东财 datacenter API — 行项目格式
    与 A 股宽表不同, US/HK 返回每行一个指标:
    {STD_ITEM_CODE, ITEM_NAME, AMOUNT, REPORT_DATE, START_DATE, ...}
    """
    params = {
        'type': table,
        'sty': 'ALL',
        'filter': f'(SECURITY_CODE="{code}")',
        'p': '1',
        'ps': str(page_size),
        'sr': '-1',
        'st': 'REPORT_DATE',
        'token': DC_TOKEN,
    }
    url = f'{DC_BASE}?{urllib.parse.urlencode(params)}'

    for attempt in range(3):
        try:
            data = _curl_json(url, timeout=18)
            if not data.get('success'):
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return []
            return data.get('result', {}).get('data', []) or []
        except Exception as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            print(f'    [WARN] {table}({code}): {e}')
            return []
    return []


def pivot_line_items(rows, item_map, only_annual=False):
    """
    行项目 → 宽表:
    1. 按 (REPORT_DATE, REPORT_TYPE) 分组
    2. 优先取无 START_DATE 的合计项
    3. 映射 STD_ITEM_CODE → 字段名
    """
    periods = defaultdict(dict)

    for row in rows:
        rd = (row.get('REPORT_DATE') or '')[:10]
        rt = row.get('REPORT_TYPE', '')
        start = row.get('START_DATE', '')

        if only_annual and rt not in ('年报', '中期', '单季报', '一季报', '三季报'):
            pass  # 仍然保留, 让前端过滤

        item_code = row.get('STD_ITEM_CODE', '')
        if item_code not in item_map:
            continue

        field = item_map[item_code]
        key = (rd, rt)

        # 优先取无 START_DATE 的合计项 (999 结尾)
        existing = periods[key].get(field)
        if not start or existing is None:
            periods[key][field] = row.get('AMOUNT')
            yoy = row.get('YOY_RATIO')
            if yoy is not None:
                periods[key][f'{field}YoY'] = round(yoy, 2)

    results = []
    for (report_date, report_type), fields in sorted(periods.items(), reverse=True):
        entry = {'reportDate': report_date, 'reportType': report_type}
        entry.update(fields)
        results.append(entry)

    return results


# ═══════════════════════════════════════════════════════
# 利润表
# ═══════════════════════════════════════════════════════

def fetch_income(code, market):
    table = 'RPT_USF10_FN_INCOME' if market == 'us' else 'RPT_HKF10_FN_INCOME'
    item_map = US_INCOME_ITEMS if market == 'us' else HK_INCOME_ITEMS
    rows = fetch_dc(table, code, 200)
    if not rows:
        return []

    items = pivot_line_items(rows, item_map)

    for entry in items:
        rev = sv(entry.get('revenue'))
        cost = sv(entry.get('operateCost'))
        pnl = sv(entry.get('parentNetProfit'))
        np_ = sv(entry.get('netProfit'))

        if rev and cost and not entry.get('grossProfit'):
            entry['grossProfit'] = round(rev - cost, 2)
        if rev and cost:
            entry['grossMargin'] = round((rev - cost) / rev * 100, 2)
        if rev and pnl:
            entry['netMargin'] = round(pnl / rev * 100, 2)

        # 费用率
        for exp_field in ('saleExpense', 'manageExpense', 'researchExpense', 'financeExpense'):
            exp = sv(entry.get(exp_field))
            if exp and rev:
                entry[f'{exp_field}Ratio'] = round(exp / rev * 100, 2)

    return items


# ═══════════════════════════════════════════════════════
# 资产负债表
# ═══════════════════════════════════════════════════════

def fetch_balance(code, market):
    table = 'RPT_USF10_FN_BALANCE' if market == 'us' else 'RPT_HKF10_FN_BALANCE'
    rows = fetch_dc(table, code, 200)
    if not rows:
        return []

    items = pivot_line_items(rows, BALANCE_ITEMS)

    for entry in items:
        ta = sv(entry.get('totalAssets'))
        tl = sv(entry.get('totalLiabilities'))
        ca = sv(entry.get('currentAssets'))
        cl = sv(entry.get('currentLiabilities'))

        if ta and tl:
            entry['debtRatio'] = round(tl / ta * 100, 2)
        if ca and cl:
            entry['currentRatio'] = round(ca / cl, 2)
        if ta and sv(entry.get('totalEquity')):
            entry['equityMultiplier'] = round(ta / entry['totalEquity'], 2)

    return items


# ═══════════════════════════════════════════════════════
# SEC EDGAR 现金流 (仅美股)
# ═══════════════════════════════════════════════════════

_cik_cache = None

def _get_cik_map():
    """从 SEC 获取 ticker → CIK 映射"""
    global _cik_cache
    if _cik_cache:
        return _cik_cache
    try:
        data = _curl_json(
            'https://www.sec.gov/files/company_tickers.json',
            headers={'User-Agent': SEC_UA}, timeout=20)
        _cik_cache = {}
        for entry in data.values():
            ticker = entry.get('ticker', '').upper()
            cik = str(entry.get('cik_int', '')).zfill(10)
            if ticker and cik:
                _cik_cache[ticker] = cik
        return _cik_cache
    except Exception as e:
        print(f'    [WARN] SEC CIK map: {e}')
        return {}


def fetch_cashflow_sec(ticker):
    """从 SEC EDGAR 获取美股现金流量表"""
    cik_map = _get_cik_map()
    cik = cik_map.get(ticker.upper())
    if not cik:
        return []

    try:
        data = _curl_json(
            f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json',
            headers={'User-Agent': SEC_UA}, timeout=30)
    except Exception as e:
        print(f'    [WARN] SEC EDGAR {ticker}: {e}')
        return []

    facts = data.get('facts', {}).get('us-gaap', {})

    # 映射 XBRL concept → 字段名
    CF_MAP = {
        'NetCashProvidedByUsedInOperatingActivities': 'netOperatingCash',
        'NetCashProvidedByUsedInInvestingActivities': 'netInvestCash',
        'NetCashProvidedByUsedInFinancingActivities': 'netFinanceCash',
        'PaymentsToAcquirePropertyPlantAndEquipment': 'fixedAssetInvest',
        'DepreciationDepletionAndAmortization': 'depreciation',
        'PaymentsOfDividends': 'dividendsPaid',
    }

    periods = defaultdict(dict)
    for concept, field in CF_MAP.items():
        entries = facts.get(concept, {}).get('units', {}).get('USD', [])
        for e in entries:
            if e.get('form') in ('10-K', '10-Q'):
                end = e.get('end', '')
                key = end
                if e.get('fp') == 'FY':
                    periods[key]['_annual'] = True
                if 'start' in e:
                    periods[key][field] = e.get('val')

    results = []
    for end_date, fields in sorted(periods.items(), reverse=True):
        if not fields.get('_annual'):
            continue
        entry = {'reportDate': end_date, 'reportType': '年报'}
        del fields['_annual']
        entry.update(fields)
        results.append(entry)

    return results[:10]


# ═══════════════════════════════════════════════════════
# 股票列表
# ═══════════════════════════════════════════════════════

def fetch_stock_list(market, size=150):
    """从 push2 clist 按市值排序获取股票列表"""
    if market == 'us':
        fs = 'm:105,m:106'
    elif market == 'hk':
        fs = 'm:116'
    else:
        return []

    url = (f'https://push2.eastmoney.com/api/qt/clist/get?'
           f'pn=1&pz={size}&po=1&np=1&fltt=2&invt=2&fid=f20'
           f'&fs={fs}&fields=f2,f3,f12,f13,f14,f20')

    try:
        data = _curl_json(url, timeout=15)
        items = data.get('data', {}).get('diff', [])
    except Exception as e:
        print(f'[ERROR] 获取 {market} 列表失败: {e}')
        return []

    results = []
    seen_codes = set()

    for item in items:
        code = item.get('f12', '')
        name = item.get('f14', '')
        mkt = item.get('f13', 0)
        mv = item.get('f20', 0)

        # 过滤
        if market == 'hk':
            # 排除人民币柜台 (8xxxx), 排除 ETF (代码以 02/03 开头且名称含 ETF)
            if code.startswith('8'):
                continue
            if 'ETF' in name.upper() or '基金' in name:
                continue
            # 排除权证 (代码以 01/02 开头但长度 > 5)
            if len(code) != 5:
                continue

        if market == 'us':
            # 排除重复股份类 (GOOG vs GOOGL, 保留 A 类)
            base = code.rstrip('-A').rstrip('-B').rstrip('-C')
            # 简单去重: 同名公司保留市值最大的
            if name and any(name == r.get('name') for r in results):
                continue

        if code in seen_codes:
            continue
        seen_codes.add(code)

        results.append({
            'code': code,
            'name': name,
            'market': market,
            'secid': f'{mkt}.{code}',
            'totalMV': mv,
        })

    return results


def build_stock_names(entries):
    """构建 stock_names.json 格式的条目"""
    names = []
    for s in entries:
        names.append({'c': s['code'], 'n': s['name'], 'm': s['market']})
    return names


# ═══════════════════════════════════════════════════════
# 单只股票全量抓取
# ═══════════════════════════════════════════════════════

def process_stock(code, market, secid=''):
    """抓取单只美股/港股的全量数据"""
    output_file = os.path.join(EASTMONEY_DIR, filename_for(code, market))

    print(f'  [{market.upper()}] {code}')

    # 1) 行情
    quote = fetch_quote_tencent(code, market)
    if not quote:
        quote = fetch_quote_push2(code, secid)
    name = quote.get('name', '') if quote else ''
    time.sleep(0.2)

    # 2) 利润表
    income = fetch_income(code, market)
    time.sleep(0.3)

    # 3) 资产负债表
    balance = fetch_balance(code, market)
    time.sleep(0.3)

    # 4) 现金流 (仅美股, 从 SEC EDGAR)
    cashflow = []
    if market == 'us':
        cashflow = fetch_cashflow_sec(code)
        time.sleep(0.15)

    # 组装输出
    result = {
        'code': code,
        'name': name,
        'market': market,
        'currency': 'USD' if market == 'us' else 'HKD',
        'fetchTime': datetime.now().isoformat(),
        'profile': {
            'name': name,
            'code': code,
            'fullName': name,
            'securityType': '美股' if market == 'us' else '港股',
        },
        'financial': [],   # US/HK 无 MAINFINADATA 汇总表, 由 income+balance 替代
        'income': income,
        'balance': balance,
        'cashflow': cashflow,
        'shareholder': {},  # 东财不提供美股/港股股东数据
        'quote': quote,
    }

    # 保存
    os.makedirs(EASTMONEY_DIR, exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(output_file) / 1024
    inc_n = len(income)
    bal_n = len(balance)
    cf_n = len(cashflow)
    print(f'    -> {inc_n}期利润, {bal_n}期资产, {cf_n}期现金流 ({size_kb:.1f}KB)')

    return True


# ═══════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='美股/港股数据抓取')
    parser.add_argument('--market', choices=['us', 'hk', 'all'], default='all')
    parser.add_argument('--ticker', nargs='+', help='指定股票代码')
    parser.add_argument('--refresh-list', action='store_true', help='仅刷新股票列表')
    parser.add_argument('--resume', action='store_true', help='跳过已有文件')
    parser.add_argument('--us-size', type=int, default=150, help='美股数量 (默认150)')
    parser.add_argument('--hk-size', type=int, default=50, help='港股数量 (默认50)')
    args = parser.parse_args()

    os.makedirs(EASTMONEY_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    # ── 指定股票 ──
    if args.ticker:
        for code in args.ticker:
            market = args.market if args.market != 'all' else detect_market(code)
            process_stock(code, market)
            time.sleep(0.5)
        print('\n完成!')
        return

    # ── 确定市场 ──
    markets = ['us', 'hk'] if args.market == 'all' else [args.market]

    # ── 获取股票列表 ──
    global_codes_path = os.path.join(DATA_DIR, 'global_codes.json')
    all_entries = []

    # 尝试加载已有列表
    existing = {}
    if os.path.exists(global_codes_path):
        try:
            with open(global_codes_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            pass

    def _normalize(codes, market):
        """将纯字符串列表转为 dict 列表 (兼容静态列表格式)"""
        result = []
        for item in codes:
            if isinstance(item, str):
                code = item
                if market == 'us':
                    secid = f'105.{code}'
                elif market == 'hk':
                    secid = f'116.{code}'
                else:
                    secid = code
                result.append({'code': code, 'name': '', 'market': market, 'secid': secid, 'totalMV': 0})
            elif isinstance(item, dict):
                result.append(item)
        return result

    for market in markets:
        size = args.us_size if market == 'us' else args.hk_size
        cached = existing.get(market, [])

        # 如果已有缓存且不超过 1 天, 直接用
        if cached and not args.refresh_list:
            entries = _normalize(cached, market)
            print(f'[{market.upper()}] 使用缓存列表 ({len(entries)} 只)')
        else:
            print(f'[{market.upper()}] 获取市值 Top {size} ...')
            entries = fetch_stock_list(market, size)
            print(f'  获取到 {len(entries)} 只股票')
            # 如果 push2 clist 失败, 回退到静态列表
            if not entries and cached:
                entries = _normalize(cached, market)
                print(f'  push2 失败, 回退到缓存列表 ({len(entries)} 只)')

        all_entries.extend(entries)

    # ── 仅刷新列表 ──
    if args.refresh_list:
        output = {m: [e for e in all_entries if e['market'] == m] for m in markets}
        output['fetchTime'] = datetime.now().isoformat()
        with open(global_codes_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f'\n股票列表已保存到 {global_codes_path}')
        for m in markets:
            print(f'  {m.upper()}: {len(output.get(m, []))} 只')
        return

    # ── 保存列表 ──
    list_output = {}
    for m in ['us', 'hk']:
        list_output[m] = [e for e in all_entries if e.get('market') == m]
    list_output['fetchTime'] = datetime.now().isoformat()
    with open(global_codes_path, 'w', encoding='utf-8') as f:
        json.dump(list_output, f, ensure_ascii=False, indent=2)

    # ── 逐只抓取 ──
    total = len(all_entries)
    print(f'\n{"="*60}')
    print(f'共 {total} 只股票待抓取')
    print(f'{"="*60}\n')

    success = 0
    fail = 0

    for i, stock in enumerate(all_entries, 1):
        code = stock['code']
        market = stock['market']
        secid = stock.get('secid', '')

        # --resume: 跳过已有
        if args.resume:
            fpath = os.path.join(EASTMONEY_DIR, filename_for(code, market))
            if os.path.exists(fpath):
                success += 1
                print(f'  [{i}/{total}] {market.upper()} {code} (跳过, 已存在)')
                continue

        print(f'\n[{i}/{total}] ', end='')
        try:
            process_stock(code, market, secid)
            success += 1
        except Exception as e:
            print(f'    [FAIL] {code}: {e}')
            fail += 1
        time.sleep(0.5)

    # ── 汇总 ──
    print(f'\n{"="*60}')
    print(f'完成: 成功={success}, 失败={fail}, 共={total}')
    print(f'时间: {datetime.now().strftime("%H:%M:%S")}')

    # ── 更新 stock_names.json ──
    _update_stock_names(all_entries)


def _update_stock_names(global_entries):
    """将美股/港股条目合并到 stock_names.json"""
    sn_path = os.path.join(DATA_DIR, 'stock_names.json')

    # 读取现有 A 股
    existing = []
    if os.path.exists(sn_path):
        try:
            with open(sn_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            pass

    # 给 A 股加 m=cn (如果没有)
    for item in existing:
        if 'm' not in item:
            item['m'] = 'cn'

    # 从已生成的 JSON 文件中补全名字 (静态列表可能没有 name)
    name_map = {}
    for stock in global_entries:
        code = stock['code']
        market = stock['market']
        fpath = os.path.join(EASTMONEY_DIR, filename_for(code, market))
        if os.path.exists(fpath):
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    name_map[code] = data.get('name', '') or ''
            except Exception:
                pass

    # 添加美股/港股
    existing_codes = {item['c'] for item in existing}
    for stock in global_entries:
        code = stock['code']
        if code not in existing_codes:
            name = name_map.get(code) or stock.get('name', '')
            existing.append({
                'c': code,
                'n': name,
                'm': stock['market'],
            })
            existing_codes.add(code)

    # 排序: cn → us → hk, 各自按代码排
    def sort_key(item):
        m = item.get('m', 'cn')
        return ({'cn': 0, 'us': 1, 'hk': 2}.get(m, 3), item['c'])

    existing.sort(key=sort_key)

    with open(sn_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    us_n = sum(1 for e in global_entries if e['market'] == 'us')
    hk_n = sum(1 for e in global_entries if e['market'] == 'hk')
    print(f'\nstock_names.json 已更新: +{us_n} 美股, +{hk_n} 港股, 共 {len(existing)} 条')


if __name__ == '__main__':
    main()
