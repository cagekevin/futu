"""富途平台源（`fetch/sources/futu/`）—— 两个通道。

| 文件 | 通道 | 用途 |
|---|---|---|
| `opend.py` | **OpenD**（本机 `127.0.0.1:11111`） | 全市场快照、少量快照、自选、分钟线含 4H、复权因子 |
| `rest.py` | **REST**（`webapi.futunn.com`，Ed25519） | 历史 K线（无额度）、交易日历 |

两者在导入时**自注册**到 `fetch/registry.py`（源名 `futu` / `futu-rest`）。
分工与坑见 `docs/reference/futu/`。
"""
