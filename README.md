# A股财务雷达 (Financial Radar)

A 股上市公司财报分析工具，动态抓取财报数据，多维度分析。

## 技术栈

- 前端：React 18 + Babel Standalone @7 + Tailwind CDN (单文件 HTML)
- 数据层：Python ETL (东财 datacenter API + 巨潮公告)
- 部署：Cloudflare Pages (静态站) + Workers (API 代理)

## 快速开始

```bash
# 1. 抓取数据
cd etl
python3 fetch_eastmoney.py 600519        # 单只股票
python3 fetch_eastmoney.py --all          # 抓取 samples 列表

# 2. 巨潮年报章节抽取 (需 pdfplumber)
python3 fetch_cninfo.py 600519            # 完整流程 (含 PDF 下载)
python3 fetch_cninfo.py 600519 --skip-pdf # 仅拉公告列表

# 3. 本地启动
cd ..
python3 -m http.server 8899
# 打开 http://localhost:8899/frontend/index.html
```

## 部署到 Cloudflare Pages

1. 推送到 GitHub
2. 在 CF Dashboard 创建 Pages 项目，build output 指向 `frontend/`
3. Workers 代理单独部署到 `workers/` 目录

## 数据说明

- `data/eastmoney/{code}.json` - 结构化财务数据 (20期报表)
- `data/cninfo/{code}_sections.json` - 年报章节抽取
- `data/pdfs/` - 年报 PDF 原件
- `data/tickers.json` - 股票列表
