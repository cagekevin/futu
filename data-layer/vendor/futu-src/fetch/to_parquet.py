"""REST JSON → futu_algo Parquet

**通道无关**：只读已经落盘的 JSON、写 Parquet，不连 OpenD、不碰 REST 接口。
所以它放在 `fetch/` 下，而不是 `fetch/opend/`（那是 OpenD 通道）。

输入：`data/kline/<period>/<SYMBOL>.json`   REST 通道拉下来的原始返回（19 个字段，冗余）
输出：`data/bars/<KTYPE>/<adjust>/<MARKET>/<code>.parquet`   6 列 + tz-aware 索引
      `data/bars/<KTYPE>/<adjust>/<MARKET>/<code>.json`      coverage 元数据

直接用 futu_algo 的 ParquetStore 写，格式天然一致。

用法：
    python3 fetch/to_parquet.py                     # 三个周期全转
    python3 fetch/to_parquet.py --period 30m        # 只转一个周期
    python3 fetch/to_parquet.py --only US.AAPL      # 只转指定标的

实现要点（都是踩过的坑，别改）：
  1. 时区必须用**命名时区**，不能用固定偏移 —— 美股的 time_zone 字段有 -5/-4 两种
     （夏令时切换），固定偏移会把一半的 bar 标错。
  2. **日线用 `date`，分钟线必须用 `time_key`** —— 分钟线同一天有多根，用 `date`
     建索引会把一天压成一根（索引重复）。`time_key` 是 **UTC 毫秒**，
     转成命名时区后正好落在交易所本地时间（美股开盘 = 09:30）。
  3. **volume 不要再除** —— REST 侧落盘时已按 volume_precision 还原过
     （CC.BTC 存的是 1497.26，原始值是 1497260）。
  4. futu_algo 的 MARKETS 只定义了 HK/US，自选清单里还有 SH/SZ/CC/FX/BD ——
     市场补丁在 `fetch/opend/futu_data.py`（导入即生效），读写本地缓存一律从那里导入 store 类。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根

import pandas as pd  # noqa: E402
from futu_algo.market.instrument import MARKETS, parse_symbol  # noqa: E402

from fetch.opend.futu_data import Coverage, ParquetStore, SeriesKey  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "data" / "kline"
DST_ROOT = ROOT / "data"

# 富途 REST 的 ktype / autype 整数 → futu_algo 的字符串
KTYPE = {
    1: "K_1M", 5: "K_3M", 6: "K_5M", 7: "K_15M", 8: "K_30M", 9: "K_60M",
    2: "K_DAY", 3: "K_WEEK", 4: "K_MON",
}
INTRADAY_KTYPES = {1, 5, 6, 7, 8, 9}
AUTYPE = {0: "none", 1: "qfq", 2: "hfq"}
BAR_COLUMNS = ("open", "high", "low", "close", "volume", "turnover")


def to_frame(doc: dict) -> pd.DataFrame | None:
    """JSON 的 bars 数组 → futu_algo 的规范 bar frame。

    只保留 6 列，丢掉 name/sc_name/tc_name/last_close/change_rate/settle_price/
    implied_volatility/open_interest/turnover_rate/pe_ratio 以及重复的时间表示。
    """
    bars = doc["bars"]
    if not bars:
        return None

    market = parse_symbol(doc["symbol"])[0]
    tz = MARKETS[market].tz

    if int(doc["ktype"]) in INTRADAY_KTYPES:
        # 分钟线：必须用 time_key（UTC 毫秒），否则同一天的多根会撞在一起
        index = pd.to_datetime([b["time_key"] for b in bars], unit="ms", utc=True)
        index = index.tz_convert(tz).rename("time")
    else:
        # 日线及以上：date 是 YYYYMMDD 的本地日期，直接本地化
        index = pd.to_datetime([str(b["date"]) for b in bars], format="%Y%m%d")
        index = index.tz_localize(tz).rename("time")

    # volume 已由 REST 侧还原，不能再除
    data = {col: [float(b[col]) for b in bars] for col in BAR_COLUMNS}
    return pd.DataFrame(data, index=index).loc[:, list(BAR_COLUMNS)]


def convert_period(period: str, store: ParquetStore, only: list[str] | None) -> None:
    src = SRC_ROOT / period
    if not src.is_dir():
        print(f"✗ 没有这个周期目录：{src}")
        return

    if only:
        paths = [src / f"{s.upper()}.json" for s in only]
    else:
        paths = [
            Path(p)
            for p in sorted(glob.glob(str(src / "*.json")))
            if not p.endswith("_manifest.json")
        ]

    ok = failed = 0
    total_bars = 0
    by_ktype: dict[str, int] = {}

    for path in paths:
        if not path.exists():
            print(f"  ✗ 找不到 {path.name}")
            failed += 1
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            symbol = doc["symbol"]
            ktype = KTYPE.get(int(doc["ktype"]))
            adjust = AUTYPE.get(int(doc["autype"]))
            if ktype is None or adjust is None:
                print(f"  ✗ {symbol}: 未知 ktype/autype {doc['ktype']}/{doc['autype']}")
                failed += 1
                continue

            frame = to_frame(doc)
            if frame is None:
                continue

            store.write(
                SeriesKey(symbol, ktype, adjust),
                frame,
                Coverage(frame.index[0].date(), frame.index[-1].date()),
                source="rest",
            )
            by_ktype[ktype] = by_ktype.get(ktype, 0) + 1
            total_bars += len(frame)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {path.name}: {type(exc).__name__}: {exc}")
            failed += 1

    detail = ", ".join(f"{k} {n}" for k, n in sorted(by_ktype.items()))
    print(f"[{period}] {ok} 只 / {total_bars:,} 根 bar / 失败 {failed}   （{detail}）")


def main() -> None:
    ap = argparse.ArgumentParser(description="REST JSON → futu_algo Parquet")
    ap.add_argument("--period", help="只转某个周期（daily / 30m / 60m）；不给就全转")
    ap.add_argument("--only", nargs="*", help="只处理指定标的，如 US.AAPL US.NVDA")
    args = ap.parse_args()

    periods = (
        [args.period]
        if args.period
        else sorted(p.name for p in SRC_ROOT.iterdir() if p.is_dir())
    )

    store = ParquetStore(DST_ROOT)
    for period in periods:
        convert_period(period, store, args.only)

    print(f"\n输出目录：{DST_ROOT / 'bars'}")


if __name__ == "__main__":
    main()
