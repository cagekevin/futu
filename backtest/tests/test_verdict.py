"""`verdict` —— 判决模块的测试。

## 为什么需要它（第三轮独立复审第 3 条）

`backtest_config.py` 里**早就预注册了判据**，而 Tugboat 这条路**一条都没用**。
⇒ 我一直在说"样本太短 ⇒ 说不清"，可**仓库自己的尺子量出来是 `INVALID`**。

这一组测试钉住三件事：

1. **判据只有一处来源** —— 阈值全部来自 `backtest_config`，`verdict` 里**不写数字**
2. **总判决取最坏的那条**（`INVALID` 压过 `SUSPICIOUS` 压过 `PASS`）
3. **`PASS` 不等于"有优势"** —— 它只表示"没被这几条判据拦下"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtest_config as cfg  # noqa: E402
import verdict  # noqa: E402


def _rep(**kw) -> dict:
    base = {"cagr": 0.10, "sharpe": 1.0, "max_drawdown": -0.05, "n_trades": 200}
    return {**base, **kw}


def test_thresholds_come_from_backtest_config() -> bool:
    """★ **判据只有一个来源** —— 改 `backtest_config` 必须立刻改变判决。

    （若 `verdict` 里自己写死了数字，这条会红。）
    """
    lo = _rep(cagr=cfg.MIN_ANN_RET - 0.001)
    hi = _rep(cagr=cfg.MIN_ANN_RET + 0.001)
    a = [j.rule for j in verdict.judge(lo) if j.level == verdict.INVALID]
    b = [j.rule for j in verdict.judge(hi) if j.level == verdict.INVALID]
    ok = "MIN_ANN_RET" in a and "MIN_ANN_RET" not in b
    print(f"{'[PASS]' if ok else '[FAIL]'} 年化阈值取自 `backtest_config`"
          f"（{cfg.MIN_ANN_RET} 上下分别：{a} / {b}）")
    return ok


def test_invalid_beats_suspicious() -> bool:
    """总判决取**最坏**的那条 —— `INVALID` 压过 `SUSPICIOUS`。"""
    js = verdict.judge(_rep(cagr=0.01, sharpe=0.1, n_trades=10))
    ok = verdict.worst(js) == verdict.INVALID
    print(f"{'[PASS]' if ok else '[FAIL]'} 最坏的压过其余（{verdict.worst(js)}）")
    return ok


def test_mdd_has_two_levels() -> bool:
    """★ 最大回撤**两档**：`>MDD_INVALID` ⇒ INVALID；`>MDD_SUSPICIOUS` ⇒ SUSPICIOUS。"""
    mid = _rep(max_drawdown=-(cfg.MDD_SUSPICIOUS + cfg.MDD_INVALID) / 2)
    bad = _rep(max_drawdown=-(cfg.MDD_INVALID + 0.01))
    lv_mid = {j.rule: j.level for j in verdict.judge(mid)}
    lv_bad = {j.rule: j.level for j in verdict.judge(bad)}
    ok = (lv_mid.get("MDD_SUSPICIOUS") == verdict.SUSPICIOUS
          and lv_bad.get("MDD_INVALID") == verdict.INVALID)
    print(f"{'[PASS]' if ok else '[FAIL]'} 回撤两档"
          f"（中档 {lv_mid.get('MDD_SUSPICIOUS')} / 差档 {lv_bad.get('MDD_INVALID')}）")
    return ok


def test_side_ratio_flags_beta() -> bool:
    """单边占比过高 ⇒ **疑似 beta**（`MAX_SIDE_RATIO`）。"""
    js = verdict.judge(_rep(), side_ratio=cfg.MAX_SIDE_RATIO + 0.01)
    ok = any(j.rule == "MAX_SIDE_RATIO" and j.level == verdict.SUSPICIOUS for j in js)
    print(f"{'[PASS]' if ok else '[FAIL]'} 单边占比过高 ⇒ 疑似 beta")
    return ok


def test_side_ratio_omitted_does_not_fire() -> bool:
    """不传 `side_ratio` ⇒ **不判这一条**（不是当成 0 通过）。"""
    js = verdict.judge(_rep())
    ok = all(j.rule != "MAX_SIDE_RATIO" for j in js)
    print(f"{'[PASS]' if ok else '[FAIL]'} 没传单边占比 ⇒ 不判那一条")
    return ok


def test_pass_is_not_endorsement() -> bool:
    """★ **`PASS` 的文字必须写明"不等于有优势"** —— 防被读成背书。"""
    text = verdict.render_verdict([verdict.Judgement("x", verdict.PASS, "ok")])
    ok = "不等于" in text and "有优势" in text
    print(f"{'[PASS]' if ok else '[FAIL]'} PASS 的措辞防误读")
    return ok


def test_invalid_text_says_not_unsure() -> bool:
    """★ `INVALID` 的文字必须写明「**不是说不清，是不合格**」——
    这正是这一条存在的理由：**不许拿"样本短"盖过自己那道门**。
    """
    text = verdict.render_verdict([verdict.Judgement("x", verdict.INVALID, "bad")])
    ok = "说不清" in text and "不合格" in text
    print(f"{'[PASS]' if ok else '[FAIL]'} INVALID 的措辞写明「不是说不清」")
    return ok


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            if not fn():
                failed += 1
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {fn.__name__}: 抛异常")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
