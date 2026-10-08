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

import universe
from config import DATA_DIR, ET
from engine import gex as engine_gex
from engine import industry as engine_industry
from fetch import fetch_api
from fetch import risk_free_rate_source
from store import axis
from store.keys import UNIVERSE_SYMBOL
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
    ktype: str | None = None,
    root=None,
    source_name: str | None = None,
    full_years: int | None = None,
    full_months: int | None = None,
    refresh_last_day: bool = True,
    force_full: bool = False,
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
    # **默认值只有一处**（`universe` 设置项）；调用方给了就用调用方的（承"唯一设置项"）。
    ktype = ktype or universe.KLINE_KTYPE
    full_years = universe.KLINE_FULL_YEARS if full_years is None else full_years
    as_of = trading_day(datetime.now(ET))
    steps: list[StepResult] = []
    written = 0
    for sym in symbols:
        # `force_full` = 忽略库里已有数据、重拉全窗口（**加长历史**的唯一办法 ——
        # 增量逻辑只会"从最后一天往前补"，不会回溯延长）。
        last = None if force_full else axis.latest_day(sym, "kline", root=root)
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
        log.info("K线 %s: %d 天入库", sym, len(by_day))   # 长跑要有进度（-v 可见）
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


# ── 板块（概念 / 行业）成分股（吸收自参照项目 `fetch/opend/plates.py`）──

def run_plates(plate_codes: list[str], *, market: str = "US",
               plate_types: tuple[str, ...] = ("CONCEPT", "INDUSTRY"),
               root=None) -> dict:
    """拉**板块名册** + 指定板块的**成分股**，入库。

    落两条：
      · `plate_list`（**全局项**，标地位 None）：当天的板块名册（代码 / 名字 / 类型）；
      · `plate_members`（**带标的**，标地位 = 板块代码）：该板块的成分股。

    ⚠️ 成分股**变化很慢**（参照项目把它放 `data/plate_members.json`、不按天落）——
       不必天天拉；这里按"取数日"记一份，天然留历史（承 X1）。
       名册里没有的代码 → **跳过并上报**（承 F4：不静默）。
    """
    root = root if root is not None else DATA_DIR
    day = trading_day(datetime.now(ET))
    steps: list[StepResult] = []

    # ① 名册（各类型合并成一份全局项）
    roster: list[dict] = []
    for ptype in plate_types:
        res = fetch_api.plate_list(market=market, plate_type=ptype)
        roster.extend(res.rows)
    axis.put(day, None, "plate_list",
             {"market": market, "plates": roster}, root=root)
    steps.append(StepResult("plates:list", f"{len(roster)} 个板块（{market}）"))

    # ② 指定板块的成分股（逐只隔离失败）
    name_of = {r["symbol"]: r.get("name") for r in roster}
    skipped = [c for c in plate_codes if c not in name_of]
    written, failures = 0, []
    for code in plate_codes:
        if code not in name_of:
            continue
        try:
            res = fetch_api.plate_members(code)
            axis.put(day, code, "plate_members",
                     {"name": name_of[code], "market": market,
                      "members": res.rows,
                      "fetched_at": to_unix_seconds(res.fetched_at)},
                     root=root)
            written += 1
        except Exception as e:
            failures.append({"item": "plates", "symbol": code,
                             "error": str(e)[:200]})
    steps.append(StepResult("plates:members",
                            f"{written}/{len(plate_codes)} 个板块"))
    return {"day": day, "steps": steps, "skipped": skipped, "failures": failures}


# ── 全市场快照（行业 / RPS 的原料，**不吃历史额度**）────────────────────

def run_snapshot(*, market: str = "US", root=None) -> dict:
    """全市场快照入库 —— 键 `(当天, UNIVERSE, snapshot)`。

    走 V2 服务器端筛（`get_stock_screen`，**不吃历史额度**）：
    每只的 价 / 市值 / **行业** / N 日涨幅 —— 这是 `engine/industry.py` 的原料。
    """
    root = root if root is not None else DATA_DIR
    day = trading_day(datetime.now(ET))
    res = fetch_api.snapshot(market=market)
    axis.put(day, UNIVERSE_SYMBOL, "snapshot",
             {"market": market, "rows": res.rows,
              "fetched_at": to_unix_seconds(res.fetched_at)}, root=root)
    return {"day": day, "rows": len(res.rows),
            "steps": [StepResult("snapshot", f"{len(res.rows)} 只（{market}）")]}


# ── 行业 / 板块 的集体行为 + 状态机（纯计算在 `engine`，这里只编排）─────

# 状态机跨周期看：现在(20) / 中期(120) / 长期(250)。
_STATE_PERIODS = (20, 120, 250)


def _group_state(rows: list[dict], name_of: dict[str, str], *,
                 min_count: int) -> dict[str, dict]:
    """把 `rows`（含 `industry` 字段 + `chgN`）聚合成 `{组名: 记录}`。

    `name_of`：`{组名: 板块代码}`（行业没有代码 → 传 `{}`）。
    ⚠️ **复用** `engine.industry.industry_stats` —— 把板块名当行业名喂同一个函数，
       **一行不用改**（吸收自参照项目 `plates.py` 的结论）。
    """
    by_period = {
        n: {s.industry: s
            for s in engine_industry.industry_stats(rows, key=f"chg{n}",
                                                    min_count=min_count)}
        for n in _STATE_PERIODS
    }
    names = sorted({name for d in by_period.values() for name in d})
    out: dict[str, dict] = {}
    for name in names:
        base = (by_period[250].get(name) or by_period[120].get(name)
                or by_period[20].get(name))
        periods = {str(n): {"median": by_period[n][name].median,
                            "up_ratio": by_period[n][name].up_ratio}
                   for n in _STATE_PERIODS if name in by_period[n]}
        out[name] = {
            "name": name, "symbol": name_of.get(name),
            "count": base.count, "median": base.median, "mean": base.mean,
            "std": base.std, "up_ratio": base.up_ratio, "rps": base.rps,
            "state": engine_industry.classify(periods), "periods": periods,
        }
    return out


def run_industry_state(*, day: str | None = None, root=None,
                       min_count: int = 5) -> dict:
    """行业集体行为 + 状态机 → **全局项** `industry_state`。

    原料：当天的 `snapshot`（每只的 `industry` + `chgN`）。
    （行业名含中文/空格、不是合法代码 → 只能做全局项；板块有代码 → `run_plate_state`。）
    """
    root = root if root is not None else DATA_DIR
    day = day or trading_day(datetime.now(ET))
    rows = axis.get(day, UNIVERSE_SYMBOL, "snapshot", root=root)["rows"]
    states = _group_state(rows, {}, min_count=min_count)
    axis.put(day, None, "industry_state",
             {"periods": list(_STATE_PERIODS),
              "industries": list(states.values())}, root=root)
    return {"day": day, "count": len(states),
            "steps": [StepResult("industry_state", f"{len(states)} 个行业")]}


def run_plate_state(*, day: str | None = None, root=None) -> dict:
    """板块集体行为 + 状态机 → **带标的项** `plate_state`（标地位 = 板块代码）。

    原料：当天的 `snapshot`（每只的 `chgN`）+ 各板块的 `plate_members`。
    算法与行业**同一个** `_group_state`（板块名当行业名）。
    """
    root = root if root is not None else DATA_DIR
    day = day or trading_day(datetime.now(ET))
    by_symbol = {r["symbol"]: r
                 for r in axis.get(day, UNIVERSE_SYMBOL, "snapshot",
                                   root=root)["rows"]}

    rows: list[dict] = []
    name_of: dict[str, str] = {}
    for code in universe.load_plate_codes():
        if not axis.exists(day, code, "plate_members", root=root):
            continue
        rec = axis.get(day, code, "plate_members", root=root)
        name_of[rec["name"]] = code
        for member in rec["members"]:
            row = by_symbol.get(member["symbol"])
            if row:                       # 快照里没有的（新股/退市）→ 跳过，不编造
                rows.append({**row, "industry": rec["name"]})
    states = _group_state(rows, name_of, min_count=1)

    written = 0
    for state in states.values():
        code = state.get("symbol")
        if not code:
            continue
        axis.put(day, code, "plate_state", state, root=root)
        written += 1
    return {"day": day, "count": written,
            "steps": [StepResult("plate_state", f"{written} 个板块")]}


# ── 每日更新（一条命令跑完 `universe.PULL_ITEMS`）──────────────────────

def _report_failures(failures: list[dict]) -> None:
    """逐条上报失败（承 F4：失败必报，但个别失败不该中断整天）。"""
    if not failures:
        return
    print(f"⚠️ {len(failures)} 只失败（逐条上报，承 F4）：")
    for f in failures:
        print(f"   [{f['item']}] {f['symbol']}: {f['error']}")

def run_per_symbol(item: str, symbols: list[str], fn):
    """逐只跑，**隔离失败**（一只坏不拖垮全批），失败逐条上报（承 F4）。

    `fn(sym) -> int`（该只写入的记录数）。返回 `(ok, total_records, failures)`。

    为什么 bulk 路径要**隔离**、而不像 `run_day` 那样 fail-fast（承 L2）：
      K线 / 复权**逐只独立**（标的不共享状态），而网络抖动是常态 ——
      一只标的一次代理断连，不该让另外 300 只白跑。
      而"一天一批"的期权链是**原子**的（半批入库更糟），那条路径保持 fail-fast。
    """
    ok, total, failures = 0, 0, []
    for sym in symbols:
        try:
            total += fn(sym)
            ok += 1
        except Exception as e:
            failures.append({"item": item, "symbol": sym, "error": str(e)[:200]})
    return ok, total, failures


def run_kline_bulk(symbols: list[str], *, ktype: str, full_years: int,
                   force_full: bool = False, root=None):
    """批量 K线（逐只隔离失败）。返回 `(StepResult, failures)`。"""
    ok, total, failures = run_per_symbol(
        "kline", symbols,
        lambda s: run_kline([s], ktype=ktype, full_years=full_years,
                            force_full=force_full, root=root)["days"])
    return StepResult("kline", f"{ok}/{len(symbols)} 只，{total} 天"), failures


def run_adjust_factors_bulk(symbols: list[str], *, root=None):
    """批量复权因子（逐只隔离失败）。返回 `(StepResult, failures)`。"""
    ok, total, failures = run_per_symbol(
        "adjust_factor", symbols,
        lambda s: run_adjust_factors([s], root=root)["records"])
    return StepResult("adjust_factor", f"{ok}/{len(symbols)} 只，{total} 条"), failures


def run_daily(*, day: str | None = None, ktype: str | None = None,
              full_years: int | None = None, root=None) -> dict:
    """每日更新：按 `universe` 的设置，把配置的项各跑一遍（**一条命令**）。

    `ktype` / `full_years` 省略 → 用 `universe` 的默认；给了就覆盖（同 CLI）。
    """
    root = root if root is not None else DATA_DIR
    day = day or trading_day()
    symbols = universe.load_symbols()
    steps: list[StepResult] = []
    failures: list[dict] = []

    for item in universe.PULL_ITEMS:
        if item == "calendar":
            out = run_calendar(list(universe.CALENDAR_MARKETS), day, day, root=root)
            steps.append(StepResult("daily:calendar", f"{out['days']} 天"))
        elif item == "kline":
            step, fails = run_kline_bulk(
                symbols, ktype=ktype or universe.KLINE_KTYPE,
                full_years=(universe.KLINE_FULL_YEARS if full_years is None
                            else full_years),
                root=root)
            steps.append(step)
            failures += fails
        elif item == "adjust_factor":
            step, fails = run_adjust_factors_bulk(symbols, root=root)
            steps.append(step)
            failures += fails
        elif item == "plates":
            out = run_plates(universe.load_plate_codes(),
                             market=universe.PLATE_MARKET,
                             plate_types=universe.PLATE_TYPES, root=root)
            steps.append(out["steps"][-1])
            failures += out["failures"]
        elif item == "snapshot":
            steps.append(run_snapshot(market=universe.SNAPSHOT_MARKET,
                                      root=root)["steps"][0])
        elif item == "industry_state":
            steps.append(run_industry_state(day=day, root=root)["steps"][0])
        elif item == "plate_state":
            steps.append(run_plate_state(day=day, root=root)["steps"][0])
        elif item == "chain":
            out = run_day(day, list(universe.CHAIN_SYMBOLS), root=root)
            steps.append(StepResult("daily:chain", f"{out['records']} 条"))

    return {"day": day, "steps": steps, "failures": failures}


# ── CLI ──────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="trading-desk 数据管线")
    ap.add_argument("--day", default=None, help="交易日（美东，默认今天）")
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="标的；**省略 = 用 universe 清单**（唯一设置项）")
    ap.add_argument("--daily", action="store_true",
                    help="每日更新：按 universe 设置跑完所有项（一条命令）")
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
    ap.add_argument("--snapshot", action="store_true",
                    help="只跑全市场快照（行业 / RPS 的原料；**不吃历史额度**）")
    ap.add_argument("--states", action="store_true",
                    help="只跑行业 + 板块的集体行为/状态（需当天已有 snapshot）")
    ap.add_argument("--plates", action="store_true",
                    help="只跑板块名册 + 成分股（--plate-codes；省略 = universe 的板块清单）")
    ap.add_argument("--plate-codes", nargs="*", default=None,
                    help="要拉成分股的板块代码；省略 = universe 的板块清单")
    ap.add_argument("--kline", action="store_true",
                    help="只跑 K线（--symbols/--ktype；默认 REST，按 bar 的美东日入库）")
    ap.add_argument("--ktype", default=universe.KLINE_KTYPE,
                    help="K线周期；**默认取 universe 设置**")
    ap.add_argument("--full-years", type=int, default=universe.KLINE_FULL_YEARS,
                    help="K线**首次全量**的窗口（年）；**默认取 universe 设置**（唯一设置项）")
    ap.add_argument("--force-full", action="store_true",
                    help="忽略库里已有数据、重拉全窗口（用于**加长历史**）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    day = args.day or trading_day()

    def _symbols() -> list[str]:
        """标的：命令行给了就用；没给 → **用 universe 清单**（唯一设置项）。"""
        return args.symbols if args.symbols else universe.load_symbols()

    # 每日更新：按 universe 设置跑完所有项
    if args.daily:
        out = run_daily(day=day, ktype=args.ktype, full_years=args.full_years)
        print(f"✅ 每日更新 {out['day']}：{len(out['steps'])} 项")
        for s in out["steps"]:
            print(f"   [{s.step}] {s.detail}")
        if out["failures"]:
            print(f"⚠️ {len(out['failures'])} 只失败（逐条上报，承 F4）：")
            for f in out["failures"]:
                print(f"   [{f['item']}] {f['symbol']}: {f['error']}")
        return

    # 日历模式：只跑日历（全局项），不跑标的
    if args.calendar:
        end = args.end or day
        start = args.start or end
        out = run_calendar(args.markets, start, end)
        print(f"✅ 日历 {start}..{end}：{out['days']} 天入库（市场 {out['markets']}）")
        for s in out["steps"]:
            print(f"   [{s.step}] {s.detail}")
        return

    # 快照模式：全市场快照（行业 / RPS 的原料）
    if args.snapshot:
        out = run_snapshot(market=universe.SNAPSHOT_MARKET)
        print(f"✅ 快照 {out['day']}：{out['steps'][0].detail}")
        return

    # 状态模式：行业 + 板块的集体行为 / 状态机
    if args.states:
        a = run_industry_state(day=day)
        b = run_plate_state(day=day)
        print(f"✅ 状态 {day}：{a['steps'][0].detail}；{b['steps'][0].detail}")
        return

    # 板块模式：名册 + 成分股
    if args.plates:
        codes = (args.plate_codes if args.plate_codes
                 else universe.load_plate_codes())
        out = run_plates(codes, market=universe.PLATE_MARKET,
                         plate_types=universe.PLATE_TYPES)
        print(f"✅ 板块 {out['day']}：{len(out['steps'])} 步")
        for s in out["steps"]:
            print(f"   [{s.step}] {s.detail}")
        if out["skipped"]:
            print(f"⚠️ 名册里没有、已跳过 {len(out['skipped'])} 个：{out['skipped']}")
        _report_failures(out["failures"])
        return

    # 复权因子模式：只跑公司行动（逐只隔离失败）
    if args.adjust_factors:
        step, failures = run_adjust_factors_bulk(_symbols())
        print(f"✅ 复权因子：{step.detail}")
        _report_failures(failures)
        return

    # K线模式：按 bar 的美东日入库（缓存 + 增量；逐只隔离失败）
    if args.kline:
        step, failures = run_kline_bulk(_symbols(), ktype=args.ktype,
                                        full_years=args.full_years,
                                        force_full=args.force_full)
        print(f"✅ K线（{args.ktype}，首次 {args.full_years} 年）：{step.detail}")
        _report_failures(failures)
        return

    out = run_day(day, _symbols(), allow_rate_fallback=args.rate_fallback,
                  bucket=args.bucket)
    print(f"✅ {out['day']} 完成：r={out['r']:.4f}，{out['records']} 条入库")
    for s in out["steps"]:
        print(f"   [{s.step}] {s.detail}")


if __name__ == "__main__":
    main()
