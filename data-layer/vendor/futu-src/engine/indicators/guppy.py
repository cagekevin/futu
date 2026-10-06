"""顾比线突破 —— 全序列版（一次算完整条序列）

顾比线用**通达信原式**（见 `tdx.GUPPY`）：

    GB_T    := LLV(L, 61);
    GB_LINE := REF(H, BARSLAST(L = GB_T));

即「**最近一次创 N 日新低**那根 K 线的最高价」。

关键：`BARSLAST` 是在**整条历史**里往回找，不受 61 根窗口限制 ——
只要没再创新低，那根就一直有效，哪怕它早已滑出窗口。
这正是「没跌破上一次最低价就不换」这条规则的写法。

（对比：有状态引擎把底座写成「最近 N 根窗口里最低那根」，老底座滑出窗口后
 会被强制换掉，还要额外判 `isExpired`。那是另一种口径，不是通达信这个。）

本模块在这条线上逐根推进「突破」：

    底座换了一根  ⇒  解锁
    没锁 且 收盘上穿顾比线  ⇒  记一次突破，上锁

**不做**「周期结束 / 结构失效」的判定 —— 只关心突破。

⚠️ 预热：`LLV` 在序列开头是截断窗口，前 period 根内算出的顾比线不可信。
   调用方请保证「关心的区间」之前至少有 period 根历史。
"""
from . import tdx

NAN = float("nan")
PERIOD = 61
EPS = 1e-9


def scan(bars, period=PERIOD, eps=EPS):
    """逐根推进，返回 (breakouts, lines)

    bars      : 升序 K 线，每根需含 'date' / 'low' / 'high' / 'close'
    breakouts : [{'date','index','close','guppy','base_low','base_date'}]
    lines     : {'guppy': [...], 'base_low': [...], 'base_index': [...]}，与 bars 逐根对齐
    """
    n = len(bars)
    if n == 0:
        return [], {"guppy": [], "base_low": [], "base_index": []}

    low = [b["low"] for b in bars]
    high = [b["high"] for b in bars]
    close = [b["close"] for b in bars]

    # 顾比线 = 最近一次「创 period 日新低」那根的 high
    gb_t = tdx.llv(low, period)
    k = tdx.barslast(tdx.EQ(low, gb_t))  # 距那根几根
    guppy = tdx.refv(high, k)
    base_low = tdx.refv(low, k)
    base_index = [i - int(round(k[i])) for i in range(n)]

    breakouts = []
    locked = False
    prev_base = -1

    for i in range(n):
        if base_index[i] != prev_base:
            locked = False  # 底座换了一根 ⇒ 解锁
            prev_base = base_index[i]

        if i > 0 and not locked:
            # 收盘上穿顾比线：今天在上、昨天在下
            if close[i] > guppy[i] + eps and close[i - 1] <= guppy[i - 1] + eps:
                locked = True
                b = base_index[i]
                breakouts.append({
                    "date": bars[i]["date"],
                    "index": i,
                    "close": close[i],
                    "guppy": guppy[i],
                    "base_low": low[b],
                    "base_date": bars[b]["date"],
                })

    return breakouts, {"guppy": guppy, "base_low": base_low, "base_index": base_index}


def line(bars, period=PERIOD):
    """只要顾比线本身（等价于 `tdx.GUPPY`）"""
    return scan(bars, period=period)[1]["guppy"]
