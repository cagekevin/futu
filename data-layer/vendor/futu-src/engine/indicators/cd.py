"""CD 指标 —— MACD 底部背离

公式来源：用户提供的通达信公式（原样转写，变量名保留 _CD 后缀便于对照）
唯一被 DRAWTEXT 画出的信号是 DXDX_CD（在最低价画 ▲）。

    D  := EMA(C,12) - EMA(C,26)              DIF
    A  := EMA(D,9)                           DEA
    M  := (D - A) * 2                        MACD 柱
    N1 := BARSLAST(M 由上转下)                 距最近一次死叉
    MM1:= BARSLAST(M 由下转上)                 距最近一次金叉

    CC1 = LLV(C, N1+1)      本段下跌最低收盘      DIFL1 = LLV(D, N1+1)
    CC2 = REF(CC1, MM1+1)   上一段               DIFL2 = REF(DIFL1, MM1+1)
    CC3 = REF(CC2, MM1+1)   上上段               DIFL3 = REF(DIFL2, MM1+1)
    CH1/2/3、DIFH1/2/3 取段内最高（本公式未用到，保留以便扩展）

    AAA := CC1 < CC2 且 DIFL1 > DIFL2 且 M前值<0 且 D<0     价格新低、DIF 抬高 → 底背离
    BBB := CC1 < CC3 且 DIFL1 < DIFL2 且 DIFL1 > DIFL3 且 M前值<0 且 D<0   更深一层背离
    CCC := (AAA 或 BBB) 且 D<0                              底背离成立
    LLL := REF(CCC,1)=0 且 CCC                              背离「首次」出现
    XXX := 背离失效
    JJJ := REF(CCC,1) 且 |REF(D,1)| >= |D|*1.01             背离后 DIF 继续下行 1% 以上
    DXDX:= REF(JJJ,1)=0 且 JJJ                              ← 画 ▲ 的信号（JJJ 首次成立）

CH*/DIFH* 与 DXX_CD/DJXX_CD 一并算出并返回，便于后续调整筛选口径。
"""

from . import tdx as T


def calc_cd(bars):
    """输入 bars: [{'date':int,'open':..,'high':..,'low':..,'close':..,'volume':..}, ...]（按日期升序）"""
    C = [b["close"] for b in bars]

    D = T.SUB(T.ema(C, 12), T.ema(C, 26))  # DIF
    A = T.ema(D, 9)  # DEA
    M = T.SCALE(T.SUB(D, A), 2)  # MACD 柱

    M1 = T.ref(M, 1)
    N1 = T.barslast(T.AND(T.GE(M1, 0), T.LT(M, 0)))  # 死叉
    MM1 = T.barslast(T.AND(T.LE(M1, 0), T.GT(M, 0)))  # 金叉
    N1p = [v + 1 for v in N1]
    MM1p = [v + 1 for v in MM1]

    CC1 = T.llv(C, N1p)
    CC2 = T.refv(CC1, MM1p)
    CC3 = T.refv(CC2, MM1p)

    DIFL1 = T.llv(D, N1p)
    DIFL2 = T.refv(DIFL1, MM1p)
    DIFL3 = T.refv(DIFL2, MM1p)

    CH1 = T.hhv(C, MM1p)
    CH2 = T.refv(CH1, N1p)
    CH3 = T.refv(CH2, N1p)

    DIFH1 = T.hhv(D, MM1p)
    DIFH2 = T.refv(DIFH1, N1p)
    DIFH3 = T.refv(DIFH2, N1p)

    AAA = T.AND(T.LT(CC1, CC2), T.GT(DIFL1, DIFL2), T.LT(M1, 0), T.LT(D, 0))
    BBB = T.AND(T.LT(CC1, CC3), T.LT(DIFL1, DIFL2), T.GT(DIFL1, DIFL3), T.LT(M1, 0), T.LT(D, 0))
    CCC = T.AND(T.OR(AAA, BBB), T.LT(D, 0))

    CCC1 = T.ref(CCC, 1)
    LLL = T.AND(T.NOT(CCC1), CCC)

    XXX = T.OR(
        T.AND(T.ref(AAA, 1), T.LE(DIFL1, DIFL2), T.LT(D, A)),
        T.AND(T.ref(BBB, 1), T.LE(DIFL1, DIFL3), T.LT(D, A)),
    )

    D1 = T.ref(D, 1)
    JJJ = T.AND(CCC1, T.GE(T.ABS(D1), T.SCALE(T.ABS(D), 1.01)))
    BLBL = T.AND(T.ref(JJJ, 1), CCC, T.LE(T.SCALE(T.ABS(D1), 1.01), T.ABS(D)))

    JJJ1 = T.ref(JJJ, 1)
    DXDX = T.AND(T.NOT(JJJ1), JJJ)  # ← 画 ▲ 的信号

    DJGXX = T.AND(
        T.OR(T.LT(C, CC2), T.LT(C, CC1)),
        T.OR(T.refv(JJJ, MM1p), T.refv(JJJ, MM1)),
        T.NOT(T.ref(LLL, 1)),
        T.GE(T.count(JJJ, 24), 1),
    )
    DJXX = T.AND(T.NOT(T.GE(T.count(T.ref(DJGXX, 1), 2), 1)), DJGXX)
    DXX = T.AND(T.OR(XXX, DJXX), T.NOT(CCC))

    return {
        "D": D, "A": A, "M": M, "N1": N1, "MM1": MM1,
        "CC1": CC1, "CC2": CC2, "CC3": CC3,
        "DIFL1": DIFL1, "DIFL2": DIFL2, "DIFL3": DIFL3,
        "CH1": CH1, "CH2": CH2, "CH3": CH3,
        "DIFH1": DIFH1, "DIFH2": DIFH2, "DIFH3": DIFH3,
        "AAA": AAA, "BBB": BBB, "CCC": CCC, "LLL": LLL, "XXX": XXX,
        "JJJ": JJJ, "BLBL": BLBL, "DXDX": DXDX,
        "DJGXX": DJGXX, "DJXX": DJXX, "DXX": DXX,
    }
