"""
东财 F10 接口映射表
所有接口均使用 GET 请求，返回 JSON

接口格式: https://emweb.securities.eastmoney.com/PC_HSF10/{section}/...

市场代码规则:
  沪市 60xxxx → SH60xxxx
  深市 00xxxx → SZ00xxxx
  深市 30xxxx → SZ30xxxx
  北交所 8xxxxx → BJ8xxxxx
"""

def em_market_prefix(code: str) -> str:
    """根据股票代码返回东财市场前缀"""
    if code.startswith('6'):
        return 'SH'
    elif code.startswith(('0', '3')):
        return 'SZ'
    elif code.startswith(('8', '4')):
        return 'BJ'
    else:
        return 'SZ'


def em_secid(code: str) -> str:
    """生成东财 secid (市场号.代码)"""
    if code.startswith('6'):
        return f'1.{code}'
    elif code.startswith(('0', '3')):
        return f'0.{code}'
    elif code.startswith(('8', '4')):
        return f'0.{code}'
    else:
        return f'0.{code}'


def em_f10_url(code: str, section: str) -> dict:
    """
    生成东财 F10 各模块 URL
    section: financial | income | balance | cashflow | holder | dividend
    """
    prefix = em_market_prefix(code)
    scode = f"{prefix}{code}"

    urls = {
        # 主要财务指标（扣非净利、ROE等）
        "financial": {
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYFXListV2Ajax?companyType=4&reportDateType=0&reportType=1&endDate=&code={scode}",
            "desc": "主要财务指标（含扣非净利）",
            "fields": ["REPORT_DATE", "BASIC_EPS", "TOTAL_OPERATE_INCOME", "TOTAL_PROFIT",
                       "DEDUCT_PARENT_NETPROFIT", "WEIGHTAVG_ROE", "MGJYXJJE", "XSMLL"]
        },

        # 利润表
        "income": {
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/lrbTableAjax?companyType=4&reportDateType=0&reportType=1&endDate=&code={scode}",
            "desc": "利润表",
            "fields": ["REPORT_DATE", "TOTAL_OPERATE_INCOME", "OPERATE_PROFIT",
                       "TOTAL_PROFIT", "NETPROFIT", "PARENT_NETPROFIT",
                       "OPERATE_INCOME", "OPERATE_COST", "OPERATE_EXPENSE",
                       "SALE_EXPENSE", "MANAGE_EXPENSE", "RESEARCH_EXPENSE",
                       "FINANCE_EXPENSE"]
        },

        # 资产负债表
        "balance": {
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbTableAjax?companyType=4&reportDateType=0&reportType=1&endDate=&code={scode}",
            "desc": "资产负债表",
            "fields": ["REPORT_DATE", "TOTAL_ASSETS", "TOTAL_LIABILITIES",
                       "TOTAL_EQUITY", "PARENT_EQUITY", "MONETARYFUNDS",
                       "INVENTORY", "ACCOUNTS_RECE", "FIXED_ASSET",
                       "SHORT_LOAN", "LONG_LOAN"]
        },

        # 现金流量表
        "cashflow": {
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbTableAjax?companyType=4&reportDateType=0&reportType=1&endDate=&code={scode}",
            "desc": "现金流量表",
            "fields": ["REPORT_DATE", "SALES_SERVICES", "NETCASH_OPERATE",
                       "NETCASH_INVEST", "NETCASH_FINANCE", "CCE_ADD",
                       "FREE_CASHFLOW"]
        },

        # 前十大股东
        "holder": {
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={scode}",
            "desc": "十大股东+股东户数",
            "multi": True  # 返回多个key
        },

        # 分红送配
        "dividend": {
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={scode}",
            "desc": "分红送配方案",
            "multi": True
        },

        # 公司概况
        "profile": {
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={scode}",
            "desc": "公司概况（行业分类、主营业务）"
        },

        # 管理层讨论与分析（经营情况讨论与分析）
        "manage_discuss": {
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/CoreReadPage/PageAjax?code={scode}",
            "desc": "经营情况讨论与分析"
        },

        # 海外营收（分部信息）
        "segment": {
            "url": f"https://emweb.securities.eastmoney.com/PC_HSF10/CoreReadPage/PageAjax?code={scode}",
            "desc": "分部信息（含海外营收）"
        }
    }

    return urls.get(section, {})


# 东财行情接口（push2）
EM_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EM_QUOTE_FIELDS = "f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f116,f117,f162,f167,f168,f169,f170"

# 东财搜索接口（模糊搜索）
EM_SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"


def em_search_params(keyword: str) -> dict:
    """生成东财搜索请求参数"""
    return {
        "input": keyword,
        "type": "14",
        "token": "D43BF722C8E33BDC906FB84D85E326E8",
        "count": "30",
        "cb": ""
    }
