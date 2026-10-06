// 周期定义的**唯一出处**。
//
// kline.mjs / validate.mjs / 以后的脚本都从这里读 ——
// 别各自维护一份，否则加周期时必然有一处忘了改（validate 只查 daily 就是这么来的）。
//
// ktype 是富途的 K 线类型编号；intraday 决定「去重/排序键」用哪个字段。

export const PERIODS = {
  daily: { ktype: 2, label: '日线', intraday: false, winArg: 'years', winDefault: 2 },
  '30m': { ktype: 8, label: '30分钟', intraday: true, winArg: 'months', winDefault: 3 },
  '60m': { ktype: 9, label: '60分钟', intraday: true, winArg: 'months', winDefault: 3 },
};

export const PERIOD_NAMES = Object.keys(PERIODS);

/**
 * 去重 / 排序键。
 *
 * 日线用 `date`；**分钟线必须用 `time_key`** —— 同一天有多根，
 * 用 `date` 判递增会满屏误报，去重还会把一天压成一根。
 */
export function barKeyFn(period) {
  const { intraday } = PERIODS[period];
  return (bar) => (intraday ? bar.time_key : bar.date);
}
