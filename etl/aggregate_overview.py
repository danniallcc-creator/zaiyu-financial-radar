#!/usr/bin/env python3
"""
aggregate_overview.py — 聚合 A 股出海营收市场概览数据

输入:
  - data/overseas_raw/all_mainop.json (批量抓取的地区分拆数据)
  - data/industry_classify.json       (申万行业分类)
  - data/eastmoney/*.json             (个股 profile，用于获取板块/行业信息)

输出:
  - data/market_overview.json

用法:
    python etl/aggregate_overview.py
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

# ─── 路径 ────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
RAW_FILE = os.path.join(DATA_DIR, 'overseas_raw', 'all_mainop.json')
CLASSIFY_FILE = os.path.join(DATA_DIR, 'industry_classify.json')
EASTMONEY_DIR = os.path.join(DATA_DIR, 'eastmoney')
OUTPUT_FILE = os.path.join(DATA_DIR, 'market_overview.json')

# ─── 常量 ────────────────────────────────────────────────────────────────────

# 5 年时间窗口
YEARS = list(range(2025, 2020, -1))  # 2025, 2024, 2023, 2022, 2021

# 报告期类型
PERIOD_TYPES = {
    'annual': ['12-31'],
    'interim': ['06-30'],
    'q1': ['03-31'],
    'q3': ['09-30'],
}

# 海外关键词正则
OVERSEAS_RE = re.compile(r'海外|境外|国际|国外|出口|外国|overseas|abroad', re.IGNORECASE)
DOMESTIC_RE = re.compile(r'国内|境内|中国|内地|内销|大陆|中港澳|含港澳台', re.IGNORECASE)
SKIP_RE = re.compile(r'合计|抵消|调整|内部|补充|其他|未分配', re.IGNORECASE)


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def get_board(code: str) -> str:
    """根据股票代码判断板块"""
    if code.startswith(('8', '4')):
        return '北交所'
    if code.startswith(('688', '689')):
        return '科创板'
    if code.startswith(('3',)):
        return '创业板'
    if code.startswith(('6',)):
        return '沪主板'
    return '深主板'


def get_shenwan_l1(industry: str) -> str:
    """从申万行业字段提取一级行业"""
    if not industry:
        return '未分类'
    return industry.split('-')[0].strip()


def get_csrc_l1(industry_csrc: str) -> str:
    """从证监会行业字段提取大类"""
    if not industry_csrc:
        return '未分类'
    return industry_csrc.split('-')[0].strip()


def is_overseas_item(name: str) -> bool:
    """判断一个 ITEM_NAME 是否为海外收入"""
    if not name:
        return False
    if SKIP_RE.search(name):
        return False
    return bool(OVERSEAS_RE.search(name))


def safe_float(val):
    """安全数值转换"""
    if val is None or val == '' or val == '-':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ─── 数据加载 ────────────────────────────────────────────────────────────────

def load_classify() -> dict:
    """
    加载申万行业分类索引: code → {name, industry, industryCSRC}
    合并两个数据源:
      1. industry_classify.json (4887 只新股)
      2. eastmoney/*.json profile (338 只 HS300 老股)
    """
    index = {}

    # 1. 从 industry_classify.json 加载
    if os.path.exists(CLASSIFY_FILE):
        with open(CLASSIFY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for s in data:
            code = s.get('code', '')
            index[code] = {
                'name': s.get('name', ''),
                'industry': s.get('industry', ''),
                'industryCSRC': s.get('industryCSRC', ''),
            }
        print(f'  classify: {len(data)} 只')

    # 2. 从 eastmoney profile 补充缺失的股票
    if os.path.isdir(EASTMONEY_DIR):
        added = 0
        for fname in os.listdir(EASTMONEY_DIR):
            if not fname.endswith('.json'):
                continue
            code = fname[:-5]
            if code in index:
                continue
            fpath = os.path.join(EASTMONEY_DIR, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                p = d.get('profile', {})
                index[code] = {
                    'name': d.get('name', p.get('name', '')),
                    'industry': p.get('industry', ''),
                    'industryCSRC': p.get('industryCSRC', ''),
                }
                added += 1
            except Exception:
                continue
        if added:
            print(f'  eastmoney profile 补充: {added} 只')

    return index


def load_profiles() -> dict:
    """加载个股 profile 信息: code → {securityType, industry, industryCSRC, name}"""
    profiles = {}
    if not os.path.isdir(EASTMONEY_DIR):
        return profiles
    for fname in os.listdir(EASTMONEY_DIR):
        if not fname.endswith('.json'):
            continue
        code = fname[:-5]
        fpath = os.path.join(EASTMONEY_DIR, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                d = json.load(f)
            p = d.get('profile', {})
            profiles[code] = {
                'name': d.get('name', p.get('name', '')),
                'securityType': p.get('securityType', ''),
                'industry': p.get('industry', ''),
                'industryCSRC': p.get('industryCSRC', ''),
            }
        except Exception:
            continue
    return profiles


def load_raw_mainop() -> dict:
    """加载批量抓取的 MAINOP 原始数据，按股票+日期分组"""
    if not os.path.exists(RAW_FILE):
        print(f'[ERROR] 未找到 {RAW_FILE}，请先运行 fetch_overseas_batch.py')
        sys.exit(1)

    with open(RAW_FILE, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    items = raw.get('items', [])
    print(f'加载 {len(items)} 条 MAINOP 原始记录')

    # 当前日期，用于过滤未来日期（数据错误）
    today = datetime.now().strftime('%Y-%m-%d')
    # 只保留标准报告期后缀
    valid_suffixes = {'03-31', '06-30', '09-30', '12-31'}

    # 按 SECUCODE + REPORT_DATE 分组
    grouped = defaultdict(list)
    skipped_future = 0
    skipped_nonstandard = 0
    for item in items:
        sc = item.get('SECUCODE', '')
        rd = (item.get('REPORT_DATE') or '')[:10]
        if not sc or not rd:
            continue
        # 跳过未来日期（数据异常）
        if rd > today:
            skipped_future += 1
            continue
        # 只保留标准报告期
        suffix = rd[5:]
        if suffix not in valid_suffixes:
            skipped_nonstandard += 1
            continue
        code = sc.split('.')[0]
        grouped[(code, rd)].append(item)

    if skipped_future:
        print(f'  跳过 {skipped_future} 条未来日期记录')
    if skipped_nonstandard:
        print(f'  跳过 {skipped_nonstandard} 条非标准报告期记录')

    return grouped


# ─── 聚合逻辑 ────────────────────────────────────────────────────────────────

class Aggregator:
    """按维度聚合海外营收数据"""

    def __init__(self):
        # key: (period_type, report_date, dimension, group_name)
        self.accumulators = defaultdict(lambda: {
            'n_stocks': set(),
            'totalRev': 0.0,
            'overseasRev': 0.0,
            'overseasProfit': 0.0,
            'overseasIncome': 0.0,
        })

    def add_stock(self, code: str, report_date: str,
                  board: str, shenwan: str, csrc: str,
                  items: list):
        """
        将一只股票在某个报告期的地区分拆数据加入聚合
        items: MAINOP_TYPE=3 的记录列表
        """
        # 计算总营收和海外营收
        total_rev = 0.0
        overseas_rev = 0.0
        overseas_profit = 0.0
        overseas_income = 0.0
        has_data = False

        for item in items:
            income = safe_float(item.get('MAIN_BUSINESS_INCOME'))
            profit = safe_float(item.get('MAIN_BUSINESS_RPOFIT'))
            name = item.get('ITEM_NAME', '')

            if income is None:
                continue

            has_data = True
            total_rev += abs(income)

            if is_overseas_item(name):
                overseas_rev += abs(income)
                if income is not None:
                    overseas_income += income
                if profit is not None:
                    overseas_profit += profit

        if not has_data or total_rev == 0:
            return

        # 判断报告期类型
        for ptype, suffixes in PERIOD_TYPES.items():
            if any(report_date.endswith(s) for s in suffixes):
                # 按板块聚合
                self._accumulate(ptype, report_date, 'board', board,
                                 code, total_rev, overseas_rev,
                                 overseas_income, overseas_profit)
                # 按申万一级聚合
                self._accumulate(ptype, report_date, 'shenwan', shenwan,
                                 code, total_rev, overseas_rev,
                                 overseas_income, overseas_profit)
                # 按证监会行业聚合
                self._accumulate(ptype, report_date, 'csrc', csrc,
                                 code, total_rev, overseas_rev,
                                 overseas_income, overseas_profit)
                break

    def _accumulate(self, ptype, rdate, dim, group, code,
                    total_rev, overseas_rev, overseas_income, overseas_profit):
        key = (ptype, rdate, dim, group)
        acc = self.accumulators[key]
        acc['n_stocks'].add(code)
        acc['totalRev'] += total_rev
        acc['overseasRev'] += overseas_rev
        acc['overseasIncome'] += overseas_income
        acc['overseasProfit'] += overseas_profit

    def build_result(self) -> dict:
        """构建最终输出"""
        result = {}
        for ptype in PERIOD_TYPES:
            result[ptype] = {}
            # 收集该 ptype 的所有日期
            dates = sorted(set(
                rdate for (pt, rdate, _, _) in self.accumulators.keys()
                if pt == ptype
            ), reverse=True)

            # 只保留最近 5 年
            dates = [d for d in dates if int(d[:4]) >= min(YEARS)]

            for rdate in dates:
                period_data = {'board': {}, 'csrc': {}, 'shenwan': {}}
                total_coverage = 0

                for dim in ('board', 'csrc', 'shenwan'):
                    groups = {}
                    for (pt, rd, d, g) in self.accumulators.keys():
                        if pt == ptype and rd == rdate and d == dim:
                            acc = self.accumulators[(pt, rd, d, g)]
                            n = len(acc['n_stocks'])
                            totalRev = round(acc['totalRev'], 2)
                            overseasRev = round(acc['overseasRev'], 2)
                            ratio = round(overseasRev / totalRev * 100, 2) if totalRev > 0 else None
                            gm = None
                            if acc['overseasIncome'] and acc['overseasIncome'] > 0:
                                gm = round(acc['overseasProfit'] / acc['overseasIncome'] * 100, 2)

                            groups[g] = {
                                'n': n,
                                'totalRev': totalRev,
                                'overseasRev': overseasRev,
                                'ratio': ratio,
                                'gm': gm,
                            }
                            if dim == 'board':
                                total_coverage += n

                    period_data[dim] = groups

                # 计算同比 (YoY)
                prev_date = self._prev_year_date(rdate)
                if prev_date:
                    for dim in ('board', 'csrc', 'shenwan'):
                        prev_key_base = (ptype, prev_date, dim)
                        for g, cur in period_data[dim].items():
                            prev_key = (ptype, prev_date, dim, g)
                            if prev_key in self.accumulators:
                                prev_acc = self.accumulators[prev_key]
                                prev_overseas = round(prev_acc['overseasRev'], 2)
                                if prev_overseas > 0 and cur['overseasRev'] is not None:
                                    yoy = round((cur['overseasRev'] - prev_overseas) / prev_overseas * 100, 2)
                                    cur['yoy'] = yoy
                                else:
                                    cur['yoy'] = None
                            else:
                                cur['yoy'] = None

                # 标记稀疏数据
                sparse = total_coverage < 50
                result[ptype][rdate] = {
                    **period_data,
                    '_coverage': total_coverage,
                    '_sparse': sparse,
                }

        return result

    def _prev_year_date(self, rdate: str) -> str:
        """获取去年同期的日期"""
        try:
            y = int(rdate[:4]) - 1
            return f'{y}{rdate[4:]}'
        except Exception:
            return None


# ─── TOP 排行榜 ───────────────────────────────────────────────────────────────

def build_top_overseas(grouped: dict, classify: dict, profiles: dict) -> list:
    """构建出海营收 TOP 100 排行榜 (取最新年报)"""
    # 找最新年报日期
    all_dates = set()
    for (code, rd) in grouped.keys():
        if rd.endswith('12-31'):
            all_dates.add(rd)
    if not all_dates:
        return []

    latest = max(all_dates)
    prev_year = f'{int(latest[:4]) - 1}{latest[4:]}'

    stocks = []
    for (code, rd), items in grouped.items():
        if rd != latest:
            continue

        total_rev = 0.0
        overseas_rev = 0.0
        for item in items:
            income = safe_float(item.get('MAIN_BUSINESS_INCOME'))
            if income is None:
                continue
            total_rev += abs(income)
            if is_overseas_item(item.get('ITEM_NAME', '')):
                overseas_rev += abs(income)

        if overseas_rev <= 0 or total_rev <= 0:
            continue

        info = classify.get(code, profiles.get(code, {}))
        name = info.get('name', '')
        shenwan = get_shenwan_l1(info.get('industry', ''))
        board = get_board(code)
        ratio = round(overseas_rev / total_rev * 100, 2)

        # 读取总市值 (从 eastmoney quote 数据)
        totalMV = None
        em_path = os.path.join(EASTMONEY_DIR, f'{code}.json')
        if os.path.exists(em_path):
            try:
                with open(em_path, 'r', encoding='utf-8') as f:
                    em_data = json.load(f)
                mv = em_data.get('quote', {}).get('totalMV')
                if mv and mv > 0:
                    totalMV = round(mv, 2)
            except Exception:
                pass

        # 计算 YoY
        yoy = None
        totalRevYoy = None
        prev_items = grouped.get((code, prev_year), [])
        if prev_items:
            prev_overseas = 0.0
            prev_total = 0.0
            for item in prev_items:
                income = safe_float(item.get('MAIN_BUSINESS_INCOME'))
                if income is None:
                    continue
                prev_total += abs(income)
                if is_overseas_item(item.get('ITEM_NAME', '')):
                    prev_overseas += abs(income)
            if prev_overseas > 0:
                yoy = round((overseas_rev - prev_overseas) / prev_overseas * 100, 2)
            if prev_total > 0:
                totalRevYoy = round((total_rev - prev_total) / prev_total * 100, 2)

        stocks.append({
            'code': code,
            'name': name,
            'board': board,
            'sw': shenwan,
            'overseasRev': round(overseas_rev, 2),
            'totalRev': round(total_rev, 2),
            'totalRevYoy': totalRevYoy,
            'ratio': ratio,
            'yoy': yoy,
            'totalMV': totalMV,
        })

    # 按海外营收降序
    stocks.sort(key=lambda x: x['overseasRev'], reverse=True)
    return stocks[:100]


# ─── 主流程 ──────────────────────────────────────────────────────────────────

def main():
    print('=== A 股出海营收市场概览 — 聚合脚本 ===\n')

    # 1. 加载数据
    print('[1/4] 加载分类数据...')
    classify = load_classify()
    print(f'  分类: {len(classify)} 只')

    print('[2/4] 加载个股 profile...')
    profiles = load_profiles()
    print(f'  Profile: {len(profiles)} 只')

    print('[3/4] 加载 MAINOP 原始数据...')
    grouped = load_raw_mainop()
    unique_stocks = set(code for (code, _) in grouped.keys())
    print(f'  覆盖: {len(unique_stocks)} 只股票, {len(grouped)} 个 (股票, 报告期) 组合')

    # 2. 聚合
    print('[4/4] 聚合计算...')
    agg = Aggregator()

    for (code, rd), items in grouped.items():
        # 获取分类信息
        info = classify.get(code, {})
        if not info:
            info = profiles.get(code, {})

        name = info.get('name', '')
        industry = info.get('industry', '')
        industry_csrc = info.get('industryCSRC', '')

        board = get_board(code)
        shenwan = get_shenwan_l1(industry)
        csrc = get_csrc_l1(industry_csrc)

        agg.add_stock(code, rd, board, shenwan, csrc, items)

    # 3. 构建结果
    result_data = agg.build_result()

    # 4. 构建 TOP 100
    top_overseas = build_top_overseas(grouped, classify, profiles)
    print(f'  TOP 100: {len(top_overseas)} 只 (最新年报)')

    # 5. 统计信息
    meta = {
        'generatedAt': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'stockCount': len(unique_stocks),
        'classifyCount': len(classify),
        'source': 'RPT_F10_FN_MAINOP (MAINOP_TYPE=3)',
        'years': YEARS,
    }

    # 统计各报告期覆盖情况
    period_coverage = {}
    for ptype in PERIOD_TYPES:
        if ptype in result_data:
            for rdate, pdata in result_data[ptype].items():
                if not rdate.startswith('_'):
                    period_coverage[f'{ptype}:{rdate}'] = pdata.get('_coverage', 0)
    meta['periodCoverage'] = period_coverage

    # 提取最新年报年份
    annual_dates = sorted([k for k in result_data.get('annual', {}).keys() if not k.startswith('_')], reverse=True)
    if annual_dates:
        meta['latestYear'] = int(annual_dates[0][:4])

    # 6. 输出
    output = {
        'meta': meta,
        **result_data,
        'topOverseas': top_overseas,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=None)

    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f'\n{"="*60}')
    print(f'输出: {OUTPUT_FILE} ({size_kb:.0f} KB)')

    # 打印摘要
    for ptype in ('annual', 'interim', 'q3', 'q1'):
        if ptype in result_data:
            dates = sorted([d for d in result_data[ptype].keys() if not d.startswith('_')], reverse=True)
            if dates:
                latest = dates[0]
                pdata = result_data[ptype][latest]
                coverage = pdata.get('_coverage', 0)
                n_boards = len([k for k in pdata.get('board', {}).keys() if not k.startswith('_')])
                n_sw = len([k for k in pdata.get('shenwan', {}).keys() if not k.startswith('_')])
                sparse = pdata.get('_sparse', False)
                print(f'  {ptype:8s} {latest}: {coverage} 只, {n_boards} 板块, {n_sw} 申万一级 {"⚠稀疏" if sparse else ""}')


if __name__ == '__main__':
    main()
