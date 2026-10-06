// 探测某标的某周期的历史深度：一路往前翻页直到没有数据，报告最早日期与总根数
//
// 用法：
//   node fetch/rest/depth.mjs US.AAPL 8,9        # 30分钟、60分钟
//   node fetch/rest/depth.mjs US.AAPL 2,8,9      # 日线、30分钟、60分钟 对比
//
// ktype: 1=1分 6=5分 7=15分 8=30分 9=60分 14=120分 2=日 3=周 4=月
//
// ⚠️ 日内 K 线同一天有多根，**不能按 date 去重**（会collapse掉），必须按 time_key。
//    翻页时 end 取「本页最早那根的日期」（不减 1 天），避免同日漏根；靠 time_key 去重处理重叠。
import { historyKline, syncTime } from './lib/futu-client.mjs';

const [symbol = 'US.AAPL', ktypes = '8,9'] = process.argv.slice(2);
const GAP_MS = 700;
const MAX_PAGES = 100;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ymdToISO = (d) => `${String(d).slice(0, 4)}-${String(d).slice(4, 6)}-${String(d).slice(6, 8)}`;

const KNAME = { 1: '1分', 6: '5分', 7: '15分', 8: '30分', 9: '60分', 14: '120分', 2: '日', 3: '周', 4: '月' };

await syncTime();
console.log(`标的 ${symbol}\n`);

for (const kt of ktypes.split(',').map(Number)) {
  const bars = new Map(); // time_key → bar
  let end = new Date().toISOString().slice(0, 10);
  let pages = 0;
  let prevEarliest = null;

  for (let p = 0; p < MAX_PAGES; p++) {
    const r = await historyKline(symbol, { end, num: 370, ktype: kt });
    if (r.ret_code !== 0) {
      console.log(`ktype=${kt} (${KNAME[kt] ?? '?'}) 失败: ${r.ret_code} ${r.ret_msg}`);
      break;
    }
    const list = r.data?.kline_list ?? [];
    if (list.length === 0) break;

    for (const b of list) bars.set(b.time_key, b);
    pages++;

    const earliest = list[0];
    if (prevEarliest !== null && earliest.time_key >= prevEarliest) break; // 无进展，防死循环
    prevEarliest = earliest.time_key;
    end = ymdToISO(earliest.date);
    await sleep(GAP_MS);
  }

  const arr = [...bars.values()].sort((a, b) => a.time_key - b.time_key);
  if (!arr.length) {
    console.log(`ktype=${kt} (${KNAME[kt] ?? '?'}): 无数据`);
    continue;
  }
  const first = arr[0];
  const last = arr.at(-1);
  const days = Math.round((last.time_key - first.time_key) / 86400_000);
  const reached = pages >= MAX_PAGES ? `（达到 ${MAX_PAGES} 页上限，可能还能更早）` : '（已到数据尽头）';
  console.log(
    `ktype=${kt} (${KNAME[kt] ?? '?'}): ${arr.length} 根 / ${pages} 页 | ` +
      `${ymdToISO(first.date)} → ${ymdToISO(last.date)} | 跨 ${days} 自然日 ${reached}`
  );
}
