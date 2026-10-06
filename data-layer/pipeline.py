"""M5 管线（pipeline）—— 编排一天的数据生产。

定交易日 → 调 M2 取数 → 调 M3 计算 → 调 M1 入库。

承 L1（交易日 = 美东日期，非本机日期）
承 L2（任一步失败 → 立即停，报错不继续）
承 L3（只编排，不实现能力：无业务算法，只有"调 M2/M3/M1"）
承 L4（一天 = 一个原子单位：中途失败 → 库里当天数据不残）

用法：
    python pipeline.py                 # 跑今天（美东）
    python pipeline.py --day 2026-10-06
    python pipeline.py --symbols SPX NDX QQQ
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from config import DATA_DIR, ET
from engine import gex as engine_gex
from fetch import fetch_api
from fetch import risk_free_rate_source
from store import axis
from trading_time import to_unix_seconds, trading_day as bar_et_day

log = logging.getLogger("pipeline")


class PipelineError(Exception):
    """管线某步失败 —— 立即停，附失败步 / 原因（承 L2）。"""


@dataclass(frozen=True)
class StepResult:
    step: str
    detail: str


# ── M5.1 定日期（美东，不是本机日期）────────────────────────────────────

def trading_day(now_et: datetime | None = None) -> str:
    """交易日 = **美东**日期（承 L1）。

    在香港白天跑美股时，本机日期可能是次日 —— 必须用 ET。
    """
    now = now_et or datetime.now(ET)
    return now.astimezone(ET).strftime("%Y-%m-%d")


# ── M5.2 编排 ────────────────────────────────────────────────────────────

def run_day(
    day: str,
    symbols: list[str],
    *,
    root=None,
    r: float | None = None,
    allow_rate_fallback: bool = False,
    bucket: str = "Tout",
    source_name: str | None = None,
) -> dict:
    """跑一天：对每个标的 取数 → 计算 → 入库（当天一批，原子）。

    任一步失败 → 抛 PipelineError，**不入库该标的**（承 L2）；
    批次写入保证"全成 / 全不写"（承 L4）。
    """
    root = root if root is not None else DATA_DIR
    now_et = datetime.now(ET)

    # 交易日自检（承 L1 / P4）：不允许把"请求日"和"美东今天"混为一谈。
    today_et = trading_day(now_et)
    if day != today_et:
        log.info("管线：day=%s 与美东今天 %s 不一致（回填/补跑）", day, today_et)

    # ① 取无风险利率（IO 归 fetch；engine 只接收值，承 G3）
    if r is None:
        rr = risk_free_rate_source.fetch_rate(allow_fallback=allow_rate_fallback)
        r = rr.value
        if rr.is_fallback:
            log.warning("管线：利率用了兜底常量（已显形，承 P1）")
    log.info("管线：day=%s r=%.4f symbols=%s", day, r, symbols)

    steps: list[StepResult] = []
    records: list[tuple[str, str, object]] = []

    for sym in symbols:
        # ② 取数（M2）
        try:
            res = fetch_api.chain(sym, as_of=day, source=source_name)
        except Exception as e:
            raise PipelineError(f"[{sym}] 取数失败，停止（承 L2）：{e}") from e
        steps.append(StepResult("fetch", f"{sym}: {len(res.rows)} 合约 spot={res.spot}"))

        # ③ 计算（M3）—— 纯函数，输入底层数据，输出指标；自带来源（承 G4）
        source_key = f"{sym}:{day}:chain"
        try:
            metrics = engine_gex.compute(
                res.rows, res.spot,
                as_of=day, now_et=now_et, r=r,
                source_key=source_key, bucket=bucket,
            )
        except Exception as e:
            raise PipelineError(f"[{sym}] 计算失败，停止（承 L2）：{e}") from e
        steps.append(StepResult("compute", f"{sym}: net_gex={metrics['net_gex']:.2e}"))

        # ④ 组装入库记录（底层 + 指标；键不含源名，承 X4）
        records.append((sym, "chain", _chain_payload(res, day)))
        records.append((sym, "spot", {"value": res.spot,
                                      "feed_timestamp": to_unix_seconds(res.feed_timestamp),
                                      "fetched_at": to_unix_seconds(res.fetched_at)}))
        records.append((sym, "net_gex", {"value": metrics["net_gex"], **self_prov(metrics)}))
        records.append((sym, "net_gex_0dte", {"value": metrics["net_gex_0dte"], **self_prov(metrics)}))
        records.append((sym, "net_dex", {"value": metrics["net_dex"], **self_prov(metrics)}))
        records.append((sym, "zero_gamma", {"value": metrics["zero_gamma"], **self_prov(metrics)}))
        records.append((sym, "call_wall", {"value": metrics["call_wall"], **self_prov(metrics)}))
        records.append((sym, "put_wall", {"value": metrics["put_wall"], **self_prov(metrics)}))
        records.append((sym, "abs_call_wall", {"value": metrics["abs_call_wall"], **self_prov(metrics)}))
        records.append((sym, "abs_put_wall", {"value": metrics["abs_put_wall"], **self_prov(metrics)}))
        records.append((sym, "pc_oi", {"value": metrics["pc_oi"], **self_prov(metrics)}))
        records.append((sym, "pc_volume", {"value": metrics["pc_volume"], **self_prov(metrics)}))

    # ⑤ 入库（M1）—— 一天一批，原子（承 L4 / X2）
    try:
        axis.put_many(day, records, root=root)
    except Exception as e:
        raise PipelineError(f"入库失败，当天不残（承 L4）：{e}") from e
    steps.append(StepResult("store", f"{day}: {len(records)} 条已入库"))

    return {"day": day, "r": r, "steps": steps, "records": len(records)}


# ── 交易日历（全局数据项，一天一条，承"无标的数据项"）──────────────────

def run_calendar(
    markets: list[str],
    start: str,
    end: str,
    *,
    root=None,
    source_name: str | None = None,
) -> dict:
    """取 [start, end] 区间内若干市场的**开市日清单**，逐日写成全局项 `calendar`。

    形态（承数据原生）：
      - 键：`(交易日, None, calendar)`（**无标的**，全局）
      - 值：`{market, trade_date_type, trade_second}`（**接口原样**，不推断）
      - **只写开市日**（非交易日不存在 —— 缺就是缺，承 K4）

    ⚠️ 单次上限约 4000 条：区间过长请分段调用。
    """
    root = root if root is not None else DATA_DIR
    steps: list[StepResult] = []
    per_day: dict[str, dict[str, dict]] = {}  # {day: {market: record}}
    for mkt in markets:
        try:
            res = fetch_api.trading_days(mkt, start, end, source=source_name)
        except Exception as e:
            raise PipelineError(f"[日历 {mkt}] 取数失败，停止（承 L2）：{e}") from e
        for row in res.rows:
            d = row["day"]
            per_day.setdefault(d, {})[mkt] = {
                "market": mkt,
                "trade_date_type": row["trade_date_type"],
                "trade_second": row["trade_second"],
            }
        steps.append(StepResult("calendar-fetch",
                                f"{mkt}: {len(res.rows)} 开市日 [{start}..{end}]"))

    # 逐日入库：一天一条全局项。值为 `{市场: 开市记录}` —— 该天的各市场开市事实。
    written = 0
    for d in sorted(per_day):
        payload = per_day[d]                    # {"US": {...}, "HK": {...}}
        try:
            axis.put(d, None, "calendar", payload, root=root)
        except Exception as e:
            raise PipelineError(f"[日历 {d}] 入库失败（承 L4）：{e}") from e
        written += 1
    steps.append(StepResult("calendar-store", f"{written} 天已入库（全局项）"))
    return {"markets": markets, "start": start, "end": end,
            "days": written, "steps": steps}


# ── 复权因子（公司行动，每除权日一条，承"K线存 raw + 因子单独存"）────────

def run_adjust_factors(symbols: list[str], *, root=None,
                       source_name: str | None = None) -> dict:
    """取若干标的的**复权因子**，按除权日写成数据项 `adjust_factor`。

    形态：键 `(除权日, 标的, adjust_factor)`；值 = 该除权日的因子行（原样）。
    与 K线（存不复权）配套：下游要前/后复权价时**自算**（承"底层 + 可自算"）。
    走 OpenD（`get_rehab`，**不吃历史 K线额度**）。
    """
    root = root if root is not None else DATA_DIR
    steps: list[StepResult] = []
    total = 0
    for sym in symbols:
        try:
            res = fetch_api.rehab(sym, source=source_name)
        except Exception as e:
            raise PipelineError(f"[复权 {sym}] 取数失败，停止（承 L2）：{e}") from e
        for row in res.rows:
            d = row["ex_div_date"]
            try:
                axis.put(d, sym, "adjust_factor", row, root=root)
            except Exception as e:
                raise PipelineError(f"[复权 {sym} {d}] 入库失败（承 L4）：{e}") from e
            total += 1
        steps.append(StepResult("rehab", f"{sym}: {len(res.rows)} 个除权日"))
    steps.append(StepResult("rehab-store", f"{total} 条 adjust_factor 入库"))
    return {"symbols": symbols, "records": total, "steps": steps}


# ── K线（底层，按 bar 的美东日入库；缓存 + 增量，承"四问·缓存"）────────

def run_kline(
    symbols: list[str],
    *,
    ktype: str = "K_DAY",
    root=None,
    source_name: str | None = None,
    full_years: int = 3,
    full_months: int | None = None,
    refresh_last_day: bool = True,
) -> dict:
    """取若干标的的 K线，按**每根 bar 的美东交易日**入库（数据项 `kline`）。

    承"数据层四问 · 缓存 + 增量"：
      - **首次**（库里没有该标的的 kline）→ 取全窗口（日线 `full_years` 年 /
        分钟线 `full_months` 月）；
      - **已有** → 只补**从最后一天起**（`refresh_last_day=True` 会重取最后一天，
        因为当天那根可能是半成品），**不重拉全历史**。

    形态（数据天然形态）：键 `(bar 的美东日, 标的, kline)`；
    值 = `{bars: [该日各根], ktype, as_of, fetched_at}`。
    走 `fetch_api.kline`（默认 REST、无额度；4H 需显式 `source="futu-opend"`）。
    """
    root = root if root is not None else DATA_DIR
    as_of = trading_day(datetime.now(ET))
    steps: list[StepResult] = []
    written = 0
    for sym in symbols:
        last = axis.latest_day(sym, "kline", root=root)
        if last is None:
            since = None
            years, months = full_years, full_months
        else:
            since = last if refresh_last_day else _next_day(last)
            years, months = _window_for(since, as_of)
        try:
            res = fetch_api.kline(sym, as_of=as_of, ktype=ktype,
                                  years=years, months=months, source=source_name)
        except Exception as e:
            raise PipelineError(f"[K线 {sym}] 取数失败，停止（承 L2）：{e}") from e
        by_day: dict[str, list[dict]] = {}
        for b in res.rows:
            d = bar_et_day(b["time_key"], unit="s")   # 库里 time_key 已是 Unix 秒
            if since is not None and d < since:
                continue
            by_day.setdefault(d, []).append(b)
        for d in sorted(by_day):
            payload = {
                "bars": by_day[d],
                "ktype": res.extra.get("ktype"),
                "as_of": d,
                "fetched_at": to_unix_seconds(res.fetched_at),
            }
            try:
                axis.put(d, sym, "kline", payload, root=root)
            except Exception as e:
                raise PipelineError(f"[K线 {sym} {d}] 入库失败（承 L4）：{e}") from e
            written += 1
        steps.append(StepResult(
            "kline", f"{sym}: since={since or '全量'} → {len(by_day)} 天"))
    steps.append(StepResult("kline-store", f"{written} 天入库"))
    return {"symbols": symbols, "ktype": ktype, "days": written, "steps": steps}


def _next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _window_for(since: str, as_of: str) -> tuple[int, int]:
    """由 `[since, as_of]` 跨度推一个**足够覆盖**的取数窗口（年 / 月，各 +1 余量）。"""
    gap = max(0, (date.fromisoformat(as_of) - date.fromisoformat(since)).days)
    return max(1, gap // 365 + 1), max(1, gap // 30 + 1)


def self_prov(metrics: dict) -> dict:
    """指标的自证字段（承 G4）：版本 + 计算时刻 + 口径。

    指标**只存"怎么算"（依赖的 source_key/参数），不冗余存底层值**（承 X1）。
    """
    return {
        "source_key": metrics["source_key"],
        "computed_at": metrics["computed_at"],
        "bucket": metrics["bucket"],
        "weight_col": metrics["weight_col"],
        "as_of": metrics["as_of"],
    }


def _chain_payload(res, day: str) -> dict:
    """期权链的落库结构（底层数据，承 F5：每合约都在）。

    时间戳一律 **Unix 秒**（承"时区统一"）；`as_of` 是交易日（日期，非时刻）。
    """
    return {
        "spot": res.spot,
        "feed_timestamp": to_unix_seconds(res.feed_timestamp),
        "fetched_at": to_unix_seconds(res.fetched_at),
        "as_of": day,
        "rows": res.rows,
        "extra": res.extra,
    }


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="trading-desk 数据管线")
    ap.add_argument("--day", default=None, help="交易日（美东，默认今天）")
    ap.add_argument("--symbols", nargs="*", default=["SPX", "NDX", "SPY", "QQQ", "IWM"])
    ap.add_argument("--bucket", default="Tout")
    ap.add_argument("--rate-fallback", action="store_true",
                    help="允许利率用兜底常量（默认不允许，承 P6）")
    # 交易日历（全局项）：独立一步，可单独跑
    ap.add_argument("--calendar", action="store_true",
                    help="只跑交易日历（--markets/--start/--end）")
    ap.add_argument("--markets", nargs="*", default=["US"])
    ap.add_argument("--start", default=None, help="日历起始（YYYY-MM-DD）")
    ap.add_argument("--end", default=None, help="日历结束（YYYY-MM-DD，默认今天）")
    ap.add_argument("--adjust-factors", action="store_true",
                    help="只跑复权因子（--symbols）")
    ap.add_argument("--kline", action="store_true",
                    help="只跑 K线（--symbols/--ktype；默认 REST，按 bar 的美东日入库）")
    ap.add_argument("--ktype", default="K_DAY", help="K线周期（默认 K_DAY）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    day = args.day or trading_day()

    # 日历模式：只跑日历（全局项），不跑标的
    if args.calendar:
        end = args.end or day
        start = args.start or end
        out = run_calendar(args.markets, start, end)
        print(f"✅ 日历 {start}..{end}：{out['days']} 天入库（市场 {out['markets']}）")
        for s in out["steps"]:
            print(f"   [{s.step}] {s.detail}")
        return

    # 复权因子模式：只跑公司行动
    if args.adjust_factors:
        out = run_adjust_factors(args.symbols)
        print(f"✅ 复权因子：{out['records']} 条入库（{out['symbols']}）")
        for s in out["steps"]:
            print(f"   [{s.step}] {s.detail}")
        return

    # K线模式：按 bar 的美东日入库（缓存 + 增量）
    if args.kline:
        out = run_kline(args.symbols, ktype=args.ktype)
        print(f"✅ K线 {out['ktype']}：{out['days']} 天入库（{out['symbols']}）")
        for s in out["steps"]:
            print(f"   [{s.step}] {s.detail}")
        return

    out = run_day(day, args.symbols, allow_rate_fallback=args.rate_fallback,
                  bucket=args.bucket)
    print(f"✅ {out['day']} 完成：r={out['r']:.4f}，{out['records']} 条入库")
    for s in out["steps"]:
        print(f"   [{s.step}] {s.detail}")


if __name__ == "__main__":
    main()
