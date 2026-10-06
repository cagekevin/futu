"""通达信公式函数子集 —— 纯标准库，无第三方依赖

约定（与通达信一致）：
  · 序列为 list[float]，长度相同；NaN 表示无效值
  · 布尔用 1/0 表示；NaN 参与条件判断时视作「不成立」
  · 二元运算支持「数组 vs 标量」，标量自动广播
  · BARSLAST 若条件从未成立，返回 i+1（视作「很久以前」），
    这样下游 LLV/HHV 退化成「全窗口」，不会产生 NaN 断链

原项目已实现：EMA / REF / REFV(变量位移) / LLV / HHV / BARSLAST / COUNT
              AND / OR / NOT / GT / LT / GE / LE / EQ / ADD / SUB / MUL / ABS / SCALE

本项目追加（为了能跑更多通达信公式；语义对齐 vendor/futu_algo/indicators/tdx.py 的 pandas 版）：
    基础：MA / SMA / SUM / WMA / STD / IF / MAX / MIN / CROSS / EVERY / EXIST
    指标：MACD / KDJ / RSI / DMI / BOLL / ATR / ZIG

为什么以 list 版为基础合并，而不是用 futu_algo 的 pandas 版：
    futu_algo 版的 LLV/REF 只接受**标量**窗口/位移，而 CD 这类公式需要
    「逐根不同的窗口/位移」（如 LLV(C, N1+1)，N1 本身是序列）—— 只有 list 版支持。
"""

import math

NAN = float("nan")


def is_num(v):
    """有限数值（排除 NaN / None / 非数字）"""
    return isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v))


def _as_list(v, n):
    return v if isinstance(v, list) else [v] * n


# ---------------------------------------------------------------- 逻辑
def AND(*arrs):
    n = len(arrs[0])
    return [1 if all(a[i] == 1 for a in arrs) else 0 for i in range(n)]


def OR(*arrs):
    n = len(arrs[0])
    return [1 if any(a[i] == 1 for a in arrs) else 0 for i in range(n)]


def NOT(a):
    return [0 if v == 1 else 1 for v in a]


# ---------------------------------------------------------------- 比较（NaN 一律判否）
def _cmp(a, b, f):
    n = len(a) if isinstance(a, list) else len(b)
    aa, bb = _as_list(a, n), _as_list(b, n)
    return [1 if is_num(x) and is_num(y) and f(x, y) else 0 for x, y in zip(aa, bb)]


def GT(a, b):
    return _cmp(a, b, lambda x, y: x > y)


def LT(a, b):
    return _cmp(a, b, lambda x, y: x < y)


def GE(a, b):
    return _cmp(a, b, lambda x, y: x >= y)


def LE(a, b):
    return _cmp(a, b, lambda x, y: x <= y)


def EQ(a, b):
    return _cmp(a, b, lambda x, y: x == y)


# ---------------------------------------------------------------- 算术
def _arith(a, b, f):
    n = len(a) if isinstance(a, list) else len(b)
    aa, bb = _as_list(a, n), _as_list(b, n)
    return [f(x, y) for x, y in zip(aa, bb)]


def ADD(a, b):
    return _arith(a, b, lambda x, y: x + y)


def SUB(a, b):
    return _arith(a, b, lambda x, y: x - y)


def MUL(a, b):
    return _arith(a, b, lambda x, y: x * y)


def ABS(a):
    return [abs(v) for v in a]


def SCALE(a, k):
    return [v * k for v in a]


# ---------------------------------------------------------------- 序列函数
def ema(x, n):
    """EMA(X, N)：alpha = 2/(N+1)，初值取首个有效值"""
    a = 2.0 / (n + 1)
    out = [NAN] * len(x)
    prev = NAN
    for i, v in enumerate(x):
        if not is_num(v):
            out[i] = prev
            continue
        prev = a * v + (1 - a) * prev if is_num(prev) else v
        out[i] = prev
    return out


def ref(x, k):
    """REF(X, K)：向前取 K 根前的值（K 为标量）"""
    out = [NAN] * len(x)
    for i in range(k, len(x)):
        out[i] = x[i - k]
    return out


def refv(x, ks):
    """REFV(X, K[])：位移量逐根不同（对应 REF(X, 变量)）"""
    out = [NAN] * len(x)
    for i, k in enumerate(ks):
        if is_num(k) and k >= 0 and i - k >= 0:
            out[i] = x[i - int(round(k))]
    return out


def _window(x, ns, pick, seed):
    """LLV/HHV 共用：窗口长度可逐根不同（数组或标量）"""
    out = [NAN] * len(x)
    for i in range(len(x)):
        n = ns[i] if isinstance(ns, list) else ns
        if not is_num(n) or n < 1:
            continue
        start = max(0, i - int(round(n)) + 1)
        acc, any_ = seed, False
        for j in range(start, i + 1):
            if not is_num(x[j]):
                continue
            acc = pick(acc, x[j]) if any_ else x[j]
            any_ = True
        if any_:
            out[i] = acc
    return out


def llv(x, ns):
    return _window(x, ns, min, float("inf"))


def hhv(x, ns):
    return _window(x, ns, max, float("-inf"))


def barslast(c):
    """BARSLAST(C)：距上一次 C 成立的周期数（当根成立为 0）"""
    out = [NAN] * len(c)
    last = -1
    for i, v in enumerate(c):
        if v == 1:
            last = i
        out[i] = i + 1 if last < 0 else i - last
    return out


def count(c, n):
    """COUNT(C, N)：最近 N 根内 C 成立的次数"""
    out = [NAN] * len(c)
    for i in range(len(c)):
        start = max(0, i - n + 1)
        out[i] = sum(1 for j in range(start, i + 1) if c[j] == 1)
    return out


# ======================================================================
# 本项目追加
#
# 命名：TDX 原名一律**大写**，避免和 Python 内置的 sum/max/min/if 冲突
#       （注意本文件上面的 count() 用了内置 sum，所以不能定义小写的 sum）。
# NaN 语义对齐 futu_algo 的 pandas 版：
#   · 窗口函数（MA/SUM/WMA/STD）窗口内任一 NaN ⇒ 结果 NaN（= min_periods=n）
#   · MAX/MIN 任一为 NaN ⇒ 结果 NaN（= skipna=False）
# ======================================================================


# ---------------------------------------------------------------- 基础函数
def MA(x, n):
    """MA(X, N)：简单移动平均，前 N-1 根为 NaN"""
    out = [NAN] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1 : i + 1]
        if all(is_num(v) for v in w):
            out[i] = math.fsum(w) / n
    return out


def SMA(x, n, m=1):
    """SMA(X, N, M)：TDX 的**递归平滑** Y = (M*X + (N-M)*Y') / N

    ⚠️ 这不是简单平均。KDJ / RSI 等公式依赖它；当成 rolling mean 算会得到不同数值
    （futu_algo 的注释专门警告过这点）。首根用 X 播种。
    """
    if not 0 < m <= n:
        raise ValueError(f"SMA 要求 0 < M <= N，收到 N={n}, M={m}")
    a = m / n
    out = [NAN] * len(x)
    prev = NAN
    for i, v in enumerate(x):
        if not is_num(v):
            out[i] = prev
            continue
        prev = a * v + (1 - a) * prev if is_num(prev) else v
        out[i] = prev
    return out


def SUM(x, n):
    """SUM(X, N)：最近 N 根求和；N=0 表示从第一根起累计"""
    out = [NAN] * len(x)
    if n == 0:
        acc = 0.0
        for i, v in enumerate(x):
            if is_num(v):
                acc += v
            out[i] = acc
        return out
    for i in range(n - 1, len(x)):
        w = x[i - n + 1 : i + 1]
        if all(is_num(v) for v in w):
            out[i] = math.fsum(w)
    return out


def WMA(x, n):
    """WMA(X, N)：加权移动平均，权重 1..N（最新一根权重最大）"""
    weights = list(range(1, n + 1))
    total = sum(weights)
    out = [NAN] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1 : i + 1]
        if all(is_num(v) for v in w):
            out[i] = math.fsum(v * wt for v, wt in zip(w, weights)) / total
    return out


def STD(x, n):
    """STD(X, N)：样本标准差（ddof=1，对齐 pandas rolling().std()）"""
    out = [NAN] * len(x)
    if n < 2:
        return out
    for i in range(n - 1, len(x)):
        w = x[i - n + 1 : i + 1]
        if all(is_num(v) for v in w):
            mean = math.fsum(w) / n
            out[i] = math.sqrt(math.fsum((v - mean) ** 2 for v in w) / (n - 1))
    return out


def IF(c, a, b):
    """IF(C, A, B)：C 为 1 取 A，否则取 B（数组/标量皆可）"""
    n = len(c)
    aa, bb = _as_list(a, n), _as_list(b, n)
    return [aa[i] if c[i] == 1 else bb[i] for i in range(n)]


def MAX(a, b):
    """MAX(A, B)：逐根取大；任一为 NaN 则结果 NaN"""
    return _arith(a, b, lambda x, y: max(x, y) if is_num(x) and is_num(y) else NAN)


def MIN(a, b):
    """MIN(A, B)：逐根取小；任一为 NaN 则结果 NaN"""
    return _arith(a, b, lambda x, y: min(x, y) if is_num(x) and is_num(y) else NAN)


def CROSS(a, b):
    """CROSS(A, B)：A 上穿 B —— 本根 A>B 且上一根 A<=B"""
    return AND(_cmp(a, b, lambda x, y: x > y), ref(_cmp(a, b, lambda x, y: x <= y), 1))


def EVERY(c, n):
    """EVERY(C, N)：最近 N 根 C 全部成立（不足 N 根时为 NaN）"""
    out = [NAN] * len(c)
    for i in range(n - 1, len(c)):
        out[i] = 1 if all(c[j] == 1 for j in range(i - n + 1, i + 1)) else 0
    return out


def EXIST(c, n):
    """EXIST(C, N)：最近 N 根内 C 至少成立一次"""
    out = [NAN] * len(c)
    for i in range(len(c)):
        start = max(0, i - n + 1)
        out[i] = 1 if any(c[j] == 1 for j in range(start, i + 1)) else 0
    return out


# ---------------------------------------------------------------- 常用指标
def MACD(close, fast=12, slow=26, signal=9):
    """TDX MACD：DIF / DEA / MACD柱(=2*(DIF-DEA))，返回 (D, A, M) 三个序列"""
    d = SUB(ema(close, fast), ema(close, slow))
    a = ema(d, signal)
    return d, a, SCALE(SUB(d, a), 2)


def KDJ(high, low, close, n=9, m1=3, m2=3):
    """TDX KDJ：RSV=(C-LLV(L,N))/(HHV(H,N)-LLV(L,N))*100，K=SMA(RSV,M1,1)，D=SMA(K,M2,1)"""
    llv_, hhv_ = llv(low, n), hhv(high, n)
    span = SUB(hhv_, llv_)
    rsv = [NAN if not (is_num(c) and is_num(lo) and is_num(sp) and sp != 0)
           else (c - lo) / sp * 100 for c, lo, sp in zip(close, llv_, span)]
    k = SMA(rsv, m1, 1)
    d = SMA(k, m2, 1)
    return k, d, SUB(SCALE(k, 3), SCALE(d, 2))


def RSI(close, n=6):
    """TDX RSI：UP=SMA(MAX(C-REF(C,1),0),N,1)，TOTAL=SMA(ABS(C-REF(C,1)),N,1)，RSI=UP/TOTAL*100"""
    lc = ref(close, 1)
    diff = SUB(close, lc)
    up = SMA(MAX(diff, 0.0), n, 1)
    total = SMA(ABS(diff), n, 1)
    return [NAN if not (is_num(u) and is_num(t) and t != 0) else u / t * 100 for u, t in zip(up, total)]


def DMI(high, low, close, n=14, m=6):
    """TDX DMI：返回 (PDI, MDI, ADX, ADXR)"""
    lc = ref(close, 1)
    tr = MAX(MAX(SUB(high, low), ABS(SUB(high, lc))), ABS(SUB(lc, low)))
    mtr = SUM(tr, n)
    hd = SUB(high, ref(high, 1))
    ld = SUB(ref(low, 1), low)
    dmp = SUM(IF(AND(GT(hd, 0), GT(hd, ld)), hd, 0.0), n)
    dmm = SUM(IF(AND(GT(ld, 0), GT(ld, hd)), ld, 0.0), n)
    pdi = [NAN if not (is_num(a) and is_num(b) and b != 0) else a * 100 / b for a, b in zip(dmp, mtr)]
    mdi = [NAN if not (is_num(a) and is_num(b) and b != 0) else a * 100 / b for a, b in zip(dmm, mtr)]
    # ADX = MA(|MDI-PDI| / (MDI+PDI) * 100, M) —— 分母是两者之和，别写成只除 PDI
    adx = MA([NAN if not (is_num(a) and is_num(b) and (a + b) != 0) else abs(a - b) / (a + b) * 100
              for a, b in zip(mdi, pdi)], m)
    adxr = SCALE(ADD(adx, ref(adx, m)), 0.5)
    return pdi, mdi, adx, adxr


def BOLL(close, n=20, k=2.0):
    """TDX BOLL：中轨=MA(C,N)，上下轨=中轨 ± K×**总体**标准差（ddof=0，注意不是 STD 的 ddof=1）"""
    mid = MA(close, n)
    out_up, out_dn = [NAN] * len(close), [NAN] * len(close)
    for i in range(n - 1, len(close)):
        w = close[i - n + 1 : i + 1]
        if all(is_num(v) for v in w):
            mean = math.fsum(w) / n
            dev = math.sqrt(math.fsum((v - mean) ** 2 for v in w) / n)
            out_up[i] = mid[i] + k * dev
            out_dn[i] = mid[i] - k * dev
    return mid, out_up, out_dn


def ATR(high, low, close, n=14):
    """ATR：真实波幅 TR 的 N 日简单均值"""
    lc = ref(close, 1)
    tr = MAX(MAX(SUB(high, low), ABS(SUB(high, lc))), ABS(SUB(lc, low)))
    return MA(tr, n)


# ---------------------------------------------------------------- 顾比线
def guppy_base(low, n=61):
    """底座：最近 N 根里最低那根的「最低价」及其「距今天数」。

    返回 (底座最低价, 底座位置)。并列最低时 BARSLAST 取**最近一次**成立，
    即取后面那根 —— 与通达信 `BARSLAST(L = GB_T)` 一致。
    """
    gb_t = llv(low, n)
    return gb_t, barslast(EQ(low, gb_t))


def GUPPY(low, high, n=61):
    """顾比线（GUCCI LINE）—— 通达信原式，逐行对应：

        GB_T    := LLV(L, 61);                  →  llv(low, n)
        GB_LINE := REF(H, BARSLAST(L = GB_T));  →  refv(high, 底座位置)

    含义：**最近 N 根里最低那根 K 线的最高价**。
    无状态、每根都重算；N 根前的低点滑出窗口后会自动跳变。
    """
    _, k = guppy_base(low, n)
    return refv(high, k)


# ---------------------------------------------------------------- ZIG（未来函数）
def zig_pivots(values, pct):
    """ZIG 的转折点下标（pct 为反转百分比阈值）。

    第一根和最后一根永远是转折点；**未走完那一段的极值也是转折点** ——
    这正是它「每来一根新 K 线就可能重绘」的原因。
    """
    n = len(values)
    if n == 0:
        return []
    t = pct / 100.0
    pivots, trend, ext = [0], 0, 0
    for i in range(1, n):
        x = values[i]
        if not is_num(x):
            continue
        if trend == 0:
            if x >= values[0] * (1 + t):
                trend, ext = 1, i
            elif x <= values[0] * (1 - t):
                trend, ext = -1, i
        elif trend == 1:
            if x >= values[ext]:
                ext = i
            elif x <= values[ext] * (1 - t):
                pivots.append(ext)
                trend, ext = -1, i
        else:
            if x <= values[ext]:
                ext = i
            elif x >= values[ext] * (1 + t):
                pivots.append(ext)
                trend, ext = 1, i
    if trend != 0 and ext != pivots[-1]:
        pivots.append(ext)
    if pivots[-1] != n - 1:
        pivots.append(n - 1)
    return pivots


def ZIG(x, pct):
    """TDX ZIG(3, N)：转折点之间线性插值。

    ⚠️ **未来函数** —— 第 i 根的值依赖 i 之后的数据。直接拿去回测会虚高，
    要用 point_in_time()（见 py/indicators.py）逐根回放。
    """
    out = [NAN] * len(x)
    pivots = zig_pivots(x, pct)
    if not pivots:
        return out
    if len(pivots) == 1:
        out[pivots[0]] = x[pivots[0]]
        return out
    for k in range(len(pivots) - 1):
        i0, i1 = pivots[k], pivots[k + 1]
        if i1 == i0:
            out[i0] = x[i0]
            continue
        v0, v1 = x[i0], x[i1]
        for i in range(i0, i1):
            out[i] = v0 + (v1 - v0) * (i - i0) / (i1 - i0)
        out[i1] = v1
    return out
