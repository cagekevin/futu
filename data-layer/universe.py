"""唯一的数据拉取设置（SSOT）—— 「每天拉哪些标的 × 哪些数据项」只在这里定义一次。

## 为什么要有它（根因）

此前"拉什么"散在**三处**：
  1. `pipeline.py` 里**写死**的默认 5 个（`SPX NDX SPY QQQ IWM`）；
  2. 每次命令行**临时**传的 `--symbols`；
  3. 富途自选（358 个）—— 而 pipeline **根本不读它**。
⇒ 谁跑、哪天跑，集合都可能不同：**今天漏掉的标的数据陈旧而你不知道**，明天又多拉一批。
⇒ 收敛到**这一处**：日常跑不带参数就用它，结果**可复现**（清单变了，git 里看得见）。

## 真源与快照（2026-10-06 定：**以富途自选为准，覆盖**）

  - **富途自选 = 真源** —— 加/减标的请**在富途里改**；
  - `universe.txt` = 它的**快照**，由 `--sync` **覆盖式**重写；
  - ⇒ **别手改 `universe.txt`**：下次 `--sync` 会把它冲掉。
    （这是刻意的：两处都能改 = 两份真相 = 迟早对不上。）

## 用法

    python universe.py --show      # 看当前清单（标的数 + 拉哪些项）
    python universe.py --sync      # 从富途自选重拉 → 覆盖 universe.txt（打印增/删）
    python universe.py --sync --dry-run   # 只看差异，不写文件
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UNIVERSE_PATH = ROOT / "universe.txt"

# ── 设置项 ①：每天拉哪些项（顺序 = 执行顺序）─────────────────────────────
# ⚠️ 顺序 = 执行顺序：`snapshot` 必须在两个 `*_state` 之前；`plates` 必须在 `plate_state` 之前。
PULL_ITEMS = ("calendar", "plates", "snapshot", "kline", "adjust_factor", "chain",
              "industry_state", "plate_state")

# ── 设置项 ②：各数据项的参数（这里是**默认值**；命令行可逐次覆盖）─────────
# 标的清单：K线 / 复权 用 `universe.txt`（真源 = 富途自选）；这里只声明"从哪读"。
KLINE_KTYPE = "K_DAY"         # 默认周期；命令行 `--ktype` 可覆盖
KLINE_FULL_YEARS = 3          # **首次全量**默认窗口（年）；命令行 `--full-years` 可覆盖
# ⚠️ 只是"**首次全量**"的窗口：库里已有数据后，增量只会**往前补**，不会回溯加长历史。
#    要加长历史：`--full-years 5 --force-full`（强制重拉全窗口）。

CALENDAR_MARKETS = ("US",)

# ⚠️ 期权链只对**有期权**的标的成立 —— 对 328 只全拉会全崩。
# 所以链的标的**单独写死**（指数 / 大盘 ETF）。
CHAIN_SYMBOLS = ("SPX", "NDX", "SPY", "QQQ", "IWM")

# ── 设置项 ③：板块（概念 / 行业）─────────────────────────────────────────
# 板块 = **一揽子股票**（概念与行业走同一条通路；吸收自参照项目 `plates.py`）。
# 要拉成分股的板块代码在 `universe_plates.txt`（同由 `--sync` 从自选生成）。
PLATE_MARKET = "US"
PLATE_TYPES = ("CONCEPT", "INDUSTRY")     # 概念 + 行业
PLATES_PATH = ROOT / "universe_plates.txt"

# ── 设置项 ④：全市场快照（行业 / RPS 的原料）─────────────────────────────
SNAPSHOT_MARKET = "US"


def load_symbols() -> list[str]:
    """读清单快照（`universe.txt`）。缺 / 空 → **报错**（承 P1：不静默给空）。"""
    if not UNIVERSE_PATH.exists():
        raise FileNotFoundError(
            f"清单不存在：{UNIVERSE_PATH} —— 先跑 `python universe.py --sync`"
        )
    symbols = [
        line.strip()
        for line in UNIVERSE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not symbols:
        raise ValueError(f"清单为空：{UNIVERSE_PATH}（承 P1：不静默给空）")
    return symbols


def load_plate_codes() -> list[str]:
    """读**板块清单**（`universe_plates.txt`）—— 要拉成分股的板块代码。

    与标的清单同源（都来自富途自选，`--sync` 生成）。
    **没有该文件 = 没配板块** → 返回空（合法状态，不算错）；
    调用方会报"0 个板块"（**显形**，不静默，承 P1）。
    """
    if not PLATES_PATH.exists():
        return []
    return [
        line.strip()
        for line in PLATES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


# ── 同步（覆盖式）────────────────────────────────────────────────────────

# 自选里混着"自选列表"代码（`LIST####`）—— 它们是**列表**，不是标的。
_LIST_CODE = re.compile(r"LIST\d+")
# 可拉的标的形态 = 纯美股代码。
# REST **不支持**期货连续（`RTYmain`）与 A 股指数（`399006`）——
# 它们会被**显式排除并在文件头写明**（承 P1：排除必须显形，不静默丢）。
_PLAIN_TICKER = re.compile(r"[A-Z]{1,6}")


def sync(*, dry_run: bool = False) -> dict:
    """从富途自选重拉 → **覆盖式**重写 `universe.txt`；打印增/删（承 P1：变动显形）。"""
    from fetch import fetch_api            # 延迟 import：日常跑不必拉起取数栈

    result = fetch_api.watchlist()
    seen: list[str] = []
    for row in result.rows:
        symbol = row["symbol"]
        if symbol not in seen:
            seen.append(symbol)

    list_codes = [s for s in seen if _LIST_CODE.fullmatch(s)]
    tickers = sorted(s for s in seen if _PLAIN_TICKER.fullmatch(s))
    unsupported = [s for s in seen
                   if not _LIST_CODE.fullmatch(s) and not _PLAIN_TICKER.fullmatch(s)]

    old = load_symbols() if UNIVERSE_PATH.exists() else []
    old_set, new_set = set(old), set(tickers)
    added = [s for s in tickers if s not in old_set]
    removed = [s for s in old if s not in new_set]

    if not dry_run:
        header = [
            "# 由 `python universe.py --sync` 生成 —— **勿手改**（下次同步会覆盖）。",
            f"# 真源：富途自选（{result.extra.get('groups')} 个分组，{len(seen)} 个代码）",
            f"# 本次：可拉 {len(tickers)} 只；"
            f"排除 自选列表(LIST####)×{len(list_codes)}、非美股代码×{len(unsupported)}",
        ]
        if unsupported:
            header.append(
                f"# 非美股代码（REST 不支持，已排除）：{' '.join(sorted(unsupported))}"
            )
        UNIVERSE_PATH.write_text(
            "\n".join(header + tickers) + "\n", encoding="utf-8"
        )
        # 板块代码（`LIST####`）**不是标的、不进 universe.txt**，
        # 但它们是你自选里的**板块** —— 单独落 `universe_plates.txt`（要拉成分股的就是这些）。
        plate_header = [
            "# 由 `python universe.py --sync` 生成 —— **勿手改**（下次同步会覆盖）。",
            f"# 自选里的板块代码（LIST####）×{len(list_codes)}",
        ]
        PLATES_PATH.write_text(
            "\n".join(plate_header + sorted(list_codes)) + "\n", encoding="utf-8"
        )

    return {"total_codes": len(seen), "tickers": tickers,
            "list_codes": list_codes, "unsupported": unsupported,
            "added": added, "removed": removed, "written": not dry_run}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="数据拉取清单（唯一设置项）")
    ap.add_argument("--show", action="store_true", help="看当前清单与拉取项")
    ap.add_argument("--sync", action="store_true", help="从富途自选重拉并**覆盖**清单")
    ap.add_argument("--dry-run", action="store_true", help="只报告差异，不写文件")
    args = ap.parse_args(argv)

    if args.sync:
        out = sync(dry_run=args.dry_run)
        print(f"自选 {out['total_codes']} 个代码 → 可拉 {len(out['tickers'])} 只"
              f"（排除 LIST####×{len(out['list_codes'])}、"
              f"非美股×{len(out['unsupported'])}）")
        if out["unsupported"]:
            print(f"  非美股（REST 不支持）：{out['unsupported']}")
        print(f"  + 新增 {len(out['added'])}: {out['added']}")
        print(f"  - 删除 {len(out['removed'])}: {out['removed']}")
        print("  " + ("（dry-run，未写文件）"
                      if args.dry_run
                      else f"已覆盖 {UNIVERSE_PATH.name} + {PLATES_PATH.name}"))
        return 0

    symbols = load_symbols()
    plates = load_plate_codes()
    print(f"{UNIVERSE_PATH.name}：{len(symbols)} 只")
    print(f"  前 10: {symbols[:10]}")
    print(f"  后 10: {symbols[-10:]}")
    print(f"{PLATES_PATH.name}：{len(plates)} 个板块"
          + (f"（前 5: {plates[:5]}）" if plates else "（没配 —— 跑 `--sync` 生成）"))
    print(f"  拉取项: {PULL_ITEMS}")
    print(f"  K线: {KLINE_KTYPE} / 首次 {KLINE_FULL_YEARS} 年"
          f"；链: {CHAIN_SYMBOLS}；日历: {CALENDAR_MARKETS}"
          f"；板块: {PLATE_MARKET}/{PLATE_TYPES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
