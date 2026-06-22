/**
 * Cloudflare Workers 代理层
 * 代理东财 F10、行情、巨潮公告等无 CORS 的金融数据接口
 * 支持 A 股 / 美股 / 港股
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
          result = await handleSearch(url.searchParams.get('q') || '', url.searchParams.get('market') || '');
          break;

        case path === '/api/quote':
          result = await handleQuote(url.searchParams.get('code') || '', url.searchParams.get('market') || '');
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

        case path === '/api/global/list':
          result = await handleGlobalList(url.searchParams);
          break;

        case path === '/api/health':
          result = { status: 'ok', time: new Date().toISOString() };
          break;

        case path === '/api/market-overview':
          result = await handleMarketOverview(env);
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
  },

  async scheduled(event, env, ctx) {
    // 定时刷新市场概览数据缓存
    // 仅在财报季 (1,4,7,8,10月) 执行增量更新
    const month = new Date().getMonth() + 1;
    const earningsMonths = [1, 4, 7, 8, 10];

    if (!earningsMonths.includes(month)) {
      console.log(`非财报季(${month}月)，跳过更新`);
      return;
    }

    // 从 Pages 静态文件获取最新数据并存入 KV 缓存
    try {
      const pagesUrl = env.PAGES_URL || 'https://danniallcc-creator.github.io/zaiyu-financial-radar';
      const resp = await fetch(`${pagesUrl}/data/market_overview.json`);
      if (resp.ok) {
        const data = await resp.json();
        if (env.FIN_CACHE) {
          await env.FIN_CACHE.put('market_overview', JSON.stringify(data), {
            expirationTtl: 172800 // 48h TTL
          });
          console.log(`KV 缓存更新成功: ${data.meta?.generatedAt}`);
        }
      }
    } catch (e) {
      console.error('市场概览缓存更新失败:', e.message);
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
  if (path.includes('global')) return 'public, max-age=300';
  return 'public, max-age=300';
}

// ============ 市场检测工具 ============

/**
 * 从代码格式推断市场: 'cn' | 'us' | 'hk'
 */
function detectMarket(code) {
  if (/^[A-Za-z]/.test(code)) return 'us';
  if (/^\d{5}$/.test(code)) return 'hk';
  return 'cn';
}

/**
 * 生成腾讯行情代码 (支持 A/美/港)
 */
function tencentCode(code, market) {
  if (market === 'us') return `us${code}`;
  if (market === 'hk') return `hk${code}`;
  return code.startsWith('6') ? `sh${code}` : `sz${code}`;
}

/**
 * 市场前缀: SH / SZ / BJ (仅 A 股)
 */
function emPrefix(code) {
  if (code.startsWith('6')) return 'SH';
  if (code.startsWith('0') || code.startsWith('3')) return 'SZ';
  if (code.startsWith('8') || code.startsWith('4')) return 'BJ';
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

// ============ 搜索 (多市场) ============

async function handleSearch(keyword, market) {
  if (!keyword || keyword.length < 1) {
    return { items: [] };
  }

  const url = `https://searchapi.eastmoney.com/api/suggest/get?input=${encodeURIComponent(keyword)}&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=30`;

  const resp = await safeFetch(url, {}, 8000, 0);
  const text = await resp.text();

  let data;
  try {
    data = JSON.parse(text);
  } catch {
    const match = text.match(/\((\{[\s\S]*\})\)/);
    if (match) {
      data = JSON.parse(match[1]);
    } else {
      data = { QuotationCodeTable: { Data: [] } };
    }
  }

  let items = (data?.QuotationCodeTable?.Data || []).map(item => ({
    code: item.Code,
    name: item.Name,
    market: item.MktNum,
    type: item.SecurityTypeName,
    industry: item.IndustryName || '',
    secid: item.QuoteID || '',
  }));

  // 按市场过滤
  if (market === 'cn') {
    items = items.filter(item =>
      (item.market === '0' || item.market === '1') &&
      (item.type === '沪A' || item.type === '深A' || item.type === '创业板' || item.type === '科创板' || item.type === '北交所')
    );
  } else if (market === 'us') {
    items = items.filter(item =>
      item.type === '美股' || item.market === '105' || item.market === '106'
    );
  } else if (market === 'hk') {
    items = items.filter(item =>
      item.type === '港股' || item.market === '116'
    );
  }
  // market 为空: 返回全部 (不过滤)

  return { items };
}

// ============ 实时行情 (多市场) ============

async function handleQuote(code, market) {
  if (!code) return { error: 'code required' };

  const mkt = market || detectMarket(code);
  const tcCode = tencentCode(code, mkt);
  const url = `https://qt.gtimg.cn/q=${tcCode}`;

  const resp = await safeFetch(url, {
    headers: { 'Referer': 'https://finance.qq.com' }
  }, 10000, 1);

  const buf = await resp.arrayBuffer();
  const decoder = new TextDecoder('gbk');
  const text = decoder.decode(buf);

  const content = text.split('"')[1];
  if (!content) return { error: 'no data' };

  const f = content.split('~');
  if (!f[3]) return { error: 'no data' };

  const pf = (v) => { const n = parseFloat(v); return isNaN(n) ? null : n; };

  // 美股/港股字段位置与 A 股不同
  if (mkt === 'us') {
    if (f.length < 50) return { error: 'no data' };
    return {
      code: f[2],
      name: f[1],
      price: pf(f[3]),
      change: pf(f[31]),
      changePct: pf(f[32]),
      open: pf(f[5]),
      high: pf(f[33]),
      low: pf(f[34]),
      prevClose: pf(f[4]),
      volume: parseInt(f[6]) || null,
      amount: pf(f[37]),
      totalMV: (pf(f[45]) || 0) * 1e8,       // 亿美元 → 美元
      circMV: (pf(f[44]) || 0) * 1e8,
      pe: pf(f[39]),
      pb: pf(f[41]),                            // US: PB at [41]
      turnover: pf(f[38]),
      currency: 'USD',
    };
  }

  if (mkt === 'hk') {
    if (f.length < 50) return { error: 'no data' };
    return {
      code: f[2],
      name: f[1],
      price: pf(f[3]),
      change: pf(f[31]),
      changePct: pf(f[32]),
      open: pf(f[5]),
      high: pf(f[33]),
      low: pf(f[34]),
      prevClose: pf(f[4]),
      volume: parseInt(f[6]) || null,
      amount: pf(f[37]),
      totalMV: (pf(f[45]) || 0) * 1e8,       // 亿港元 → 港元
      circMV: (pf(f[44]) || 0) * 1e8,
      pe: pf(f[39]),
      pb: pf(f[47]),                            // HK: PB at [47]
      turnover: pf(f[38]),
      currency: 'HKD',
    };
  }

  // A 股 (原有逻辑)
  if (f.length < 47) return { error: 'no data' };
  return {
    code: f[2],
    name: f[1],
    price: pf(f[3]),
    change: pf(f[31]),
    changePct: pf(f[32]),
    open: pf(f[5]),
    high: pf(f[33]),
    low: pf(f[34]),
    prevClose: pf(f[4]),
    volume: parseInt(f[6]) || null,
    amount: pf(f[37]),
    totalMV: (pf(f[45]) || 0) * 1e8,
    circMV: (pf(f[44]) || 0) * 1e8,
    pe: pf(f[39]),
    pb: pf(f[46]),
    turnover: pf(f[38]),
  };
}

// ============ F10 各模块 (多市场) ============

async function handleF10(path, params) {
  const parts = path.split('/');
  // /api/f10/{section}/{code}
  const section = parts[3];
  const code = parts[4];

  if (!code || !section) return { error: 'section and code required' };

  const market = params.get('market') || detectMarket(code);
  const dcToken = '894050c76af8597a853f5b408b759f5d';
  const dcBase = 'https://datacenter.eastmoney.com/securities/api/data/get';

  // 根据市场确定 SECUCODE 和 datacenter 表
  let scode, dcTableMap, emwebMap;

  if (market === 'us') {
    // 美股: SECUCODE 用 SECURITY_CODE 过滤 (跨交易所)
    scode = null; // 使用 SECURITY_CODE 而非 SECUCODE
    dcTableMap = {
      income: { table: 'RPT_USF10_FN_INCOME', ps: 200, filterField: 'SECURITY_CODE' },
      balance: { table: 'RPT_USF10_FN_BALANCE', ps: 200, filterField: 'SECURITY_CODE' },
    };
    emwebMap = {}; // 美股无 emweb 数据
  } else if (market === 'hk') {
    scode = null;
    dcTableMap = {
      income: { table: 'RPT_HKF10_FN_INCOME', ps: 200, filterField: 'SECURITY_CODE' },
      balance: { table: 'RPT_HKF10_FN_BALANCE', ps: 200, filterField: 'SECURITY_CODE' },
    };
    emwebMap = {};
  } else {
    // A 股
    scode = code.startsWith('6') ? `${code}.SH` : code.startsWith('0') || code.startsWith('3') ? `${code}.SZ` : `${code}.BJ`;
    dcTableMap = {
      financial: { table: 'RPT_F10_FINANCE_MAINFINADATA', ps: 20 },
      income: { table: 'RPT_F10_FINANCE_GINCOME', ps: 20 },
      balance: { table: 'RPT_F10_FINANCE_GBALANCE', ps: 20 },
      cashflow: { table: 'RPT_F10_FINANCE_GCASHFLOW', ps: 20 },
    };
    emwebMap = {
      holder: `https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code=${emPrefix(code)}${code}`,
      dividend: `https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code=${emPrefix(code)}${code}`,
      profile: `https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code=${emPrefix(code)}${code}`,
      manage: `https://emweb.securities.eastmoney.com/PC_HSF10/CoreReadPage/PageAjax?code=${emPrefix(code)}${code}`,
    };
  }

  function buildDcUrl(cfg) {
    const filterField = cfg.filterField || 'SECUCODE';
    const filterVal = filterField === 'SECURITY_CODE' ? code : scode;
    return `${dcBase}?type=${cfg.table}&sty=ALL&filter=(${filterField}="${filterVal}")&p=1&ps=${cfg.ps}&sr=-1&st=REPORT_DATE&token=${dcToken}`;
  }

  // All data: fetch all modules in parallel
  if (section === 'all') {
    const promises = [];
    for (const [key, cfg] of Object.entries(dcTableMap)) {
      promises.push((async () => {
        const url = buildDcUrl(cfg);
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
    const url = buildDcUrl(cfg);
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

  return { error: `section '${section}' not available for ${market} market` };
}

// ============ 全球股票市值排名 ============

async function handleGlobalList(params) {
  const market = params.get('market') || 'us';
  const size = Math.min(parseInt(params.get('size') || '100'), 200);

  let fs;
  if (market === 'us') {
    fs = 'm:105,m:106';
  } else if (market === 'hk') {
    fs = 'm:116';
  } else {
    fs = 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23';
  }

  const url = `https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=${size}&po=1&np=1&fltt=2&invt=2&fid=f20&fs=${fs}&fields=f2,f3,f4,f9,f12,f13,f14,f20,f23,f116`;

  const resp = await safeFetch(url, {}, 10000, 1);
  const data = await resp.json();

  const items = (data?.data?.diff || []).map(item => ({
    code: item.f12,
    name: item.f14,
    price: item.f2,
    changePct: item.f3,
    change: item.f4,
    totalMV: item.f20,
    pe: item.f9,
    pb: item.f23,
    market: item.f13 === 105 ? 'NASDAQ' : item.f13 === 106 ? 'NYSE' : item.f13 === 116 ? 'HKEX' : String(item.f13),
  }));

  return {
    total: data?.data?.total || 0,
    market,
    items,
  };
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
    market: item.category,
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
  const category = params.get('category') || 'category_ndbg_szsh';

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
      type: announcementsTypeMap[category] || category
    }))
  };
}

// ============ 行业横向对比 ============

async function handleIndustry(path, params) {
  const parts = path.split('/');
  const action = parts[3];

  if (action === 'list') {
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

/**
 * 市场概览 API — 优先从 KV 缓存读取，回退到 Pages 静态文件
 */
async function handleMarketOverview(env) {
  if (env.FIN_CACHE) {
    try {
      const cached = await env.FIN_CACHE.get('market_overview', 'json');
      if (cached) return cached;
    } catch (e) { /* KV 不可用，继续 fallback */ }
  }

  const pagesUrl = env.PAGES_URL || 'https://danniallcc-creator.github.io/zaiyu-financial-radar';
  const resp = await fetch(`${pagesUrl}/data/market_overview.json`);
  if (!resp.ok) {
    return { error: 'market_overview data not available', status: resp.status };
  }
  return await resp.json();
}
