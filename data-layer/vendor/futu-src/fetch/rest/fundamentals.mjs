// 批量拉基本面数据并落盘
//
// 用法：
//   node fetch/rest/fundamentals.mjs                      默认：全部标的、STOCK 类型、7 天 TTL
//   node fetch/rest/fundamentals.mjs --only US.AAPL,US.TSLA
//   node fetch/rest/fundamentals.mjs --what metrics,consensus    只拉指定项
//   node fetch/rest/fundamentals.mjs --ttl 0 --force      强制全部重拉
//   node fetch/rest/fundamentals.mjs --gap 1000 --budget 280     分段跑，可反复续拉
//
// 拉取项（每只 6 次请求）—— 只保留"最关键"的：
//   metrics    主要指标      financials/statements?statement_type=4   ROE/毛利率/净利率/负债率…（4 期）
//   pe         估值 PE       valuation/detail?valuation_type=1        含历史分位 valuation_percentile
//   pb         估值 PB       valuation/detail?valuation_type=2
//   consensus  分析师一致预期 research/analyst-consensus              目标均价/最高/最低/评级分布
//   dividends  分红派息      corporate-actions/dividends
//   splits     拆合股        corporate-actions/splits                与复权校验直接相关
//
// 路径规律：文档路由 = API 路径，即 /api/v1.0/quote/{symbol}/<分类>/<名称>
//
// 产物：data/fundamentals/<SYMBOL>.json
//   { symbol, name, stock_type, fetched_at, results: { <key>: { ok, data | error } } }
import { mkdirSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { apiGet, syncTime } from './lib/futu-client.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..', '..');
const OUT_DIR = join(ROOT, 'data', 'fundamentals');
mkdirSync(OUT_DIR, { recursive: true });

// ---------------------------------------------------------------- 拉取项定义
const TARGETS = {
  metrics: { label: '主要指标', path: (s) => `/api/v1.0/quote/${s}/financials/statements`, params: { statement_type: '4', limit: '4' } },
  pe: { label: '估值PE', path: (s) => `/api/v1.0/quote/${s}/valuation/detail`, params: { valuation_type: '1', interval_type: '3' } },
  pb: { label: '估值PB', path: (s) => `/api/v1.0/quote/${s}/valuation/detail`, params: { valuation_type: '2', interval_type: '3' } },
  consensus: { label: '分析师一致预期', path: (s) => `/api/v1.0/quote/${s}/research/analyst-consensus`, params: {} },
  dividends: { label: '分红派息', path: (s) => `/api/v1.0/quote/${s}/corporate-actions/dividends`, params: {} },
  splits: { label: '拆合股', path: (s) => `/api/v1.0/quote/${s}/corporate-actions/splits`, params: {} },
};

// ---------------------------------------------------------------- 参数
const argv = process.argv.slice(2);
const arg = (name, def) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : def;
};
const flag = (name) => argv.includes(`--${name}`);

const FORCE = flag('force');
const ONLY = arg('only', '') ? arg('only').split(',') : null;
const TYPES = arg('types', 'STOCK').split(',').filter(Boolean);
const WHAT = arg('what', '') ? arg('what').split(',') : Object.keys(TARGETS);
const TTL_DAYS = Number(arg('ttl', 7)); // 文件比这个新就跳过（基本面按季度更新，无需天天拉）
const GAP_MS = Number(arg('gap', 700));
const BUDGET_S = Number(arg('budget', 0));

for (const w of WHAT) if (!TARGETS[w]) throw new Error(`未知拉取项 ${w}，可选: ${Object.keys(TARGETS).join(', ')}`);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- 标的清单
const symbolsFile = join(ROOT, 'data', 'watchlist', 'symbols.json');
if (!existsSync(symbolsFile)) throw new Error(`缺少 ${symbolsFile}，先跑 node src/fetch-watchlist.mjs`);
let symbols = JSON.parse(readFileSync(symbolsFile, 'utf8')).symbols;
if (ONLY) symbols = symbols.filter((s) => ONLY.includes(s.code));
if (TYPES.length) symbols = symbols.filter((s) => TYPES.includes(s.type));

await syncTime();
console.log(`拉取项: ${WHAT.map((w) => `${w}(${TARGETS[w].label})`).join(' / ')}`);
console.log(`标的 ${symbols.length} 只（类型 ${TYPES.join(',') || '全部'}）| TTL ${TTL_DAYS} 天 | 间隔 ${GAP_MS}ms`);
console.log(`预计请求上限 ${symbols.length * WHAT.length} 次\n`);

// ---------------------------------------------------------------- 主循环
const stat = { fresh: 0, skipped: 0, partial: 0, failed: 0, requests: 0 };
const startedAt = Date.now();
const manifest = [];

for (const [i, s] of symbols.entries()) {
  const tag = `[${String(i + 1).padStart(4)}/${symbols.length}] ${s.code.padEnd(14)}`;
  const file = join(OUT_DIR, `${s.code}.json`);

  if (BUDGET_S && (Date.now() - startedAt) / 1000 > BUDGET_S) {
    console.log(`\n⏱ 达到时间预算 ${BUDGET_S}s，提前结束（再次运行自动续拉）`);
    break;
  }

  // TTL：文件足够新且未强制 ⇒ 跳过（0 请求）
  if (!FORCE && TTL_DAYS > 0 && existsSync(file)) {
    try {
      const prev = JSON.parse(readFileSync(file, 'utf8'));
      const ageDays = (Date.now() - new Date(prev.fetched_at).getTime()) / 86400_000;
      if (ageDays < TTL_DAYS) {
        stat.skipped++;
        manifest.push({ code: s.code, action: 'skipped', age_days: Number(ageDays.toFixed(1)) });
        process.stdout.write(`${tag} · 跳过（${ageDays.toFixed(1)} 天前拉过）\n`);
        continue;
      }
    } catch {
      /* 文件坏了就重拉 */
    }
  }

  const results = {};
  let okCount = 0;
  let errCount = 0;

  for (const key of WHAT) {
    const t = TARGETS[key];
    try {
      const r = await apiGet(t.path(s.code), t.params);
      stat.requests++;
      if (r.ret_code === 0) {
        results[key] = { ok: true, data: r.data ?? null };
        okCount++;
      } else if (r.ret_code === -10) {
        // no_data：合法标的但该报表无数据（如 ETF 没有财报），属正常空结果
        results[key] = { ok: true, data: null, note: 'no_data' };
        okCount++;
      } else {
        results[key] = { ok: false, error: `${r.ret_code} ${r.ret_msg}` };
        errCount++;
      }
    } catch (e) {
      results[key] = { ok: false, error: String(e.message) };
      errCount++;
    }
    await sleep(GAP_MS);
  }

  writeFileSync(
    file,
    JSON.stringify(
      { symbol: s.code, name: s.name, stock_type: s.type, fetched_at: new Date().toISOString(), ok: okCount, err: errCount, results },
      null,
      2
    )
  );

  if (errCount === 0) stat.fresh++;
  else if (okCount > 0) stat.partial++;
  else stat.failed++;

  manifest.push({ code: s.code, action: errCount === 0 ? 'ok' : 'partial', ok: okCount, err: errCount });
  process.stdout.write(`${tag} ${errCount === 0 ? '✓' : '⚠'} ${okCount}/${WHAT.length} 项${errCount ? `（${errCount} 失败）` : ''}\n`);
}

// ---------------------------------------------------------------- 汇总
writeFileSync(
  join(OUT_DIR, '_manifest.json'),
  JSON.stringify({ fetched_at: new Date().toISOString(), what: WHAT, ttl_days: TTL_DAYS, stat, items: manifest }, null, 2)
);

console.log(
  `\n完成: 成功 ${stat.fresh} | 部分成功 ${stat.partial} | 跳过 ${stat.skipped} | 全失败 ${stat.failed} | 共 ${stat.requests} 次请求`
);
