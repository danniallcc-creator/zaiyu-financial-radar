/**
 * Cloudflare Workers 代理层
 * 代理东财 F10、行情、巨潮公告等无 CORS 的金融数据接口
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS headers
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Access-Control-Max-Age': '86400',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      let result;

      switch (true) {
        case path === '/api/search':
          result = await handleSearch(url.searchParams.get('q') || '');
          break;

        case path === '/api/quote':
          result = await handleQuote(url.searchParams.get('code') || '');
          break;

        case path.startsWith('/api/f10/'):
          result = await handleF10(path, url.searchParams);
          break;

        case path === '/api/cninfo/search':
          result = await handleCninfoSearch(url.searchParams.get('q') || '');
          break;

        case path === '/api/cninfo/announcements':
          result = await handleCninfoAnnouncement(url.searchParams);
          break;

        case path.startsWith('/api/industry/'):
          result = await handleIndustry(path, url.searchParams);
          break;

        case path === '/api/health':
          result = { status: 'ok', time: new Date().toISOString() };
          break;

        default:
          return new Response(JSON.stringify({ error: 'Not Found' }), {
            status: 404,
            headers: { ...corsHeaders, 'Content-Type': 'application/json' }
          });
      }

      return new Response(JSON.stringify(result), {
        headers: {
          ...corsHeaders,
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': getCacheControl(path),
        }
      });

    } catch (e) {
      return new Response(JSON.stringify({
        error: e.message,
        stack: e.stack
      }), {
        status: 500,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' }
      });
    }
  }
};

/**
 * 根据接口类型返回缓存 TTL
 */
function getCacheControl(path) {
  if (path.includes('search')) return 'public, max-age=3600';
  if (path.includes('quote')) return 'public, max-age=300';
  if (path.includes('f10') || path.includes('cninfo')) return 'public, max-age=3600';
  if (path.includes('industry')) return 'public, max-age=7200';
  return 'public, max-age=300';
}

/**
 * 市场前缀: SH / SZ / BJ
 */
function emPrefix(code) {
  if (code.startsWith('6')) return 'SH';
  if (code.startsWith(('0', '3'))) return 'SZ';
  if (code.startsWith(('8', '4'))) return 'BJ';
  return 'SZ';
}

function emSecid(code) {
  if (code.startsWith('6')) return `1.${code}`;
  return `0.${code}`;
}

/**
 * 带超时和重试的 fetch
 */
async function safeFetch(url, opts = {}, timeoutMs = 12000, retries = 1) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  for (let i = 0; i <= retries; i++) {
    try {
      const resp = await fetch(url, {
        ...opts,
        signal: controller.signal,
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
          'Accept': 'application/json, text/plain, */*',
          ...opts.headers
        }
      });
      clearTimeout(timer);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp;
    } catch (e) {
      if (i === retries) {
        clearTimeout(timer);
        throw e;
      }
      await new Promise(r => setTimeout(r, 1000 * (i + 1)));
    }
  }
}

// ============ 搜索 ============

async function handleSearch(keyword) {
  if (!keyword || keyword.length < 1) {
    return { items: [] };
  }

  const url = `https://searchapi.eastmoney.com/api/suggest/get?input=${encodeURIComponent(keyword)}&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=30`;

  // searchapi 无 CORS，需要 JSONP 方式或直接抓取
  // 该接口返回格式: jQuery(...){...} 或直接 JSON
  const resp = await safeFetch(url, {}, 8000, 0);
  const text = await resp.text();

  let data;
  try {
    // 尝试直接解析 JSON
    data = JSON.parse(text);
  } catch {
    // 可能是 JSONP: jQuery1234567({...})
    const match = text.match(/\((\{[\s\S]*\})\)/);
    if (match) {
      data = JSON.parse(match[1]);
    } else {
      data = { QuotationCodeTable: { Data: [] } };
    }
  }

  const items = (data?.QuotationCodeTable?.Data || []).map(item => ({
    code: item.Code,
    name: item.Name,
    market: item.MktNum,
    type: item.SecurityTypeName,
    industry: item.IndustryName || ''
  })).filter(item =>
    // 只保留 A 股（沪深北）
    (item.market === '0' || item.market === '1') &&
    (item.type === '沪A' || item.type === '深A' || item.type === '创业板' || item.type === '科创板' || item.type === '北交所')
  );

  return { items };
}

// ============ 实时行情 ============

async function handleQuote(code) {
  if (!code) return { error: 'code required' };

  const secid = emSecid(code);
  const url = `https://push2.eastmoney.com/api/qt/stock/get?secid=${secid}&fields=f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f116,f117,f162,f167,f168,f169,f170,f171`;

  const resp = await safeFetch(url, {}, 10000, 1);
  const data = await resp.json();
  const d = data?.data;

  if (!d) return { error: 'no data' };

  return {
    code: d.f57,
    name: d.f58,
    price: d.f43 / 100,
    change: d.f169 / 100,
    changePct: d.f170 / 100,
    open: d.f44 / 100,
    high: d.f45 / 100,
    low: d.f46 / 100,
    prevClose: d.f60 / 100,
    volume: d.f47,
    amount: d.f48,
    totalMV: d.f116,
    circMV: d.f117,
    pe: d.f162 / 100,
    pb: d.f167 / 100,
    turnover: d.f168 / 100,
  };
}

// ============ F10 各模块 ============

async function handleF10(path, params) {
  const parts = path.split('/');
  // /api/f10/{section}/{code}
  const section = parts[3];
  const code = parts[4];

  if (!code || !section) return { error: 'section and code required' };

  const dcToken = '894050c76af8597a853f5b408b759f5d';
  const dcBase = 'https://datacenter.eastmoney.com/securities/api/data/get';
  const scode = code.startsWith('6') ? `${code}.SH` : code.startsWith(('0','3')) ? `${code}.SZ` : `${code}.BJ`;

  // Map section names to datacenter API tables
  const dcTableMap = {
    financial: { table: 'RPT_F10_FINANCE_MAINFINADATA', ps: 20 },
    income: { table: 'RPT_F10_FINANCE_GINCOME', ps: 20 },
    balance: { table: 'RPT_F10_FINANCE_GBALANCE', ps: 20 },
    cashflow: { table: 'RPT_F10_FINANCE_GCASHFLOW', ps: 20 },
  };

  // Profile / holder / manage still use emweb (they work with Referer header)
  const emwebMap = {
    holder: `https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code=${emPrefix(code)}${code}`,
    dividend: `https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code=${emPrefix(code)}${code}`,
    profile: `https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code=${emPrefix(code)}${code}`,
    manage: `https://emweb.securities.eastmoney.com/PC_HSF10/CoreReadPage/PageAjax?code=${emPrefix(code)}${code}`,
  };

  // All data: fetch all modules in parallel
  if (section === 'all') {
    const promises = [];
    for (const [key, cfg] of Object.entries(dcTableMap)) {
      promises.push((async () => {
        const url = `${dcBase}?type=${cfg.table}&sty=ALL&filter=(SECUCODE="${scode}")&p=1&ps=${cfg.ps}&sr=-1&st=REPORT_DATE&token=${dcToken}`;
        const resp = await safeFetch(url, {}, 15000, 1);
        const data = await resp.json();
        return { key, data: data?.result?.data || [] };
      })());
    }
    for (const [key, url] of Object.entries(emwebMap)) {
      promises.push((async () => {
        const resp = await safeFetch(url, {
          headers: { 'Referer': 'https://emweb.securities.eastmoney.com/' }
        }, 15000, 1);
        const data = await resp.json();
        return { key, data };
      })());
    }
    const results = await Promise.allSettled(promises);
    const output = {};
    for (const r of results) {
      if (r.status === 'fulfilled') {
        output[r.value.key] = r.value.data;
      }
    }
    return output;
  }

  // Single section
  if (dcTableMap[section]) {
    const cfg = dcTableMap[section];
    const url = `${dcBase}?type=${cfg.table}&sty=ALL&filter=(SECUCODE="${scode}")&p=1&ps=${cfg.ps}&sr=-1&st=REPORT_DATE&token=${dcToken}`;
    const resp = await safeFetch(url, {}, 15000, 1);
    const data = await resp.json();
    return data?.result?.data || [];
  }

  if (emwebMap[section]) {
    const resp = await safeFetch(emwebMap[section], {
      headers: { 'Referer': 'https://emweb.securities.eastmoney.com/' }
    }, 15000, 1);
    return await resp.json();
  }
}

// ============ 巨潮资讯 ============

async function handleCninfoSearch(keyword) {
  if (!keyword) return { items: [] };

  const url = 'http://www.cninfo.com.cn/new/information/topSearch/query';
  const body = `keyWord=${encodeURIComponent(keyword)}&maxNum=20`;

  const resp = await safeFetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      'Accept': 'application/json',
      'Referer': 'http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice'
    },
    body
  }, 10000, 1);

  const data = await resp.json();
  const items = (data?.keyBoardList || []).map(item => ({
    code: item.code,
    name: item.secAbbr || item.secName,
    market: item.category,       // "深圳主板" "上海主板" etc
    orgId: item.orgId,
    zwjgdm: item.zwjgdm
  }));

  return { items };
}

async function handleCninfoAnnouncement(params) {
  const code = params.get('code');
  const orgId = params.get('orgId');
  const pageNum = parseInt(params.get('page') || '1');
  const pageSize = parseInt(params.get('size') || '20');
  const category = params.get('category') || 'category_ndbg_szsh'; // 年报

  if (!code) return { error: 'code required' };

  const url = 'http://www.cninfo.com.cn/new/hisAnnouncement/query';
  const body = new URLSearchParams({
    stock: code,
    tabName: 'fulltext',
    plate: 'sz;sh',
    category: category,
    pageNum: String(pageNum),
    pageSize: String(pageSize),
    column: 'szse',
    seDate: '',
    searchkey: '',
    secid: orgId || '',
    sortName: '',
    sortType: '',
    isHLtitle: 'true'
  });

  const resp = await safeFetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
      'Accept': 'application/json',
      'Referer': 'http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice'
    },
    body: body.toString()
  }, 12000, 1);

  const data = await resp.json();

  const announcementsTypeMap = {
    'category_ndbg_szsh': '年报',
    'category_bndbg_szsh': '半年报',
    'category_sjdbg_szsh': '季报',
    'category_ipo_szsh': '招股说明书',
  };

  return {
    total: data?.totalAnnouncement || 0,
    items: (data?.announcements || []).map(item => ({
      title: (item.announcementTitle || '').replace(/<[^>]+>/g, ''),
      time: item.announcementTime ? new Date(item.announcementTime).toISOString().split('T')[0] : '',
      pdfUrl: `http://static.cninfo.com.cn/${item.adjunctUrl}`,
      size: item.adjunctSize,
      type: announcementTypeMap[category] || category
    }))
  };
}

// ============ 行业横向对比 ============

async function handleIndustry(path, params) {
  const parts = path.split('/');
  const action = parts[3]; // list / compare

  if (action === 'list') {
    // 获取行业板块列表
    const url = 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f2,f3,f4,f12,f14';
    const resp = await safeFetch(url, {}, 10000, 1);
    const data = await resp.json();

    return {
      items: (data?.data?.diff || []).map(item => ({
        code: item.f12,
        name: item.f14,
        changePct: item.f3,
      }))
    };
  }

  if (action === 'stocks') {
    // 获取行业内个股
    const industryCode = params.get('code');
    if (!industryCode) return { error: 'industry code required' };

    const url = `https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=50&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:${industryCode}&fields=f2,f3,f4,f9,f12,f14,f20,f23,f115,f128,f140,f141`;
    const resp = await safeFetch(url, {}, 10000, 1);
    const data = await resp.json();

    return {
      items: (data?.data?.diff || []).map(item => ({
        code: item.f12,
        name: item.f14,
        price: item.f2,
        changePct: item.f3,
        totalMV: item.f20,
        pe: item.f9,
        pb: item.f23,
      }))
    };
  }

  return { error: `unknown action: ${action}` };
}
