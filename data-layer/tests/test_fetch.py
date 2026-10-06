"""S3 验证 —— 对照 PRD M2 的可验证标准（F1–F5）。

F1/F3 是静态检查（grep）；F2/F4/F5 用真实取数 + 断网模拟。
跑法：.venv/bin/python tests/test_fetch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetch import fetch_api as api  # noqa: E402
from fetch.fetch_types import FetchError, CHAIN_FIELDS  # noqa: E402


def test_f2_universal_fields_and_extra():
    """F2：通用字段统一命名 + 源特有集中在 extra。"""
    r = api.chain("SPX")
    assert set(CHAIN_FIELDS).issubset(r.rows[0].keys()), r.rows[0].keys()
    assert "extra" in r.__dataclass_fields__
    # 通用字段两源一致 —— 这里验证单一源的字段集与清单完全吻合
    for row in r.rows[:50]:
        assert set(row.keys()) == set(CHAIN_FIELDS), set(row.keys()) ^ set(CHAIN_FIELDS)


def test_f5_whole_chain_present():
    """F5：取到的期权链含全部合约。"""
    r = api.chain("SPX")
    assert len(r.rows) > 1000, len(r.rows)
    assert r.extra["raw_option_count"] == len(r.rows)  # 无遗漏


def test_f4_failure_raises_not_silent():
    """F4：断网跑取数 → 抛错，不静默返回空。"""
    import fetch.sources.cboe_options_source as cboe

    class _BoomSession:
        def get(self, *a, **k):
            raise __import__("requests").ConnectionError("no network")

    orig = cboe._SESSION
    cboe._SESSION = _BoomSession()
    try:
        try:
            cboe.CboeSource().fetch_chain(
                __import__("fetch.fetch_types", fromlist=["Request"]).Request(symbol="SPX"))
        except FetchError as e:
            assert "取数失败" in str(e)
        else:
            raise AssertionError("断网应抛 FetchError（承 F4/F6）")
    finally:
        cboe._SESSION = orig


def test_p4_future_date_raises():
    """P4/P5：源返回**晚于**请求日的内容 → 报（异常值显形）。

    ⚠️ 反向（feed 日 ≤ 请求日）是**正常的**：盘前/周末取数，feed 时刻停在
    上一交易日。实测得出，不能一律判错（否则盘前跑管线必挂）。
    """
    try:
        api.chain("SPX", as_of="1999-01-01")  # 源必返回晚于它 → 报
    except FetchError as e:
        assert "请求" in str(e) and "晚于" in str(e), str(e)
    else:
        raise AssertionError("feed 日晚于请求日应抛错（承 P4/P5）")


def test_p4_offhours_not_rejected():
    """盘前取数：feed 日 ≤ 请求日应被接受（回归 —— 曾误判为错）。"""
    r = api.chain("SPX", as_of="2099-01-01")  # 请求日远在未来 → feed 必然 ≤ 它
    assert len(r.rows) > 0


def test_f1_f3_no_source_name_outside_fetch():
    """F1/F3：源码（fetch/ 之外）不出现源名（cboe/futu）。"""
    import re

    root = Path(__file__).resolve().parent.parent
    offenders = []
    # 词边界匹配，避免 `from __future__` 里的 "futu" 假阳性（承 F1/F3）。
    pat = re.compile(r"\b(cboe|futu)\b", re.IGNORECASE)
    for sub in ("store", "engine", "provide"):
        d = root / sub
        if not d.exists():
            continue
        for p in d.rglob("*.py"):
            for m in pat.finditer(p.read_text(encoding="utf-8")):
                offenders.append((str(p.relative_to(root)), m.group(0)))
    # pipeline.py 允许做"源选择"编排，但 store/engine/provide 不行
    assert not offenders, f"源名泄漏到 M2 之外：{offenders}"


def test_f1_changed_source_only_touches_m2():
    """F3：加新源只新增 fetch/ 文件 —— 注册表可枚举。"""
    assert "cboe" in api.available_sources()


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
