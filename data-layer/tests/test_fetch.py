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


def _source_name_offenders(text: str) -> list[str]:
    """源码文本里的**源名泄漏**（承 F1/F3）。

    ★ 判据：F1 要防的是**代码依赖源名**（`import futu` / `source="futu-opend"`），
    **不是**"文档里提到源"。**文档路径引用**（如 ``docs/reference/futu/x.md``）
    不构成依赖 —— 改了源名只是链接失效，不会让上游跟着改代码，故**不算违规**。

    实现：词边界匹配（避开 `from __future__` 的假阳性）+ 跳过**路径的一段**
    （该词前后是 `/` 或 `\\`）。
    """
    import re

    pat = re.compile(r"\b(cboe|futu)\b", re.IGNORECASE)
    out: list[str] = []
    for m in pat.finditer(text):
        before = text[m.start() - 1] if m.start() > 0 else ""
        after = text[m.end()] if m.end() < len(text) else ""
        # ⚠️ 必须用**元组**判断：`"" in "/\\"` 恒为 True（空串是任何串的子串），
        #    会把"行尾的词"全部误跳过 —— 阳性对照就是为了拦这个。
        if before in ("/", "\\") or after in ("/", "\\"):
            continue          # 文档路径引用 —— 不是代码依赖
        out.append(m.group(0))
    return out


def test_f1_f3_no_source_name_outside_fetch():
    """F1/F3：源码（fetch/ 之外）不出现源名（cboe/futu）—— 文档路径引用不算。"""
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for sub in ("store", "engine", "provide"):
        d = root / sub
        if not d.exists():
            continue
        for p in d.rglob("*.py"):
            for hit in _source_name_offenders(p.read_text(encoding="utf-8")):
                offenders.append((str(p.relative_to(root)), hit))
    # pipeline.py 允许做"源选择"编排，但 store/engine/provide 不行
    assert not offenders, f"源名泄漏到 M2 之外：{offenders}"


def test_f1_check_still_catches_real_leak():
    """★ 阳性对照：排除"文档路径引用"之后，检查**仍能**抓到真实的代码依赖。

    没有这条，上一条可能被"放宽到什么都不报"而形同虚设。
    """
    # 代码依赖 → 必须抓
    assert _source_name_offenders("import futu") == ["futu"]
    assert _source_name_offenders("from futu import OpenQuoteContext") == ["futu"]
    assert _source_name_offenders('source = "futu-opend"') == ["futu"]
    assert _source_name_offenders("if name == 'cboe': pass") == ["cboe"]
    assert _source_name_offenders("SOURCES = ('cboe', 'futu')") == ["cboe", "futu"]
    # 文档路径引用 → 不算（不构成代码依赖）
    assert _source_name_offenders("见 docs/reference/futu/x.md") == []
    assert _source_name_offenders("承 `docs/reference/cboe/y.md` §1") == []
    # `from __future__` 不是源名（词边界）
    assert _source_name_offenders("from __future__ import annotations") == []


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
