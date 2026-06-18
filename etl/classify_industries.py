#!/usr/bin/env python3
"""
classify_industries.py — 并发查询全A股申万行业分类

从 sina_all_a.json 读取股票列表，排除已有 eastmoney 数据，
并发调用 emweb CompanySurvey API 获取申万行业分类。

输出: data/industry_classify.json
"""
import json, os, sys, time, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
EM_DIR = os.path.join(DATA_DIR, 'eastmoney')

# ── 已有股票 ──
existing = set()
for f in os.listdir(EM_DIR):
    if f.endswith('.json'):
        existing.add(f[:-5])
print(f'已有数据: {len(existing)} 只')

# ── 新浪全A股列表 ──
with open(os.path.join(DATA_DIR, 'sina_all_a.json')) as f:
    all_stocks = json.load(f)

# 过滤: 沪深A股 + 排除已有
new_stocks = [s for s in all_stocks if s['code'] not in existing]
print(f'新增待查: {len(new_stocks)} 只')

# ── 行业查询 ──
def get_industry(stock):
    code = stock['code']
    prefix = 'SH' if code.startswith('6') else 'SZ'
    scode = f'{prefix}{code}'
    url = f'https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={scode}'
    try:
        result = subprocess.run(
            ['curl', '-s', '--max-time', '8', url,
             '-H', 'User-Agent: Mozilla/5.0',
             '-H', 'Referer: https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/Index'],
            capture_output=True, text=True, timeout=12
        )
        if result.returncode != 0 or not result.stdout:
            return None
        d = json.loads(result.stdout)
        jbzl = d.get('jbzl', [])
        if jbzl:
            info = jbzl[0] if isinstance(jbzl, list) else jbzl
            return {
                'code': code,
                'name': info.get('SECURITY_NAME_ABBR', stock.get('name', '')),
                'industry': info.get('EM2016', ''),
                'industryCSRC': info.get('INDUSTRYCSRC1', ''),
            }
    except:
        pass
    return None

# ── 并发执行 ──
WORKERS = 10
results = []
failed = 0
t0 = time.time()

print(f'\n开始并发查询 ({WORKERS} workers)...')
with ThreadPoolExecutor(max_workers=WORKERS) as executor:
    futures = {executor.submit(get_industry, s): s for s in new_stocks}
    done_count = 0
    for future in as_completed(futures):
        done_count += 1
        r = future.result()
        if r:
            results.append(r)
        else:
            failed += 1
        if done_count % 200 == 0:
            elapsed = time.time() - t0
            speed = done_count / elapsed
            eta = (len(new_stocks) - done_count) / speed if speed > 0 else 0
            print(f'  进度: {done_count}/{len(new_stocks)} ({elapsed:.0f}s, {speed:.1f}/s, ETA {eta:.0f}s)')

elapsed = time.time() - t0
print(f'\n完成: {len(results)} 成功, {failed} 失败, 耗时 {elapsed:.1f}s')

# ── 保存结果 ──
out_path = os.path.join(DATA_DIR, 'industry_classify.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=None)
print(f'已保存: {out_path} ({len(results)} 条)')

# ── 统计行业分布 ──
from collections import Counter
l1_industries = Counter()
for r in results:
    ind = r.get('industry', '')
    if ind:
        l1 = ind.split('-')[0] if '-' in ind else ind
        l1_industries[l1] += 1

print(f'\n申万一级行业分布 ({len(l1_industries)} 个):')
for name, cnt in l1_industries.most_common():
    print(f'  {name:8s} {cnt:4d} 只')
