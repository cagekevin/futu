"""统计判据工具的行为断言（承 V11 / V12 / V18 / TD-05-4）。

判据：承 `架构师改码7步法` §7.2「禁自证式断言」——
每条断言都必须**依赖被测实现之外的事实**（手算数学答案 / 数据的物理构造），
把实现改坏必红。禁止只断"没抛错"、断自己刚写的常量、断源码文本。

核心那条（`test_簇级区间必须宽于假装独立`）直接对着 design 01 §4.11 的反例：
同一批信号，把 150 笔观测当 150 次独立实验 ⇒ 区间虚假地窄约 4 倍。

多重检验与事件去重同样以**手算答案**为锚（TD-05-2 / TD-05-4）。
"""
from __future__ import annotations

import unittest

from panel_statistics import (
    SINGLE_SERIES_IN_CLUSTER,
    benjamini_hochberg,
    clustered_cmh,
    clustered_interval,
    dedupe_overlapping_events,
    effective_sample_size,
    format_rate_with_clusters,
    wilson_interval,
)


def _panel_clusters() -> dict[str, list[int]]:
    """物理真实的面板构造：**同一天所有品种同涨同跌**（日内零相关），日间独立。

    30 个品种 × 5 个交易日 = 150 观测，成功 82 ⇒ 胜率 54.7%。
    日间胜率差异极大（100% / 6.7% / 83.3% / 16.7% / 66.7%）—— 这正是面板的形态。
    """
    return {
        "2026-01-01": [1] * 30,
        "2026-01-02": [1] * 2 + [0] * 28,
        "2026-01-03": [1] * 25 + [0] * 5,
        "2026-01-04": [1] * 5 + [0] * 25,
        "2026-01-05": [1] * 20 + [0] * 10,
    }


class TestWilsonScopeGuard(unittest.TestCase):
    """V12 的守卫：Wilson 只允许簇内单序列。"""

    def test_手算对齐_小样本(self) -> None:
        """k=3, n=5 的 Wilson 有独立闭式解（中心 0.5566、半宽 0.3258）。"""
        lower, upper = wilson_interval(3, 5, n_clusters=1, scope=SINGLE_SERIES_IN_CLUSTER)
        self.assertAlmostEqual(lower, 0.2307, places=4)
        self.assertAlmostEqual(upper, 0.8824, places=4)

    def test_手算对齐_大样本(self) -> None:
        """k=120, n=200 → [0.5308, 0.6654]（同上闭式解）。"""
        lower, upper = wilson_interval(120, 200, n_clusters=1, scope=SINGLE_SERIES_IN_CLUSTER)
        self.assertAlmostEqual(lower, 0.5308, places=4)
        self.assertAlmostEqual(upper, 0.6654, places=4)

    def test_面板守卫必须拒(self) -> None:
        """5 个簇 = 面板数据 ⇒ 必须拒绝，并指向簇级口径。"""
        with self.assertRaises(ValueError) as raised:
            wilson_interval(3, 5, n_clusters=5, scope=SINGLE_SERIES_IN_CLUSTER)
        self.assertIn("clustered_interval", str(raised.exception))

    def test_作用域必须显式声明且唯一(self) -> None:
        """scope 拼错 ⇒ 拒绝（不做作用域推断）。"""
        with self.assertRaises(ValueError) as raised:
            wilson_interval(3, 5, n_clusters=1, scope="whatever")
        self.assertIn("scope", str(raised.exception))

    def test_非法计数必须报错不兜底(self) -> None:
        """successes 超出 [0, n] ⇒ 报错，不裁剪、不夹紧。"""
        with self.assertRaises(ValueError):
            wilson_interval(9, 5, n_clusters=1, scope=SINGLE_SERIES_IN_CLUSTER)


class TestClusteredInterval(unittest.TestCase):
    """簇级bootstrap —— 本仓比例/均值结论的默认口径。"""

    def test_簇级区间必须宽于假装独立(self) -> None:
        """★ 本模块存在的理由（TD-05-1）。

        同一批 82/150 的信号：
        - 假装 150 笔独立 → Wilson 宽度 0.157
        - 真实的 5 个交易日簇 → 簇级 bootstrap 宽度 0.640（**≈ 4 倍**）
        ⇒ 把面板当独立样本，会把区间**虚假收窄约 4 倍**。
        """
        clusters = _panel_clusters()
        observations, cluster_count = effective_sample_size(clusters)
        self.assertEqual(observations, 150)
        self.assertEqual(cluster_count, 5)

        interval = clustered_interval(clusters)
        naive_lower, naive_upper = wilson_interval(
            82, observations, n_clusters=1, scope=SINGLE_SERIES_IN_CLUSTER
        )
        naive_width = naive_upper - naive_lower
        clustered_width = interval.upper - interval.lower

        self.assertGreater(clustered_width, 3.0 * naive_width)

    def test_点估计一致但下界远低于朴素(self) -> None:
        """点估计必须一致（同一批数据），但簇级下界远低于朴素下界 ⇒ 朴素漏掉真实风险。"""
        clusters = _panel_clusters()
        interval = clustered_interval(clusters)
        naive_lower, _ = wilson_interval(82, 150, n_clusters=1, scope=SINGLE_SERIES_IN_CLUSTER)
        self.assertAlmostEqual(interval.point_estimate, 82 / 150, places=12)
        self.assertLess(interval.lower, naive_lower - 0.2)

    def test_独立构造下两者宽度一致(self) -> None:
        """★ 反向对照：簇数 = 观测数（真独立）时，簇级 ≈ 朴素。

        这条证明「簇级更宽」不是"实现随便放大区间"，而是**相关性造成的**。
        """
        independent = {f"obs-{index}": [1 if index < 30 else 0] for index in range(60)}
        interval = clustered_interval(independent)
        naive_lower, naive_upper = wilson_interval(
            30, 60, n_clusters=1, scope=SINGLE_SERIES_IN_CLUSTER
        )
        naive_width = naive_upper - naive_lower
        clustered_width = interval.upper - interval.lower
        self.assertAlmostEqual(interval.point_estimate, 0.5, places=12)
        self.assertAlmostEqual(clustered_width / naive_width, 1.0, delta=0.35)

    def test_簇间零方差时区间退化(self) -> None:
        """所有簇完全相同 ⇒ 无不确定性 ⇒ 区间宽度为 0（正确行为，不是 bug）。"""
        identical = {f"2026-02-{day:02d}": [1] * 10 + [0] * 10 for day in range(1, 6)}
        interval = clustered_interval(identical)
        self.assertAlmostEqual(interval.point_estimate, 0.5, places=12)
        self.assertAlmostEqual(interval.upper - interval.lower, 0.0, places=9)

    def test_确定性_同种子必同结果(self) -> None:
        """可复现（承 design 01 最高目标：两年后能逐位重算）。"""
        clusters = _panel_clusters()
        first = clustered_interval(clusters, seed=12345)
        second = clustered_interval(clusters, seed=12345)
        self.assertEqual((first.lower, first.upper), (second.lower, second.upper))

    def test_独立性缺口随簇数下降(self) -> None:
        """`independence_gap` 是非独立程度的自证字段。"""
        self.assertAlmostEqual(
            clustered_interval(_panel_clusters()).independence_gap, 1 - 5 / 150, places=12
        )
        self.assertAlmostEqual(
            clustered_interval({f"o-{index}": [1] for index in range(20)}).independence_gap,
            0.0,
            places=12,
        )

    def test_空簇必须报错不兜底(self) -> None:
        """空簇会让区间虚窄 ⇒ 直接拒绝，不静默跳过。"""
        with self.assertRaises(ValueError) as raised:
            clustered_interval({"2026-01-01": [1, 0], "2026-01-02": []})
        self.assertIn("空簇", str(raised.exception))

    def test_空输入必须报错(self) -> None:
        with self.assertRaises(ValueError):
            clustered_interval({})

    def test_非法抽样参数必须报错(self) -> None:
        with self.assertRaises(ValueError):
            clustered_interval(_panel_clusters(), iterations=0)
        with self.assertRaises(ValueError):
            clustered_interval(_panel_clusters(), alpha=0.0)


class TestFormatRate(unittest.TestCase):
    """V12 的验收格式 —— 没有"只报裸比例"的出口。"""

    def test_格式必须同时给出观测数与簇数(self) -> None:
        text = format_rate_with_clusters(82, _panel_clusters())
        self.assertIn("54.7%", text)
        self.assertIn("簇级 95%", text)
        self.assertIn("n=150 观测", text)
        self.assertIn("5 簇", text)

    def test_单簇时格式退化为朴素口径(self) -> None:
        """单簇（真独立）时簇数=1，格式仍自洽 —— 口径统一，不需两套格式。"""
        text = format_rate_with_clusters(3, {"2026-01-01": [1, 1, 1, 0, 0]})
        self.assertIn("60.0%", text)
        self.assertIn("1 簇", text)

    def test_successes_超出观测数必须报错(self) -> None:
        with self.assertRaises(ValueError):
            format_rate_with_clusters(999, _panel_clusters())


class TestClusteredCmh(unittest.TestCase):
    """V11 的分层比较（分层维度由调用方声明，本仓默认 = 交易日）。"""

    def test_手算对齐_优势比(self) -> None:
        """两层各 2x2，MH 合并优势比可手算。

        2x2 表按 `[a b / c d]` = `[成功 失败]`：
            OR_MH = Σ(a·d/n) / Σ(b·c/n)

        层1 treated 3成1败、control 2成2败 → 分子 3·2/8 = 0.75，分母 1·2/8 = 0.25
        层2 treated 2成2败、control 1成3败 → 分子 2·3/8 = 0.75，分母 2·1/8 = 0.25
        MH OR = (0.75+0.75) / (0.25+0.25) = **3.0**
        """
        result = clustered_cmh(
            {
                "2026-01-01": {"treated": [1, 1, 1, 0], "control": [0, 0, 1, 1]},
                "2026-01-02": {"treated": [1, 1, 0, 0], "control": [0, 0, 0, 1]},
            }
        )
        self.assertAlmostEqual(result.odds_ratio, 3.0, places=4)
        self.assertEqual(result.strata_count, 2)

    def test_每层同向时优势比指向该方向(self) -> None:
        """处理组在每层都更好 ⇒ OR > 1（层大小不该翻转方向）。"""
        strata = {
            "big_stratum": {"treated": [1] * 18 + [0] * 2, "control": [1] * 10 + [0] * 10},
            "small_stratum": {"treated": [1] * 9 + [0] * 1, "control": [1] * 5 + [0] * 5},
        }
        result = clustered_cmh(strata)
        self.assertAlmostEqual(result.odds_ratio, 9.0, places=4)

    def test_方向相反时大层主导(self) -> None:
        """★ V11 的核心：**层大小 = 权重**。

        处理组在大层更差、在小层更好 ⇒ 合并优势比由大层决定 ⇒ OR 明显 < 1。
        这就是"必须分层、且分层维度要事先声明"的理由：
        不分层只看合并比例，方向会被样本量大的层单方面决定。
        """
        strata = {
            "hard_big": {"treated": [1] * 2 + [0] * 18, "control": [1] * 10 + [0] * 10},
            "easy_small": {"treated": [1] * 10 + [0] * 2, "control": [1] * 3 + [0] * 1},
        }
        result = clustered_cmh(strata)
        self.assertLess(result.odds_ratio, 0.3)

    def test_无对比信息的层必须报错不兜底(self) -> None:
        """两组结果完全相同 ⇒ CMH 无定义 ⇒ 拒绝，不给默认值。"""
        with self.assertRaises(ValueError) as raised:
            clustered_cmh({"2026-01-01": {"treated": [1, 1], "control": [1, 1]}})
        self.assertIn("对比信息", str(raised.exception))

    def test_缺一臂必须报错(self) -> None:
        with self.assertRaises(ValueError) as raised:
            clustered_cmh({"2026-01-01": {"treated": [1, 0]}})
        self.assertIn("缺一组", str(raised.exception))

    def test_空层必须报错(self) -> None:
        with self.assertRaises(ValueError):
            clustered_cmh({})


class TestMultipleTesting(unittest.TestCase):
    """V18 多重检验校正（TD-05-2）—— 报 p 值的唯一判决出口。"""

    def test_试十次全不显著_而裸判会有五个(self) -> None:
        """★ 本函数存在的理由（TD-05-2 的量化）。

        10 次检验、alpha=0.05：**裸判** p<0.05 有 **5 个**「显著」；
        BH 校正后**一个都不显著**（最小 p=0.006 > alpha/m = 0.005）。

        这就是「1 − 0.95¹⁸ ≈ 60%」那条 —— 只报单个 p<0.05 而不报分母，
        等于把「试出来的」当成「发现的」。
        """
        p_values = [0.006, 0.02, 0.03, 0.04, 0.045, 0.05, 0.06, 0.07, 0.08, 0.09]
        naive = sum(1 for p in p_values if p < 0.05)
        self.assertEqual(naive, 5)

        result = benjamini_hochberg(p_values, alpha=0.05)
        self.assertEqual(result.family_size, 10)
        self.assertEqual(result.n_discoveries, 0)

    def test_调整后q值手算对齐(self) -> None:
        """同一组 p 值，BH 的 step-up 累积最小值可手算。

        q(1) = min over j >= 1 of (10/j · p(j))
             = min(0.06, 0.1, 0.1, 0.1, 0.09, 0.08333, 0.08571, 0.0875, 0.08889, 0.09)
             = **0.06**（p(1)=0.006 这一项最小）
        """
        result = benjamini_hochberg(
            [0.006, 0.02, 0.03, 0.04, 0.045, 0.05, 0.06, 0.07, 0.08, 0.09], alpha=0.05
        )
        self.assertAlmostEqual(result.adjusted[0], 0.06, places=6)
        self.assertAlmostEqual(result.adjusted[9], 0.09, places=6)

    def test_step_up_拒绝集是前缀(self) -> None:
        """★ BH 是 **step-up**：找到**最大**的 k 使 p(k) <= (k/m)·alpha，它之前全拒绝。

        p = [0.001, 0.002, 0.5]，m=3，alpha=0.05：
          k=1: 0.001 <= 0.01667 ✅   k=2: 0.002 <= 0.03333 ✅   k=3: 0.5 <= 0.05 ✗
        ⇒ 拒绝前 2 个（**不是**只拒绝「每个都满足」的那些）。
        """
        result = benjamini_hochberg([0.001, 0.002, 0.5], alpha=0.05)
        self.assertEqual(result.n_discoveries, 2)
        self.assertTrue(result.is_significant(0))
        self.assertTrue(result.is_significant(1))
        self.assertFalse(result.is_significant(2))
        self.assertAlmostEqual(result.threshold, 0.002, places=9)

    def test_单次检验退化为裸判(self) -> None:
        """m == 1 时 BH == `p < alpha` ⇒ **「只做一次」也必须走这里**（口径统一）。"""
        significant = benjamini_hochberg([0.03], alpha=0.05)
        self.assertTrue(significant.is_significant(0))
        not_significant = benjamini_hochberg([0.3], alpha=0.05)
        self.assertFalse(not_significant.is_significant(0))

    def test_空输入必须报错(self) -> None:
        """没有检验就没有校正 —— 不静默返回空结果（承 P1）。"""
        with self.assertRaises(ValueError) as raised:
            benjamini_hochberg([])
        self.assertIn("没有检验", str(raised.exception))

    def test_非法p值与alpha必须报错(self) -> None:
        with self.assertRaises(ValueError):
            benjamini_hochberg([0.01], alpha=0.0)
        with self.assertRaises(ValueError):
            benjamini_hochberg([1.5])
        with self.assertRaises(ValueError):
            benjamini_hochberg([-0.1])
        with self.assertRaises(ValueError):
            benjamini_hochberg([float("nan")])

    def test_下标越界必须报错(self) -> None:
        result = benjamini_hochberg([0.01, 0.02])
        with self.assertRaises(IndexError):
            result.is_significant(2)


class TestEventDedupe(unittest.TestCase):
    """TD-05-4 事件去重 —— 持有期内重叠的触发只保留一个。"""

    def test_手算对齐_保留间隔足够的事件(self) -> None:
        """持有期 20 根 bar 的触发序列，手算保留集。

        positions = [0, 1, 2, 5, 19, 20, 25]，min_gap=20：
          保留 0（首个）；1/2/5/19 距 0 都 < 20 → 跳；
          20 − 0 = 20 >= 20 → 保留；25 − 20 = 5 < 20 → 跳
        ⇒ 保留下标 **[0, 5]**（其余 5 个是同一段行情的重复计入）。
        """
        kept = dedupe_overlapping_events([0, 1, 2, 5, 19, 20, 25], min_gap=20)
        self.assertEqual(kept, [0, 5])

    def test_同日聚集必须压成一个(self) -> None:
        """★ TD-05-4 的形态：同一天 N 个信号 = **相同位置** ⇒ 只算 1 次。

        （futu 实测：4,076 笔只落在 992 天，单日最多 108 笔 ——
        按笔 vs 按日，结论**符号翻转**。）
        """
        kept = dedupe_overlapping_events([3, 3, 3, 3, 10], min_gap=20)
        self.assertEqual(kept, [0])

    def test_间隔足够时一个都不去(self) -> None:
        """反向对照：事件本来就稀疏 ⇒ 去重不许误杀。"""
        kept = dedupe_overlapping_events([0, 30, 60], min_gap=20)
        self.assertEqual(kept, [0, 1, 2])

    def test_空事件返回空(self) -> None:
        """没有事件就没有可保留的事件 —— 空进空出是正确语义，不是兜底。"""
        self.assertEqual(dedupe_overlapping_events([], min_gap=20), [])

    def test_零gap必须报错(self) -> None:
        """min_gap=0 会让去重变成空操作 ⇒ 报错，不静默放过（承 P1）。"""
        with self.assertRaises(ValueError) as raised:
            dedupe_overlapping_events([0, 1, 2], min_gap=0)
        self.assertIn("min_gap", str(raised.exception))

    def test_非升序必须报错(self) -> None:
        with self.assertRaises(ValueError) as raised:
            dedupe_overlapping_events([5, 3], min_gap=20)
        self.assertIn("非递减", str(raised.exception))


if __name__ == "__main__":
    unittest.main()