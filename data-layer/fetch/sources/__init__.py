"""数据源（`fetch/sources/`）—— **一个平台一处**。

换平台只看这个目录：每个数据平台一个文件（单通道）或一个子包（多通道）。
每个源在导入时**自注册**到 `fetch/registry.py`（承 F3：换源只动 fetch/）。

| 源 | 形态 | 通道 |
|---|---|---|
| `cboe.py` | 文件 | CBOE 公开接口（期权链） |
| `futu/` | 子包 | 富途平台：`opend.py`（OpenD）+ `rest.py`（REST） |
"""
