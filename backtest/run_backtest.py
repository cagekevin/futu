"""端到端入口：拿数据 → 跑策略 → 审计 → 出报告。

回测层**不落盘任何数据** —— 只打印报告（结论）。

用法：
    python3 run_backtest.py --list-strategies
    python3 run_backtest.py --symbols SPY QQQ --strategy momentum
    python3 run_backtest.py --synthetic --symbols SPY --strategy regime_gated_momentum
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone

import data_source
import independent_audit
import pnl_engine
import strategy_contract
import strategies  # noqa: F401  —— 导入即注册所有策略
import target_label

# 合成数据的起始日（仅用于生成合法的 `date`，见 synthetic_panel）。
_SYNTHETIC_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def synthetic_panel(symbols: list[str], n_bars: int, seed: int) -> dict:
    """合成随机游走面板（**仅用于验证代码能跑通**，绝不用来判断策略好坏）。

    为什么要有：框架的正确性不该依赖"你的库里有没有 K线"（承 02 §0）。
    ⚠️ 合成数据要么是纯噪声、要么是"完美可预测"—— 动量信号在它上面的盈亏
    **不代表**真实能力。要判断策略，必须用真实数据 + placebo 对照（承 V10）。

    ★ `date` 生成**合法 ISO 日期**（不是 `str(t)`）—— 否则破坏 K线契约，
    将来"按交易日过滤 / 停牌判断"会**静默错**（承 P2：不造假结构）。
    """
    rng = random.Random(seed)
    index = [i * 3600 for i in range(n_bars)]
    values: dict[str, list[dict]] = {}
    for symbol in symbols:
        bars: list[dict] = []
        price = 100.0
        for t in range(n_bars):
            open_price = price
            price = price * (1.0 + rng.gauss(0.0, 0.004))
            bars.append({
                "time_key": index[t],
                "date": (_SYNTHETIC_EPOCH + timedelta(hours=t)).strftime("%Y-%m-%d"),
                "open": open_price, "high": max(open_price, price),
                "low": min(open_price, price), "close": price, "volume": 1000.0,
            })
        values[symbol] = bars
    return {
        "index": index, "values": values,
        "strategy": data_source.ALIGNMENT_INTERSECTION,
        "n_symbols": len(symbols), "n_bars": n_bars,
    }


def build_market_facts(symbol: str,
                       fields: dict[str, dict[str, list[float]]],
                       times: list[int]) -> strategy_contract.MarketFacts:
    """把面板里某标的的一列列，组装成策略契约要求的 `MarketFacts`（不可变 tuple）。"""
    return strategy_contract.MarketFacts(
        times=tuple(times),
        open=tuple(fields["open"][symbol]),
        high=tuple(fields["high"][symbol]),
        low=tuple(fields["low"][symbol]),
        close=tuple(fields["close"][symbol]),
        volume=tuple(fields["volume"][symbol]),
        extra={},   # 预留：非 bar 级数据项，见 data_source.align_daily_items
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回测与验证（端到端）")
    parser.add_argument("--symbols", nargs="+", help="如 SPY QQQ")
    parser.add_argument("--item", default="kline")
    parser.add_argument("--strategy", default="momentum", help="策略注册名")
    parser.add_argument("--list-strategies", action="store_true",
                        help="列出已注册策略后退出")
    parser.add_argument("--synthetic", action="store_true",
                        help="用合成随机游走数据（仅验证代码，不可判断策略）")
    parser.add_argument("--synthetic-bars", type=int, default=600)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    if args.list_strategies:
        for name in strategy_contract.available_strategies():
            print(name)
        return 0

    if not args.symbols:
        parser.error("--symbols 必填（或用 --list-strategies）")

    strategy = strategy_contract.get_strategy(args.strategy)

    # ① 拿数据（唯一入口）
    if args.synthetic:
        panel = synthetic_panel(args.symbols, args.synthetic_bars, args.seed)
    else:
        try:
            panel = data_source.align_panel(args.symbols, item=args.item)
        except RuntimeError as exc:
            # 取数失败**必报**（承 F4），但不糊用户一脸 traceback —— 根因在消息里。
            print(f"[取数失败] {exc}", file=sys.stderr)
            return 2

    times = [int(t) for t in panel["index"]]
    try:
        # 抽值即校验：降级（含捏造 bar）/ 缺 bar 都会在这里**报错**（承 D1/P6）。
        # 字段清单只认 data_source.KLINE_BAR_FIELDS 一处，这里不再抄第二份（修一.5）。
        fields = {f: data_source.extract_field(panel, f)
                  for f in data_source.KLINE_BAR_FIELDS}
    except ValueError as exc:
        print(f"[拒绝] {exc}", file=sys.stderr)
        return 2

    # ② 逐标的审计
    reports = []
    for symbol in args.symbols:
        if symbol not in fields["close"]:
            print(f"[跳过] {symbol}：面板里没有该标的", file=sys.stderr)
            continue
        facts = build_market_facts(symbol, fields, times)
        factors = strategy.compute_factors(facts)
        if len(factors) != len(times):
            raise ValueError(
                f"策略 {strategy.name} 返回 {len(factors)} 个因子，"
                f"应为 {len(times)}（与 times 等长）"
            )
        positions = pnl_engine.positions_from_factors(factors)
        target_returns = target_label.target_ret(facts.open)
        reports.append(independent_audit.audit_strategy(
            positions, target_returns, times, label=f"{symbol} / {strategy.name}"
        ))

    if not reports:
        print("没有可审计的标的", file=sys.stderr)
        return 1

    # ③ 出报告
    print()
    for report in reports:
        print(independent_audit.format_audit_report(report))
        print()

    # ④ 汇总
    n_invalid = sum(1 for r in reports if r["verdict"] == "INVALID")
    n_suspicious = sum(1 for r in reports if r["verdict"] == "SUSPICIOUS")
    print(f"══ 汇总：{len(reports)} 个标的  "
          f"INVALID {n_invalid}  SUSPICIOUS {n_suspicious}  "
          f"VALID {len(reports) - n_invalid - n_suspicious} ══")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
