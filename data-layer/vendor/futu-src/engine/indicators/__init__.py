"""指标库

    tdx.py      —— 通达信公式函数子集（基础；纯标准库、无第三方依赖）
    cd.py       —— CD 底背离（MACD 底部背离）
    guppy.py    —— 顾比线突破
    rs.py       —— 相对强度（RS 线 / Mansfield / RPS）—— **RPS 只有这一份，别另写**
    industry.py —— 行业集体行为统计

约定：这里都是**纯函数** —— 输入 bars（`list[dict]`）或行集，输出序列 / 统计，
不碰 IO、不连 OpenD、不读文件。取数 / 扫描 / 通知放在 `fetch/` 和 `stock/` 里。

用法（调用方先把**项目根**塞进 sys.path）：

    from engine.indicators.cd import calc_cd
    from engine.indicators.guppy import scan as guppy_scan
    from engine.indicators import rs
    from engine.indicators.industry import industry_stats
"""
