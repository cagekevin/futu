"""每日「期权结构地图」—— 中文单文件 HTML 报告。

为什么是「每天一份」而不是「一张图」
------------------------------------
一张图的价值在于**看**，日频报告的价值在于**攒**。

    「Gamma Flip 移了没」「墙还在不在原地」「结构有没有迁移」
    —— 这些问题只能靠时间序列回答，而时间序列 = 每天一份。

所以这个模块是**幂等**的：

  * 随时跑都能出（数据来自 `data/snapshots/` 和 `data/history/metrics.parquet`）
  * **断一天不会烂** —— 对比项直接从 `history/metrics.parquet` 里按日期找，
    不依赖「昨天有没有生成过 HTML」

落盘三处
--------
    reports/gex/<日期>.html    给人看的（单文件，离线可开）
    reports/gex/<日期>.json    给机器看的（结构化，供回测/复盘）
    reports/gex/latest.html    最新一份的快捷入口

数据来源（全部是**已在本地**或**公开免账号**的）
------------------------------------------------
    期权链      data/snapshots/<SYM>/<日期>/<时间>.parquet   ← scheduler 每天在落
    历史指标    data/history/metrics.parquet                ← scheduler 每次 pull 追加一行
    指数日线    CBOE 官方收盘价 CSV（gex/backfill.SPOT_SOURCES）
    无风险利率  gex/rates（SOFR）

⚠️ 只有**收盘价**，所以画**折线**而不是蜡烛 ——
   画蜡烛等于假装我们有盘中 OHLC，那是假的。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import metrics
from .config import DATA_DIR, SETTINGS
from .ingest import ChainSnapshot

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
REPORT_DIR = Path(SETTINGS.data_dir).parent / "reports" / "gex"

# 参与「跨标的结构」的标的（按这个顺序显示）。只取 targets，不含 constituents。
CROSS_ASSETS = ("SPX", "NDX", "SPY", "QQQ", "IWM")

# 关键位所属的到期日范围。参考图用的是「DTE 0-45 天」，
# 这里用 metrics 自己的 "Mois"（≤35 天）—— 口径与 dashboard 完全一致，
# 不自己发明一套筛选。
BUCKET = "Mois"

# 价格图回看的交易日数（参考图是 45 日）。
LOOKBACK_DAYS = 45

# 结构剖面画哪个标的。**默认 SPY 而不是 SPX** —— 原因是数据不是偏好：
#
#   有 OHLC（能画蜡烛）   ETF：SPY / QQQ / IWM  ← futu OpenD
#   只有收盘价（只能折线） 指数：SPX / NDX       ← CBOE CSV / FRED
#
# 蜡烛比折线多一层「当日振幅」的信息，而且价格带与 GEX 条对齐后更像参考图。
# SPX 的指数行情权限在 futu 侧没开（实测「暂不支持美股指数」），
# 想换回 SPX 就设 GEX_PRIMARY=SPX —— 会自动退回折线，不会画假蜡烛。
PRIMARY_CHART = os.getenv("GEX_PRIMARY", "SPY")


# ══════════════════════════════════════════════════════════════════════════
#  数据装载
# ══════════════════════════════════════════════════════════════════════════
def _snapshot_files(symbol: str) -> list[Path]:
    """某标的的全部快照文件，按时间升序。"""
    d = Path(SETTINGS.data_dir) / "snapshots" / symbol
    if not d.exists():
        return []
    return sorted(d.glob("*/*.parquet"))


def load_chain_snapshot(symbol: str) -> ChainSnapshot | None:
    """从最近的快照 parquet 重建 `ChainSnapshot`。

    重建而不是直接算，是为了**走和 dashboard 完全相同的管线**
    （`metrics.enrich` → `compute_levels`）。自己另写一套算法，
    两边的数字迟早会分叉。
    """
    files = _snapshot_files(symbol)
    if not files:
        return None
    path = files[-1]
    df = pd.read_parquet(path)
    if df.empty:
        return None

    keep = ["contract", "expiry", "type", "strike", "bid", "ask", "iv",
            "open_interest", "volume", "delta_cboe", "gamma_cboe", "last_trade_price"]
    missing = [c for c in keep if c not in df.columns]
    if missing:
        log.warning("report: %s 快照缺列 %s", symbol, missing)
        return None

    # 快照目录名 = 交易日；文件名 = HHMMSS
    day = path.parent.name
    hms = path.stem
    try:
        ts = datetime.strptime(f"{day} {hms}", "%Y-%m-%d %H%M%S")
    except ValueError:
        ts = datetime.now(ET).replace(tzinfo=None)

    return ChainSnapshot(
        symbol=symbol,
        spot=float(df["spot"].iloc[0]),
        feed_timestamp=ts,
        fetched_at=ts,
        options=df[keep].copy(),
    )


def _ohlc_from_cboe(symbol: str) -> pd.DataFrame:
    """指数日线 —— **只有收盘价**（CBOE 官方 CSV / FRED 都只给 close）。

    复用 `backfill.SPOT_SOURCES` —— 和仓库的 backfill 用**同一个源、同一套解析**。
    返回的 DataFrame 里 `open/high/low` 全部等于 `close`，用 `has_ohlc()` 能分辨。
    """
    from .backfill import SPOT_SOURCES
    import io
    import requests

    if symbol not in SPOT_SOURCES:
        return pd.DataFrame()
    url, fmt, dcol, vcol = SPOT_SOURCES[symbol]
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        days = pd.to_datetime(df[dcol], format=fmt).dt.date
        vals = pd.to_numeric(df[vcol], errors="coerce")
        s = pd.Series(vals.to_numpy(), index=days)
        s = s[s.notna()].sort_index()
        out = pd.DataFrame({"close": s})
        for c in ("open", "high", "low"):
            out[c] = out["close"]          # 指数没有 OHLC，用 close 顶替
        out.index.name = "date"
        log.info("report: %s 日线 %d 天（CBOE/FRED 收盘价，无 OHLC），至 %s",
                 symbol, len(out), out.index[-1])
        return out
    except Exception as e:  # noqa: BLE001 — 拿不到就不画价格，不阻断报告
        log.warning("report: %s CBOE 日线失败 — %s", symbol, e)
        return pd.DataFrame()


def _ohlc_from_futu(symbol: str, days: int = 90) -> pd.DataFrame:
    """ETF 日线 **OHLC**，走 Futu OpenD。

    为什么 ETF 用 futu 而指数不用：CBOE 的 `daily_prices/*_History.csv` 只覆盖
    **指数**，ETF 请求返回 403；而 futu 的指数行情权限**没开**
    （`US..SPX` → 「暂不支持美股指数」），ETF 却完全正常。两源各覆盖对方
    拿不到的那一半。

    ★ `autype=None` —— **不复权**，这不是可选项：

        期权行权价是**绝对价**（Call Wall = 780 就是 780）。futu 默认
        `autype='qfq'`（前复权）会把历史价格按今天口径整体平移 ——
        实测 SPY：33/45 天有差异，最大 1.93 点。把复权价和行权价画在
        同一根 y 轴上，**历史蜡烛相对「墙」的位置就是错的**。
    """
    try:
        from futu import OpenQuoteContext, RET_OK
    except ImportError:
        log.info("report: 没装 futu-api，%s 跳过价格", symbol)
        return pd.DataFrame()

    import os

    end = datetime.now(ET).date()
    start = end - pd.Timedelta(days=days)
    ctx = None
    try:
        ctx = OpenQuoteContext(
            host=os.getenv("FUTU_OPEND_HOST", "127.0.0.1"),
            port=int(os.getenv("FUTU_OPEND_PORT", "11111")))
        r = ctx.request_history_kline(
            f"US.{symbol}", start=str(start), end=str(end), ktype="K_DAY",
            autype=None, max_count=days)          # ★ 不复权
        if r[0] != RET_OK:
            log.warning("report: %s futu 日线失败 — %s", symbol, r[1])
            return pd.DataFrame()
        df = r[1].copy()
        df.index = pd.to_datetime(df["time_key"]).dt.date
        out = df[["open", "high", "low", "close"]].apply(
            pd.to_numeric, errors="coerce").dropna(subset=["close"]).sort_index()
        out.index.name = "date"
        log.info("report: %s 日线 %d 天（futu OpenD，不复权，含 OHLC），至 %s",
                 symbol, len(out), out.index[-1])
        return out
    except Exception as e:  # noqa: BLE001 — OpenD 没启动是常态，不该阻断报告
        log.info("report: %s futu 日线不可用（%s），跳过价格", symbol, e)
        return pd.DataFrame()
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass


def has_ohlc(df: pd.DataFrame) -> bool:
    """这份价格数据能不能画蜡烛？

    `open/high/low` 全等于 `close` ⇒ 是「只有收盘价」的源（指数），
    画蜡烛等于**假装有盘中信息** —— 那时退回折线。
    """
    if df is None or df.empty or "high" not in df:
        return False
    return bool((df["high"] - df["low"]).abs().max() > 0)


def load_price_history(symbol: str) -> pd.DataFrame:
    """日线价格。列 `open/high/low/close`，索引 = date。

    **指数走 CBOE/FRED（只有 close），ETF 走 futu（完整 OHLC）** —— 两个源互补。
    调用方用 `has_ohlc()` 决定画蜡烛还是折线。
    """
    from .backfill import SPOT_SOURCES

    if symbol in SPOT_SOURCES:
        return _ohlc_from_cboe(symbol)
    return _ohlc_from_futu(symbol)


def load_history() -> pd.DataFrame:
    """`history/metrics.parquet` —— 每行一次 pull 的汇总指标。"""
    p = Path(SETTINGS.data_dir) / "history" / "metrics.parquet"
    if not p.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(p)
        df["ts"] = pd.to_datetime(df["timestamp"])
        return df
    except Exception as e:  # noqa: BLE001
        log.warning("report: history 读取失败 — %s", e)
        return pd.DataFrame()


def previous_summary(hist: pd.DataFrame, symbol: str,
                     not_after: date | None = None) -> pd.Series | None:
    """找**上一交易日**的汇总行（幂等：直接从 history 里按日期找，不依赖昨天生成过报告）。"""
    if hist.empty:
        return None
    d = hist[hist["symbol"] == symbol].copy()
    if d.empty:
        return None
    d["day"] = d["ts"].dt.date
    if not_after is not None:
        d = d[d["day"] < not_after]
    if d.empty:
        return None
    # 每天取最后一笔
    last = d.sort_values("ts").groupby("day").tail(1)
    return last.iloc[-1] if len(last) else None


# ══════════════════════════════════════════════════════════════════════════
#  组装
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#  「机械含义」查表 —— 报告里**唯一允许出现文字**的机器部分
# ══════════════════════════════════════════════════════════════════════════
# 为什么是查表而不是作文：
#
#   2026-10-06 那个反号 bug（SPX 被写成「Flip 下方；负 gamma」）就住在
#   上一版的 `reading()` 里 —— 而它旁边**每一个机器算出来的数字都是对的**。
#   自由散文是 AI 最容易出错的地方，而且错了没人能验证。
#
# 所以规则收紧成三条：
#
#   1. 机器块里的文字**只能**来自下面这两张表，或直接来自数字；
#   2. 表里只写 GEX 的**定义**（「正 gamma ⇒ 做市商对冲与行情反向」），
#      **不写对行情的判断**（「突破需要成交量配合」）；
#   3. 出现「可能 / 需要 / 否则 / 容易 / 应该 / 才能」这类**预测性词**的句子，
#      一律不许进机器块 —— 那些是推测，机器给不出，见 tests/test_report_text.py。
#
# ⚠️ **假设要随文字一起出现，不能只写在报告末尾的口径里。**
# 「正/负 gamma 意味着什么」背后是「call 正 / put 负」的做市商视角假设 ——
# 那是**模型代理**，不是实测持仓。固定表每输出一次，这个假设就被当事实
# 陈述一次，所以它必须挂在这一行上，让每次出现都自带边界。
DEALER_ASSUMPTION = "前提：call 正 / put 负 的做市商代理假设，非实测持仓"

# 状态 → 含义。键是 `below_flip`（是否在 Flip 下方）。
MECHANICS: dict[bool, str] = {
    True: f"负 gamma 区：做市商对冲方向与行情同向 → 波动被放大（{DEALER_ASSUMPTION}）",
    False: f"正 gamma 区：做市商对冲方向与行情反向 → 波动被收敛（{DEALER_ASSUMPTION}）",
}

# 状态 → 含义。键是 `wall_label()` 的返回值。
WALL_MECHANICS: dict[str, str] = {
    "贴近 Call Wall": "现价距上方正 gamma 最密处 ≤0.35%",
    "贴近 Put Wall": "现价距下方负 gamma 最密处 ≤0.35%",
    "两墙之间": "现价落在 Put Wall 与 Call Wall 之间",
    "单侧有墙": "只有一侧存在可识别的 gamma 墙",
    "无墙": "两侧都没有可识别的 gamma 墙",
    "结构不明确": "没有可用的 Gamma Flip",
}

# `wall_label()` **只能**返回这几个值。测试会检查 WALL_MECHANICS 覆盖了全部 ——
# 加一个新标签却忘了配含义，测试就红（而不是在报告里悄悄留空）。
WALL_LABELS = ("贴近 Call Wall", "贴近 Put Wall", "两墙之间", "单侧有墙",
               "无墙", "结构不明确")

# 预测性词 —— 机器块里出现任何一个都算违规（由 tests/test_report_text.py 强制）。
SPECULATIVE_WORDS = ("可能", "需要", "否则", "容易", "应该", "才能", "或许",
                     "大概", "预计", "有望", "风险在于")

# ── 可结算的阈值：**必须写死，不许留解释空间** ──────────────────────
# 「触发后 3 日波动 > 触发前 3 日」——**> 多少算大？** 1.01 倍算吗？
# 不写阈值，结算时就有解释空间，而解释空间就是自欺的入口。
VOL_RATIO = 1.2            # 触发后波动 ≥ 触发前的 1.2 倍，记为命中
VOL_WINDOW = 3             # 观察窗口（交易日）

# 墙的稳健度：`#1 与 #2 候选` 的 GEX 相对差。
# 差越小 ⇒ 墙越不唯一 ⇒ 两次取数之间跳变是**必然**，不是数据误差。
WALL_GAP_UNSTABLE = 0.10   # 差 < 10% → 墙不唯一
WALL_GAP_WEAK = 0.25       # 差 < 25% → 墙偏弱


def absolute_walls(chain: pd.DataFrame, ref_spot: float) -> dict[str, float | None]:
    """**绝对墙** —— 不按现价切，只按 GEX 的符号取极值。

    定义（**与 `metrics.key_levels` 的关键区别**）：

        Call Wall (绝对) = 正 GEX 最大的行权价
        Put Wall  (绝对) = 负 GEX 最负的行权价

    为什么必须和相对墙分开
    ----------------------
    `metrics.key_levels` 的定义是「现价**上方**正 gamma 最密处 / 现价**下方**
    负 gamma 最密处」—— 它含**现价**这一项，所以墙 = f(OI, 现价)。
    实测（2026-10-06，同一份 SPX 快照，只改传入的 spot）：

        传入 spot   7,500 → Call Wall 7,800
        传入 spot   7,850 → Call Wall 7,850      ← 墙跟着现价跑
        传入 spot   8,000 → Call Wall 8,000

    这带来两个后果，都是定义层的，不是实现 bug：

    **后果 A — 结构迁移会把「价格移动」误报成「持仓变化」**
        现价涨 1% ⇒ 切分点上移 ⇒ 某些行权价换了侧 ⇒ argmax 换人。
        报告把它记成「结构迁移」，读的人以为持仓变了。
        **实际什么都没变，只是尺子挪了。**

    **后果 B — 「收盘站上 Call Wall」在定义上自我取消**
        实测：现价 7,773.95 时 Call Wall = 7,800；
        假设收盘站上它（现价 7,801）⇒ Call Wall 变成 7,850。
        **用一个会因事件发生而消失的量，去定义那个事件。**

    ⇒ 所以：**跨日对比（结构迁移）和结算（观察清单）只能用绝对墙。**
       相对墙可以展示（描述「当前位置」），但**不能进台账**。
    """
    out: dict[str, float | None] = {"call_wall": None, "put_wall": None}
    if chain is None or chain.empty:
        return out
    try:
        # `gex_at_spot` 把希腊值重算到参考价（前收），让墙不随实时价抖动 ——
        # 但**不按现价切分**，这是与 key_levels 唯一的、也是最关键的差别。
        agg = metrics.gex_at_spot(chain, ref_spot) if ref_spot else \
            chain.groupby("strike")["gex"].sum()
        up = agg[agg > 0]
        dn = agg[agg < 0]
        if len(up):
            out["call_wall"] = float(up.idxmax())
        if len(dn):
            out["put_wall"] = float(dn.idxmin())
    except Exception as e:  # noqa: BLE001
        log.warning("report: 绝对墙计算失败 — %s", e)
    return out


@dataclass
class AssetView:
    """一个标的在某个时点的结构快照 —— 报告的全部数字都在这里。"""
    symbol: str
    spot: float
    net_gex: float
    net_gex_0dte: float
    net_dex: float
    zero_gamma: float | None          # Gamma Flip
    call_wall: float | None
    put_wall: float | None
    d1_min: float | None
    d1_max: float | None
    pc_oi: float
    pc_volume: float
    asof: str
    dte_lo: int | None = None
    dte_hi: int | None = None
    # ★ 墙的**候选**（strike, gex），按 |GEX| 降序。
    #
    # 为什么必须有这个：墙位在两次取数之间会跳（实测 SPX 同一天两版
    # 7,650↔7,700 / 7,850↔7,800）。**冻结解决了「对账」，没解决「稳健性」。**
    # 而跳变的根源当场可见 —— SPY 的 787 与 785 的 |GEX| 只差 1.9%：
    # 两个候选几乎打平，谁排第一取决于取数那一刻的毫厘之差。
    # 输出候选，是让使用者知道「这道墙不唯一」—— 比提高取数精度有效得多。
    call_candidates: list[tuple[float, float]] = field(default_factory=list)
    put_candidates: list[tuple[float, float]] = field(default_factory=list)
    # ★ 绝对墙（不按现价切）—— **结构迁移与观察清单只用这个**
    # `call_wall` / `put_wall` 是相对墙，会随现价移动，只用于「当前位置描述」。
    abs_call_wall: float | None = None
    abs_put_wall: float | None = None

    def wall_gap(self, side: str) -> float | None:
        """`#1 与 #2 候选` 的 |GEX| 相对差。`None` = 只有一个候选。

        越小 ⇒ 墙越不唯一。`< WALL_GAP_UNSTABLE` 时，两次取数给出不同的墙
        是**必然**，不是数据误差 —— 观察清单里那条就会跟着不稳。
        """
        cands = self.call_candidates if side == "call" else self.put_candidates
        if len(cands) < 2:
            return None
        g1, g2 = abs(cands[0][1]), abs(cands[1][1])
        return None if g1 == 0 else abs(g1 - g2) / g1

    def wall_stability(self, side: str) -> str:
        """墙稳健度 —— 机器判定，分档阈值写死在模块常量里。"""
        gap = self.wall_gap(side)
        if gap is None:
            return "唯一候选"
        if gap < WALL_GAP_UNSTABLE:
            return "墙不唯一"
        if gap < WALL_GAP_WEAK:
            return "墙偏弱"
        return "墙明确"

    # ---- 派生量：这是这份报告最想突出的东西 ----
    def dist(self, level: float | None) -> float | None:
        """关键位相对现价的百分比距离。**这就是「还差多少」**。"""
        if level is None or not self.spot:
            return None
        return (level - self.spot) / self.spot * 100.0

    @property
    def flip_dist(self) -> float | None:
        return self.dist(self.zero_gamma)

    @property
    def wall_span(self) -> tuple[float, float] | None:
        """[Put Wall, Call Wall] 围成的区间 —— 结构上的「房间」。"""
        if self.put_wall is None or self.call_wall is None:
            return None
        return (self.put_wall, self.call_wall)

    # ── ★ 位置判据：**全类唯一的一个** ────────────────────────────────
    @property
    def below_flip(self) -> bool | None:
        """现价是否在 Gamma Flip **下方**。`None` = 没有 Flip，判不了。

        ⚠️ **不要用 `flip_dist` 的符号去推这件事。**
        `dist()` 的口径是 `(关键位 − 现价) / 现价`，所以

            flip_dist > 0  意思是「墙在现价**上方**」 → 现价在墙**下方**
            flip_dist < 0  意思是「墙在现价**下方**」 → 现价在墙**上方**

        —— 符号的含义和「价格在哪一侧」**正好相反**。

        这个 bug 真实发生过（2026-10-06 用户发现）：`reading()` 按 `fd >= 0`
        判成「现价在 Flip 上方」，于是 SPX（fd = −1.31%，实为**上方**）被写成
        「下方」，IWM（fd = +3.69%，实为**下方**）被写成「上方」——
        **两条都错，方向相反**。而且它印在报告里、会被抄进台账。

        根因不是写错一个符号，是**用「派生量的符号」替代了「两个价格直接比较」**。
        所以判据只留这一个，`position_label` / `regime` / `reading` 全部走它。
        """
        if self.zero_gamma is None:
            return None
        return self.spot < self.zero_gamma

    @property
    def sign_conflict(self) -> bool:
        """`net_gex` 的符号与「在 Flip 哪一侧」是否**自相矛盾**。

        理论上两者必须同号（Gamma Flip 就是净 GEX 归零的位置）。
        不同号说明快照本身有问题（取数时刻、OI 结算批次、希腊值退化……），
        这时**任何一句叙述都不可信** —— 必须在报告里显形，不能默默输出。
        """
        b = self.below_flip
        if b is None or not self.net_gex:
            return False
        return b != (self.net_gex < 0)

    def position_label(self) -> str:
        """现价相对 Gamma Flip 的位置 —— 一句话的第一半。"""
        b = self.below_flip
        if b is None:
            return "结构不明确"
        return "Flip 下方" if b else "Flip 上方"

    def wall_label(self) -> str:
        """现价相对两道墙的位置 —— 第二半。"""
        if self.call_wall is None and self.put_wall is None:
            return "无墙"
        d_c = self.dist(self.call_wall)
        d_p = self.dist(self.put_wall)
        if d_c is not None and abs(d_c) <= 0.35:
            return "贴近 Call Wall"
        if d_p is not None and abs(d_p) <= 0.35:
            return "贴近 Put Wall"
        if d_c is not None and d_p is not None:
            return "两墙之间"
        return "单侧有墙"

    def regime(self) -> str:
        """正/负 gamma 决定的是「追涨杀跌」还是「高抛低吸」。

        **走 `below_flip` 而不是 `net_gex`** —— 位置的定义是「在 Flip 哪一侧」，
        两者同号是常态（见 `sign_conflict`），不同号时以位置为准并报警。
        """
        b = self.below_flip
        if b is None:
            return "结构不明确"
        return "负 gamma · 不稳定" if b else "正 gamma · 稳定"

    def facts(self) -> list[tuple[str, str]]:
        """结构事实 —— **(标签, 内容) 的列表，全部机器生成**。

        每一行都能追到「一个数字」或「查一张表」，没有一处是写出来的：

            状态       ← below_flip + wall_label（都是比较）
            依据       ← 现场拼的实际数字
            gamma 性质 ← 因标的而异的短标签（完整解释在 glossary.html）

        **旧版这里叫 `reading()`，是自由散文 —— 反号 bug 就住在里面。**
        散文的问题不是写得不好，是**错了没人能验证**：那个「下方」看起来
        和「−1.31%」一样自然。
        """
        out: list[tuple[str, str]] = []
        b = self.below_flip
        w = self.wall_label()
        if b is None:
            # ⚠️ 必须**明说判不出来**。旧版这里只回一个「两墙之间」，
            # 读起来像是有结论，实际是「Flip 缺失，位置根本没判」——
            # 把「缺数据」包装成「有结论」正是散文最容易犯的错。
            out.append(("状态", f"{w} · 无 Gamma Flip，判不出在 Flip 哪一侧"))
            out.append(("依据", f"现价 {self.spot:,.2f} · Gamma Flip 缺失"))
            return out

        out.append(("状态", f"{'Flip 下方' if b else 'Flip 上方'} · {w}"))

        # 依据：把判定用到的数字原样摊开，谁都能复核
        ev = [f"现价 {self.spot:,.2f}",
              f"{'<' if b else '≥'} Flip {self.zero_gamma:,.2f}"]
        for lvl, nm in ((self.flip_dist, "距 Flip"),
                        (self.dist(self.call_wall), "距 Call Wall"),
                        (self.dist(self.put_wall), "距 Put Wall")):
            if lvl is not None:
                ev.append(f"{nm} {lvl:+.2f}%")
        out.append(("依据", " · ".join(ev)))

        # ⚠️ 这里**不放** `MECHANICS[b]` 那句完整解释了 —— 它是**固定字符串**，
        #    在 5 个标的卡片里各印一遍 = 同一句话印 5 遍（信息量 0）。
        #    完整含义在 `glossary.html`，日报里只在「结构要点」出现一次。
        #    这里只留**因标的而异**的那部分。
        out.append(("gamma 性质",
                    "正 gamma（对冲收敛波动）" if not b else "负 gamma（对冲放大波动）"))

        # ── 墙稳健度：只在**不稳**时才占一行 ─────────────────────────
        # 「墙明确」是常态，不值得占版面；而「墙不唯一」必须让人看见 ——
        # 否则使用者会把一个随时会跳的价位当成硬位。
        for side, nm in (("call", "Call Wall"), ("put", "Put Wall")):
            st = self.wall_stability(side)
            if st in ("墙不唯一", "墙偏弱"):
                gap = self.wall_gap(side)
                cands = (self.call_candidates if side == "call"
                         else self.put_candidates)
                alt = " / ".join(f"{c[0]:,.0f}" for c in cands[:3])
                out.append(("墙稳健度",
                            f"{nm} {st} —— 候选 {alt}，"
                            f"#1 与 #2 的 |GEX| 只差 {gap * 100:.1f}%"
                            if gap is not None else f"{nm} {st}"))
        return out

    def move_ref(self) -> float | None:
        """隐含的 1 日波动幅度（straddle ATM）——「距离」的天然参照物。"""
        if self.d1_min is None or self.d1_max is None:
            return None
        return (self.d1_max - self.d1_min) / 2.0


def build_view(symbol: str, hist: pd.DataFrame,
               snap: ChainSnapshot | None = None,
               ref_spot: float | None = None) -> AssetView | None:
    """算一个标的的全部结构指标。走 dashboard 同一套 `metrics` 管线。

    `ref_spot` = **前收**（结构冻结用的参考价）。绝对墙与关键位的**大小**
    都以它为准，只有「在哪一侧」才用现价 —— 这是 `metrics.compute_levels`
    的原意，但绝对墙连「哪一侧」都不用。
    """
    snap = snap or load_chain_snapshot(symbol)
    if snap is None:
        return None
    enriched = metrics.enrich(snap)
    if enriched.empty:
        return None

    # ★ DTE 窗口的基准日 = **快照的数据日**，不是「今天」。
    #
    # 旧版用 `datetime.now(ET).date()` —— 同一份快照，换一天生成就换一个窗口：
    #
    #     快照日（正确）   DTE  1–35   合约 11148
    #     今天 10-06      DTE  0–35   合约 11294   ← DTE 变 0，已过期的合约被算进 GEX
    #     明天 10-07      DTE -1–35   合约 11442   ← 负 DTE
    #
    # 而结构迁移是**跨日对比**的 —— 口径一变，对比就无效。
    # 这也是上一版写「DTE 1–35」、这一版写「DTE 1–32」的原因。
    today = snap.feed_timestamp.date()
    # 结构图 / 关键位的口径：与 dashboard 完全一致
    lv = metrics.compute_levels(enriched, structural_spot=snap.spot,
                                live_spot=snap.spot, bucket=BUCKET, today=today)
    keys = lv["keys"]

    sub = enriched[metrics.bucket_mask(enriched, BUCKET, today)]
    dte = None
    if not sub.empty:
        dte = int(sub["expiry"].apply(lambda e: (e - today).days).min())
        dte_hi = int(sub["expiry"].apply(lambda e: (e - today).days).max())
    else:
        dte_hi = None

    # ── 候选墙 ────────────────────────────────────────────────────────
    # 用和 `key_levels` **完全同一套过滤**（上方正 gamma / 下方负 gamma），
    # 只是各取前 3 名而不是第 1 名。第 2 名存在的意义，是让使用者看到
    # 「第 1 名赢得有多勉强」—— 那是墙位会不会跳的唯一线索。
    call_c: list[tuple[float, float]] = []
    put_c: list[tuple[float, float]] = []
    try:
        if not sub.empty:
            agg = metrics.gex_at_spot(sub, snap.spot)
            up = agg[(agg.index >= snap.spot) & (agg > 0)]
            dn = agg[(agg.index <= snap.spot) & (agg < 0)]
            call_c = [(float(k), float(v)) for k, v in
                      up.sort_values(ascending=False).head(3).items()]
            put_c = [(float(k), float(v)) for k, v in
                     dn.sort_values().head(3).items()]
    except Exception as e:  # noqa: BLE001 — 候选算不出不该毁掉整张卡
        log.warning("report: %s 候选墙计算失败 — %s", symbol, e)

    # 绝对墙：用**前收**做参考价（structure figée），且不按现价切分。
    # 这是跨日对比与结算唯一可用的墙 —— 见 `absolute_walls` 的说明。
    ref = float(ref_spot) if ref_spot else snap.spot
    aw = absolute_walls(sub if not sub.empty else enriched, ref)

    sm = metrics.summarize(snap, enriched, with_basis=symbol in ("SPX", "NDX"))
    return AssetView(
        symbol=symbol,
        spot=float(sm.spot),
        net_gex=float(sm.net_gex),
        net_gex_0dte=float(sm.net_gex_0dte),
        net_dex=float(sm.net_dex),
        zero_gamma=None if sm.zero_gamma is None else float(sm.zero_gamma),
        call_wall=keys.get("call_wall"),
        put_wall=keys.get("put_support"),
        d1_min=keys.get("d1_min"),
        d1_max=keys.get("d1_max"),
        pc_oi=float(sm.pc_oi),
        pc_volume=float(sm.pc_volume),
        asof=snap.feed_timestamp.strftime("%Y-%m-%d %H:%M"),
        dte_lo=dte,
        dte_hi=dte_hi,
        call_candidates=call_c,
        put_candidates=put_c,
        abs_call_wall=aw.get("call_wall"),
        abs_put_wall=aw.get("put_wall"),
    )


# ══════════════════════════════════════════════════════════════════════════
#  市场状态 —— 补上参考图里「VIX / 参与度」那一块
# ══════════════════════════════════════════════════════════════════════════
# VIX 分档沿用 `digest.VIX_GRADES` 的口径（同一套阈值，不另立一套），
# 只把标签译成中文。
VIX_ZH = {
    "Complaisance": "过度自满",
    "Calme": "平静",
    "Normal-haut": "偏高",
    "Élevé": "高",
    "Stress": "压力",
    "Panique": "恐慌",
}

# 结构广度最少要有多少只成分股才敢报 —— 少了就是在报噪音。
BREADTH_MIN = 8


def load_vix() -> float | None:
    """最近一次 pull 的 VIX（`store.append_index_spot("vix", …)` 落的盘）。"""
    p = Path(SETTINGS.data_dir) / "history" / "vix.parquet"
    if not p.exists():
        return None
    try:
        d = pd.read_parquet(p)
        return float(d["vix"].iloc[-1]) if len(d) else None
    except Exception:  # noqa: BLE001
        return None


def structural_breadth(hist: pd.DataFrame) -> tuple[int, int]:
    """**结构性**广度：成分股里正 gamma 的只数 / 总数。

    ⚠️ 这不是价格广度（涨跌家数）。gex-dashboard 只存成分股的 spot 快照，
    没有它们的日涨跌，所以参考图那种「上涨比例 58.3%」现在算不出来。
    这里改用 gamma 符号分布，**并且在报告里写明它衡量的是什么** ——
    含混地把一个指标说成另一个，比没有这个指标更糟。
    """
    if hist.empty:
        return (0, 0)
    last = hist.sort_values("ts").groupby("symbol").tail(1)
    c = last[~last["symbol"].isin(CROSS_ASSETS)]
    if c.empty:
        return (0, 0)
    return (int((c["net_gex"] > 0).sum()), len(c))


# ══════════════════════════════════════════════════════════════════════════
#  观察清单 —— 「可自我对账」的那一半
# ══════════════════════════════════════════════════════════════════════════
# 设计要点：**每条观察项必须是一个可判真假的命题**，否则明天没法对账。
#
#   ✗ 「关注 IWM 的负 gamma 结构」        —— 无法判真假
#   ✓ 「IWM 若收在 294 之上即进入正 gamma」—— 明天查收盘价即可
#
# 于是观察项被写成 (kind, level) 的机器可读形式，报告下次生成时自动结算。
# 这是这份报告和参考图**唯一实质性的差别**：他们给结论，我们给结论 + 对账。
@dataclass
class WatchItem:
    key: str            # 稳定 id（去重 + 只结算一次靠它）
    symbol: str
    kind: str           # "close_above" | "close_below"
    level: float
    label: str          # 关键位名字（Call Wall / Put Wall / Gamma Flip）
    text: str           # 给人读的命题
    why: str            # 为什么盯它
    dist: float         # 距现价 %（越近越可能明天发生）
    # ★ 「触发」之外还要有**可结算的预期**，否则攒一年只得到
    # 「墙被碰到过几次」的统计（触发率），算不出命中率。
    #
    # 预期写成**波动率**而不是方向，是因为：
    #   1. GEX 框架最硬的一条命题就是波动率 —— 正 gamma 压制、负 gamma 放大；
    #   2. 方向我们没有任何依据去预测，写「触发后看涨」是编的。
    # 结算方式：触发后 `expect_window` 个交易日的已实现波动，
    # 与触发前同样长度的窗口比。
    expect: str = "vol_up"
    expect_window: int = 3

    def expect_text(self) -> str:
        n = self.expect_window
        return f"触发后 {n} 日内已实现波动 > 触发前 {n} 日" if self.expect == "vol_up" else "—"


def build_watchlist(views: dict[str, AssetView], limit: int = 8) -> list[WatchItem]:
    """从结构自动生成**可对账**的观察项，按「离现价最近的先」排序。"""
    cands: list[WatchItem] = []
    for sym, v in views.items():
        below = v.spot < (v.zero_gamma or v.spot)   # 现价在 Flip 下方？
        # ★ 阈值一律取**绝对墙**（不按现价切）——
        # 相对墙会随现价移动，用它做判据的话，「站上 Call Wall」这个事件
        # 会在触发那一刻把判据本身改掉（见 absolute_walls 的后果 B）。
        specs = [
            (v.abs_call_wall, "Call Wall", "close_above",
             "绝对墙：正 GEX 最大处（不按现价切）",
             "收盘站上 {lv:,.2f}（Call Wall · 绝对）→ 越过正 gamma 最密处"),
            (v.abs_put_wall, "Put Wall", "close_below",
             "绝对墙：负 GEX 最负处（不按现价切）",
             "收盘跌破 {lv:,.2f}（Put Wall · 绝对）→ 跌破负 gamma 最密处"),
            (v.zero_gamma, "Gamma Flip",
             "close_above" if below else "close_below",
             "结构定性分水岭",
             ("收盘站上 {lv:,.2f}（Gamma Flip）→ **从负 gamma 进入正 gamma**，波动收敛"
              if below else
              "收盘跌破 {lv:,.2f}（Gamma Flip）→ **从正 gamma 跌入负 gamma**，波动放大")),
        ]
        for lvl, label, kind, why, tmpl in specs:
            if lvl is None:
                continue
            d = v.dist(lvl)
            if d is None:
                continue
            cands.append(WatchItem(
                key=f"{sym}:{kind}:{round(float(lvl))}",
                symbol=sym, kind=kind, level=float(lvl), label=label,
                text=f"{sym} " + tmpl.format(lv=float(lvl)),
                why=f"{why} · 距现价 {d:+.2f}%", dist=d))

    # 同一标的至多留 2 条（否则一个 IWM 就把清单占满），再按距离取前 limit 条
    per: dict[str, int] = {}
    out: list[WatchItem] = []
    for it in sorted(cands, key=lambda x: abs(x.dist)):
        if per.get(it.symbol, 0) >= 2:
            continue
        per[it.symbol] = per.get(it.symbol, 0) + 1
        out.append(it)
        if len(out) >= limit:
            break
    return out


def load_past_watchlist(out_dir: Path, before: str) -> list[dict]:
    """把**历史报告 JSON** 里发过的观察项收回来。

    去重键是 `(key, created)` 而**不是** `key`：
    一道墙可能连续两周都出现，那是**两次独立的观察**（各自盯各自的下一个交易日）。
    只按 `key` 去重会把后面那些全吞掉，结算结果就只剩第一天的。

    幂等性的来源：不去依赖「昨天跑过没有」，而是直接扫归档目录 ——
    少跑几天也能把该结算的都结算掉。
    """
    seen: dict[tuple, dict] = {}
    for p in sorted(out_dir.glob("*.json")):
        if p.stem in ("latest", "index") or p.stem >= before:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for it in d.get("watchlist", []):
            k = it.get("key")
            if not k:
                continue
            created = d.get("created", p.stem)
            seen.setdefault((k, created), {**it, "created": created})
    return list(seen.values())


def settle_watchlist(items: list[dict], closes: pd.Series) -> list[dict]:
    """用**下一个交易日的收盘价**结算每条观察项。

    窗口 = 下一个交易日，一次性的 —— 命中 hit / 未命中 miss。
    不设「等 N 天」，因为清单的语义就是「盯明天」；拖成开放式的话，
    这条记录既不是成功也不是失败，对不了账。
    """
    out: list[dict] = []
    for it in items:
        try:
            created = pd.Timestamp(it["created"]).date()
        except Exception:  # noqa: BLE001
            continue
        if closes is None or len(closes) == 0:
            out.append({**it, "result": "open", "witness": "没有价格数据"})
            continue
        later = [d for d in closes.index if d > created]
        if not later:
            out.append({**it, "result": "open", "witness": "还没产生下一个交易日"})
            continue
        d = later[0]
        c = float(closes[d])
        hit = (c >= it["level"]) if it["kind"] == "close_above" else (c <= it["level"])
        out.append({**it, "result": "hit" if hit else "miss",
                    "settled_on": str(d), "close": c,
                    "witness": f"{d} 收 {c:,.2f}（阈值 {it['level']:,.2f}）"})
    return out


def _realized_vol(closes: pd.Series, days: list) -> float | None:
    """给定交易日的已实现波动（日收益标准差）。少于 3 个收盘价时算不出来。"""
    if closes is None or not days:
        return None
    s = closes.loc[[d for d in days if d in closes.index]]
    if len(s) < 3:
        return None
    r = s.pct_change().dropna()
    return float(r.std()) if len(r) >= 2 else None


def base_rate_of(views: dict, closes_of) -> dict:
    """基准率 —— 主标的拿不到就退到任何一个有价格的标的。

    ⚠️ **不许静默降级。** 基准率是「这套东西有没有信息量」的分母；
    它变成 `None` 时，报告看上去一切正常，只是结论悄悄失效了 ——
    实测过一次：CBOE 请求网络抖动 ⇒ 静默变成 `{'hit': 0, 'total': 0}`。

    所以：① 逐个标的试；② 全都失败时**写进返回值并在报告里显形**。
    基准率量级是「市场波动自己会不会放大」，换个标的算不影响它的意义。
    """
    order = ["SPX", *views.keys(), "SPY", "QQQ", "IWM"]
    for sym in dict.fromkeys(order):
        try:
            hw = vol_base_rate(closes_of(sym))
        except Exception:  # noqa: BLE001
            continue
        if hw[1] >= 100:
            return {"hit": hw[0], "total": hw[1],
                    "rate": hw[0] / hw[1], "symbol": sym}
    log.warning("report: 基准率算不出来 —— 所有标的的价格历史都取不到")
    return {"hit": 0, "total": 0, "rate": None, "symbol": None}


def _next_trading_day(closes: pd.Series, after) -> object | None:
    """`after` 之后的第一个交易日（价格序列里有的）。"""
    if closes is None or len(closes) == 0:
        return None
    try:
        d = pd.Timestamp(after).date()
    except Exception:  # noqa: BLE001
        return None
    later = [x for x in closes.index if x > d]
    return later[0] if later else None


def settle_expectation(item: dict, closes: pd.Series) -> dict:
    """结算「波动预期」—— **触发组和未触发组都要测**。

    ★ 为什么未触发的也要测：**它们是对照组。**

    波动率有**均值回复**。VIX 15.52（「平静」档）时，波动大概率自己回升 ——
    **不需要任何一道墙被穿越**。所以「触发后波动放大」这件事，在**没有触发**
    的日子里也会发生。

    只统计触发组的话，攒一年会得到一个很漂亮的命中率，
    而那个数字测的是「波动率的均值回复」，不是「GEX 机制」。

    ⇒ 只有把**触发组**和**同期未触发的墙**放在一起比，
      才知道那道墙有没有信息量。

    未触发组用 `created` 的下一个交易日作参考日 —— 同一批标的、同一段日期、
    同一个度量，**唯一不同的就是「墙有没有被穿越」**。
    """
    n = int(item.get("expect_window") or VOL_WINDOW)
    treated = item.get("result") == "hit"
    ref = item.get("settled_on") if treated else None
    if not ref:
        ref = _next_trading_day(closes, item.get("created"))
    if ref is None:
        return {"expect_result": "pending", "expect_witness": "没有下一个交易日"}
    if closes is None or len(closes) == 0:
        return {"expect_result": "pending", "expect_witness": "没有价格数据"}

    days = list(closes.index)
    try:
        on = pd.Timestamp(ref).date()
    except Exception:  # noqa: BLE001
        return {"expect_result": "pending", "expect_witness": "参考日不可解析"}
    if on not in days:
        return {"expect_result": "pending", "expect_witness": "参考日不在价格序列里"}

    i = days.index(on)
    pre = _realized_vol(closes, days[max(0, i - n): i + 1])
    post_days = days[i: min(len(days), i + 1 + n)]
    post = _realized_vol(closes, post_days)

    if pre is None or post is None:
        return {"expect_result": "pending", "expect_witness": "样本不足"}
    if len(post_days) - 1 < n:
        return {"expect_result": "pending",
                "expect_witness": f"观察窗口未走完（{len(post_days) - 1}/{n} 个交易日）"}

    # ★ 阈值写死：`post >= pre × VOL_RATIO`。
    # 旧版是 `post > pre` —— 1.01 倍也算命中，那等于没有判据：
    # **有阈值才叫可结算**，没阈值就只剩解释空间，而解释空间是自欺的入口。
    ratio = (post / pre) if pre else 0.0
    hit = ratio >= VOL_RATIO
    if not treated:
        # 对照组：**不参与命中率**，只用来算基准
        return {
            "expect_result": "control",
            "expect_ratio": ratio,
            "expect_witness": (f"未触发（对照组）波动 {pre * 100:.2f}% → "
                               f"{post * 100:.2f}% = {ratio:.2f}×"),
        }
    return {
        "expect_result": "hit" if hit else "miss",
        "expect_ratio": ratio,
        "expect_witness": (f"波动 {pre * 100:.2f}% → {post * 100:.2f}%　"
                           f"= {ratio:.2f}×（阈值 {VOL_RATIO}×）"),
    }


def current_caliber() -> dict:
    """本次生成用的**口径** —— 和报告一起冻结。

    为什么只冻结时间戳不够
    ----------------------
    上一版同一天的两份报告，DTE 窗口分别是 **1–35** 和 **1–32**；
    而结构迁移是**跨日对比**的 —— 口径一变，对比就无效。

    ⇒ 每个快照记下这几项；跨日对比时**口径不一致就拒绝出迁移**。
      **宁可不比，也不要假比。**

    `wall_algorithm` 单独列出，是因为它是最容易被忽略、后果最大的一项：
    「相对墙」（按现价切）和「绝对墙」（不按现价切）会给出**不同的墙**，
    而台账上看起来是同一个字段名。
    """
    return {
        "bucket": BUCKET,                    # 到期日窗口（Mois = ≤35 天）
        "dte_basis": "snapshot_date",        # DTE 以**快照日**为基准，不是生成日
        "wall_algorithm": "absolute",        # 跨日对比只用绝对墙
        "iv_policy": "bs_then_cboe",         # Gamma 优先 BS 重算，IV 不可解退回 CBOE
        "lookback_days": LOOKBACK_DAYS,
        "vol_ratio": VOL_RATIO,              # 预期阈值（写死）
        "vol_window": VOL_WINDOW,
    }


def caliber_id(cal: dict) -> str:
    """口径的短指纹 —— 用来判断两份报告的口径是否一致。"""
    return hashlib.sha1(
        json.dumps(cal, sort_keys=True).encode("utf-8")).hexdigest()[:10]


def vol_base_rate(closes: pd.Series, n: int = VOL_WINDOW,
                  ratio: float = VOL_RATIO) -> tuple[int, int]:
    """**无条件基准率** —— 全部 n 日窗口里，波动 ≥ 前 n 日 ×ratio 的比例。

    ★ 这是整份报告最容易被忽略、却最要紧的一个数。

    为什么必须有对照组
    ------------------
    波动率有**均值回复**。现在是 VIX 15.52（「平静」档），
    **低波动之后波动大概率自己回升 —— 不需要任何一道墙被穿越。**

    没有基准率，攒一年会得到一个很漂亮的命中率，而那个数字测的是
    「波动率的均值回复」，**不是「GEX 机制」**。
    一个同源、无对照的检验，样本从 8 涨到 800 也不会更可信 ——
    它只是在更精确地测量同一个偏差。

    ⇒ 要看的是 **命中率 − 基准率**（超额），不是命中率本身。
    """
    if closes is None or len(closes) < 2 * n + 2:
        return (0, 0)
    r = closes.pct_change()
    days = list(closes.index)
    hit = tot = 0
    for i in range(2 * n, len(days)):
        pre = r.iloc[i - n + 1: i + 1].std()
        post = r.iloc[i + 1: i + 1 + n].std()
        if not (pre and post) or pd.isna(pre) or pd.isna(post):
            continue
        tot += 1
        if post / pre >= ratio:
            hit += 1
    return (hit, tot)


# ══════════════════════════════════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════════════════════════════════
def generate(symbols: list[str] | None = None, *,
             out_dir: Path | None = None,
             write: bool = True) -> dict:
    """生成一份报告。返回摘要 dict（也给 `--json` 用）。"""
    symbols = list(symbols or CROSS_ASSETS)
    out_dir = out_dir or REPORT_DIR

    hist = load_history()
    _pxc: dict[str, pd.DataFrame] = {}

    def _pxdf(sym: str) -> pd.DataFrame:
        if sym not in _pxc:
            _pxc[sym] = load_price_history(sym)
        return _pxc[sym]

    def _closes_of(sym: str) -> pd.Series:
        d = _pxdf(sym)
        return d["close"] if d is not None and not d.empty else pd.Series(dtype=float)

    def _ref_spot(sym: str, snap_day) -> float | None:
        """**前收** —— 结构冻结用的参考价。

        取快照日**之前**的最后一根收盘价（不是快照日当天）：
        当天的收盘价是「现价」，用它做结构参考就等于让墙跟着现价跑，
        正是 `absolute_walls` 要修的那个循环。
        """
        c = _closes_of(sym)
        if c.empty:
            return None
        prev = c[c.index < snap_day]
        return float(prev.iloc[-1]) if len(prev) else float(c.iloc[-1])

    views: dict[str, AssetView] = {}
    for s in symbols:
        try:
            snap = load_chain_snapshot(s)
            if snap is None:
                log.info("report: %s 无快照，跳过", s)
                continue
            v = build_view(s, hist, snap,
                           ref_spot=_ref_spot(s, snap.feed_timestamp.date()))
        except Exception as e:  # noqa: BLE001 — 一个标的失败不该毁掉整份报告
            log.warning("report: %s 构建失败 — %s", s, e)
            v = None
        if v is not None:
            views[s] = v

    if not views:
        raise SystemExit("report: 没有任何可用标的（data/snapshots/ 是空的？先跑一次 dashboard）")

    primary = views.get("SPX") or next(iter(views.values()))
    day = pd.Timestamp(primary.asof).strftime("%Y-%m-%d")

    # ---- 市场状态 ----
    vix = load_vix()
    bpos, btot = structural_breadth(hist)

    # ---- 观察清单：今天新出的 + 对历史项的结算 ----
    watch = [asdict(w) for w in build_watchlist(views)]
    _closes = _closes_of          # 复用上面那份缓存，别开第二份

    # 每个历史项按其 `created` 日结算，窗口 = 下一个交易日。
    # `load_past_watchlist` 已经把今天的文件排除掉了（今天的明天才有结果），
    # 所以同一天重跑报告不会重复结算。
    cal = current_caliber()
    cal_id = caliber_id(cal)

    settled: list[dict] = []
    if write:
        for it in load_past_watchlist(out_dir, day):
            closes = _closes(it.get("symbol", ""))
            res = settle_watchlist([it], closes)[0]
            # ★ 口径校验：两份报告的口径不一致就**拒绝结算**。
            # 宁可不比，也不要假比 —— 一个跨口径的对比看起来和正常的
            # 一模一样，但结论是错的，而且错了没人看得出来。
            cid = it.get("caliber_id")
            if cid and cid != cal_id:
                res["expect_result"] = "caliber_mismatch"
                res["expect_witness"] = f"口径不一致（{cid} ≠ {cal_id}）→ 拒绝结算"
            else:
                # 触发组算命中，未触发组算对照 —— 见 settle_expectation
                res.update(settle_expectation(res, closes))
            settled.append(res)

    # ---- 冻结：当天**第一次**生成就定版，重跑不覆盖 ----
    #
    # 为什么必须冻结：同一天两次生成（20:24 / 20:33 两次快照，现价都是 7,773.95），
    # SPX 的两道墙各跑了 **50 点**（Put Wall 7,700 → 7,650；
    # Call Wall 7,800 → 7,850），净 GEX 也从 +94.37B 变成 +91.51B。
    #
    # 墙位对「取数那一刻的快照」高度敏感 —— 延迟接口 + OI 陆续结算。
    # 不冻结的话，明天结算「收盘站上 7,850」时**说不清拿的是哪一版的墙**，
    # 这条观察就永远对不了账。
    #
    # 冻结的粒度必须是**具体一次生成**，光冻结「日期」不够。
    # 每条观察项都带上 `frozen_at`（生成时刻）与 `data_asof`（数据时刻），
    # 出来的记录本身自带版本，抄进台账也不会串。
    frozen_from = None
    if write:
        f = out_dir / f"{day}.json"
        if f.exists():
            try:
                old = json.loads(f.read_text(encoding="utf-8"))
                if old.get("watchlist") and old.get("frozen_at"):
                    watch = old["watchlist"]
                    frozen_from = old["frozen_at"]
                    log.info("report: 沿用 %s 首次生成的观察清单（冻结于 %s）",
                             day, frozen_from)
            except Exception as e:  # noqa: BLE001
                log.warning("report: 读取旧清单失败，重新冻结 — %s", e)

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if frozen_from is None:
        for w in watch:
            w["frozen_at"] = stamp
            w["data_asof"] = primary.asof
            # ★ 口径指纹：结算时用它判断两份报告是不是同一个口径
            w["caliber_id"] = cal_id

    payload = {
        "asof": primary.asof,          # 数据时刻（快照的 feed_timestamp）
        "created": day,                # 归属交易日
        "generated_at": stamp,         # 本次生成时刻
        # 观察清单定版的时刻。同一天重跑不会变 —— 见 generate() 里的「冻结」。
        # 结算时必须按这一版比对，否则说不清拿的是哪一次的墙。
        "frozen_at": frozen_from or stamp,
        # ★ 口径与指纹 —— 跨日对比的前提是口径一致，否则拒绝出迁移
        "caliber": cal,
        "caliber_id": cal_id,
        "bucket": BUCKET,
        "market_state": {
            "vix": vix,
            "breadth_pos": bpos, "breadth_total": btot,
            "breadth_note": "结构性广度（成分股正 gamma 占比），"
                            "不是价格广度的涨跌家数",
        },
        "assets": {k: asdict(v) for k, v in views.items()},
        # 派生的「距离」也存下来 —— 明天复盘时直接比这些数
        "distances": {
            k: {"flip": v.flip_dist,
                "call_wall": v.dist(v.call_wall),
                "put_wall": v.dist(v.put_wall)}
            for k, v in views.items()
        },
        "watchlist": watch,
        "settled": settled,
        # ★ 对照组：无条件基准率。**没有它，命中率测的是波动率的均值回复，
        # 不是 GEX 机制。** 样本越大越稳 —— 指数有上万天，ETF 只有几十天，
        # 所以逐个标的试，取第一个够用的（见 `base_rate_of`）。
        "base_rate": base_rate_of(views, _closes),
    }

    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        html = render_html(views, hist, payload)
        (out_dir / f"{day}.html").write_text(html, encoding="utf-8")
        (out_dir / f"{day}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        (out_dir / "latest.html").write_text(html, encoding="utf-8")
        # ★ 口径与定义：**每天完全一样**，所以单独一份，不塞进日报。
        #   覆盖式写入（内容与日期无关），保证它永远是最新的常量表。
        (out_dir / "glossary.html").write_text(
            glossary_html(), encoding="utf-8")
        # index：把过往所有日期列出来 —— 这就是「攒起来」的样子
        days = sorted(p.stem for p in out_dir.glob("*.html")
                      if p.stem not in ("latest", "index", "glossary"))
        (out_dir / "index.html").write_text(
            render_index(days), encoding="utf-8")
        log.info("report: 已写 %s", out_dir / f"{day}.html")

    return payload


def generate_daily() -> None:
    """给 APScheduler 调用的入口（收盘后跑）。异常必须吞掉，否则会杀掉调度任务。"""
    try:
        generate()
    except Exception:  # noqa: BLE001
        log.exception("report: 每日生成失败")


# ══════════════════════════════════════════════════════════════════════════
#  渲染 —— 单文件中文 HTML
# ══════════════════════════════════════════════════════════════════════════
# ── 配色：**CSS 与图表共用同一组值** ──────────────────────────────────
# 分开写一定会漂 —— 实测过：CSS 改完之后，图里还是老的 #2fd8a0 / #ff5d7a，
# 因为图表在 Python 里硬编码了另一套。所以图里不再写字面量，全部从这里取。
#
# 每个值都有**唯一归属的层**（见下面 CSS 里的配色分层）：
#   pos/neg  = 数据层 · 正 / 负 gamma（唯一的两个数据彩色）
#   hi       = 强调层 · 现价、Gamma Flip（它是**分界**，不是第三类 gamma）
#   gold     = 品牌层 · 页眉字标、章节号、导航色带（参考图的 brass）
#   candle   = 蜡烛（**故意不上色**，见 `_price_figure` 的说明）
#
# ★ 为什么允许第三种颜色（gold）
#   旧版把金色一起删掉过，理由是「它出现在 7 处纯装饰上，不编码任何东西」。
#   这个理由本身是对的，但结论下反了 —— 正确的做法不是**删掉品牌层**，
#   而是**给每种颜色划定互不越界的层**：
#
#     ① 数据层 pos/neg  只表示正/负 gamma
#     ② 强调层 hi      现价与分界线
#     ③ 品牌层 gold    页眉 / 章节号 / 色带 —— **永不靠近一个数字**
#     ④ 灰阶           其余一切
#
#   有了分层，装饰性金色就不再稀释绿/红 —— 因为读的人已经知道
#   「金色 = 导航，彩色 = 数据」。而没有分层时，金色和绿红混排
#   才会让三者一起贬值。
PALETTE = {
    "bg":     "#080c0a",
    "panel":  "#0e1512",
    "panel2": "#0b110e",
    "line":   "#1e2a24",
    "grid":   "#16201b",
    "zero":   "#2c3a33",
    "fg":     "#e9efea",
    "dim":    "#93a09a",
    "dim2":   "#78857e",
    "pos":    "#3ddc97",
    "neg":    "#f2545b",
    "hi":     "#ffffff",
    "gold":   "#c2a35c",
    "candle": "#7f8c85",
}
CSS = """
:root{
  --serif:Georgia,"Times New Roman","Songti SC",serif;
""" + "\n".join(f"  --{k}:{v};" for k, v in PALETTE.items()) + """
}
/* ── 配色分层：**颜色按层分配，层与层之间不许越界** ────────────────────
   ① 数据层  pos / neg  只表示「正 gamma / 负 gamma」—— 唯一的两个数据彩色
   ② 强调层  hi        现价、Gamma Flip（它是**分界**，不是第三类 gamma）
   ③ 品牌层  gold      页眉字标、章节号、导航色带 —— **永不靠近一个数字**
   ④ 灰阶              链接靠下划线区分，标签靠字重/字距区分，都不靠色相

   旧版把金色全删了，理由是「它不编码任何东西」—— 理由对，结论反了：
   该删的是**混排**，不是**颜色**。分层之后金色不再稀释绿/红，
   因为「金色 = 导航、彩色 = 数据」已经是一条可读的约定。
   而 --dim2 从 #57626f（2.87:1）提到 #78857e（4.9:1）——
   它被用在 9px 的字上，是全篇最该读得清、却最读不清的颜色。 */
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);
  font:11.5px/1.55 -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1240px;margin:0 auto;padding:18px 22px 30px}
a{color:var(--fg);text-decoration:underline;
  text-decoration-color:var(--dim2);text-underline-offset:2px}
a:hover{text-decoration-color:var(--fg)}
.pos{color:var(--pos)} .neg{color:var(--neg)} .dim{color:var(--dim2)}
.num{font-variant-numeric:tabular-nums}

/* ── 页眉：品牌 / 展示标题 / 元信息，三段 ─────────────── */
header{display:flex;align-items:center;gap:24px;
  border-bottom:1px solid var(--line);padding-bottom:12px}
.brand{display:flex;align-items:center;gap:9px;flex:0 0 auto}
.brand .mark{color:var(--gold);font-size:23px;line-height:1}
.wordmark{font:italic 600 15px/1.1 var(--serif);letter-spacing:2.4px}
.brand-sub{font-size:8.5px;letter-spacing:1.7px;color:var(--dim2);margin-top:3px}
.title{flex:1 1 auto;border-left:1px solid var(--line);padding-left:24px}
.t-main{font:italic 400 25px/1.05 var(--serif);letter-spacing:.3px}
.t-sub{font-size:9.5px;color:var(--dim2);letter-spacing:.7px;margin-top:4px}
.meta{flex:0 0 auto;text-align:right;white-space:nowrap}
.meta .k{font-size:8.5px;letter-spacing:1.5px;color:var(--dim2);text-transform:uppercase}
.meta .d{font:600 15.5px/1.3 var(--serif);letter-spacing:.8px;margin:1px 0}
.meta .s{font-size:8.5px;letter-spacing:1.2px;color:var(--dim2)}

/* ── 状态条 ─────────────────────────────────────────── */
.status{display:grid;grid-template-columns:1.66fr repeat(4,1fr);gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:4px;
  overflow:hidden;margin:12px 0 0}
.status>div{background:var(--panel);padding:8px 12px 9px}
.status .lb{font-size:9px;letter-spacing:.9px;color:var(--dim2);margin-bottom:3px}
.status .big{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums;
  line-height:1.2;letter-spacing:-.2px}
.status .dl{font-size:9px;color:var(--dim2);margin-top:3px;line-height:1.55}

/* ── 章节标题 ───────────────────────────────────────── */
.sec{display:flex;align-items:baseline;gap:9px;margin:20px 0 9px;
  border-bottom:1px solid var(--line);padding-bottom:5px}
.sec .n{font:600 12.5px/1 var(--serif);color:var(--gold);letter-spacing:1px}
.sec .cn{font-size:13px;font-weight:600;letter-spacing:.2px}
.sec .en{font-size:8.5px;letter-spacing:1.6px;color:var(--dim2);text-transform:uppercase}
.sec .rt{margin-left:auto;color:var(--dim2);font-size:9px;letter-spacing:.2px}

/* ── 网格 / 卡片 ────────────────────────────────────── */
.cols{display:grid;gap:9px}
/* `.c5` = 四个标的卡 + 右侧「结构要点」面板。列数由 `render_html` 按标的数
   内联覆盖 —— 标的从 4 个变成 5 个时，写死的列数会把它挤到第二行，
   而那道空行比任何图例都更容易让人误读。 */
.c5{grid-template-columns:repeat(4,1fr) 1.16fr}
.c4{grid-template-columns:repeat(4,1fr)}
.c3{grid-template-columns:repeat(3,1fr)}
.c2m{grid-template-columns:1fr 252px}
.c2{grid-template-columns:1fr 1fr}
.card{background:var(--panel);border:1px solid var(--line);border-radius:4px;
  padding:10px 12px}
/* 左缘导航色带 —— 颜色由**数据的符号**决定（绿=正 gamma / 红=负 gamma），
   位置由版式决定（卡片左缘）。于是它一眼就能在四张卡里挑出异类。 */
.bar{position:relative;padding-left:15px}
.bar::before{content:"";position:absolute;left:0;top:9px;bottom:9px;width:2px;
  border-radius:0 2px 2px 0;background:var(--gold)}
.bar.p::before{background:var(--pos)} .bar.n::before{background:var(--neg)}

/* ── 面板标题行（卡内小标题）────────────────────────── */
.ph{display:flex;align-items:center;gap:6px;margin-bottom:7px}
.ph .pt{font-size:11px;font-weight:600;letter-spacing:.3px}
.ph .pr{margin-left:auto}

/* ── 标的卡 ─────────────────────────────────────────── */
.a-hd{display:flex;align-items:center;gap:6px;margin-bottom:5px}
.a-hd .tk{font:600 13px/1 var(--serif);letter-spacing:1px}
.a-hd .bdg{margin-left:auto}
.px{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums;
  line-height:1.05;letter-spacing:-.4px}
.chg{font-size:9px;color:var(--dim2);margin:4px 0 7px}
.rows{font-size:10.5px}
.rows .r{display:flex;justify-content:space-between;gap:8px;
  padding:3px 6px;border-radius:2px}
.rows .r:nth-child(odd){background:rgba(255,255,255,.03)}
.rows .r .k{color:var(--dim)}
.rows .r .v{font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}
.tags{margin-top:7px;display:flex;gap:5px;flex-wrap:wrap}
.bdg{display:inline-block;padding:1.5px 6px;border-radius:3px;font-size:9px;
  font-weight:600;border:1px solid;white-space:nowrap;line-height:1.5}
.bdg.p{color:var(--pos);border-color:rgba(61,220,151,.32);background:rgba(61,220,151,.09)}
.bdg.n{color:var(--neg);border-color:rgba(242,84,91,.32);background:rgba(242,84,91,.09)}
.bdg.t{color:var(--dim);border-color:var(--line)}
.tiny{font-size:8.5px;color:var(--dim2);margin-top:6px;line-height:1.5}
ul.bul{margin:0;padding-left:14px;font-size:10.5px;color:var(--dim);line-height:1.8}
ul.bul li{margin:0}
ul.bul b{color:var(--fg);font-weight:600}
.spark{float:right;margin:-2px 0 0 5px}

/* ── 图上方的内联关键位行 ───────────────────────────── */
.chhdr{display:flex;flex-wrap:wrap;gap:5px 20px;align-items:baseline;
  padding:10px 12px 8px;font-size:10.5px}
.chhdr .tk{font:600 14px/1 var(--serif);letter-spacing:1px}
.chhdr .px{font-size:15.5px;letter-spacing:-.2px}
.chhdr .lb{color:var(--dim2);font-size:8.5px;letter-spacing:1.2px;
  text-transform:uppercase}
.chhdr .sp{flex:1 1 auto}

/* ── 右侧结构状态面板 ───────────────────────────────── */
.side .hd1{font-size:10px;color:var(--dim);letter-spacing:.4px;margin-bottom:6px}
.side .big{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums;
  line-height:1.02;letter-spacing:-.9px}
.side .sub{font-size:8.5px;letter-spacing:1.2px;color:var(--dim2);margin:4px 0 0}
.side .hd{font-size:8.5px;letter-spacing:1.3px;color:var(--dim2);
  text-transform:uppercase;margin:12px 0 2px;
  border-top:1px solid var(--line);padding-top:8px}
.side .r{display:flex;justify-content:space-between;gap:8px;padding:3px 0;font-size:10.5px}
.side .r .k{color:var(--dim)}
.side .r .v{font-variant-numeric:tabular-nums;font-weight:600;text-align:right}
/* 面板里那一整段说明 —— 内容是查表来的「定义」，不是散文 */
.side .say{font-size:9.5px;color:#b6c2bb;line-height:1.75;margin-top:10px;
  border-top:1px solid var(--line);padding-top:8px}

/* ── 结构事实（机器生成）────────────────────────────── */
.facts{margin-top:9px;border-top:1px solid var(--line);padding-top:8px}
.facts .fr{display:flex;gap:8px;font-size:9.5px;line-height:1.65;margin-bottom:3px}
.facts .fr .k{color:var(--dim2);white-space:nowrap;flex:0 0 auto}
.facts .fr .t{color:#b6c2bb}
/* 生成方式徽标：m = 机器生成（可复核）、a = 人工撰写（不可复核）。
   ⚠️ 两个徽标都改灰 —— 它们是**元信息**（谁写的），不是数据。
   旧版用绿/金，于是每段文字旁都挂一个彩色小标签，一屏 16 个，
   把真正要看的绿/红数字淹掉了。 */
.gen{font-size:8px;padding:0 4px;border-radius:2px;border:1px solid var(--line);
  color:var(--dim2);font-weight:600;letter-spacing:.3px;white-space:nowrap;
  vertical-align:middle}
.gen.a{border-style:dashed}

/* ── 编号卡（观察清单 / 观察重点）───────────────────── */
.focus .fh{display:flex;align-items:baseline;gap:7px;margin-bottom:5px}
.focus .fn{font:600 10px/1 var(--serif);color:var(--gold);letter-spacing:1px}
.focus .fs{font-size:8.5px;letter-spacing:1.3px;color:var(--dim2);
  text-transform:uppercase}
.focus .ttl{font-size:11.5px;font-weight:600;line-height:1.5;margin-bottom:4px}
.focus .bd{font-size:9.5px;color:var(--dim);line-height:1.7}
.focus .bd b{color:var(--fg)}

/* ── 观察清单（编号卡变体）──────────────────────────── */
.wl .w .no{font-size:8.5px;color:var(--dim2);letter-spacing:1.1px;
  text-transform:uppercase;margin-bottom:4px}
.wl .w .ttl{font-size:11px;font-weight:600;margin:3px 0 4px;line-height:1.5}
.wl .w .d{font-size:9.5px;color:var(--dim);line-height:1.65}
.wl .w .d b{color:var(--fg)}

table{width:100%;border-collapse:collapse;font-size:10.5px}
th{color:var(--dim2);font-weight:500;text-align:right;padding:4px 6px;
  border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
td{padding:3.5px 6px;text-align:right;font-variant-numeric:tabular-nums;
  border-bottom:1px solid rgba(255,255,255,.045)}
tbody tr:nth-child(even) td{background:rgba(255,255,255,.02)}

/* ── 折叠块：可对账性凭证，默认收起 ───────────────────
   折起来而不是删掉 —— **删掉就没人能复核了**。 */
details.fold{margin:9px 0 0}
details.fold>summary{cursor:pointer;list-style:none;display:flex;
  align-items:center;gap:6px;padding:4px 0;color:var(--dim);font-size:9.5px}
details.fold>summary::-webkit-details-marker{display:none}
/* 用字面字符，不写 \\25B8 —— Python 会把它当八进制转义（0o25 = chr(21)），
   浏览器拿到的是控制字符而不是 ▸。这个坑已经踩过一次。 */
details.fold>summary::before{content:"▸";font-size:9px;color:var(--dim2);
  transition:transform .15s}
details.fold[open]>summary::before{transform:rotate(90deg)}
details.fold>summary:hover{color:var(--fg)}
details.fold>summary .fold-hint{margin-left:auto;color:var(--dim2);font-size:9px}
details.fold>.fold-body{padding:2px 0 4px}
.note{font-size:9.5px;color:var(--dim);line-height:1.75;margin-top:7px}
.note b{color:var(--fg)}
footer{margin-top:22px;padding-top:10px;border-top:1px solid var(--line);
  display:flex;align-items:baseline;gap:16px;color:var(--dim2);font-size:9px;
  flex-wrap:wrap}
footer .fb{font:italic 600 10px/1 var(--serif);letter-spacing:1.7px;color:var(--dim)}
footer .r{margin-left:auto;letter-spacing:.6px}
.err{background:rgba(242,84,91,.08);border:1px solid rgba(242,84,91,.3);
  border-radius:4px;padding:10px 12px;color:#ffb0b6;font-size:11px}
"""


def _fmt(v, nd=2, sign=False, suffix=""):
    if v is None:
        return "—"
    if isinstance(v, float) and (v != v):  # NaN
        return "—"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s + suffix


def _dist_html(v: float | None) -> str:
    """距离：正=在上方，负=在下方。"""
    if v is None:
        return '<span class="dim">—</span>'
    return f'<span class="{"pos" if v >= 0 else "neg"} num">{v:+.2f}%</span>'


def _gex_html(v: float) -> str:
    return (f'<span class="{"pos" if v >= 0 else "neg"} num">'
            f'{v / 1e9:+,.2f}B</span>')


def _bdg(text: str, kind: str = "t") -> str:
    return f'<span class="bdg {kind}">{text}</span>'


def _section(num: str, cn: str, en: str, right: str = "") -> str:
    rt = f'<span class="rt">{right}</span>' if right else ""
    return (f'<div class="sec"><span class="n">{num}</span>'
            f'<span class="cn">{cn}</span><span class="en">{en}</span>{rt}</div>')


def _spark(closes: pd.Series, w: int = 62, h: int = 20) -> str:
    """内联 SVG 缩略走势线。

    参考图每张卡右上角都有一条 —— 它不承担读数，只承担**一眼的形状**。
    用 SVG 而不是 plotly：150 字节 vs 一个独立的 3MB 运行时，而这里
    只需要「涨还是跌、走的什么形状」。
    """
    if closes is None or len(closes) < 3:
        return ""
    c = closes.tail(48).to_numpy(dtype="float64")
    lo, hi = float(c.min()), float(c.max())
    rng = (hi - lo) or 1.0
    n = len(c)
    pts = " ".join(f"{i * w / (n - 1):.1f},{h - (v - lo) / rng * (h - 3) - 1.5:.1f}"
                   for i, v in enumerate(c))
    up = c[-1] >= c[0]
    col = "var(--pos)" if up else "var(--neg)"
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'aria-hidden="true"><polyline points="{pts}" fill="none" '
            f'stroke="{col}" stroke-width="1.3" stroke-linejoin="round"/></svg>')


# 卡头右上角那行小标签。参考图每张卡都有，所以这里恢复成常显 ——
# 但它**只说符号**（正 / 负 gamma），不参与任何判断的表述。
# 旧的「只在符号矛盾时出现」规则把 badge 让给了 `sign_conflict`，
# 矛盾时改用更明确的「⚠ 符号矛盾」，两者并存不冲突。
GEX_BADGE = {True: "POSITIVE GEX", False: "NEGATIVE GEX"}


def _asset_card(v: AssetView, closes: pd.Series | None = None) -> str:
    """跨标的结构卡 —— 参考图版式：色带 / 卡头 / 大价格 / 涨跌 / 四行表 / 标签。

    左缘色带按 `net_gex` 的符号取色，四张卡横排时**异类一眼可辨**，
    不用逐行去读 Net GEX 那一行的数字。

    ⚠️ 报价时间戳只在这里印**时刻**（不印日期）：五个标的同一份快照，
    日期印五遍 = 四遍冗余，完整时间在页眉与 01 节标题上。
    """
    sgn = "p" if v.net_gex >= 0 else "n"
    if v.sign_conflict:
        badge = '<span class="bdg n">⚠ 符号矛盾</span>'
    else:
        badge = f'<span class="bdg {sgn}">{GEX_BADGE[v.net_gex >= 0]}</span>'

    rows = [
        ("Gamma Flip", _fmt(v.zero_gamma, 2)),
        ("Put Wall", _fmt(v.put_wall, 2)),
        ("Call Wall", _fmt(v.call_wall, 2)),
        ("Net GEX", _gex_html(v.net_gex)),
    ]
    body = "".join(
        f'<div class="r"><span class="k">{k}</span><span class="v">{val}</span></div>'
        for k, val in rows)
    tags = (_bdg(v.position_label())
            + _bdg(v.wall_label())
            + (f' {_bdg(f"距 Flip {v.flip_dist:+.2f}%", "t")}'
               if v.flip_dist is not None else ""))
    return f"""<div class="card bar {sgn}">
<div class="a-hd"><span class="tk">{v.symbol}</span>{badge}</div>
<div class="px">{v.spot:,.2f}{_spark(closes) if closes is not None else ""}</div>
<div class="chg">{_chg_html(_pct_change(closes))}</div>
<div class="rows">{body}</div>
<div class="tags">{tags}</div>
<div class="tiny">Cboe 报价 {v.asof[-5:]} ET · DTE {v.dte_lo}–{v.dte_hi} 天</div>
</div>"""


def _structure_points(views: dict[str, AssetView]) -> str:
    """「结构要点」列 —— **全部机器生成**。

    每一条都是「从数据里数出来的」（分组 + 计数），或查 `MECHANICS` 表。
    旧版这里写的是「突破后能否站稳，要看成交量」这种句子 ——
    **那是推测，不是数据**，删掉了。
    """
    def grp(pred) -> list[str]:
        return [s for s, v in views.items() if pred(v)]

    above = grp(lambda v: v.below_flip is False)
    below = grp(lambda v: v.below_flip is True)
    near_call = grp(lambda v: 0 <= (v.dist(v.call_wall) if v.dist(v.call_wall) is not None else 99) <= 1.0)
    near_put = grp(lambda v: -1.0 <= (v.dist(v.put_wall) if v.dist(v.put_wall) is not None else -99) <= 0)
    neg = grp(lambda v: v.net_gex < 0)
    n = len(views)

    li: list[str] = []
    # ⚠️ 这里**只给短标签**，不贴 `MECHANICS` 那 64 字的整句解释。
    #    旧版正/负各贴一遍 = 同一句固定话印两遍，而它**每天完全一样**。
    #    完整解释在 glossary.html。
    if above:
        li.append(f"<b>{' / '.join(above)}</b> 位于 Flip 上方（<b>正 gamma</b>）")
    if below:
        li.append(f"<b>{' / '.join(below)}</b> 位于 Flip 下方（<b>负 gamma</b>）")
    if near_call:
        li.append(f"<b>{' / '.join(near_call)}</b> 距 Call Wall ≤1.0%")
    if near_put:
        li.append(f"<b>{' / '.join(near_put)}</b> 距 Put Wall ≤1.0%")
    if neg:
        li.append(f"<b>{' / '.join(neg)}</b> 净 GEX 为负")
    li.append(f"计数：{len(above)}/{n} 在 Flip 上方 · {len(below)}/{n} 在下方 · "
              f"{len(neg)}/{n} 净 GEX 为负 · {len(near_call)}/{n} 距 Call Wall ≤1%")
    # ★ 代理假设前提**只说一次**（原来在正/负 gamma 两条里各说一遍）。
    #   它是安全相关的限定，不能删 —— 但重复第二遍不增加信息。
    caveat = (f'<div class="note" style="margin-top:6px">'
              f'以上 gamma 性质的<b>前提</b>：{DEALER_ASSUMPTION}。'
              f'完整含义见 <a href="./glossary.html">口径与定义</a>。</div>')
    return ('<div class="card bar">'
            + _ph("结构要点")
            + f'<ul class="bul">{"".join(f"<li>{x}</li>" for x in li)}</ul>'
            + caveat + '</div>')


def _structural_facts_probe(views: dict[str, AssetView]) -> str:
    """给测试用：结构要点的**纯文本**（剥掉标签），好让禁令扫得到内容。"""
    return re.sub(r"<[^>]+>", "", _structure_points(views))


def _pct_change(closes: pd.Series | None) -> float | None:
    """最近两个收盘价之间的涨跌幅 —— 「当日」那一行。

    口径：拿价格序列里**最后两根**，不比对快照日。
    序列本身的最新一天就是数据能支撑的最后一天，写死一个日期去对齐
    反而会在停牌/节假日之后取到一根陈旧的收盘价。
    """
    if closes is None or len(closes) < 2:
        return None
    prev, last = float(closes.iloc[-2]), float(closes.iloc[-1])
    return (last / prev - 1.0) * 100.0 if prev else None


def _status_bar(views: dict[str, AssetView], payload: dict,
                closes_of=None) -> str:
    """顶部总览条 —— 参考图那种「一个判断 + 四个价格」的排布。

    版式上的一个刻意选择：**右格只放价格**，不放 VIX / 广度 / 最近墙。
    因为右格一旦混入异质指标，「一格 = 一个标的 = 一个价格」这个映射就断了，
    扫视时无法横向比较。VIX 与广度属于**判断的依据**，所以放进左格第二行 ——
    它们解释「为什么是这句结论」，而不是和结论并列。

    「当日」那一行由 `_pct_change` 从价格序列算，不写死日期。
    """
    ms = payload.get("market_state") or {}
    bpos, btot = ms.get("breadth_pos") or 0, ms.get("breadth_total") or 0
    closes_of = closes_of or (lambda _s: None)

    n_pos = sum(1 for v in views.values() if v.net_gex > 0)
    above = [s for s, v in views.items() if v.below_flip is False]
    below = [s for s, v in views.items() if v.below_flip is True]
    neg = [s for s, v in views.items() if v.net_gex <= 0]

    if n_pos == len(views):
        mood = "结构普遍稳定"
    elif n_pos == 0:
        mood = "结构普遍脆弱"
    else:
        mood = "结构分化"
    # ★ **判断一律不上色。** 绿/红只表示**符号**；「稳定 / 脆弱」是**评价**，
    # 而且是从 `MECHANICS`（自带「做市商代理假设」）推出来的。
    # 给一个有前提的推论上色，等于把它当无条件事实呈现。
    # 而且「稳定」「脆弱」两个字本身已经说完判断，颜色只是再说一遍。
    mood_cls = ""

    pos_txt = (f"{' / '.join(above)} 位于 Flip 上方" if above else "")
    if below:
        pos_txt = (pos_txt + "　" if pos_txt else "") + \
                  f"{' / '.join(below)} 位于 Flip 下方"
    if not pos_txt:
        pos_txt = "缺少 Gamma Flip，位置判不出来"

    bits = []
    vix = ms.get("vix")
    bits.append(f"VIX {_vix_plain(vix)}" if vix is not None else "VIX 未取到")
    bits.append(f"正 gamma 占比 {bpos}/{btot}" if btot >= BREADTH_MIN
                else f"正 gamma 占比 仅 {btot} 只")
    near = _nearest_plain(views)
    if near:
        bits.append(f"最近墙 {near}")
    if neg:
        bits.append(f"净 GEX 为负 {' / '.join(neg)}")

    cells = "".join(
        f'<div><div class="lb">{s}</div>'
        f'<div class="big num">{v.spot:,.2f}</div>'
        f'<div class="dl">{_chg_html(_pct_change(closes_of(s)))}</div></div>'
        for s, v in views.items())
    # ★ 列数**跟着标的数走**，不写死。
    #   写死 4 列时，第 5 个标的会自己换行，在总览条下面留一条空白格 ——
    #   那道缝比它想表达的「有 5 个标的」误导得多。
    return f"""<div class="status" style="grid-template-columns:1.66fr repeat({len(views)},1fr)">
<div><div class="lb">市场结构总览</div>
  <div class="big" style="color:var(--fg){mood_cls}">{mood}</div>
  <div class="dl">{pos_txt}<br>{' · '.join(bits)}</div></div>
{cells}
</div>"""


def _chg_html(chg: float | None) -> str:
    """涨跌幅那一行。

    ⚠️ 标签写「当日」而不是「较前收盘」：价格序列本身就是**日频**的，
    最后两根就是相邻两个交易日，所以「当日」和「较前收盘」指的是同一件事。
    短的那个在五张卡里省下的横向空间，正好留给标的代码。
    """
    if chg is None:
        return '<span class="dim">收盘</span>'
    cls = "pos" if chg >= 0 else "neg"
    return f'<span class="{cls} num">{chg:+.2f}%</span> <span class="dim">当日</span>'


def _nearest_plain(views: dict[str, AssetView]) -> str | None:
    """最近的一道墙 —— 纯文本，供状态条用（带颜色的版本在 `_nearest_html`）。"""
    best = None
    for s, v in views.items():
        for lvl, nm in ((v.call_wall, "Call"), (v.put_wall, "Put")):
            d = v.dist(lvl)
            if d is None:
                continue
            if best is None or abs(d) < abs(best[0]):
                best = (d, s, nm)
    return f"{best[1]} {best[2]} Wall {best[0]:+.2f}%" if best else None


def _nearest_html(views: dict[str, AssetView]) -> str:
    best = None
    for s, v in views.items():
        for lvl, nm in ((v.call_wall, "Call"), (v.put_wall, "Put")):
            d = v.dist(lvl)
            if d is None:
                continue
            if best is None or abs(d) < abs(best[0]):
                best = (d, s, nm, lvl)
    if best is None:
        return "—"
    d, s, nm, _lvl = best
    return (f'<span class="{"pos" if d >= 0 else "neg"} num">'
            f'{s} {nm} {d:+.2f}%</span>')


def _vix_plain(vix: float | None) -> str:
    """VIX 纯文本（含中文档位）—— 与 `_vix_html` 同一套阈值。"""
    if vix is None:
        return "未取到"
    try:
        from .digest import VIX_GRADES

        label = VIX_GRADES[-1][1]
        for sup, lb, _em in VIX_GRADES:
            if vix < sup:
                label = lb
                break
        return f"{vix:.2f} · {VIX_ZH.get(label, label)}"
    except Exception:  # noqa: BLE001
        return f"{vix:.2f}"


def _vix_html(vix: float | None) -> str:
    """VIX 分档 —— 阈值直接沿用 `digest.VIX_GRADES`，不另立一套。"""
    if vix is None:
        return '<span class="dim">未取到</span>'
    return _vix_plain(vix)



def _price_chart(v: AssetView, px: pd.DataFrame, chain: pd.DataFrame) -> str:
    """价格带 + GEX 横向条 —— **序列化成 HTML**。

    构图在 `_price_figure`，这里只负责序列化。分开是为了让
    「图上有没有多余的 / 空白的标注」这类问题**能被测试** ——
    否则只能靠人眼在图上看（`new text` 就是这么被发现的）。
    """
    try:
        fig = _price_figure(v, px, chain)
    except ImportError:
        return '<div class="err">未安装 plotly，无法绘制结构图。</div>'
    if fig is None:
        return '<div class="err">没有价格与链数据。</div>'
    return fig.to_html(full_html=False, include_plotlyjs=True,
                       config={"displayModeBar": False, "responsive": True})


def _price_figure(v: AssetView, px: pd.DataFrame, chain: pd.DataFrame):
    """价格带 + GEX 横向条，**共用同一根价格轴**。

    价格带的画法由数据决定（`has_ohlc`）：

        有 OHLC（ETF，futu）  → **蜡烛** —— 多一层「当日振幅」的信息
        只有 close（指数，CBOE）→ **折线** —— 画蜡烛等于假装有盘中信息

    两种情况共用同一套轴范围规则，所以视觉结构一致。

    没有可用数据时返回 `None`（由 `_price_chart` 转成错误提示）。
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    has_px = px is not None and not px.empty
    candles = has_ohlc(px)
    if not has_px and chain.empty:
        return None

    fig = make_subplots(rows=1, cols=2, shared_yaxes=True,
                        column_widths=[0.74, 0.26], horizontal_spacing=0.010)

    # ── ★ y 轴范围**只由价格决定** ─────────────────────────────────────
    # 旧版把 `spot ± 12%` 也并进范围，本想给 GEX 条留空间，实际后果是：
    #   45 日价格区间 7,552→7,799（跨度 247）
    #   落在 6,841→8,707（跨度 1,866）的轴上
    #   ⇒ **价格只占 13% 的高度**，被压成一条。
    # 正确做法与参考图一致：**范围由价格定，GEX 条反过来裁到价格范围内** ——
    # 这样两者高度天然对齐，也正是「一眼看出墙在价格的哪一段」的前提。
    c = px.tail(LOOKBACK_DAYS) if has_px else None
    if has_px:
        if candles:
            lo, hi = float(c["low"].min()), float(c["high"].max())
        else:
            lo, hi = float(c["close"].min()), float(c["close"].max())
    else:
        lo, hi = v.spot * 0.99, v.spot * 1.01

    # 关键位落在附近就并进来（否则墙的线画不出来）；**只并附近的** ——
    # 太远的位点会把轴重新撑开，等于把 bug 请回来。
    span = (hi - lo) or v.spot * 0.01
    for lvl in (v.zero_gamma, v.call_wall, v.put_wall, v.spot):
        if lvl is not None and lo - span * 0.6 <= lvl <= hi + span * 0.6:
            lo, hi = min(lo, lvl), max(hi, lvl)
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad

    if has_px:
        xs = [pd.Timestamp(d) for d in c.index]
        if candles:
            # ★ **蜡烛故意不上色** —— 涨跌靠「空心 / 实心」表达，不靠绿红。
            #
            # 为什么：同一张图里，右边 GEX 条的绿 = **正 gamma**，
            # 左边蜡烛的绿 = **涨** —— 同一个绿，两个含义，并排摆着。
            # 读者会以为它们相关，而它们毫无关系。
            #
            # 把绿/红**完整让给 gamma** 之后，「彩色 = gamma 结构」成为唯一读法，
            # 而涨跌由形状本身表达（这是蜡烛图最老也最清楚的约定）。
            fig.add_trace(go.Candlestick(
                x=xs, open=c["open"], high=c["high"], low=c["low"], close=c["close"],
                name="日线", showlegend=False,
                increasing=dict(line=dict(color=PALETTE["candle"], width=1),
                                fillcolor="rgba(0,0,0,0)"),      # 涨 = 空心
                decreasing=dict(line=dict(color=PALETTE["candle"], width=1),
                                fillcolor=PALETTE["candle"]),    # 跌 = 实心
                # 蜡烛的 hover 用「日期 + OHLC」——这正是蜡烛比折线多出来的信息
                hovertext=[f"{d:%m-%d}　开 {o:,.2f}　高 {h:,.2f}　"
                           f"低 {l:,.2f}　收 {cl:,.2f}"
                           for d, o, h, l, cl in zip(c.index, c["open"], c["high"],
                                                     c["low"], c["close"])],
                hoverinfo="text"), row=1, col=1)
        else:
            fig.add_trace(go.Scatter(
                x=xs, y=c["close"].to_numpy(), mode="lines",
                name="收盘价", line=dict(color=PALETTE["candle"], width=1.4),
                fill="tozeroy", fillcolor="rgba(127,140,133,.07)",
                hovertemplate="%{x|%m-%d}　%{y:,.2f}<extra></extra>"), row=1, col=1)

    # GEX 条：只画落在价格轴范围内的档位（裁到轴上，而不是撑开轴）
    if not chain.empty:
        g = chain.groupby("strike")["gex"].sum()
        g = g[(g.index >= lo) & (g.index <= hi)]
        if len(g):
            fig.add_trace(go.Bar(
                x=g.to_numpy() / 1e9, y=g.index.to_numpy(), orientation="h",
                name="GEX", marker=dict(
                    color=[PALETTE["pos"] if x >= 0 else PALETTE["neg"]
                           for x in g.to_numpy()],
                    line=dict(width=0)), opacity=.85, width=0.9,
                hovertemplate="行权价 %{y:,.0f}<br>GEX %{x:+.2f}B<extra></extra>"),
                row=1, col=2)

    # 关键位横线：两侧都画，标签贴在**价格区右侧**（不是最右边）
    #
    # ★ **Gamma Flip 用白（`--hi`），不用品牌层的金色。**
    #   它不是「第三种 gamma」—— 它是**分界**（净 GEX 穿越零的位置）。
    #   金色属于品牌层（页眉 / 章节号），一旦被拿来画一条数据线，
    #   「金色 = 导航」这条约定就破了，读者会以为它是第三类数据。
    for lvl, color, label in (
        (v.zero_gamma, PALETTE["hi"], "Gamma Flip"),
        (v.call_wall, PALETTE["pos"], "Call Wall"),
        (v.put_wall, PALETTE["neg"], "Put Wall"),
    ):
        if lvl is None:
            continue
        for col in (1, 2):
            # ⚠️ 标注参数**只在左图传**（原来两列都传，右图就冒出了 "new text"）。
            #
            # 踩过的坑：`annotation_text=None` **不足以**让 Plotly 跳过标注 ——
            # 只要还传了 `annotation_position` / `annotation_font` /
            # `annotation_bgcolor` 里的任何一个，它就会**建一个空标注对象**，
            # 然后回落到默认占位符 `"new text"`，在图上真的显示成一行
            # "new text"（那是 Plotly i18n 字典里的值）。
            #
            # 实测：传那三个参数 → 标注数 2，text 都是 None；
            #       一个都不传   → 标注数 0（正确）。
            #
            # ⇒ 要「不给标注」，必须**一个 annotation_* 都不传**，
            #   而不是把 text 设成 None。
            ann = {}
            if col == 1:
                ann = dict(annotation_text=f"{label} {lvl:,.0f}",
                           annotation_position="right",
                           annotation_font=dict(size=9, color=color),
                           annotation_bgcolor="rgba(8,12,10,.8)")
            fig.add_hline(y=lvl, line=dict(color=color, width=0.9, dash="dot"),
                          opacity=.75, row=1, col=col, **ann)

    fig.add_hline(y=v.spot, line=dict(color=PALETTE["hi"], width=1.1), row=1, col=1,
                  annotation_text=f"{v.spot:,.2f}", annotation_position="top left",
                  annotation_font=dict(size=9, color=PALETTE["hi"]),
                  annotation_bgcolor="rgba(8,12,10,.85)")

    fig.update_layout(
        height=340, margin=dict(l=52, r=62, t=6, b=4),
        paper_bgcolor=PALETTE["panel"], plot_bgcolor=PALETTE["panel"],
        font=dict(color=PALETTE["dim2"], size=10,
                  family='-apple-system,"PingFang SC",sans-serif'),
        showlegend=False, bargap=.1, hovermode="closest",
        yaxis=dict(range=[lo, hi]),
        # ⚠️ Candlestick 默认带一个 range slider，会把图挤掉一半 —— 必须关掉
        xaxis_rangeslider_visible=False,
    )
    # 去掉轴标题与重网格 —— 参考图那个高度上没有位置给装饰
    fig.update_xaxes(gridcolor=PALETTE["grid"], zeroline=False, row=1, col=1,
                     tickfont=dict(size=9), nticks=7)
    fig.update_xaxes(gridcolor=PALETTE["grid"], zeroline=True,
                     zerolinecolor=PALETTE["zero"],
                     tickfont=dict(size=9), nticks=4, row=1, col=2)
    fig.update_yaxes(gridcolor=PALETTE["grid"], zeroline=False, tickformat=",.0f",
                     tickfont=dict(size=9), nticks=8, row=1, col=1)

    return fig


def _gen(kind: str) -> str:
    """生成方式徽标。**每个文字块都必须挂一个** —— 分不清来源，就没法追责。"""
    return ('<span class="gen m">机器</span>' if kind == "m"
            else '<span class="gen a">人工</span>')


def _ph(title: str, kind: str = "m", right: str = "") -> str:
    """卡内小标题行 —— 标题在左，来源徽标在右。

    拆成独立函数是因为它出现七八次：每次都手写一遍 `ph/pt/pr` 三个类名，
    迟早有一处漏掉类名或者把徽标放到标题左边去。
    """
    r = right or _gen(kind)
    return f'<div class="ph"><span class="pt">{title}</span><span class="pr">{r}</span></div>'


def _fold(summary: str, body: str, *, hint: str = "", open_: bool = False) -> str:
    """折叠块 —— 默认收起，非必要不用看。

    这些内容是**可对账性的凭证**：需要时能查得到，不需要时不该占版面。

    ⚠️ **折起来，不是删掉。** 删掉就没人能复核了 —— 而这份报告整条
    「来源分离」的思路，前提就是**每句话都追得回出处**。
    """
    op = " open" if open_ else ""
    h = f'<span class="fold-hint">{hint}</span>' if hint else ""
    return (f'<details class="fold"{op}><summary>{summary}{h}</summary>'
            f'<div class="fold-body">{body}</div></details>')


def _facts_html(v: AssetView) -> str:
    """结构事实块 —— 全部机器生成（见 `AssetView.facts`）。"""
    rows = "".join(
        f'<div class="fr"><span class="k">{k}</span><span class="t">{t}</span></div>'
        for k, t in v.facts())
    return f'<div class="facts">{rows}</div>'


def _chart_header(v: AssetView) -> str:
    """图上方那一行内联关键位 —— 参考图 01 节的第一行。

    不用看右边面板就能读出「三条线各在哪个价位、离现价多远」，
    这是整张图最实用的一行。**全部机器生成**：价位来自关键位，
    百分比来自 `dist()`，没有任何一个数字是写出来的。
    """
    def pair(nm: str, lvl: float | None, color: str) -> str:
        if lvl is None:
            return ""
        d = v.dist(lvl)
        return (f'<span><span class="lb">{nm}</span> '
                f'<span class="num" style="color:{color};font-weight:600">'
                f'{lvl:,.2f}</span>'
                + (f' <span class="num dim">{d:+.2f}%</span>' if d is not None else "")
                + "</span>")

    return ('<div class="chhdr">'
            f'<span class="tk">{v.symbol}</span>'
            f'<span class="px num">{v.spot:,.2f}</span>'
            '<span class="lb">收盘参考价</span>'
            + pair("Gamma Flip", v.zero_gamma, "var(--hi)")
            + pair("Put Wall", v.put_wall, "var(--neg)")
            + pair("Call Wall", v.call_wall, "var(--pos)")
            + f'<span class="sp"></span>{_gen("m")}</div>')


def _side_panel(v: AssetView) -> str:
    """图右侧的「结构状态」窄面板 —— 价格距离是它的核心。

    参考图在这一格底部有一整段说明文字 ours 放的是 `MECHANICS[b]`：
    同一张表的同一句话，**查表来的**而不是散文 —— 它自带
    「做市商代理假设」的前提，所以每次出现都带边界。
    """
    gk = "pos" if v.net_gex >= 0 else "neg"
    rows = [
        ("距 Gamma Flip", _dist_html(v.flip_dist)),
        ("距 Put Wall", _dist_html(v.dist(v.put_wall))),
        ("距 Call Wall", _dist_html(v.dist(v.call_wall))),
    ]
    if v.move_ref():
        rows.append(("隐含 1 日波动",
                     f'<span class="num">±{v.move_ref():,.0f}</span> '
                     f'<span class="dim">±{v.move_ref() / v.spot * 100:.2f}%</span>'))
    body = "".join(
        f'<div class="r"><span class="k">{k}</span><span class="v">{x}</span></div>'
        for k, x in rows)
    say = MECHANICS.get(v.below_flip, "结构不明确：没有 Gamma Flip，判不出正负 gamma")
    return f"""<div class="card side">
<div class="hd1">{v.symbol} · 结构状态 {_gen("m")}</div>
<div class="big {gk}">{v.net_gex / 1e9:+,.2f}B</div>
<div class="sub">NET GEX / 净 Gamma 敞口</div>
<div class="hd">价格距离</div>
{body}
<div class="tags" style="margin-top:9px">
{_bdg(v.position_label())}{_bdg(v.wall_label())}</div>
<div class="say">{say}</div>
{_facts_html(v)}
{('<div class="err" style="margin-top:8px;font-size:9.5px;padding:7px 9px">'
  '⚠️ <b>符号矛盾</b>：净 GEX 的符号与「在 Flip 哪一侧」对不上。'
  'Gamma Flip 的定义就是净 GEX 归零处，两者本该同号 —— 不同号说明'
  '这一版快照本身有问题，<b>上面的状态判定与含义都不可信</b>，以数值为准。</div>')
 if v.sign_conflict else ''}
<div class="tiny">Cboe 报价 {v.asof} · DTE {v.dte_lo}–{v.dte_hi} 天</div>
</div>"""


def _shift_table(views: dict[str, AssetView], hist: pd.DataFrame,
                 day: date | None = None) -> str:
    """结构迁移：今天的距离 vs 上一交易日。"""
    day = day or pd.Timestamp(next(iter(views.values())).asof).date()
    rows = []
    for sym, v in views.items():
        prev = previous_summary(hist, sym, not_after=day)
        rows.append((sym, v, prev))

    if not any(p is not None for _s, _v, p in rows):
        return ('<div class="note dim">首日：库里还没有更早的交易日。'
                '对比项放在 <code>data/history/metrics.parquet</code>，'
                '由 dashboard 的 scheduler 每次 pull 追加一行 —— '
                '<b>不需要</b>依赖「昨天生成过报告」。明天起这里自动出对比。</div>'
                '<div class="note">⚠️ 墙位位移将用 <b>绝对墙</b>（不按现价切）。'
                '用相对墙的话，现价涨 1% 就会让「切分点上移 → argmax 换人」'
                '被记成「结构迁移」—— <b>实际持仓没动，只是尺子挪了</b>。</div>')

    th = ("<tr><th>标的</th><th>Flip 位移</th><th>Call Wall 位移</th>"
          "<th>净 GEX 变化</th><th>结构</th></tr>")
    body = []
    for sym, v, prev in rows:
        if prev is None:
            body.append(f"<tr><td>{sym}</td>"
                        f"<td colspan=4 class='dim'>首日，无对比</td></tr>")
            continue
        dz = (v.zero_gamma - prev["zero_gamma"]
              if v.zero_gamma is not None and pd.notna(prev.get("zero_gamma")) else None)
        # ★ 墙位位移一律用**绝对墙**。用相对墙的话，现价涨 1% 就会让
        # 「切分点上移 → argmax 换人」被记成「结构迁移」——
        # 实际持仓没动，只是尺子挪了（见 absolute_walls 的后果 A）。
        p_aw = prev.get("abs_call_wall")
        if p_aw is None or pd.isna(p_aw):
            p_aw = prev.get("call_wall")          # 旧版存档没有绝对墙，退回相对墙
        dcw = (v.abs_call_wall - float(p_aw)
               if v.abs_call_wall is not None and p_aw is not None
               and pd.notna(p_aw) else None)
        dn = v.net_gex - prev["net_gex"]
        d = ("<span class='dim'>—</span>" if dz is None else
             f"<span class='{'pos' if dz >= 0 else 'neg'} num'>{dz:+,.0f}</span>")
        dc = ("<span class='dim'>—</span>" if dcw is None else
              f"<span class='{'pos' if dcw >= 0 else 'neg'} num'>{dcw:+,.0f}</span>")
        moved = dz is not None and abs(dz) >= 5
        wall_moved = dcw is not None and abs(dcw) >= 5
        if wall_moved:
            vd = _bdg(f"墙移动 {abs(dcw):,.0f} 点")
        elif moved:
            vd = _bdg(f"Flip 移动 {abs(dz):,.0f} 点")
        else:
            vd = _bdg("结构稳定")
        body.append(f"<tr><td>{sym}</td><td>{d}</td><td>{dc}</td>"
                    f"<td>{_gex_html(dn)}</td><td>{vd}</td></tr>")
    return (f"<table><tbody>{th}{''.join(body)}</tbody></table>"
            '<div class="note">墙位位移用<b>绝对墙</b>（不按现价切）。'
            '用相对墙的话，现价涨 1% 就会让「切分点上移 → argmax 换人」'
            '被记成「结构迁移」—— 实际持仓没动，只是尺子挪了。</div>')


def _md(s: str) -> str:
    """把 `**强调**` 转成 `<b>`（归档 JSON 保持纯文本，只在展示层转换）。"""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", str(s))


def _expect_text(w: dict) -> str:
    """预期文案 —— **阈值写死在里面**，不留解释空间。"""
    n = int(w.get("expect_window") or VOL_WINDOW)
    if w.get("expect") == "vol_up":
        return f"触发后 {n} 日已实现波动 ≥ 触发前的 {VOL_RATIO}×"
    return "—"


def _settled_html(settled: list[dict], payload: dict | None = None) -> str:
    payload = payload or {}
    done = [s for s in settled if s.get("result") in ("hit", "miss")]
    br0 = payload.get("base_rate") or {}
    if not done:
        # ★ 首日也要出基准率 —— 它是**今天就能算**的，而且是整份报告里
        # 最重要的一个数：不看它，明天开始攒的命中率没有意义。
        brtxt = ""
        if br0.get("rate") is not None:
            brtxt = (f'<div class="note">无条件基准率（今天就能算）：'
                     f'{br0.get("symbol", "—")} 的全部历史 '
                     f'<b>{br0.get("total", 0):,}</b> 个 3 日窗口中，'
                     f'波动 ≥{VOL_RATIO}× 前 3 日的占 '
                     # 基准率**不上色** —— 它是个**参照系**，没有「好/坏」。
                     # 上绿会让读者以为「高基准率是好事」，而它的作用是**被减掉**。
                     f'<b>{br0["rate"] * 100:.1f}%</b>。<br>'
                     f'⚠️ <b>这就是为什么必须设对照组</b>：波动率有均值回复 —— '
                     f'低波动之后它自己会回升，<b>不需要任何一道墙被穿越</b>。'
                     f'从明天起，触发组的命中率要<b>减去这个基准率</b>才有意义；'
                     f'<b>超额接近 0 就说明这套东西什么都没预测到。</b></div>')
        else:
            brtxt = ('<div class="err" style="margin-top:8px">'
                     '⚠️ <b>基准率算不出来</b>（所有标的的价格历史都取不到）。'
                     '这时命中率<b>没有意义</b> —— 不要在没有基准率的情况下'
                     '解读结算数字。</div>')
        return ('<div class="note dim">首日：还没有上期清单可结算。'
                '从明天起，这里会列出「昨天说的对不对」。</div>' + brtxt)

    trig = sum(1 for s in done if s["result"] == "hit")
    # ★ 三组分开，绝不混：
    #   触发组   = 墙被穿越 → 参与「命中率」
    #   对照组   = 同期**没被穿越**的墙，用同样方法测 → 只用来算基准
    #   待观察   = 窗口没走完
    treated = [s for s in done if s.get("expect_result") in ("hit", "miss")]
    control = [s for s in done if s.get("expect_result") == "control"]
    exp_hit = sum(1 for s in treated if s["expect_result"] == "hit")
    ctl_hit = sum(1 for s in control if (s.get("expect_ratio") or 0) >= VOL_RATIO)
    br = payload.get("base_rate") or {}

    th = ("<tr><th>命题（已冻结版本）</th><th>结算日</th><th>触发</th>"
          "<th>实际</th><th>波动预期</th></tr>")
    body = []
    for s in done:
        er = s.get("expect_result")
        eb = {"hit": _bdg("成立", "p"), "miss": _bdg("落空", "n"),
              "control": _bdg("对照"), "pending": _bdg("待观察"),
              "caliber_mismatch": _bdg("口径不符", "n")}.get(er, _bdg("—"))
        body.append(
            f'<tr><td>{_md(s.get("text", ""))}'
            f'<span class="dim">　[冻结 {str(s.get("frozen_at", ""))[:16].replace("T", " ")}]</span></td>'
            f'<td>{s.get("settled_on", "—")}</td>'
            f'<td>{_bdg("触发", "p") if s["result"] == "hit" else _bdg("未触发")}</td>'
            f'<td class="dim">{s.get("witness", "")}</td>'
            f'<td>{eb}<div class="dim" style="font-size:9px">{s.get("expect_witness", "")}</div></td></tr>')

    # 分组统计：条数 ≠ 独立样本数（同源标的同日触发 = 一件事）
    by_day: dict[str, list[dict]] = {}
    for s in treated:
        by_day.setdefault(str(s.get("settled_on", "?")), []).append(s)
    day_hit = sum(
        1 for rs in by_day.values()
        if sum(1 for r in rs if r["expect_result"] == "hit") * 2 > len(rs))

    lines = [f'上期 <b>{len(done)}</b> 条，触发 <b>{trig}</b> 条'
             f'（触发率 {trig / len(done) * 100:.0f}%）']

    # ★★ 三方对照 —— 这才是「这套东西有没有信息量」的唯一答案
    if treated or control or br.get("rate") is not None:
        tbl = ['<table style="margin-top:6px"><tbody>'
               '<tr><th>组</th><th>样本</th><th>波动放大比例</th><th>说明</th></tr>']
        if treated:
            r = exp_hit / len(treated)
            tbl.append(f'<tr><td>触发组</td><td>{len(treated)}</td>'
                       f'<td class="pos num">{r * 100:.0f}%</td>'
                       f'<td class="dim">墙被穿越后</td></tr>')
        if control:
            r = ctl_hit / len(control)
            tbl.append(f'<tr><td>对照组</td><td>{len(control)}</td>'
                       f'<td class="num">{r * 100:.0f}%</td>'
                       f'<td class="dim">同期<b>没被穿越</b>的墙</td></tr>')
        if br.get("rate") is not None:
            tbl.append(f'<tr><td>无条件基准率</td><td>{br.get("total", 0)}</td>'
                       f'<td class="num">{br["rate"] * 100:.0f}%</td>'
                       f'<td class="dim">全部历史 3 日窗口</td></tr>')
        if treated and br.get("rate") is not None:
            edge = exp_hit / len(treated) - br["rate"]
            cls = "pos" if edge > 0.05 else ("neg" if edge < -0.05 else "dim")
            tbl.append(f'<tr><td><b>超额</b></td><td>—</td>'
                       f'<td class="{cls} num"><b>{edge * 100:+.0f}pp</b></td>'
                       f'<td class="dim"><b>触发组 − 基准率 = 真实信息量</b></td></tr>')
        tbl.append('</tbody></table>')
        lines.append("".join(tbl))

    if treated:
        lines.append(f'<b>按结算日归并后</b>：{len(by_day)} 组，'
                     f'组内多数命中 <b>{day_hit}</b> 组'
                     f'（<b>独立命中率 {day_hit / len(by_day) * 100:.0f}%</b>）')
    else:
        lines.append('<b>触发组的命中率还出不来</b> —— 触发后要再走满 '
                     f'{VOL_WINDOW} 个交易日才结算预期')

    lines.append('⚠️ <b>不看基准率，命中率没有意义。</b>波动率有均值回复 —— '
                 '低波动之后它自己会回升，<b>不需要任何一道墙被穿越</b>。'
                 '只看触发组，测的是「波动率的均值回复」，不是 GEX 机制。'
                 '<b>超额接近 0 就说明这套东西什么都没预测到。</b>')
    lines.append('⚠️ <b>对照组是同期没被穿越的墙</b> —— 同一批标的、同一段日期、'
                 '同一个度量，唯一不同的就是「墙有没有被穿越」。')
    lines.append(f'⚠️ <b>这 {len(done)} 条不是 {len(done)} 个独立样本</b>：'
                 '预期完全一样，而且 SPX/SPY/QQQ 同源、同一天一起触发 —— '
                 '<b>那是一件事，不是三件</b>。')
    lines.append(f'⚠️ 预期阈值写死为 <b>{VOL_RATIO}×</b>（触发后 {VOL_WINDOW} 日波动 '
                 f'÷ 触发前 {VOL_WINDOW} 日）。没阈值就只剩解释空间，'
                 '而解释空间是自欺的入口。')
    lines.append('⚠️ 命中率<b>不是胜率</b>，是「结构推理的命中率」，样本还小。')

    return (f'<table><tbody>{th}{"".join(body)}</tbody></table>'
            f'<div class="note">{"<br>".join(lines)}</div>')


def _watchlist_cards(watch: list[dict]) -> str:
    """观察清单 —— 参考图那种「编号 + 分类 + 标题 + 正文」的卡片。

    每条仍是一个**可判真假的命题**（明天查收盘价即可对账），
    版式换成卡片只是让它和右下的观察重点读起来是同一类东西。
    """
    if not watch:
        return '<div class="note dim">结构上没有可设阈值的位点。</div>'
    boxes = []
    for i, w in enumerate(watch, 1):
        d = w.get("dist")
        # 近距离的项标注出来 —— 它们触发率高，但信息量低（见上期结算的说明）
        easy = (d is not None and abs(d) <= 0.6)
        kind = "易触发 · 信息量低" if easy else "远 · 更有信息量"
        kc = "n" if easy else "p"
        boxes.append(f"""<div class="card bar focus {kc}">
<div class="fh"><span class="fn">{i:02d}</span>
  <span class="fs">{w.get("symbol", "")} · {kind}</span></div>
<div class="ttl">{_md(w.get("text", ""))}</div>
<div class="bd">{w.get("why", "")}　{_dist_html(d)}</div>
<div class="bd" style="margin-top:3px">预期：{_expect_text(w)}</div>
</div>""")
    ncol = min(4, max(1, len(boxes)))
    return (f'<div class="cols c4 wl" '
            f'style="grid-template-columns:repeat({ncol},1fr)">{"".join(boxes)}</div>')


def _provenance_html() -> str:
    """日报里的 Provenance 入口 —— 只留一个折叠块。

    内容全部是**每天一样**的（谁算的、谁写的，不随行情变），
    所以它不该占日报版面；完整表在 `glossary.html`（见 `glossary_html`）。
    """
    return _fold(f'展开完整对照表 {_gen("m")}',
                 f'<div class="card">{_provenance_body()}</div>',
                 hint="可对账性凭证 · 非必要不用看")


def _provenance_body() -> str:
    """逐块列明「机器算的 / 人写的」。

    为什么值得单独一节
    ------------------
    2026-10-06 那个反号 bug **只出现在人写的那一段散文里** ——
    同一张卡上，每一个机器算出来的数字都是对的（现价、Flip、两墙、距离
    全对），错的是把 `-1.31%` 读成「现价在 Flip 下方」的那句话。

    更麻烦的是：**散文错了没人能验证**。那个「下方」读起来和「−1.31%」
    一样自然。数字不一样 —— 数字可以拿计算器复核。

    所以这份报告把口径收紧成：**能算的一律算，算不出来的标「人工」，
    并且不许用推测词充数**。分不清来源，就不知道该复核哪里。
    """
    rows = [
        ("全部数值（现价 / 净 GEX / Flip / 两墙 / 距离 / P-C 比）", "m",
         "直接从快照与 metrics 管线算出"),
        ("位置判定（Flip 上/下方、贴近哪道墙）", "m",
         "两个价格直接比较，不用派生量的符号 —— 见 tests/test_report_signs.py"),
        ("状态含义（正/负 gamma 意味着什么）", "m",
         "查 MECHANICS / WALL_MECHANICS 两张固定表，只写 GEX 的**定义**，"
         "且**自带「做市商代理假设」的前提**"),
        ("墙稳健度（候选墙 #1 与 #2 的差）", "m",
         "候选墙各取前 3 名；分档阈值写死在模块常量里 —— "
         "墙位会不会跳，取决于 #1 赢得有多勉强"),
        ("结构要点与计数", "m", "分组与计数，从数据里数出来的"),
        ("观察清单（阈值 + 预期）", "m",
         f"阈值取自关键位；预期阈值**写死为 {VOL_RATIO}×**，不留解释空间"),
        ("上期结算（触发 / 波动预期 / 命中率）", "m",
         "用下一个交易日的收盘价与已实现波动算；"
         "**独立命中率按结算日归并** —— 条数 ≠ 独立样本数"),
        ("VIX 分档", "m", "沿用 digest.VIX_GRADES 的阈值，不另立一套"),
        ("观察重点", "a", "人工撰写的操作提示 —— **不可对账，别当结论**"),
        ("数据口径 / 边界说明", "a", "描述数据来源与已知偏差，不是结论"),
    ]
    body = "".join(
        f'<tr><td>{t} {_gen(k)}</td><td class="dim" style="text-align:left">{d}</td></tr>'
        for t, k, d in rows)
    inner = (f'<table><tbody>'
             f'<tr><th>内容块</th><th style="text-align:left">来源</th></tr>'
             f'{body}</tbody></table>'
             f'<div class="note">'
             f'机器块有一条**硬规矩**：不许出现'
             f'「{" / ".join(SPECULATIVE_WORDS[:6])}」这类推测词 —— '
             f'那是预测，机器给不出，<code>tests/test_report_text.py</code> 强制拦下。<br>'
             f'标「人工」的块<b>不参与任何结算</b>，只是给你看的。</div>')
    return inner


def glossary_html() -> str:
    """**口径与定义** —— 所有「每天完全一样」的文字，只在这里出现一次。

    为什么要有这一页
    ----------------
    判据只有一个问句：

        **这段文字今天变了，读者会做什么不同的事？**

        · 会        → 留在日报
        · 不会      → 移到这里
        · 不会且不可对账 → 删掉

    日报里凡是**每天一样**的文字，信息量是 **0**。它不提高理解，
    只提高「看起来严谨」的感觉 —— 而那种感觉是**有害的**：
    它让真正变化的几个数，淹没在不变的话里。

    另一条同样重要：

        **「可对账性」决定能不能信，不决定该不该看。**
        Provenance 的价值在**出错时追责**，不在平时阅读。
        把它放在日报里，等于每天把审计报告念一遍。

    ⇒ 所以这一页不是「藏起来」，是**移走**：随时查得到，但不占注意力。
    """
    mech = "".join(
        f'<div class="r"><span class="k">{WALL_MECHANICS.get(k, k)}</span>'
        f'<span class="v">{k}</span></div>'
        for k in WALL_MECHANICS)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>口径与定义 · 期权结构地图</title>
<style>{CSS}
.gloss h2{{font-size:12.5px;font-weight:600;margin:0 0 8px;
  padding-bottom:6px;border-bottom:1px solid var(--line)}}
.gloss .card{{margin-bottom:12px}}
</style></head><body><div class="wrap gloss">

{_page_header("Glossary", "口径与定义 · 不随行情变化，只此一份",
              "CONSTANT / 固定表", "GEX", "每天完全一致")}

<div class="note" style="margin:12px 0 16px">
  这一页的内容<b>每天完全一样</b>，所以从日报里移了出来。
  日报只留「今天变了的东西」——
  <b>凡是每天一样的文字，信息量是 0，却会把真正变化的几个数淹掉。</b>
</div>

<div class="card">
  <h2>状态含义 {_gen("m")}</h2>
  <div class="note" style="margin:0 0 8px">
    机器只查这两张<b>固定表</b>，不生成新句子 —— 表里只写 GEX 的<b>定义</b>，
    且<b>自带「做市商代理假设」的前提</b>。
  </div>
  <div class="rows">
    <div class="r"><span class="k">位于 Flip 上方</span>
      <span class="v">{MECHANICS[False]}</span></div>
    <div class="r"><span class="k">位于 Flip 下方</span>
      <span class="v">{MECHANICS[True]}</span></div>
  </div>
  <div class="chg" style="margin:11px 0 4px">墙标签 → 含义</div>
  <div class="rows">{mech}</div>
</div>

<div class="card">
  <h2>数据口径与定义 {_gen("a")}</h2>
  {_caliber_human()}
</div>

<div class="card">
  <h2>操作上要记住的几条 {_gen("a")}</h2>
  <ul class="bul">
    <li>钉在<b>距现价最近的那道墙</b>上。注意<b>相对墙的算法里含现价</b> ——
        它会随价格移动，所以<b>对比与结算只认绝对墙</b>（绝对墙 = 全曲线正/负 GEX 极值）</li>
    <li>跨越 <b>Gamma Flip</b> 会换掉正负 gamma 的性质</li>
    <li><b>同一快照内</b>墙位固定；但<b>跨快照墙会移动</b>（实测同一天两版 SPX
        两道墙各差 50 点），所以清单冻结在首次生成那一版</li>
    <li>清单按距离排序，<b>近距离的天然容易触发</b> —— 那只是「墙被碰过几次」，
        <b>不是命中率</b>。真正有信息量的是<b>远的</b>那几条</li>
    <li>看结算数字前<b>先看无条件基准率</b> —— 波动率有均值回复，
        低波动之后它自己会回升，<b>不需要任何一道墙被穿越</b></li>
  </ul>
</div>

<div class="card">
  <h2>生成方式 · 哪些是机器算的，哪些是人写的 {_gen("m")}</h2>
  {_provenance_body()}
</div>

{_page_footer("口径与定义 · 覆盖式写入，永远是最新常量表")}
</div></body></html>
"""


def _caliber_html(payload: dict, primary: AssetView, px: pd.DataFrame) -> str:
    """数据口径 —— **凡是能被数据判定真假的陈述，一律机器生成。**

    为什么这条规则是必要的：人工块里已经出现过**两处与实际输出矛盾** ——

        ① 口径里写「墙由 OI 决定，一天公布一次」
           ↔ 而定义是「现价**上方**/下方最密处」—— 含现价，不是纯 OI

        ② 口径里写「画折线而非蜡烛，因为指数只有收盘价」
           ↔ 而 02 已经是 **SPY 的蜡烛图**（含 OHLC）

    人工块说错没人拦，**而且用户看到「人工撰写」反而更信它**。
    ⇒ 所以把「本次运行的实际配置」全部改成从参数生成，只留下
      不可能被数据推翻的描述性文字。
    """
    cal = payload.get("caliber") or {}
    machine = [
        ("DTE 窗口", f"{primary.dte_lo}–{primary.dte_hi} 天"
                     f"（{cal.get('bucket', '—')} · 基准日 {cal.get('dte_basis', '—')}）"),
        ("主图标的", f"{primary.symbol} · 近 {LOOKBACK_DAYS} 个交易日 · "
                     f"{'蜡烛（含 OHLC）' if has_ohlc(px) else '折线（该标的只有收盘价）'}"),
        ("墙位算法", f"{cal.get('wall_algorithm', '—')} —— 绝对墙不按现价切，"
                     f"跨日对比与结算只用它"),
        ("IV 策略", f"{cal.get('iv_policy', '—')}"),
        ("预期阈值", f"{cal.get('vol_ratio', '—')}× / {cal.get('vol_window', '—')} 日"),
        ("口径指纹", f"{payload.get('caliber_id', '—')}"
                     f"<span class='dim'>　跨日对比时用它校验，不一致就拒绝结算</span>"),
    ]
    rows = "".join(
        f'<div class="r"><span class="k">{k}</span><span class="v">{v}</span></div>'
        for k, v in machine)
    return (f'<div class="card">'
            + _ph("本次运行配置")
            + f'<div class="rows">{rows}</div>'
            + f'<div class="note" style="margin-top:7px">'
              f'定义、公式与数据来源见 <a href="./glossary.html">口径与定义</a> —— '
              f'那些文字<b>每天完全一样</b>，信息量为 0，不该占日报版面。</div></div>')


def _caliber_human() -> str:
    """定义与来源 —— **每天完全一样**。

    所以它不进日报，只在 `glossary.html` 里出现一次。

    ⚠️ 这不是「藏起来」，是**移走**：可对账性要求它随时能查到，
    但不要求它每天占读者的注意力。
    """
    return (
        '<div class="note" style="margin:0">'
        "<b>期权链</b> CBOE 公开延迟接口（约 15 分钟延迟，无需账号）<br>"
        "<b>日线价格</b> 指数走 CBOE 官方收盘价 / FRED；ETF 走 Futu OpenD，"
        "<b>不复权</b> —— 期权行权价是绝对价，复权会让历史蜡烛相对墙的位置错位<br>"
        "<b>GEX</b> = OI × Γ × 100 × S² × 0.01，做市商视角（call 正 / put 负）<br>"
        "<b>关键位</b> 相对墙 = 现价上/下方最密处（<b>仅用于位置描述</b>）；"
        "绝对墙 = 全曲线正/负 GEX 极值（<b>用于对比与结算</b>）；"
        "Gamma Flip = 累计净 GEX 穿越零（线性插值）<br>"
        "<b>「距离」</b> = (关键位 − 现价) / 现价；正数在上方，负数在下方<br>"
        "<b>结构广度</b> = 成分股里正 gamma 的占比，<b>不是涨跌家数</b><br>"
        "<b>⚠️ 边界</b> 机械解读，不是入场信号，也不保证方向。"
        "</div>")


def _page_header(title_en: str, title_cn: str,
                 meta_k: str, meta_main: str, meta_s: str) -> str:
    """三段式页眉 —— 品牌 / 展示标题 / 元信息。

    为什么标题用衬线斜体、数据用无衬线：这一页里**读数**（价格、GEX、墙位）
    必须是最快的，而标题是「这是一份什么文件」的标识，不需要快。
    两种字型在视觉上分开，扫视时不会把标题里的字误当成读数。

    金色只出现在这一层（logo + 字标）—— 品牌层的边界就是页眉。
    """
    return f"""<header>
  <div class="brand">
    <span class="mark">&#10035;</span>
    <div><div class="wordmark">TREND ADAPTIVE</div>
      <div class="brand-sub">趋势自适应系统</div></div>
  </div>
  <div class="title">
    <div class="t-main">{title_en}</div>
    <div class="t-sub">{title_cn}</div>
  </div>
  <div class="meta">
    <div class="k">{meta_k}</div>
    <div class="d">{meta_main}</div>
    <div class="s">{meta_s}</div>
  </div>
</header>"""


def _page_footer(right: str = "") -> str:
    """页脚 —— 品牌 / 免责 / 生成时刻。免责声明居中，来源可追。"""
    r = f'<span class="r">{right}</span>' if right else ""
    return f"""<footer>
  <span class="fb">TREND ADAPTIVE <span style="letter-spacing:1px">/ 趋势自适应系统</span></span>
  <span>仅供信息参考，不构成投资建议</span>
  <span><a href="./glossary.html">口径与定义</a></span>
  <span>归档 <a href="./index.html">reports/gex/</a></span>
  {r}
</footer>"""


def render_html(views: dict[str, AssetView], hist: pd.DataFrame,
                payload: dict | None = None) -> str:
    payload = payload or {}
    # 主图标的选择：见 PRIMARY_CHART 的说明（默认 SPY，因为它有 OHLC 能画蜡烛）
    primary_key = (PRIMARY_CHART if PRIMARY_CHART in views
                   else ("SPY" if "SPY" in views else next(iter(views))))
    primary = views[primary_key]

    px_cache: dict[str, pd.DataFrame] = {}

    def _px(sym: str) -> pd.DataFrame:
        if sym not in px_cache:
            px_cache[sym] = load_price_history(sym)
        return px_cache[sym]

    def _cl(sym: str) -> pd.Series:
        d = _px(sym)
        return d["close"] if d is not None and not d.empty else pd.Series(dtype=float)

    px = _px(primary_key)
    snap = load_chain_snapshot(primary_key)
    chain = pd.DataFrame()
    if snap is not None:
        try:
            enriched = metrics.enrich(snap)
            chain = enriched[metrics.bucket_mask(
                enriched, BUCKET, datetime.now(ET).date())]
        except Exception as e:  # noqa: BLE001
            log.warning("report: 主图链路失败 — %s", e)

    cards = "".join(_asset_card(v, _cl(s)) for s, v in views.items())
    cross = cards + _structure_points(views)

    watch = payload.get("watchlist") or []
    settled = payload.get("settled") or []
    day = primary.asof[:10]
    now = datetime.now(ET)

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>期权结构地图 · {day}</title>
<style>{CSS}</style></head><body><div class="wrap">

{_page_header("Options Market Map", "期权结构地图 · 位置 与 Gamma 分布",
              "REVIEW / 复盘快照", day, "US MARKET / 收盘结构快照")}

{_status_bar(views, payload, _cl)}

{_section("01", "结构剖面", "Price & Gamma Profile",
          f'{_gen("m")} 自选 · Cboe 延迟期权快照 · DTE {primary.dte_lo}–'
          f'{primary.dte_hi} 天 · {primary_key} 近 {LOOKBACK_DAYS} 个交易日'
          f'{"蜡烛（含 OHLC）" if has_ohlc(px) else "折线（该标的只有收盘价）"} · 不复权')}
<div class="cols c2m">
  <div class="card" style="padding:0 4px 2px">
    {_chart_header(primary)}
    {_price_chart(primary, px, chain)}</div>
  {_side_panel(primary)}
</div>

{_section("02", "市场结构", "Market Structure",
          f'{_gen("m")} Cboe 延迟期权快照 · DTE {primary.dte_lo}–'
          f'{primary.dte_hi} 天 · 位置、墙与 GEX 独立描述')}
<div class="cols c5" style="grid-template-columns:repeat({len(views)},1fr) 1.2fr">{cross}</div>

{_section("03", "观察清单", "Watchlist",
          f'{_gen("m")} 冻结于 {str(payload.get("frozen_at", ""))[:16].replace("T", " ")} UTC · '
          f'数据 {primary.asof} · 结算窗口 = 下一个交易日')}
{_watchlist_cards(watch)}

{_section("04", "上期结算", "Settlement",
          f"{_gen('m')} 用下一个交易日收盘价结算 · 含对照组与无条件基准率")}
{_settled_html(settled, payload)}

{_section("05", "结构迁移 · 口径 · 来源", "Structure Shift · Caliber · Provenance",
          f'{_gen("m")} 全部机器生成 · 人工块不参与任何结算')}
<div class="cols c3" style="grid-template-columns:1.3fr 1fr 1.08fr">
  <div class="card">
    {_ph("结构迁移（对比上一交易日）", "m")}
    {_shift_table(views, hist)}
  </div>
  {_caliber_html(payload, primary, px)}
  <div class="card">
    {_ph("生成方式 · 哪些能复核", "m")}
    {_provenance_body()}
  </div>
</div>

{_page_footer(f"{day} 美东时间 {now.strftime('%H:%M')} · 每日自动生成")}
</div></body></html>"""


def render_index(days: list[str]) -> str:
    """归档页 —— 「每天一份攒起来」的样子。"""
    items = "".join(
        f'<li><a href="./{d}.html">{d}</a> '
        f'<span class="dim">· <a class="dim" href="./{d}.json">json</a></span></li>'
        for d in sorted(days, reverse=True))
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>期权结构地图 · 归档</title><style>{CSS}</style></head><body><div class="wrap">
{_page_header("Archive", "期权结构地图 · 归档索引",
              "ARCHIVE / 归档", f"{len(days)} 份", "每日一份 · 攒起来")}
<div class="card" style="margin-top:14px">
{_ph("全部报告")}
<ul class="bul" style="line-height:2.1">{items or '<li class="dim">还没有报告</li>'}</ul>
</div>
{_page_footer("最新一份见 latest.html")}
</div></body></html>"""


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description="生成每日「期权结构地图」中文报告")
    ap.add_argument("--symbols", nargs="*", default=list(CROSS_ASSETS))
    ap.add_argument("--out", type=Path, default=REPORT_DIR)
    ap.add_argument("--no-write", action="store_true", help="只打印摘要，不落盘")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s")

    p = generate(args.symbols, out_dir=args.out, write=not args.no_write)
    print(json.dumps(p["distances"], ensure_ascii=False, indent=2, default=str))
    if not args.no_write:
        print(f"\n✅ 已写 {args.out}/")


if __name__ == "__main__":
    main()
