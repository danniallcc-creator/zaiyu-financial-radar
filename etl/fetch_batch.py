#!/usr/bin/env python3
"""
fetch_batch.py — 批量抓取 A 股核心财务数据 (v2)

输出格式与 fetch_eastmoney.py 完全一致，前端无需区分数据来源。
输出: data/eastmoney/{code}.json

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
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
EASTMONEY_DIR = os.path.join(DATA_DIR, 'eastmoney')

TOKEN = '894050c76af8597a853f5b408b759f5d'
DC_BASE = 'https://datacenter.eastmoney.com/securities/api/data/get'

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


def rd(val):
    """安全数值并四舍五入到2位"""
    v = sv(val)
    if v is not None:
        return round(v, 2)
    return None


def pct(val):
    """百分比值（已经是百分比，保留2位）"""
    v = sv(val)
    if v is not None:
        return round(v, 2)
    return None


def secucode(code):
    """生成 SECUCODE: 600519.SH / 000858.SZ"""
    if code.startswith('6'):
        return f'{code}.SH'
    elif code.startswith(('0', '3')):
        return f'{code}.SZ'
    elif code.startswith(('8', '4')):
        return f'{code}.BJ'
    return f'{code}.SZ'


def em_prefix(code):
    if code.startswith('6'):
        return 'SH'
    if code.startswith(('0', '3')):
        return 'SZ'
    return 'SZ'


# ─────────────────────────────────────────────────────────────────────────────
# HTTP 工具
# ─────────────────────────────────────────────────────────────────────────────

def curl_json(url, timeout=15, referer=None):
    cmd = ['curl', '-s', '-f', '--max-time', str(timeout), url,
           '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36']
    if referer:
        cmd += ['-H', f'Referer: {referer}']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 东财 datacenter API 调用
# ─────────────────────────────────────────────────────────────────────────────

def fetch_dc(table, scode, page_size=20):
    """从东财 datacenter API 拉数据"""
    url = (
        f'{DC_BASE}?type={table}'
        f'&sty=ALL'
        f'&filter=(SECUCODE=%22{scode}%22)'
        f'&p=1&ps={page_size}&sr=-1&st=REPORT_DATE&token={TOKEN}'
    )
    data = curl_json(url)
    if not data or not data.get('success'):
        return []
    return (data.get('result') or {}).get('data', []) or []


# ─────────────────────────────────────────────────────────────────────────────
# 主要财务指标 (与 fetch_eastmoney.py 完全一致)
# ─────────────────────────────────────────────────────────────────────────────

def transform_financial(raw_list):
    """转换主要财务指标 → 前端期望的字段名"""
    results = []
    for r in raw_list:
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
            'netProfit': parent_np,
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


# ─────────────────────────────────────────────────────────────────────────────
# 利润表 (与 fetch_eastmoney.py 完全一致)
# ─────────────────────────────────────────────────────────────────────────────

def transform_income(raw_list):
    """转换利润表 → 前端期望的字段名"""
    results = []
    for r in raw_list:
        income_val = sv(r.get('OPERATE_INCOME'))
        cost = sv(r.get('OPERATE_COST'))
        pnl = sv(r.get('PARENTNETPROFIT'))
        sale_exp = sv(r.get('SALE_EXPENSE'))
        mgmt_exp = sv(r.get('MANAGE_EXPENSE'))
        rd_exp = sv(r.get('RESEARCH_EXPENSE'))
        fin_exp = sv(r.get('FINANCE_EXPENSE'))

        results.append({
            'reportDate': (r.get('REPORT_DATE') or '')[:10],
            'reportName': r.get('REPORT_DATE_NAME', ''),
            'operateIncome': sv(r.get('TOTAL_OPERATE_INCOME')),
            'operateIncomeYoY': pct(r.get('TOTAL_OPERATE_INCOME_YOY')),
            'revenue': income_val,
            'revenueYoY': pct(r.get('OPERATE_INCOME_YOY')),
            'operateCost': cost,
            'operateCostYoY': pct(r.get('OPERATE_COST_YOY')),
            'grossProfit': rd((income_val - cost) if income_val and cost else None),
            'grossMargin': rd(((income_val - cost) / income_val * 100) if income_val and cost else None),
            'totalProfit': sv(r.get('TOTAL_PROFIT')),
            'netProfit': sv(r.get('NETPROFIT')),
            'parentNetProfit': pnl,
            'parentNetProfitYoY': pct(r.get('PARENTNETPROFIT_YOY')),
            # 费用
            'saleExpense': sale_exp,
            'saleExpenseRatio': rd(sale_exp / income_val * 100 if income_val and sale_exp else None),
            'manageExpense': mgmt_exp,
            'manageExpenseRatio': rd(mgmt_exp / income_val * 100 if income_val and mgmt_exp else None),
            'researchExpense': rd_exp,
            'researchRatio': rd(rd_exp / income_val * 100 if income_val and rd_exp else None),
            'financeExpense': fin_exp,
            'financeExpenseRatio': rd(fin_exp / income_val * 100 if income_val and fin_exp else None),
            # 净利率
            'netMargin': rd(pnl / income_val * 100 if income_val and pnl else None),
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 资产负债表 (与 fetch_eastmoney.py 完全一致)
# ─────────────────────────────────────────────────────────────────────────────

def transform_balance(raw_list):
    """转换资产负债表 → 前端期望的字段名"""
    results = []
    for r in raw_list:
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
            'currentRatio': rd(
                sv(r.get('TOTAL_CURRENT_ASSETS')) / sv(r.get('TOTAL_CURRENT_LIAB'))
                if sv(r.get('TOTAL_CURRENT_ASSETS')) and sv(r.get('TOTAL_CURRENT_LIAB')) else None
            ),
            'equityMultiplier': rd(
                ta / sv(r.get('TOTAL_EQUITY'))
                if ta and sv(r.get('TOTAL_EQUITY')) else None
            ),
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 现金流量表 (与 fetch_eastmoney.py 完全一致)
# ─────────────────────────────────────────────────────────────────────────────

def transform_cashflow(raw_list):
    """转换现金流量表 → 前端期望的字段名"""
    results = []
    for r in raw_list:
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


# ─────────────────────────────────────────────────────────────────────────────
# 公司概况 (emweb CompanySurvey — 更丰富的字段)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_profile(code):
    """从 emweb 获取公司概况"""
    scode = f"{em_prefix(code)}{code}"
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={scode}"
    data = curl_json(url, timeout=15,
                     referer='https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/Index')
    if not data:
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


def fetch_profile_fallback(scode):
    """从 datacenter API 获取公司概况 (备用, 字段较少)"""
    url = (
        f'{DC_BASE}?type=RPT_F10_ORG_BASICINFO'
        f'&sty=SECUCODE,SECURITY_NAME_ABBR,SECURITY_CODE,EM2016,CSRC_INDUSTRY_NAME,'
        f'ORG_NAME,CHAIRMAN,PROVINCE,TRADE_MARKET,SECURITY_TYPE'
        f'&filter=(SECUCODE=%22{scode}%22)'
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
        'code': it.get('SECURITY_CODE', scode.split('.')[0]),
        'fullName': it.get('ORG_NAME', ''),
        'chairman': it.get('CHAIRMAN', ''),
        'legalPerson': '',
        'secretary': '',
        'industry': it.get('EM2016', ''),
        'industryCSRC': it.get('CSRC_INDUSTRY_NAME', ''),
        'mainBusiness': '',
        'province': it.get('PROVINCE', ''),
        'website': '',
        'description': '',
        'setupDate': '',
        'listDate': '',
        'securityType': it.get('SECURITY_TYPE', ''),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 股东信息 (emweb)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_shareholders(code):
    """十大股东 + 十大流通股东 + 股东户数 + 机构持仓 + 基金持仓 + 分红"""
    scode = f"{em_prefix(code)}{code}"
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={scode}"
    data = curl_json(url, timeout=15,
                     referer='https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/Index')
    if not data:
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


# ─────────────────────────────────────────────────────────────────────────────
# 分红数据 (datacenter RPT_SHAREBONUS_DET)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_dividends(scode):
    """获取分红记录"""
    url = (
        f'{DC_BASE}?type=RPT_SHAREBONUS_DET'
        f'&sty=ALL'
        f'&filter=(SECUCODE=%22{scode}%22)'
        f'&p=1&ps=15&sr=-1&st=REPORT_DATE&token={TOKEN}'
    )
    data = curl_json(url)
    if not data or not data.get('success'):
        return []
    items = (data.get('result') or {}).get('data', []) or []
    results = []
    for d in items:
        results.append({
            'noticeDate': (d.get('PLAN_NOTICE_DATE') or '')[:10],
            'reportDate': (d.get('REPORT_DATE') or '')[:10],
            'bonusPer10': d.get('PRETAX_BONUS_RMB'),
            'stockBonusPer10': d.get('BONUS_IT_RATIO'),
            'convertPer10': d.get('CONVERT_IT_RATIO'),
            'status': d.get('ASSIGN_PROGRESS', ''),
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def process_stock(scode):
    """处理单只股票, 输出与 fetch_eastmoney.py 完全一致的格式"""
    code = scode.split('.')[0]
    out_file = os.path.join(EASTMONEY_DIR, f'{code}.json')

    print(f"  {code} ({scode})...", end='', flush=True)

    # 1. 公司概况: 优先 emweb (丰富), 失败回退 datacenter
    profile = fetch_profile(code)
    if not profile or not profile.get('name'):
        profile = fetch_profile_fallback(scode) or {}
    if not profile.get('name'):
        print(" 无公司信息, 跳过")
        return None
    time.sleep(0.2)

    # 2. 主要财务指标 (20期)
    raw_financial = fetch_dc('RPT_F10_FINANCE_MAINFINADATA', scode, 20)
    financial = transform_financial(raw_financial)
    time.sleep(0.2)

    # 3. 利润表 (20期)
    raw_income = fetch_dc('RPT_F10_FINANCE_GINCOME', scode, 20)
    income = transform_income(raw_income)
    time.sleep(0.2)

    # 4. 资产负债表 (20期, 银行/保险返回空)
    raw_balance = fetch_dc('RPT_F10_FINANCE_GBALANCE', scode, 20)
    balance = transform_balance(raw_balance)
    time.sleep(0.2)

    # 5. 现金流量表 (20期, 银行/保险返回空)
    raw_cashflow = fetch_dc('RPT_F10_FINANCE_GCASHFLOW', scode, 20)
    cashflow = transform_cashflow(raw_cashflow)
    time.sleep(0.2)

    # 6. 股东 (emweb)
    shareholder = fetch_shareholders(code)
    time.sleep(0.2)

    # 7. 分红 (datacenter)
    dividends = fetch_dividends(scode)
    if dividends:
        shareholder['dividends'] = dividends
    time.sleep(0.2)

    # 8. 组装 — 键名与 fetch_eastmoney.py 完全一致
    result = {
        'code': code,
        'name': profile.get('name', ''),
        'fetchTime': datetime.now().isoformat(),
        'profile': profile,
        'financial': financial,
        'income': income,
        'balance': balance,
        'cashflow': cashflow,
        'shareholder': shareholder,
    }

    # 如果已有文件, 保留 cninfo 等额外字段
    if os.path.exists(out_file):
        try:
            with open(out_file) as f:
                existing = json.load(f)
            for key in ['cninfo', 'quote', 'industryPeers']:
                if key in existing and key not in result:
                    result[key] = existing[key]
        except Exception:
            pass

    # 8. 保存
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    fin_count = len(financial)
    bal_count = len(balance)
    cf_count = len(cashflow)
    sh_count = len(shareholder.get('top10Holders', []))
    div_count = len(shareholder.get('dividends', []))
    print(f" OK ({profile.get('name', '?')} | fin:{fin_count} bal:{bal_count} cf:{cf_count} sh:{sh_count} div:{div_count})")
    return result


def main():
    resume = '--resume' in sys.argv
    codes = [a for a in sys.argv[1:] if not a.startswith('--')]

    if not codes:
        codes_file = os.path.join(DATA_DIR, 'hs300_codes.json')
        if not os.path.exists(codes_file):
            print(f"错误: 未找到 {codes_file}")
            sys.exit(1)
        with open(codes_file) as f:
            data = json.load(f)
        codes = data['codes']
        print(f"从 hs300_codes.json 加载 {len(codes)} 只股票")

    total = len(codes)
    done = 0
    skipped = 0
    failed = 0

    for i, scode in enumerate(codes):
        code = scode.split('.')[0]
        out_file = os.path.join(EASTMONEY_DIR, f'{code}.json')

        if resume and os.path.exists(out_file):
            skipped += 1
            continue

        try:
            result = process_stock(scode)
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

        # 限速
        time.sleep(0.2)

    print(f"\n{'='*60}")
    print(f"完成! 总计: {total}, 新处理: {done}, 跳过: {skipped}, 失败: {failed}")


if __name__ == '__main__':
    main()
