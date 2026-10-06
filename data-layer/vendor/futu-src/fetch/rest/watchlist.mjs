// 拉取自选股全量清单：所有分组 → 逐组拉标的 → 并集去重 → 落盘
//
// 接口：
//   GET /api/v1.0/quote/user-security-group         分组列表
//   GET /api/v1.0/quote/user-security?group_name=X  某分组的标的
//
// 产物（data/watchlist/）：
//   groups.json    分组元信息快照
//   by-group.json  分组 → 代码数组
//   symbols.json   并集去重（含 stock_type 分类），供拉 K 线使用
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { apiGet, syncTime } from './lib/futu-client.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(HERE, '..', '..', 'data', 'watchlist');
mkdirSync(OUT_DIR, { recursive: true });

await syncTime();

// 1. 分组列表
const g = await apiGet('/api/v1.0/quote/user-security-group');
if (g.ret_code !== 0) throw new Error(`拉分组失败: ${g.ret_code} ${g.ret_msg}`);
const groups = g.data.group_list;
console.log(`分组 ${groups.length} 个: ${groups.map((x) => x.group_name).join(' / ')}\n`);

// 2. 逐组拉标的
const byGroup = {};
const merged = new Map();
for (const { group_name } of groups) {
  const r = await apiGet('/api/v1.0/quote/user-security', { group_name });
  if (r.ret_code !== 0) {
    console.warn(`  ✗ ${group_name}: ${r.ret_code} ${r.ret_msg}`);
    continue;
  }
  const list = r.data.security_list ?? [];
  byGroup[group_name] = list.map((x) => x.code).filter(Boolean);
  for (const s of list) {
    if (!s.code) continue; // 少数条目只有 stock_id 没有 code（退市残留），跳过
    if (!merged.has(s.code)) {
      merged.set(s.code, { code: s.code, name: s.sc_name || s.name, type: s.stock_type, groups: [] });
    }
    merged.get(s.code).groups.push(group_name);
  }
  console.log(`  ✓ ${group_name.padEnd(18)} ${String(list.length).padStart(4)} 条`);
}

// 3. 落盘
const stamp = new Date().toISOString();
writeFileSync(join(OUT_DIR, 'groups.json'), JSON.stringify({ fetched_at: stamp, groups }, null, 2));
writeFileSync(join(OUT_DIR, 'by-group.json'), JSON.stringify({ fetched_at: stamp, by_group: byGroup }, null, 2));

const symbols = [...merged.values()].sort((a, b) => a.code.localeCompare(b.code));
const byType = symbols.reduce((m, s) => ((m[s.type] = (m[s.type] ?? 0) + 1), m), {});
writeFileSync(
  join(OUT_DIR, 'symbols.json'),
  JSON.stringify({ fetched_at: stamp, count: symbols.length, by_type: byType, symbols }, null, 2)
);

console.log(`\n并集去重后 ${symbols.length} 只`);
console.log('按类型:', Object.entries(byType).map(([k, v]) => `${k}=${v}`).join('  '));
