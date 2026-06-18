#!/usr/bin/env python3
"""
东财 F10 结构化数据抓取脚本 (datacenter API 版)
使用东财 datacenter 公开 API，数据稳定可靠

用法:
  python fetch_eastmoney.py 600519                    # 单只股票全量
  python fetch_eastmoney.py 600519,000858,000001      # 多只股票
  python fetch_eastmoney.py --all                     # 抓 samples 列表
  python fetch_eastmoney.py --search 茅台             # 搜索股票
"""

import sys
import json
import os
import time
import subprocess
import urllib.parse
from datetime import datetime

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'eastmoney')

DC_TOKEN = '894050c76af8597a853f5b408b759f5d'
DC_BASE = 'https://datacenter.eastmoney.com/securities/api/data/get'


def secucode(code: str) -> str:
    """生成 SECUCODE: 600519.SH / 000858.SZ"""
    if code.startswith('6'):
        return f'{code}.SH'
    elif code.startswith(('0', '3')):
        return f'{code}.SZ'
    elif code.startswith(('8', '4')):
        return f'{code}.BJ'
    return f'{code}.SZ'


def em_prefix(code: str) -> str:
    if code.startswith('6'): return 'SH'
    if code.startswith(('0', '3')): return 'SZ'
    return 'SZ'


def fetch_dc(table: str, code: str, page_size: int = 20) -> list:
    """从东财 datacenter API 拉数据"""
    scode = secucode(code)
    params = {
        'type': table,
        'sty': 'ALL',
        'filter': f'(SECUCODE="{scode}")',
        'p': '1',
        'ps': str(page_size),
        'sr': '-1',
        'st': 'REPORT_DATE',
        'token': DC_TOKEN
    }
    url = f"{DC_BASE}?{urllib.parse.urlencode(params)}"

    for attempt in range(3):
        try:
            result = subprocess.run(
                ['curl', '-s', '-f', '--max-time', '15', url,
                 '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'],
                capture_output=True, text=True, timeout=20
            )
            if result.returncode != 0:
                raise Exception(f"curl exit {result.returncode}")
            data = json.loads(result.stdout)
            if not data.get('success'):
                raise Exception(f"API error: {data.get('message')}")
            return data.get('result', {}).get('data', []) or []
        except Exception as e:
            if attempt == 2:
                print(f"    [WARN] {table} 失败: {e}")
                return []
            time.sleep(1 * (attempt + 1))
    return []


def fetch_push2(code: str) -> dict:
    """实时行情 (push2 API)"""
    if code.startswith('6'):
        sid = f'1.{code}'
    else:
        sid = f'0.{code}'
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={sid}&fields=f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f116,f117,f162,f167,f168,f169,f170"
    try:
        result = subprocess.run(
            ['curl', '-s', '-f', '--max-time', '10', url,
             '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
        d = data.get('data')
        if not d:
            return {}
        def sv(v, div=100):
            if v is None or v == '-': return None
            try: return round(float(v) / div, 4)
            except: return None
        return {
            'code': d.get('f57', ''),
            'name': d.get('f58', ''),
            'price': sv(d.get('f43')),
            'change': sv(d.get('f169')),
            'changePct': sv(d.get('f170')),
            'open': sv(d.get('f44')),
            'high': sv(d.get('f45')),
            'low': sv(d.get('f46')),
            'prevClose': sv(d.get('f60')),
            'volume': d.get('f47'),
            'amount': d.get('f48'),
            'totalMV': d.get('f116'),
            'circMV': d.get('f117'),
            'pe': sv(d.get('f162')),
            'pb': sv(d.get('f167')),
            'turnover': sv(d.get('f168')),
        }
    except Exception as e:
        print(f"    [WARN] 行情失败: {e}")
        return {}


def search_stock(keyword: str) -> list:
    """搜索股票"""
    url = f"https://searchapi.eastmoney.com/api/suggest/get?input={urllib.parse.quote(keyword)}&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=30"
    try:
        result = subprocess.run(
            ['curl', '-s', '-f', '--max-time', '8', url,
             '-H', 'User-Agent: Mozilla/5.0'],
            capture_output=True, text=True, timeout=12
        )
        data = json.loads(result.stdout)
    except:
        return []
    items = data.get('QuotationCodeTable', {}).get('Data', []) or []
    return [
        {'code': i.get('Code', ''), 'name': i.get('Name', ''),
         'market': i.get('MktNum', ''), 'type': i.get('SecurityTypeName', '')}
        for i in items
        if i.get('MktNum') in ('0', '1')
    ]


def sv(val):
    """安全数值"""
    if val is None or val == '-' or val == '': return None
    try: return float(val)
    except: return None


def rd(val):
    """安全数值并四舍五入到2位"""
    v = sv(val)
    if v is not None: return round(v, 2)
    return None


def pct(val):
    """百分比值（已经是百分比，保留2位）"""
    v = sv(val)
    if v is not None: return round(v, 2)
    return None


# ============ 主要财务指标 ============

def fetch_financial_indicators(code: str) -> list:
    """主要财务指标"""
    rows = fetch_dc('RPT_F10_FINANCE_MAINFINADATA', code, 20)
    results = []
    for r in rows:
        report_date = (r.get('REPORT_DATE') or '')[:10]
        parent_np = sv(r.get('PARENTNETPROFIT'))
        deduct_np = sv(r.get('KCFJCXSYJLR'))
        non_recurring = None
        if parent_np is not None and deduct_np is not None:
            non_recurring = round(parent_np - deduct_np, 2)

        results.append({
            'reportDate': report_date,
            'reportName': r.get('REPORT_DATE_NAME', ''),
            'reportType': r.get('REPORT_TYPE', ''),
            # EPS
            'basicEPS': sv(r.get('EPSJB')),
            'dilutedEPS': sv(r.get('EPSXS')),
            'deductEPS': sv(r.get('EPSKCJB')),
            # 每股
            'bps': sv(r.get('BPS')),
            'opsCashPerShare': sv(r.get('MGJYXJJE')),
            # 营收
            'totalRevenue': sv(r.get('TOTALOPERATEREVE')),
            'totalRevenueYoY': pct(r.get('TOTALOPERATEREVETZ')),
            'totalRevenueQoQ': pct(r.get('YYZSRGDHBZC')),
            # 毛利
            'grossProfit': sv(r.get('MLR')),
            # 净利
            'netProfit': sv(r.get('PARENTNETPROFIT')),
            'netProfitYoY': pct(r.get('PARENTNETPROFITTZ')),
            'netProfitQoQ': pct(r.get('NETPROFITRPHBZC')),
            # 扣非净利
            'deductNetProfit': deduct_np,
            'deductNetProfitYoY': pct(r.get('KCFJCXSYJLRTZ')),
            'deductNetProfitQoQ': pct(r.get('KFJLRGDHBZC')),
            # 非经常损益
            'nonRecurringPnL': non_recurring,
            'nonRecurringRatio': rd((non_recurring / parent_np * 100) if parent_np and non_recurring else None),
            # ROE
            'weightedROE': pct(r.get('ROEJQ')),
            'deductROE': pct(r.get('ROEKCJQ')),
            # 利润率
            'grossMargin': pct(r.get('XSMLL')),
            'netMargin': pct(r.get('XSJLL')),
            'cashToRevenue': pct(r.get('JYXJLYYSR')),
            'cashToProfit': pct(r.get('XSJXLYYSR')),
            # 偿债
            'debtRatio': pct(r.get('ZCFZL')),
            'currentRatio': sv(r.get('LD')),
            'quickRatio': sv(r.get('SD')),
            # 运营效率
            'assetTurnover': sv(r.get('TOAZZL')),
            'inventoryTurnoverDays': sv(r.get('CHZZTS')),
            'receivableTurnoverDays': sv(r.get('YSZKZZTS')),
        })
    return results


# ============ 利润表 ============

def fetch_income(code: str) -> list:
    rows = fetch_dc('RPT_F10_FINANCE_GINCOME', code, 20)
    results = []
    for r in rows:
        income = sv(r.get('OPERATE_INCOME'))
        cost = sv(r.get('OPERATE_COST'))
        pnl = sv(r.get('PARENTNETPROFIT'))
        results.append({
            'reportDate': (r.get('REPORT_DATE') or '')[:10],
            'reportName': r.get('REPORT_DATE_NAME', ''),
            'operateIncome': sv(r.get('TOTAL_OPERATE_INCOME')),
            'operateIncomeYoY': pct(r.get('TOTAL_OPERATE_INCOME_YOY')),
            'revenue': income,
            'revenueYoY': pct(r.get('OPERATE_INCOME_YOY')),
            'operateCost': cost,
            'operateCostYoY': pct(r.get('OPERATE_COST_YOY')),
            'grossProfit': rd((income - cost) if income and cost else None),
            'grossMargin': rd(((income - cost) / income * 100) if income and cost else None),
            'totalProfit': sv(r.get('TOTAL_PROFIT')),
            'netProfit': sv(r.get('NETPROFIT')),
            'parentNetProfit': pnl,
            'parentNetProfitYoY': pct(r.get('PARENTNETPROFIT_YOY')),
            # 费用
            'saleExpense': sv(r.get('SALE_EXPENSE')),
            'saleExpenseRatio': rd(sv(r.get('SALE_EXPENSE')) / income * 100 if income and sv(r.get('SALE_EXPENSE')) else None),
            'manageExpense': sv(r.get('MANAGE_EXPENSE')),
            'manageExpenseRatio': rd(sv(r.get('MANAGE_EXPENSE')) / income * 100 if income and sv(r.get('MANAGE_EXPENSE')) else None),
            'researchExpense': sv(r.get('RESEARCH_EXPENSE')),
            'researchRatio': rd(sv(r.get('RESEARCH_EXPENSE')) / income * 100 if income and sv(r.get('RESEARCH_EXPENSE')) else None),
            'financeExpense': sv(r.get('FINANCE_EXPENSE')),
            'financeExpenseRatio': rd(sv(r.get('FINANCE_EXPENSE')) / income * 100 if income and sv(r.get('FINANCE_EXPENSE')) else None),
            # 净利率
            'netMargin': rd(pnl / income * 100 if income and pnl else None),
        })
    return results


# ============ 资产负债表 ============

def fetch_balance(code: str) -> list:
    rows = fetch_dc('RPT_F10_FINANCE_GBALANCE', code, 20)
    results = []
    for r in rows:
        ta = sv(r.get('TOTAL_ASSETS'))
        tl = sv(r.get('TOTAL_LIABILITIES'))
        results.append({
            'reportDate': (r.get('REPORT_DATE') or '')[:10],
            'reportName': r.get('REPORT_DATE_NAME', ''),
            # 资产
            'totalAssets': ta,
            'totalAssetsYoY': pct(r.get('TOTAL_ASSETS_YOY')),
            'currentAssets': sv(r.get('TOTAL_CURRENT_ASSETS')),
            'nonCurrentAssets': sv(r.get('TOTAL_NONCURRENT_ASSETS')),
            'monetaryFunds': sv(r.get('MONETARYFUNDS')),
            'accountsReceivable': sv(r.get('ACCOUNTS_RECE')),
            'inventory': sv(r.get('INVENTORY')),
            'fixedAsset': sv(r.get('FIXED_ASSET')),
            'intangibleAsset': sv(r.get('INTANGIBLE_ASSET')),
            'goodwill': sv(r.get('GOODWILL')),
            # 负债
            'totalLiabilities': tl,
            'totalLiabilitiesYoY': pct(r.get('TOTAL_LIABILITIES_YOY')),
            'currentLiabilities': sv(r.get('TOTAL_CURRENT_LIAB')),
            'nonCurrentLiabilities': sv(r.get('TOTAL_NONCURRENT_LIAB')),
            'shortLoan': sv(r.get('SHORT_LOAN')),
            'longLoan': sv(r.get('LONG_LOAN')),
            'bondsPayable': sv(r.get('BOND_PAYABLE')),
            # 权益
            'totalEquity': sv(r.get('TOTAL_EQUITY')),
            'totalEquityYoY': pct(r.get('TOTAL_EQUITY_YOY')),
            'parentEquity': sv(r.get('TOTAL_PARENT_EQUITY')),
            'minorityEquity': sv(r.get('MINORITY_EQUITY')),
            # 指标
            'debtRatio': rd(tl / ta * 100 if ta and tl else None),
            'currentRatio': rd(sv(r.get('TOTAL_CURRENT_ASSETS')) / sv(r.get('TOTAL_CURRENT_LIAB')) if sv(r.get('TOTAL_CURRENT_ASSETS')) and sv(r.get('TOTAL_CURRENT_LIAB')) else None),
            'equityMultiplier': rd(ta / sv(r.get('TOTAL_EQUITY')) if ta and sv(r.get('TOTAL_EQUITY')) else None),
        })
    return results


# ============ 现金流量表 ============

def fetch_cashflow(code: str) -> list:
    rows = fetch_dc('RPT_F10_FINANCE_GCASHFLOW', code, 20)
    results = []
    for r in rows:
        results.append({
            'reportDate': (r.get('REPORT_DATE') or '')[:10],
            'reportName': r.get('REPORT_DATE_NAME', ''),
            # 经营活动
            'salesCash': sv(r.get('SALES_SERVICES')),
            'operatingCashInflow': sv(r.get('TOTAL_OPERATE_INFLOW')),
            'operatingCashOutflow': sv(r.get('TOTAL_OPERATE_OUTFLOW')),
            'netOperatingCash': sv(r.get('NETCASH_OPERATE')),
            'netOperatingCashYoY': pct(r.get('NETCASH_OPERATE_YOY')),
            # 投资活动
            'investCashInflow': sv(r.get('TOTAL_INVEST_INFLOW')),
            'investCashOutflow': sv(r.get('TOTAL_INVEST_OUTFLOW')),
            'netInvestCash': sv(r.get('NETCASH_INVEST')),
            'netInvestCashYoY': pct(r.get('NETCASH_INVEST_YOY')),
            'fixedAssetInvest': sv(r.get('FIXED_ASSET_INVEST')),
            # 筹资活动
            'financeCashInflow': sv(r.get('TOTAL_FINANCE_INFLOW')),
            'financeCashOutflow': sv(r.get('TOTAL_FINANCE_OUTFLOW')),
            'netFinanceCash': sv(r.get('NETCASH_FINANCE')),
            'netFinanceCashYoY': pct(r.get('NETCASH_FINANCE_YOY')),
            # 现金变化
            'netCashIncrease': sv(r.get('CCE_ADD')),
        })
    return results


# ============ 股东 & 分红 ============

def fetch_shareholders(code: str) -> dict:
    """十大股东 + 分红"""
    scode = f"{em_prefix(code)}{code}"
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={scode}"
    try:
        result = subprocess.run(
            ['curl', '-s', '-f', '--max-time', '15', url,
             '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
             '-H', 'Referer: https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/Index'],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"    [WARN] 股东数据失败: {e}")
        return {}

    output = {}

    # 十大股东
    sdgd = data.get('sdgd', [])
    if sdgd and isinstance(sdgd, list):
        output['top10Holders'] = [{
            'endDate': (h.get('END_DATE') or '')[:10],
            'rank': h.get('HOLDER_RANK'),
            'name': h.get('HOLDER_NAME', ''),
            'sharesType': h.get('SHARES_TYPE', ''),
            'shares': sv(h.get('HOLD_NUM')),
            'sharesRatio': sv(h.get('HOLD_NUM_RATIO')),
            'change': h.get('HOLD_NUM_CHANGE', ''),
        } for h in sdgd[:10]]

    # 十大流通股东
    sdltgd = data.get('sdltgd', [])
    if sdltgd and isinstance(sdltgd, list):
        output['top10CircHolders'] = [{
            'endDate': (h.get('END_DATE') or '')[:10],
            'rank': h.get('HOLDER_RANK'),
            'name': h.get('HOLDER_NAME', ''),
            'sharesType': h.get('SHARES_TYPE', ''),
            'shares': sv(h.get('HOLD_NUM')),
            'sharesRatio': sv(h.get('FREE_HOLDNUM_RATIO')),
            'change': h.get('HOLD_NUM_CHANGE', ''),
            'type': h.get('HOLDER_TYPE', '')
        } for h in sdltgd[:10]]

    # 股东户数
    gdrs = data.get('gdrs', [])
    if gdrs and isinstance(gdrs, list):
        output['holderCountHistory'] = [{
            'endDate': (h.get('END_DATE') or '')[:10],
            'count': sv(h.get('HOLDER_TOTAL_NUM')),
            'changeRatio': sv(h.get('TOTAL_NUM_RATIO')),
            'avgShares': sv(h.get('AVG_FREE_SHARES')),
            'avgHoldAmount': sv(h.get('AVG_HOLD_AMT')),
            'holdFocus': h.get('HOLD_FOCUS', ''),
            'price': sv(h.get('PRICE')),
        } for h in gdrs[:20]]

    # 机构持仓
    jgcc = data.get('jgcc', [])
    if jgcc and isinstance(jgcc, list):
        output['institutionalHoldings'] = [{
            'reportDate': (h.get('REPORT_DATE') or '')[:10],
            'totalOrgs': sv(h.get('TOTAL_ORG_NUM')),
            'totalShares': sv(h.get('TOTAL_FREE_SHARES')),
            'totalRatio': sv(h.get('TOTAL_SHARES_RATIO')),
        } for h in jgcc[:10]]

    # 基金持仓
    jjcg = data.get('jjcg', [])
    if jjcg and isinstance(jjcg, list):
        output['fundHoldings'] = [{
            'reportDate': (h.get('REPORT_DATE') or '')[:10],
            'fundCode': h.get('FUND_CODE', ''),
            'fundName': h.get('HOLDER_NAME', ''),
            'shares': sv(h.get('TOTAL_SHARES')),
            'value': sv(h.get('HOLD_VALUE')),
            'ratio': sv(h.get('FREESHARES_RATIO')),
            'navRatio': sv(h.get('NETVALUE_RATIO')),
        } for h in jjcg[:10]]

    # 分红
    fhsp = data.get('fhsp', [])
    if fhsp and isinstance(fhsp, list):
        output['dividends'] = [{
            'noticeDate': d.get('PLAN_NOTICE_DATE', ''),
            'reportDate': (d.get('REPORT_DATE') or '')[:10],
            'bonusPer10': d.get('PRETAX_BONUS_RMB'),
            'stockBonusPer10': d.get('BONUS_IT_RATIO'),
            'convertPer10': d.get('CONVERT_IT_RATIO'),
            'status': d.get('ASSIGN_PROGRESS', ''),
        } for d in fhsp[:15]]

    return output


# ============ 公司概况 ============

def fetch_profile(code: str) -> dict:
    scode = f"{em_prefix(code)}{code}"
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={scode}"
    try:
        result = subprocess.run(
            ['curl', '-s', '-f', '--max-time', '15', url,
             '-H', 'User-Agent: Mozilla/5.0',
             '-H', 'Referer: https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/Index'],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"    [WARN] 公司概况失败: {e}")
        return {}

    info_list = data.get('jbzl', [])
    if not info_list:
        return {}
    info = info_list[0] if isinstance(info_list, list) else info_list

    return {
        'name': info.get('SECURITY_NAME_ABBR', ''),
        'code': info.get('SECURITY_CODE', ''),
        'fullName': info.get('ORG_NAME', ''),
        'chairman': info.get('PRESIDENT', ''),
        'legalPerson': info.get('LEGAL_PERSON', ''),
        'secretary': info.get('SECRETARY', ''),
        'industry': info.get('EM2016', ''),
        'industryCSRC': info.get('INDUSTRYCSRC1', ''),
        'mainBusiness': info.get('BUSINESS_SCOPE', ''),
        'province': info.get('PROVINCE', ''),
        'website': info.get('WEBSITE', ''),
        'description': info.get('ORG_PROFILE', ''),
        'setupDate': (info.get('SETUP_DATE') or '')[:10],
        'listDate': (info.get('LISTING_DATE') or '')[:10],
        'securityType': info.get('SECURITY_TYPE', ''),
    }


# ============ 经营分析/行业对比 ============

def fetch_industry_compare(code: str) -> dict:
    """行业板块对比"""
    scode = f"{em_prefix(code)}{code}"
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CoreReadPage/PageAjax?code={scode}"
    try:
        result = subprocess.run(
            ['curl', '-s', '-f', '--max-time', '15', url,
             '-H', 'User-Agent: Mozilla/5.0',
             '-H', 'Referer: https://emweb.securities.eastmoney.com/PC_HSF10/CoreReadPage/Index'],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode != 0:
            return {}
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except Exception as e:
        print(f"    [WARN] 经营分析失败: {e}")
        return {}


# ============ 全量抓取 ============

def fetch_all(code: str) -> dict:
    print(f"  抓取 {code} 全量数据...")
    result = {'code': code, 'fetchTime': datetime.now().isoformat()}

    steps = [
        ('profile', '公司概况', lambda: fetch_profile(code)),
        ('quote', '实时行情', lambda: fetch_push2(code)),
        ('financial', '财务指标', lambda: fetch_financial_indicators(code)),
        ('income', '利润表', lambda: fetch_income(code)),
        ('balance', '资产负债表', lambda: fetch_balance(code)),
        ('cashflow', '现金流量表', lambda: fetch_cashflow(code)),
        ('shareholder', '股东/分红', lambda: fetch_shareholders(code)),
    ]

    for i, (key, desc, func) in enumerate(steps, 1):
        print(f"    [{i}/{len(steps)}] {desc}...")
        data = func()
        result[key] = data
        if key == 'profile' and data.get('name'):
            result['name'] = data['name']
        time.sleep(0.3)

    return result


def save_result(code: str, data: dict):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, f"{code}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    size_kb = os.path.getsize(filepath) / 1024
    print(f"  已保存: {filepath} ({size_kb:.1f} KB)")


def main():
    args = sys.argv[1:]

    if not args:
        print("用法: python fetch_eastmoney.py <股票代码|代码1,代码2,...|--all>")
        print("  python fetch_eastmoney.py 600519")
        print("  python fetch_eastmoney.py 600519,000858,000001")
        print("  python fetch_eastmoney.py --all")
        print("  python fetch_eastmoney.py --search 茅台")
        sys.exit(1)

    if args[0] == '--search':
        keyword = args[1] if len(args) > 1 else ''
        results = search_stock(keyword)
        for r in results:
            print(f"  {r['code']} {r['name']} ({r['type']})")
        return

    if args[0] == '--all':
        tickers_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'tickers.json')
        with open(tickers_file, 'r', encoding='utf-8') as f:
            tickers = json.load(f)
        codes = [t['code'] for t in tickers.get('samples', [])]
    else:
        codes = [c.strip() for c in args[0].split(',')]

    for code in codes:
        print(f"\n{'='*50}")
        print(f"  抓取: {code}")
        print(f"{'='*50}")
        data = fetch_all(code)
        save_result(code, data)
        time.sleep(0.5)

    print(f"\n全部完成!")


if __name__ == '__main__':
    main()
