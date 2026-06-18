#!/usr/bin/env python3
"""
run_target_etl.py — 并发执行1089只目标行业股票的全量ETL

三步流水线:
  Step 1: fetch_batch (核心财务) — 并发5 workers
  Step 2: fetch_overseas (出海分析) — 并发5 workers
  Step 3: fetch_industry_batch (行业对比) — 调用main()

用法:
  python etl/run_target_etl.py            # 全量执行
  python etl/run_target_etl.py --resume    # 跳过已有
  python etl/run_target_etl.py --step 1    # 只跑某一步 (1/2/3)
"""
import json, os, sys, time, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
sys.path.insert(0, os.path.join(BASE_DIR, 'etl'))

CODES_FILE = os.path.join(DATA_DIR, 'target_codes.json')
EASTMONEY_DIR = os.path.join(DATA_DIR, 'eastmoney')
OVERSEAS_DIR = os.path.join(DATA_DIR, 'overseas')
INDUSTRY_DIR = os.path.join(DATA_DIR, 'industry')

args = sys.argv[1:]
RESUME = '--resume' in args
STEP = None
if '--step' in args:
    idx = args.index('--step')
    STEP = int(args[idx + 1])

with open(CODES_FILE) as f:
    ALL_CODES = json.load(f)['codes']  # e.g. ['600103.SH', '000001.SZ']
print(f'目标股票: {len(ALL_CODES)} 只\n')


# ═══════════════════════════════════════════════════════════════
# Step 1: fetch_batch (核心财务数据)
# ═══════════════════════════════════════════════════════════════
def run_step1():
    import fetch_batch
    print('='*60)
    print('Step 1: fetch_batch — 核心财务数据')
    print('='*60)

    codes = ALL_CODES
    if RESUME:
        codes = [c for c in codes if not os.path.exists(
            os.path.join(EASTMONEY_DIR, f"{c.split('.')[0]}.json"))]
    print(f'待处理: {len(codes)} 只 (跳过 {len(ALL_CODES)-len(codes)})')

    success, failed = 0, 0
    t0 = time.time()

    def worker(scode):
        try:
            result = fetch_batch.process_stock(scode)
            return (True, scode) if result else (False, scode)
        except Exception as e:
            return (False, scode)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(worker, c): c for c in codes}
        done = 0
        for fut in as_completed(futures):
            done += 1
            ok, scode = fut.result()
            if ok:
                success += 1
            else:
                failed += 1
            if done % 50 == 0 or done == len(codes):
                elapsed = time.time() - t0
                speed = done / elapsed if elapsed > 0 else 0
                eta = (len(codes) - done) / speed if speed > 0 else 0
                print(f'  [{done}/{len(codes)}] ✓{success} ✗{failed} '
                      f'| {elapsed:.0f}s ({speed:.1f}/s) ETA {eta:.0f}s')

    elapsed = time.time() - t0
    print(f'\nStep 1 完成: ✓{success} ✗{failed} 耗时 {elapsed:.0f}s\n')


# ═══════════════════════════════════════════════════════════════
# Step 2: fetch_overseas (出海分析数据)
# ═══════════════════════════════════════════════════════════════
def run_step2():
    import fetch_overseas
    print('='*60)
    print('Step 2: fetch_overseas — 出海分析数据')
    print('='*60)

    items = []
    for scode in ALL_CODES:
        code = scode.split('.')[0]  # raw code
        if RESUME and os.path.exists(os.path.join(OVERSEAS_DIR, f'{code}.json')):
            continue
        if not os.path.exists(os.path.join(EASTMONEY_DIR, f'{code}.json')):
            continue
        items.append((code, scode))  # (raw_code, secucode)

    print(f'待处理: {len(items)} 只')

    success, failed = 0, 0
    t0 = time.time()

    def worker(code, sc):
        try:
            name = ''
            em_path = os.path.join(EASTMONEY_DIR, f'{code}.json')
            if os.path.exists(em_path):
                with open(em_path) as f:
                    name = json.load(f).get('name', '')
            segments = fetch_overseas.fetch_segments(sc)
            analysis = fetch_overseas.fetch_analysis(sc)
            if segments or analysis:
                result = {
                    'code': code, 'name': name,
                    'fetchTime': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'segments': segments, 'analysis': analysis,
                }
                out_path = os.path.join(OVERSEAS_DIR, f'{code}.json')
                with open(out_path, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False)
                return True
            return False
        except:
            return False

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(worker, c, s): c for c, s in items}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if fut.result():
                success += 1
            else:
                failed += 1
            if done % 50 == 0 or done == len(items):
                elapsed = time.time() - t0
                speed = done / elapsed if elapsed > 0 else 0
                eta = (len(items) - done) / speed if speed > 0 else 0
                print(f'  [{done}/{len(items)}] ✓{success} ✗{failed} '
                      f'| {elapsed:.0f}s ({speed:.1f}/s) ETA {eta:.0f}s')

    elapsed = time.time() - t0
    print(f'\nStep 2 完成: ✓{success} ✗{failed} 耗时 {elapsed:.0f}s\n')


# ═══════════════════════════════════════════════════════════════
# Step 3: fetch_industry_batch (行业对比数据)
# ═══════════════════════════════════════════════════════════════
def run_step3():
    print('='*60)
    print('Step 3: fetch_industry_batch — 行业对比数据')
    print('='*60)

    # 直接调用 fetch_industry_batch 的 main()
    cmd = ['python3', os.path.join(BASE_DIR, 'etl', 'fetch_industry_batch.py')]
    if RESUME:
        cmd.append('--resume')
    t0 = time.time()
    result = subprocess.run(cmd, cwd=BASE_DIR)
    elapsed = time.time() - t0
    print(f'\nStep 3 完成: rc={result.returncode} 耗时 {elapsed:.0f}s\n')


# ═══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    t_total = time.time()
    if STEP is None or STEP == 1:
        run_step1()
    if STEP is None or STEP == 2:
        run_step2()
    if STEP is None or STEP == 3:
        run_step3()

    total = time.time() - t_total
    print('='*60)
    print(f'全部完成! 总耗时 {total:.0f}s ({total/60:.1f}分钟)')
    print('='*60)
