#!/usr/bin/env python3
"""
巨潮资讯 年报公告抓取 & PDF 结构化提取脚本

功能:
  1. 从 cninfo.com.cn 拉取年报公告列表
  2. 下载最新年报 PDF
  3. 用 pdfplumber 提取文本并按章节切分
  4. 对 MD&A 段落做情感/标签标注
  5. 输出 data/cninfo/{code}_sections.json

用法:
  python fetch_cninfo.py 600519                 # 完整流程（含 PDF 下载）
  python fetch_cninfo.py 600519 --skip-pdf      # 仅拉取公告列表，不下载 PDF
  python fetch_cninfo.py 000858,000001          # 多只股票
"""

import sys
import json
import os
import re
import time
import subprocess
import urllib.parse
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# 路径 & 常量
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
OUTPUT_DIR = os.path.join(BASE_DIR, 'data', 'cninfo')
PDF_DIR    = os.path.join(BASE_DIR, 'data', 'pdfs')

CNINFO_QUERY_URL = 'http://www.cninfo.com.cn/new/hisAnnouncement/query'
CNINFO_PDF_BASE  = 'http://static.cninfo.com.cn/'

# 年报公告类别代码（深交所 + 上交所年度报告）
CATEGORY_ANNUAL_REPORT = 'category_ndbg_szsh'

# 章节提取正则（标题匹配）
SECTION_PATTERNS = {
    'shareholderLetter': [
        r'致\s*股\s*东\s*信',
        r'致\s*全\s*体\s*股\s*东',
        r'董\s*事\s*长\s*致\s*辞',
        r'致\s*投\s*资\s*者',
    ],
    'mdna': [
        r'管\s*理\s*层\s*讨\s*论\s*与\s*分\s*析',
        r'经\s*营\s*情\s*况\s*讨\s*论\s*与\s*分\s*析',
    ],
    'relatedParty': [
        r'重\s*要\s*事\s*项',   # 在"重要事项"节内再找"关联交易"
    ],
    'top10Holders': [
        r'前\s*十\s*大\s*股\s*东',
        r'前\s*十\s*名\s*股\s*东',
    ],
    'dividend': [
        r'利\s*润\s*分\s*配',
        r'分\s*红\s*方\s*案',
        r'现\s*金\s*分\s*红',
    ],
    'segment': [
        r'分\s*部\s*信\s*息',
        r'地\s*区\s*分\s*部',
        r'境\s*外\s*营\s*业\s*收\s*入',
        r'海\s*外\s*营\s*收',
    ],
    'nonRecurring': [
        r'非\s*经\s*常\s*性\s*损\s*益',
    ],
}

# 关联交易子标题（在"重要事项"节内提取）
RELATED_PARTY_SUB_PATTERN = r'关\s*联\s*交\s*易'

# MD&A 段落情感/主题标签
SENTIMENT_LABELS = {
    '利好':  ['增长', '提升', '突破', '创新高', '同比增加', '稳步提升', '战略突破', '新高', '领先'],
    '利空':  ['下降', '下滑', '亏损', '减少', '承压', '不及预期', '风险', '挑战', '困难'],
    '增量':  ['新业务', '新产品', '新市场', '新增', '拓展', '布局', '扩张'],
    '新技术': ['研发', '专利', '人工智能', '大模型', 'AI', '技术升级', '数字化', '创新'],
    '海外':  ['海外', '出口', '境外', '国际市场', '欧洲', '北美', '东南亚', '出口额'],
    '国内':  ['国内', '境内', '中国市场', '本土', '内销'],
}

SECTION_CHAR_LIMIT = 8000


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def exchange_column(code: str) -> str:
    """根据股票代码判断交易所 column 参数"""
    if code.startswith('6'):
        return 'sse'      # 上交所
    elif code.startswith(('0', '3')):
        return 'szse'     # 深交所
    elif code.startswith(('8', '4')):
        return 'bse'      # 北交所
    return 'szse'


def exchange_plate(code: str) -> str:
    """根据股票代码判断 plate 参数"""
    if code.startswith('6'):
        return 'sh'
    elif code.startswith(('0', '3')):
        return 'sz'
    return 'sz;sh'


def lookup_org_id(code: str) -> str:
    """
    从巨潮 topSearch API 查询股票代码对应的 orgId
    例如 600519 -> gssh0600519
    返回 orgId 字符串，失败时返回空字符串
    """
    url = 'http://www.cninfo.com.cn/new/information/topSearch/query'
    body = urllib.parse.urlencode({'keyWord': code, 'maxSecNum': '10', 'maxListNum': '5'})

    for attempt in range(3):
        try:
            result = subprocess.run(
                [
                    'curl', '-s', '-f', '--max-time', '10',
                    '-X', 'POST', url,
                    '-H', 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8',
                    '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    '-H', 'Accept: application/json',
                    '-d', body,
                ],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0 or not result.stdout.strip():
                raise RuntimeError(f"curl exit {result.returncode}")
            items = json.loads(result.stdout)
            if not isinstance(items, list):
                return ''
            for item in items:
                if item.get('code') == code:
                    org_id = item.get('orgId', '')
                    print(f"    orgId: {org_id} ({item.get('zwjc', '')})")
                    return org_id
            # 如果精确匹配没找到，返回第一个
            if items:
                org_id = items[0].get('orgId', '')
                print(f"    orgId (fuzzy): {org_id} ({items[0].get('zwjc', '')})")
                return org_id
            return ''
        except Exception as e:
            if attempt == 2:
                print(f"    [WARN] orgId 查询失败: {e}")
                return ''
            time.sleep(1 * (attempt + 1))
    return ''


def curl_post(url: str, data: dict, timeout: int = 20) -> str:
    """
    使用 subprocess curl 发送 POST 请求（规避 SSL 问题）
    与 fetch_eastmoney.py 保持一致的模式
    """
    body = urllib.parse.urlencode(data, doseq=True)
    for attempt in range(3):
        try:
            result = subprocess.run(
                [
                    'curl', '-s', '-f', '--max-time', str(timeout),
                    '-X', 'POST', url,
                    '-H', 'Content-Type: application/x-www-form-urlencoded',
                    '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    '-H', 'Accept: application/json',
                    '-d', body,
                ],
                capture_output=True, text=True, timeout=timeout + 5
            )
            if result.returncode != 0:
                raise RuntimeError(f"curl exit code {result.returncode}")
            return result.stdout
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(1 * (attempt + 1))
    return ''


def curl_download(url: str, dest: str, timeout: int = 120) -> bool:
    """用 curl 下载文件到 dest 路径"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for attempt in range(3):
        try:
            result = subprocess.run(
                [
                    'curl', '-s', '-f', '-L', '--max-time', str(timeout),
                    '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    '-o', dest,
                    url,
                ],
                capture_output=True, text=True, timeout=timeout + 10
            )
            if result.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 1024:
                return True
            raise RuntimeError(f"curl download exit {result.returncode}, size={os.path.getsize(dest) if os.path.exists(dest) else 0}")
        except Exception as e:
            if attempt == 2:
                print(f"    [WARN] 下载失败: {e}")
                return False
            time.sleep(2 * (attempt + 1))
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 1：拉取年报公告列表
# ─────────────────────────────────────────────────────────────────────────────

def fetch_annual_report_announcements(code: str, page_size: int = 5) -> list:
    """
    从巨潮资讯 API 拉取年报公告列表
    返回: [{title, date, pdfUrl, adjunctUrl, secid, ...}, ...]
    """
    column = exchange_column(code)
    plate  = exchange_plate(code)

    # 先查询 orgId（cninfo 要求 stock 字段格式为 "code,orgId"）
    print(f"    查询 orgId...")
    org_id = lookup_org_id(code)
    stock_param = f"{code},{org_id}" if org_id else code

    form_data = {
        'stock':     stock_param,
        'tabName':   'fulltext',
        'plate':     plate,
        'category':  CATEGORY_ANNUAL_REPORT,
        'pageNum':   '1',
        'pageSize':  str(page_size),
        'column':    column,
        'seDate':    '',
        'searchkey': '',
        'secid':     org_id,
        'sortName':  '',
        'sortType':  '',
        'isHLtitle': 'true',
    }

    print(f"    请求巨潮公告列表: stock={stock_param}, column={column}")
    raw = curl_post(CNINFO_QUERY_URL, form_data)

    if not raw.strip():
        print("    [WARN] 空响应")
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"    [WARN] JSON 解析失败: {e}")
        return []

    announcements = data.get('announcements', []) or []
    if not announcements:
        print("    [INFO] 未找到年报公告")
        return []

    results = []
    for ann in announcements:
        adjunct_url  = ann.get('adjunctUrl', '') or ''
        pdf_url      = CNINFO_PDF_BASE + adjunct_url if adjunct_url else ''
        ann_title    = ann.get('announcementTitle', '') or ''

        # announcementTime 是毫秒级时间戳
        ann_time = ann.get('announcementTime')
        ann_date_str = ''
        if ann_time and isinstance(ann_time, (int, float)):
            ann_date_str = datetime.fromtimestamp(ann_time / 1000).strftime('%Y-%m-%d')
        elif adjunct_url:
            # 从 adjunctUrl 路径中提取日期: finalpage/2026-04-17/xxx.PDF
            dm = re.search(r'(\d{4}-\d{2}-\d{2})', adjunct_url)
            if dm:
                ann_date_str = dm.group(1)

        results.append({
            'title':      ann_title,
            'date':       ann_date_str,
            'pdfUrl':     pdf_url,
            'adjunctUrl': adjunct_url,
            'orgId':      ann.get('orgId', ''),
            'annId':      ann.get('announcementId', ''),
        })

    print(f"    获取到 {len(results)} 条公告")

    # 筛选主年报：优先选择不含"英文""摘要""已取消"的年度报告
    def _is_main_report(ann: dict) -> bool:
        title = ann.get('title', '')
        exclude_keywords = ['英文', '摘要', '已取消', '更正', '补充', '审计报告']
        return ('年度报告' in title or '年报' in title) and \
               not any(kw in title for kw in exclude_keywords)

    main_reports = [a for a in results if _is_main_report(a)]
    if main_reports:
        # 将主年报排在最前面
        results = main_reports + [a for a in results if a not in main_reports]
        print(f"    优选主年报: {main_reports[0]['title'][:50]}")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 2：下载最新年报 PDF
# ─────────────────────────────────────────────────────────────────────────────

def extract_year_from_title(title: str) -> str:
    """从公告标题中提取年份，例如 '2023年年度报告' -> '2023'"""
    m = re.search(r'(\d{4})\s*年', title)
    if m:
        return m.group(1)
    # 兜底用当前年份 - 1
    return str(datetime.now().year - 1)


def download_latest_pdf(code: str, announcements: list) -> dict:
    """
    下载最新（第一条）年报 PDF
    返回: {pdfPath, year, pdfUrl, title, date} 或 None
    """
    if not announcements:
        print("    [WARN] 无公告可下载")
        return {}

    latest = announcements[0]
    pdf_url = latest.get('pdfUrl', '')
    if not pdf_url or 'static.cninfo.com.cn' not in pdf_url:
        print(f"    [WARN] PDF URL 无效: {pdf_url}")
        return {}

    year     = extract_year_from_title(latest.get('title', ''))
    filename = f"{code}_annual_{year}.pdf"
    dest     = os.path.join(PDF_DIR, filename)

    # 跳过已存在的文件
    if os.path.exists(dest) and os.path.getsize(dest) > 1024 * 100:
        print(f"    [INFO] PDF 已存在，跳过下载: {dest}")
        return {
            'pdfPath': dest,
            'year':    year,
            'pdfUrl':  pdf_url,
            'title':   latest.get('title', ''),
            'date':    latest.get('date', ''),
        }

    print(f"    下载 PDF: {filename}")
    print(f"    URL: {pdf_url}")
    ok = curl_download(pdf_url, dest)
    if not ok:
        return {}

    size_mb = os.path.getsize(dest) / (1024 * 1024)
    print(f"    下载完成: {dest} ({size_mb:.2f} MB)")
    return {
        'pdfPath': dest,
        'year':    year,
        'pdfUrl':  pdf_url,
        'title':   latest.get('title', ''),
        'date':    latest.get('date', ''),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 3：PDF 文本提取 & 章节切分
# ─────────────────────────────────────────────────────────────────────────────

def ensure_pdfplumber():
    """确保 pdfplumber 已安装，未安装则自动安装"""
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        print("    [INFO] pdfplumber 未安装，正在安装...")
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', 'pdfplumber', '--quiet'],
            check=True
        )
        print("    [INFO] pdfplumber 安装完成")


def extract_pdf_text(pdf_path: str) -> str:
    """
    用 pdfplumber 提取 PDF 全文
    对每一页提取文本，合并后返回
    """
    ensure_pdfplumber()
    import pdfplumber

    print(f"    提取 PDF 文本: {os.path.basename(pdf_path)}")
    pages_text = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            print(f"    共 {total_pages} 页")
            for i, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text(x_tolerance=3, y_tolerance=3)
                    if text:
                        pages_text.append(text)
                except Exception as e:
                    print(f"    [WARN] 第 {i+1} 页提取失败: {e}")
                # 每 50 页输出一次进度
                if (i + 1) % 50 == 0:
                    print(f"    进度: {i+1}/{total_pages} 页")
    except Exception as e:
        print(f"    [ERROR] PDF 打开失败: {e}")
        return ''

    full_text = '\n\n'.join(pages_text)
    print(f"    提取完成，共 {len(full_text)} 字符")
    return full_text


def find_section(text: str, patterns: list, char_limit: int = SECTION_CHAR_LIMIT) -> str:
    """
    在全文中查找第一个匹配的章节标题，提取其后 char_limit 字符
    跳过目录条目（包含 "..." 的行）
    """
    for pattern in patterns:
        # 允许标题前后有空白、换行、编号等
        # 匹配格式如 "第四节 管理层讨论与分析" 或 "三、管理层讨论与分析"
        regex = re.compile(
            r'(?:第[一二三四五六七八九十\d]+[章节]\s*|'
            r'[一二三四五六七八九十]+[、.]\s*|'
            r'\d+[、.]\s*)?'
            + pattern,
            re.IGNORECASE
        )
        search_start = 0
        for _ in range(5):
            m = regex.search(text, search_start)
            if not m:
                break

            # 检查是否在目录行中（目录行特征：标题后跟 "..." 或大量空白+页码）
            line_end = text.find('\n', m.start())
            if line_end == -1:
                line_end = len(text)
            line_text = text[m.start():line_end]

            if '...' in line_text or '…' in line_text:
                # 这是目录条目，跳过继续搜索
                search_start = line_end + 1
                continue

            start = m.end()
            # 跳过标题行剩余部分（到下一个换行）
            newline_pos = text.find('\n', start)
            if newline_pos != -1 and newline_pos - start < 100:
                start = newline_pos + 1
            extracted = text[start:start + char_limit]
            return extracted.strip()
    return ''


def find_related_party_section(text: str, char_limit: int = SECTION_CHAR_LIMIT) -> str:
    """
    在"重要事项"节内查找"关联交易"子节
    跳过目录条目
    """
    # 先找"重要事项"
    important_patterns = SECTION_PATTERNS['relatedParty']
    important_text = ''

    for pattern in important_patterns:
        regex = re.compile(
            r'(?:第[一二三四五六七八九十\d]+[章节]\s*|'
            r'[一二三四五六七八九十]+[、.]\s*|'
            r'\d+[、.]\s*)?'
            + pattern,
            re.IGNORECASE
        )
        search_start = 0
        found = False
        for _ in range(5):
            m = regex.search(text, search_start)
            if not m:
                break
            # 检查是否在目录行中
            line_end = text.find('\n', m.start())
            if line_end == -1:
                line_end = len(text)
            line_text = text[m.start():line_end]
            if '...' in line_text or '…' in line_text:
                search_start = line_end + 1
                continue
            # 找到真实章节
            important_start = m.end()
            important_text = text[important_start:important_start + 8000]
            found = True
            break
        if found:
            break

    if not important_text:
        return ''

    # 在重要事项内查找关联交易
    sub_regex = re.compile(RELATED_PARTY_SUB_PATTERN, re.IGNORECASE)
    m = sub_regex.search(important_text)
    if m:
        start = m.end()
        newline_pos = important_text.find('\n', start)
        if newline_pos != -1 and newline_pos - start < 80:
            start = newline_pos + 1
        return important_text[start:start + char_limit].strip()

    # 如果没找到子节，返回重要事项开头部分
    return important_text[:char_limit].strip()


def extract_sections(full_text: str) -> dict:
    """
    从 PDF 全文中提取所有章节
    返回: {shareholderLetter, mdna, relatedParty, top10Holders, dividend, segment, nonRecurring}
    """
    print("    提取章节...")
    sections = {}

    for section_key, patterns in SECTION_PATTERNS.items():
        if section_key == 'relatedParty':
            # 关联交易有特殊逻辑
            sections[section_key] = find_related_party_section(full_text)
        else:
            sections[section_key] = find_section(full_text, patterns)

        status = f"{len(sections[section_key])} 字符" if sections[section_key] else "未找到"
        print(f"      {section_key}: {status}")

    return sections


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 4：MD&A 段落标签标注
# ─────────────────────────────────────────────────────────────────────────────

def tag_mdna_paragraphs(mdna_text: str) -> list:
    """
    对 MD&A 章节按段落（双换行分隔）标注情感/主题标签
    返回: [{text, labels, isOverseas}, ...]
    """
    if not mdna_text:
        return []

    # PDF 文本中段落可能用单换行或双换行分隔
    # 先按双换行分，再对单换行的短行合并
    raw_paragraphs = [p.strip() for p in re.split(r'\n\s*\n', mdna_text) if p.strip()]

    # 对于太短的段落（PDF 换行造成的），尝试与下一段合并
    merged = []
    buf = ''
    for p in raw_paragraphs:
        if buf:
            buf += '\n' + p
        else:
            buf = p
        if len(buf) >= 40:
            merged.append(buf)
            buf = ''
    if buf:
        merged.append(buf)

    paragraphs = merged
    tagged = []

    for para in paragraphs:
        if len(para) < 15:
            continue

        matched_labels = []
        for label, keywords in SENTIMENT_LABELS.items():
            if any(kw in para for kw in keywords):
                matched_labels.append(label)

        is_overseas = '海外' in matched_labels

        tagged.append({
            'text':       para,
            'labels':     matched_labels,
            'isOverseas': is_overseas,
        })

    print(f"    标注了 {len(tagged)} 个段落")
    return tagged


# ─────────────────────────────────────────────────────────────────────────────
# 步骤 5：组装 & 保存 JSON
# ─────────────────────────────────────────────────────────────────────────────

def build_output(
    code: str,
    announcements: list,
    pdf_info: dict,
    sections: dict,
    tagged_paragraphs: list,
) -> dict:
    """组装最终输出 JSON"""
    latest = announcements[0] if announcements else {}

    return {
        'code':              code,
        'fetchTime':         datetime.now().isoformat(),
        'announcementTitle': pdf_info.get('title', latest.get('title', '')),
        'announcementDate':  pdf_info.get('date',  latest.get('date',  '')),
        'pdfUrl':            pdf_info.get('pdfUrl', latest.get('pdfUrl', '')),
        'sections':          sections,
        'taggedParagraphs':  tagged_paragraphs,
    }


def save_json(code: str, data: dict) -> str:
    """保存 JSON 到 data/cninfo/{code}_sections.json"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, f"{code}_sections.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    size_kb = os.path.getsize(filepath) / 1024
    print(f"  已保存: {filepath} ({size_kb:.1f} KB)")
    return filepath


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def process_stock(code: str, skip_pdf: bool = False) -> dict:
    """处理单只股票：拉公告 -> 下载PDF -> 提取章节 -> 标注段落 -> 存JSON"""
    print(f"\n{'='*55}")
    print(f"  处理股票: {code}")
    print(f"{'='*55}")

    # ── 步骤 1: 拉取公告列表 ────────────────────────────────────────────────
    print("\n  [1/5] 拉取年报公告列表...")
    announcements = fetch_annual_report_announcements(code)

    if not announcements:
        print("    [WARN] 未获取到任何公告，保存空结果")
        data = build_output(code, [], {}, {}, [])
        save_json(code, data)
        return data

    for i, ann in enumerate(announcements, 1):
        print(f"      {i}. {ann['title'][:50]}  ({ann['date']})")

    # ── 步骤 2: 下载最新 PDF ────────────────────────────────────────────────
    pdf_info  = {}
    sections  = {}
    tagged    = []

    if skip_pdf:
        print("\n  [2/5] 下载 PDF（已跳过 --skip-pdf）")
    else:
        print("\n  [2/5] 下载最新年报 PDF...")
        pdf_info = download_latest_pdf(code, announcements)

        if pdf_info and pdf_info.get('pdfPath'):
            pdf_path = pdf_info['pdfPath']

            # ── 步骤 3: 提取章节 ────────────────────────────────────────────
            print("\n  [3/5] 提取 PDF 章节...")
            full_text = extract_pdf_text(pdf_path)

            if full_text:
                sections = extract_sections(full_text)

                # ── 步骤 4: MD&A 段落标注 ───────────────────────────────────
                print("\n  [4/5] MD&A 段落标签标注...")
                mdna_text = sections.get('mdna', '')
                tagged    = tag_mdna_paragraphs(mdna_text)
            else:
                print("    [WARN] PDF 文本提取失败，跳过章节提取")
        else:
            print("    [WARN] PDF 下载失败，跳过章节提取")

    # ── 步骤 5: 保存 JSON ────────────────────────────────────────────────────
    print("\n  [5/5] 保存结构化 JSON...")
    data = build_output(code, announcements, pdf_info, sections, tagged)
    save_json(code, data)

    return data


def main():
    args = sys.argv[1:]

    if not args:
        print("用法: python fetch_cninfo.py <股票代码> [--skip-pdf]")
        print("  python fetch_cninfo.py 600519                 # 完整流程")
        print("  python fetch_cninfo.py 600519 --skip-pdf      # 仅拉公告列表")
        print("  python fetch_cninfo.py 600519,000858           # 多只股票")
        sys.exit(1)

    skip_pdf = '--skip-pdf' in args
    args     = [a for a in args if a != '--skip-pdf']

    if not args:
        print("[ERROR] 未提供股票代码")
        sys.exit(1)

    codes = [c.strip() for c in args[0].split(',') if c.strip()]

    if not codes:
        print("[ERROR] 无效的股票代码")
        sys.exit(1)

    results = []
    for code in codes:
        try:
            result = process_stock(code, skip_pdf=skip_pdf)
            results.append(result)
        except Exception as e:
            print(f"\n  [ERROR] 处理 {code} 时出错: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(1)

    print(f"\n{'='*55}")
    print(f"  全部完成! 共处理 {len(results)} 只股票")
    print(f"  输出目录: {os.path.abspath(OUTPUT_DIR)}")
    print(f"{'='*55}")


if __name__ == '__main__':
    main()
