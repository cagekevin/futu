// 数据自检：校验 data/kline/<period>/ 下所有文件的完整性
//
// 周期来自 `lib/periods.mjs`（**唯一出处**）—— 遍历它列出的**全部**周期，
// 所以以后加周期不用改这里。（以前只查 daily，30m/60m 的坏数据只能等下游炸。）
//
// 检查项（日线与分钟线分别适配）：
//   ① bars.length 与 count 字段一致
//   ② first_date / last_date 与首末根一致（分钟线额外校验 first_time / last_time）
//   ③ 排序键**严格递增且无重复** —— 日线用 date，**分钟线用 time_key**
//      （分钟线同一天有多根，用 date 判会满屏误报）
//   ④ OHLC 为正数、high >= low；**字段缺失也算错**
//      ⚠️ 接口对「正在形成」的那根会返回没有 OHLC 的占位条，这条专门抓它
//   ⑤ volume 非负（缺失也算错）
//   ⑥ volume_precision 字段存在（缺了说明是老文件，成交量可能未还原）
//
// 用法：
//   node fetch/rest/validate.mjs                # 全部周期
//   node fetch/rest/validate.mjs --period 60m   # 只查一个周期
//
// 退出码：有问题 = 1（方便挂到定时任务上）
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { PERIODS, barKeyFn } from './lib/periods.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..', '..');

const argv = process.argv.slice(2);
const i = argv.indexOf('--period');
const only = i >= 0 ? argv[i + 1] : null;
const periods = only ? [only] : Object.keys(PERIODS);
for (const p of periods) {
  if (!PERIODS[p]) throw new Error(`未知周期 ${p}，可选: ${Object.keys(PERIODS).join(' / ')}`);
}

let grandProblems = 0;

for (const period of periods) {
  const cfg = PERIODS[period];
  const dir = join(ROOT, 'data', 'kline', period);

  console.log(`\n=== ${cfg.label}(${period}) ===`);
  if (!existsSync(dir)) {
    console.log('目录不存在，跳过');
    continue;
  }

  const key = barKeyFn(period);
  const files = readdirSync(dir).filter((f) => f.endsWith('.json') && f !== '_manifest.json');
  const problems = [];
  const byType = {};
  const byPrecision = {};
  let totalBars = 0;
  let earliest = null;
  let latest = null;

  for (const f of files) {
    const d = JSON.parse(readFileSync(join(dir, f), 'utf8'));
    const b = d.bars ?? [];

    // ① 条数
    if (b.length !== d.count) problems.push(`${f}: count=${d.count} 但 bars=${b.length}`);

    // ② 首末与元数据一致
    if (b.length) {
      if (d.first_date !== b[0].date) problems.push(`${f}: first_date 与首根不符`);
      if (d.last_date !== b.at(-1).date) problems.push(`${f}: last_date 与末根不符`);
      if (cfg.intraday) {
        if (d.first_time !== b[0].time_key) problems.push(`${f}: first_time 与首根不符`);
        if (d.last_time !== b.at(-1).time_key) problems.push(`${f}: last_time 与末根不符`);
      }
    }

    // ③ 排序键严格递增（分钟线按 time_key）
    for (let k = 1; k < b.length; k++) {
      if (key(b[k]) <= key(b[k - 1])) {
        problems.push(`${f}: 排序键未严格递增 @${k} (${key(b[k - 1])} → ${key(b[k])})`);
        break;
      }
    }

    // ④⑤ 每根 bar 的字段合法性
    for (const x of b) {
      const ohlc = [x.open, x.high, x.low, x.close];
      if (!ohlc.every((v) => Number.isFinite(v) && v > 0)) {
        // 半成品 bar 会整个缺 OHLC —— Number.isFinite 一并挡住 undefined / null
        problems.push(`${f}: OHLC 非法或缺失 @${x.date ?? x.time_key}`);
        break;
      }
      if (x.high < x.low) {
        problems.push(`${f}: high<low @${x.date}`);
        break;
      }
      if (!Number.isFinite(x.volume) || x.volume < 0) {
        problems.push(`${f}: 成交量非法或缺失 @${x.date}`);
        break;
      }
    }

    // ⑥ 精度字段
    if (d.volume_precision === undefined) problems.push(`${f}: 缺 volume_precision 字段`);

    totalBars += b.length;
    byType[d.stock_type] = (byType[d.stock_type] ?? 0) + 1;
    const vp = String(d.volume_precision ?? '缺失');
    byPrecision[vp] = (byPrecision[vp] ?? 0) + 1;
    if (b.length) {
      if (earliest === null || b[0].date < earliest) earliest = b[0].date;
      if (latest === null || b.at(-1).date > latest) latest = b.at(-1).date;
    }
  }

  grandProblems += problems.length;
  console.log(`文件数   : ${files.length}`);
  console.log(`总 K 线  : ${totalBars.toLocaleString()}`);
  console.log(`日期跨度 : ${earliest} → ${latest}`);
  console.log(`类型分布 : ${Object.entries(byType).map(([k, v]) => `${k}=${v}`).join('  ')}`);
  console.log(`精度分布 : ${Object.entries(byPrecision).map(([k, v]) => `vp${k}=${v}`).join('  ')}`);
  console.log(`校验问题 : ${problems.length === 0 ? '无 ✅' : `${problems.length} 处`}`);
  for (const p of problems.slice(0, 10)) console.log(`  - ${p}`);
  if (problems.length > 10) console.log(`  ... 另有 ${problems.length - 10} 处`);
}

console.log(`\n合计问题：${grandProblems}`);
process.exitCode = grandProblems ? 1 : 0;
