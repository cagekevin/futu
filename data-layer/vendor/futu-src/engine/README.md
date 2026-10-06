# ② 引擎层

**纯计算**，不碰 IO —— 不连 OpenD、不读文件、不发通知。

```
indicators/   指标（纯函数：输入 bars 或行集，输出序列 / 统计）
              tdx / cd / guppy / rs（RS · RPS · Mansfield）/ industry
screener/     粗筛：读 fetch 落盘的数据，出候选
              cd_scan / guppy_scan / rps  —— 自选股，读本地 K 线
              market_scan / screener      —— 全市场，读快照 / 服务器端条件筛
              analyze                     —— 综合分析（读当天文件夹，出报告）
              local / table               —— 本地读取（共用）+ 中文表格对齐
counterparty/ **对家层**（纯函数，无 IO）
              reference   参考分布：按市值档算各指标分位点
              classify    分类器：前提 P0 + 3 个独立 flag（I / S / F）
              forward     前瞻验证：forward_returns / compare / verdict（**判据写死在这里**）
strategy/     公式接进回测 / 未来函数检测
```

## 两条数据来源，别搞混

| | 读什么 | 谁 |
|---|---|---|
| **自选股**（几百只） | `data/kline/daily/*.json` | `local.py` → `cd_scan` / `guppy_scan` / `rps` |
| **全市场**（~3000） | `data/scan/<日期>/universe.json` | `market_scan` / `analyze` |

`local.py` 里的 `TRADABLE_TYPES` 是**默认扫描范围** —— 唯一标准是**能不能交易**：
留 `STOCK / ETF / FUTURE / CRYPTO / FOREX`，只排除买不了的 `IDX`（指数）/ `PLATE`（板块指数）/ `BOND`。

引擎**不拉数据** —— 需要什么就让 `fetch/` 先落盘，这里只读。

**产物**：`data/scan/<日期>/`（机器读）+ `reports/scan/<日期>.md`（人看）。入口是根目录的 `scan.py`。
