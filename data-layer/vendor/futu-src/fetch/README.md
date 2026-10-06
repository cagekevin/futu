# ① 取数层

只干一件事：**把数据弄下来、落盘**。不在这里做任何计算。

```
rest/     REST 通道（Node）   —— 自选股全量历史（几百只），无历史K线额度
opend/    OpenD 通道（Python）—— screen_v2.py 是**全项目唯一**的服务器端选股出口
                                 universe.py  全市场快照（薄包装 screen_v2）
                                 futu_data.py 个股数据入口（分时 / 实时 / 多周期）
                                 counterparty.py **对家层取数**（内部人/机构/卖空/资金流/评级）
day.py    粗筛产物的落盘 —— 一天一个文件夹 `data/scan/<日期>/` + `_manifest.json`
```

产物一律落到 `data/`，下游只读不写。

**两条通道都不下载全市场 K 线** —— `opend/universe.py` 是让富途服务器筛，
本地只收结果（9000+ 只的涨幅 / 行业 / 价格 / 市值），不占历史K线额度。

**V2 通道的三个坑**（单位两头不一样 / `page_from` 是偏移量 / protobuf 必须 < 5）
全写在 `opend/screen_v2.py` 顶部 —— **改它之前先读那一节**。

**对家层（`opend/counterparty.py`）的三个坑**，全写在它自己的 docstring 里：
1. **返回值个数不固定** —— `get_short_interest` / `get_daily_short_volume` 返回 **3 个**（`[2]` 是另一个 DataFrame）
2. **返回类型不固定** —— 多数是 DataFrame，但 `get_research_rating_summary` 返回 **dict**
3. **代码格式不一致** —— `universe.json` 里是**裸代码**（`GRFS`），`picks.json['signals']` 是**带前缀的**（`US.VSAT`）
   → 必须走 `normalize_code()`

它**复用 futu 技能的 `common.py`**（连接 / 错误分类 / DataFrame 转换），技能目录可用 `FUTU_SKILL_DIR` 覆盖。
**实测不吃额度**，但**限频 28 次/30 秒** —— 已内置滑动窗口闸门 + 增量跳过。
