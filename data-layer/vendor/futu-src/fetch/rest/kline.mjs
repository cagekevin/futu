// 批量拉 K 线并落盘（支持日线 / 30 分钟 / 60 分钟）—— 增量优先，只拉「空白部分」
//
// 用法：
//   node fetch/rest/kline.mjs                          默认：日线，增量
//   node fetch/rest/kline.mjs --period 30m --months 3  拉 30 分钟，回看 3 个月
//   node fetch/rest/kline.mjs --period 60m --months 3  拉 60 分钟
//   node fetch/rest/kline.mjs --period 30m --only US.AAPL,US.TSLA
//   node fetch/rest/kline.mjs --mode full              强制全量重拉（等价 --force）
//   node fetch/rest/kline.mjs --budget 240             跑满 240 秒优雅退出，可反复续跑
//   node fetch/rest/kline.mjs --gap 1000               加大请求间隔（被限流时）
//   node fetch/rest/kline.mjs --check                  只体检不下载（三个周期一起查，纯本地）
//
// 增量算法（三层，从便宜到贵）：
//   ① 交易日闸门：用交易日历算出各市场「最近一个交易日」。已有文件的 last_date 已到该日
//      ⇒ 直接跳过，**0 次请求**。
//   ② 增量拉取：从 today 往前翻页，翻到「上一页最早日期 <= 已有 last_date」即停
//      ⇒ 正常情况**只要 1 次请求**。
//   ③ 复权基准校验 + 成交量精度校验：见下方注释。
//
// 接口：GET /api/v1.0/quote/{symbol}/history-kline   （num 上限 370）
// 限频：实测比 60 次/30 秒更紧，**日内多页翻页时容易被限流**；默认间隔 700ms，
//       futu-client 已内置 ret_code -11 自动退避（按服务端给的 retry after 等待）。
//
// ⚠️ 日内 K 线（30m/60m）与日线的三个关键差异：
//   1. 同一天有多根 ⇒ 去重键必须用 **time_key**，不能用 date（会把一天压成一根）
//   2. 翻页时 end 取「本页最早那根的日期」**不减 1 天**，否则同日后半段会被跳过
//   3. 时间窗口用 `--months`，不用 `--years`
//
// 产物：
//   data/kline/<period>/<SYMBOL>.json   每只一个文件（内容变化才重写）
//   data/kline/<period>/_manifest.json  汇总索引
import { mkdirSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { apiGet, historyKline, syncTime } from './lib/futu-client.mjs';
import { PERIODS, barKeyFn } from './lib/periods.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..', '..');

// ---------------------------------------------------------------- 参数
const argv = process.argv.slice(2);
const arg = (name, def) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : def;
};
const flag = (name) => argv.includes(`--${name}`);

const PERIOD = arg('period', 'daily');
const CFG = PERIODS[PERIOD];
if (!CFG) throw new Error(`未知周期 ${PERIOD}，可选: ${Object.keys(PERIODS).join(' / ')}`);

const OUT_DIR = join(ROOT, 'data', 'kline', PERIOD);
mkdirSync(OUT_DIR, { recursive: true });

const FORCE = flag('force');
const MODE = FORCE ? 'full' : arg('mode', 'incr'); // incr | full
const WINDOW = Number(arg(CFG.winArg, CFG.winDefault));
const ONLY = arg('only', '') ? arg('only').split(',') : null;
const TYPES = arg('types', '') ? arg('types').split(',') : null;
const GAP_MS = Number(arg('gap', 700));
const BUDGET_S = Number(arg('budget', 0));
const MAX_PAGES = 60;

// ---------------------------------------------------------------- 工具
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const todayISO = () => new Date().toISOString().slice(0, 10);
const daysAgoISO = (n) => new Date(Date.now() - n * 86400_000).toISOString().slice(0, 10);
const yearsAgoISO = (n) => new Date(Date.now() - n * 365.25 * 86400_000).toISOString().slice(0, 10);
const monthsAgoISO = (n) => new Date(Date.now() - n * 30.44 * 86400_000).toISOString().slice(0, 10);
const ymdToISO = (d) => `${String(d).slice(0, 4)}-${String(d).slice(4, 6)}-${String(d).slice(6, 8)}`;
const prevDayISO = (ymd) => {
  const dt = new Date(`${ymdToISO(ymd)}T00:00:00Z`);
  dt.setUTCDate(dt.getUTCDate() - 1);
  return dt.toISOString().slice(0, 10);
};
const windowSinceISO = () => (CFG.intraday ? monthsAgoISO(WINDOW) : yearsAgoISO(WINDOW));

/** 去重/排序键（日线 date / 分钟线 time_key）—— 定义在 lib/periods.mjs */
const barKey = barKeyFn(PERIOD);

function loadExisting(symbol) {
  const f = join(OUT_DIR, `${symbol}.json`);
  if (!existsSync(f)) return null;
  try {
    return JSON.parse(readFileSync(f, 'utf8'));
  } catch {
    return null;
  }
}

/** 从 today 往前翻页，直到最早日期 <= sinceISO */
async function pull(symbol, sinceISO) {
  const bars = new Map();
  let end = todayISO();
  let pages = 0;
  let firstPage = null;
  let volumePrecision = 0;
  let prevEarliest = null;

  for (let p = 0; p < MAX_PAGES; p++) {
    const r = await historyKline(symbol, { end, num: 370, ktype: CFG.ktype });
    if (r.ret_code !== 0) return { ok: false, error: `${r.ret_code} ${r.ret_msg}` };

    const list = r.data?.kline_list ?? [];
    if (firstPage === null) firstPage = list;
    if (r.data?.volume_precision != null) volumePrecision = r.data.volume_precision;
    if (list.length === 0) break;

    pages++;
    for (const b of list) {
      // 丢掉「半成品」bar：接口对**正在形成**的那根会返回没有 OHLC 的占位条
      // （加密 7×24 最常见 —— 它的当前 bar 永远在形成中）。
      // 不丢的话下游会在 b['open'] 上直接 KeyError（转 Parquet 时踩过）。
      if (b.open == null || b.high == null || b.low == null || b.close == null) continue;
      bars.set(barKey(b), b);
    }

    const earliest = list[0];
    if (ymdToISO(earliest.date) <= sinceISO) break;

    // 日内：end 用当天（不减 1），否则同日后半段会被跳过；靠 time_key 去重处理重叠
    end = CFG.intraday ? ymdToISO(earliest.date) : prevDayISO(earliest.date);
    if (CFG.intraday) {
      if (prevEarliest !== null && earliest.time_key >= prevEarliest) break; // 无进展，防死循环
      prevEarliest = earliest.time_key;
    }
    await sleep(GAP_MS);
  }

  // 还原成交量：接口对部分品类（加密/外汇）会把 volume 放大 10^n 倍，须除以 10^n
  if (volumePrecision > 0) {
    for (const [k, b] of bars) bars.set(k, { ...b, volume: b.volume / 10 ** volumePrecision });
  }
  return { ok: true, bars, pages, firstPage, volumePrecision };
}

function save(s, bars, volumePrecision = 0) {
  const sorted = [...bars.values()].sort((a, b) => barKey(a) - barKey(b));
  writeFileSync(
    join(OUT_DIR, `${s.code}.json`),
    JSON.stringify(
      {
        symbol: s.code,
        name: s.name,
        stock_type: s.type,
        period: PERIOD,
        fetched_at: new Date().toISOString(),
        ktype: CFG.ktype,
        autype: 1,
        // volume 已按此精度还原（真实成交量 = 接口原值 / 10^volume_precision）
        volume_precision: volumePrecision,
        count: sorted.length,
        first_date: sorted.at(0)?.date ?? null,
        last_date: sorted.at(-1)?.date ?? null,
        // 日内周期用 time_key 定位首末根（date 只到「天」）
        first_time: sorted.at(0)?.time_key ?? null,
        last_time: sorted.at(-1)?.time_key ?? null,
        bars: sorted,
      },
      null,
      2
    )
  );
  return sorted;
}

/**
 * 重拉（全量 / 复权重拉 / 精度重拉）时的起始日期。
 * 取「已有文件最早日期」与「窗口起点」的**更早者** —— 否则用 --months 3 重拉一份 1 年的文件，
 * 会把前面 9 个月直接砍掉（数据丢失）。
 */
function refreshSince(existing) {
  const byWindow = windowSinceISO();
  if (!existing?.first_date) return byWindow;
  const byFile = ymdToISO(existing.first_date);
  return byFile < byWindow ? byFile : byWindow;
}

// ---------------------------------------------------------------- 标的清单
const symbolsFile = join(ROOT, 'data', 'watchlist', 'symbols.json');
if (!existsSync(symbolsFile)) throw new Error(`缺少 ${symbolsFile}，先跑 node fetch/rest/watchlist.mjs`);
let symbols = JSON.parse(readFileSync(symbolsFile, 'utf8')).symbols;
if (ONLY) symbols = symbols.filter((s) => ONLY.includes(s.code));
if (TYPES) symbols = symbols.filter((s) => TYPES.includes(s.type));

// ---------------------------------------------------------------- 完整性检查（--check）
// 纯本地：不连网络、不消耗任何额度、不写任何文件。
// 以「该周期里最新的那一天」为基准，把每个标的分成 完整 / 落后 / 缺失。
// ⚠️ 基准取自数据本身 —— 如果**所有**标的都停在上周，它也会显示「完整」，
//    所以要同时看一眼「最新」那一列对不对。
if (flag('check')) {
  const fmt = (name, v) => {
    if (v == null) return '—';
    if (!PERIODS[name].intraday) return String(v);
    return new Date(v).toISOString().slice(0, 16).replace('T', ' ') + 'Z';
  };

  // ⚠️ 基准必须**按市场**取 —— 各市场交易日历不同：
  //    加密 7×24（周末照样跑）、A股国庆休市、美股正常。
  //    混在一起比，会被加密顶到「今天」，其他市场全成假警报。
  const byMarket = new Map();
  for (const s of symbols) {
    const m = s.code.split('.')[0];
    if (!byMarket.has(m)) byMarket.set(m, []);
    byMarket.get(m).push(s);
  }
  const markets = [...byMarket.keys()].sort();

  console.log(`标的清单 ${symbols.length} 只 | ${markets.length} 个市场（--only / --types 已生效）\n`);
  console.log('周期   市场   实有/应有    最新               完整   落后   缺失');

  for (const [name, cfg] of Object.entries(PERIODS)) {
    const dir = join(ROOT, 'data', 'kline', name);
    for (const mkt of markets) {
      const rows = byMarket.get(mkt).map((s) => {
        const f = join(dir, `${s.code}.json`);
        if (!existsSync(f)) return { code: s.code, state: 'missing' };
        try {
          const d = JSON.parse(readFileSync(f, 'utf8'));
          const key = cfg.intraday ? d.last_time : d.last_date;
          return key == null ? { code: s.code, state: 'missing' } : { code: s.code, key };
        } catch {
          return { code: s.code, state: 'missing' };
        }
      });

      const have = rows.filter((r) => r.key != null);
      // 基准取**众数**，不取最大值 —— 少数品种带盘前/盘后数据（时间戳更晚），
      // 用最大值当基准会把绝大多数正常标的误判成「落后」。
      const counts = new Map();
      for (const r of have) counts.set(r.key, (counts.get(r.key) ?? 0) + 1);
      let latest = null;
      let best = -1;
      for (const [k, n] of counts) {
        if (n > best) {
          best = n;
          latest = k;
        }
      }
      const complete = have.filter((r) => r.key >= latest).length;
      const behind = have.filter((r) => r.key < latest);
      const missing = rows.filter((r) => r.state === 'missing');

      console.log(
        `${name.padEnd(7)}${mkt.padEnd(7)}${String(have.length).padStart(4)}/${String(rows.length).padEnd(5)}` +
          `${fmt(name, latest).padEnd(19)}${String(complete).padStart(4)}` +
          `${String(behind.length).padStart(7)}${String(missing.length).padStart(7)}`
      );

      if (missing.length) {
        const list = missing.map((r) => r.code).slice(0, 20).join(' ');
        console.log(`  缺失(${missing.length}): ${list}${missing.length > 20 ? ` …+${missing.length - 20}` : ''}`);
      }
      if (behind.length && behind.length <= 8) {
        console.log(`  落后: ${behind.map((r) => `${r.code}(${fmt(name, r.key)})`).join(' ')}`);
      }
    }
  }

  console.log('\n补齐：node fetch/rest/kline.mjs --period <daily|30m|60m>');
  process.exit(0);
}

await syncTime();

// ---------------------------------------------------------------- 交易日闸门
const markets = [...new Set(symbols.map((s) => s.code.split('.')[0]))];
const latestTradingDay = {};
for (const m of markets) {
  const r = await apiGet('/api/v1.0/quote/trading-days', { market: m, start: daysAgoISO(45), end: todayISO() });
  latestTradingDay[m] = r.ret_code === 0 && r.data?.trading_days?.length ? r.data.trading_days.at(-1).time : null;
  await sleep(GAP_MS);
}
console.log(`交易日闸门: ${markets.map((m) => `${m}=${latestTradingDay[m] ?? 'n/a'}`).join('  ')}`);
console.log(
  `周期 ${CFG.label}(${PERIOD}) | 标的 ${symbols.length} 只 | 模式 ${MODE} | 回看 ${WINDOW} ${CFG.intraday ? '个月' : '年'} | 间隔 ${GAP_MS}ms\n`
);

// ---------------------------------------------------------------- 主循环
const manifest = [];
const stat = { new: 0, updated: 0, unchanged: 0, 'basis-refresh': 0, 'full-refresh': 0, failed: 0, addedBars: 0, revisedBars: 0 };
const startedAt = Date.now();

for (const [i, s] of symbols.entries()) {
  const tag = `[${String(i + 1).padStart(4)}/${symbols.length}] ${s.code.padEnd(14)}`;
  const existing = loadExisting(s.code);
  const gate = latestTradingDay[s.code.split('.')[0]];

  if (BUDGET_S && (Date.now() - startedAt) / 1000 > BUDGET_S) {
    console.log(`\n⏱ 达到时间预算 ${BUDGET_S}s，提前结束（再次运行自动续拉）`);
    break;
  }

  // ① 闸门：已到最近交易日 **且最后一根不是「标的市场今天」** → 0 请求。
  //    必须用标的市场本地日期（见 time_zone），不能用本机日期，否则港股白天跑美股会误跳过。
  const tzMin = existing?.bars?.at(-1)?.time_zone ?? 0;
  const marketToday = new Date(Date.now() + tzMin * 60_000).toISOString().slice(0, 10);
  const lastISO = existing ? ymdToISO(existing.last_date) : null;
  if (existing && MODE === 'incr' && gate && lastISO < marketToday && lastISO >= gate) {
    stat.unchanged++;
    manifest.push({ ...s, action: 'unchanged', count: existing.count, last: existing.last_date });
    process.stdout.write(`${tag} · 已最新\n`);
    continue;
  }

  try {
    if (!existing || MODE === 'full') {
      const r = await pull(s.code, refreshSince(existing));
      if (!r.ok) throw new Error(r.error);
      const sorted = save(s, r.bars, r.volumePrecision);
      const action = existing ? 'full-refresh' : 'new';
      stat[action]++;
      stat.addedBars += sorted.length - (existing?.count ?? 0);
      manifest.push({ ...s, action, count: sorted.length, last: sorted.at(-1)?.date, pages: r.pages });
      process.stdout.write(`${tag} ✓ ${action.padEnd(13)} ${String(sorted.length).padStart(6)} 根 / ${r.pages} 页\n`);
    } else {
      const r = await pull(s.code, ymdToISO(existing.last_date));
      if (!r.ok) throw new Error(r.error);

      // 复权基准校验：挑一根「已收盘」的已知 K 线做对照（不能用最后一根，7×24 品种当日仍在变）。
      // ⚠️ 对照必须按 barKey 匹配：日内一天有多根，用 date 匹配会拿到当天第一根，误判为基准变化。
      const todayYmd = Number(marketToday.replace(/-/g, ''));
      const oldProbe = [...existing.bars].reverse().find((b) => b.date < todayYmd);
      const newProbe = oldProbe ? r.firstPage?.find((b) => barKey(b) === barKey(oldProbe)) : null;
      const basisChanged = Boolean(oldProbe && newProbe && Math.abs(newProbe.close - oldProbe.close) > 1e-6);

      // 成交量精度变化 ⇒ 已存 volume 不可信，必须全量重拉
      const storedVp = existing.volume_precision ?? 0;
      const precisionChanged = r.volumePrecision !== storedVp;

      if (basisChanged || precisionChanged) {
        const f = await pull(s.code, refreshSince(existing));
        if (!f.ok) throw new Error(f.error);
        const sorted = save(s, f.bars, f.volumePrecision);
        const reason = basisChanged ? '复权基准变化' : `成交量精度变化(${storedVp}→${r.volumePrecision})`;
        stat['basis-refresh']++;
        manifest.push({ ...s, action: 'basis-refresh', reason, count: sorted.length, last: sorted.at(-1)?.date, pages: r.pages + f.pages });
        process.stdout.write(`${tag} ⟳ ${reason}，全量重拉 ${sorted.length} 根\n`);
      } else {
        const merged = new Map(existing.bars.map((b) => [barKey(b), b]));
        let added = 0;
        let revised = 0;
        for (const [k, b] of r.bars) {
          const prev = merged.get(k);
          if (!prev) {
            merged.set(k, b);
            added++;
          } else if (prev.close !== b.close || prev.high !== b.high || prev.low !== b.low || prev.volume !== b.volume) {
            // 覆盖而非跳过：当日/当根 K 线仍在变化，必须用新值修正半成品
            merged.set(k, b);
            revised++;
          }
        }
        if (added === 0 && revised === 0) {
          stat.unchanged++;
          manifest.push({ ...s, action: 'unchanged', count: existing.count, last: existing.last_date });
          process.stdout.write(`${tag} · 无新增\n`);
        } else {
          const sorted = save(s, merged, r.volumePrecision);
          stat.updated++;
          stat.addedBars += added;
          stat.revisedBars += revised;
          manifest.push({ ...s, action: 'updated', added, revised, count: sorted.length, last: sorted.at(-1)?.date, pages: r.pages });
          process.stdout.write(
            `${tag} + 新增 ${String(added).padStart(4)} 根${revised ? ` / 修正 ${revised} 根` : ''} → ${sorted.length} 根\n`
          );
        }
      }
    }
  } catch (e) {
    stat.failed++;
    manifest.push({ ...s, action: 'failed', error: e.message });
    process.stdout.write(`${tag} ✗ ${e.message}\n`);
  }

  await sleep(GAP_MS);
}

// ---------------------------------------------------------------- 汇总
writeFileSync(
  join(OUT_DIR, '_manifest.json'),
  JSON.stringify(
    { fetched_at: new Date().toISOString(), period: PERIOD, ktype: CFG.ktype, mode: MODE, window: WINDOW, latest_trading_day: latestTradingDay, stat, items: manifest },
    null,
    2
  )
);

console.log(
  `\n完成: 新增 ${stat.new} | 更新 ${stat.updated} | 无变化 ${stat.unchanged} | 复权重拉 ${stat['basis-refresh']} | 全量重拉 ${stat['full-refresh']} | 失败 ${stat.failed}`
);
console.log(`本次新增 K 线 ${stat.addedBars} 根，修正半成品 ${stat.revisedBars} 根`);
