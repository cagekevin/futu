# ADR —— 架构判据（Architecture Decision Records）

> **同一件事只许一条判据。** 本目录是**判据本体**的落点（"它凭什么成立 / 被谁推翻过"）。
> **真源 = 各 `ADR-NNNN-*.md` 的头部字段**；**下方索引表是产物**（由 `scripts/adr.py` 生成，**禁手改**）。

## 怎么用（读写唯一入口 = `scripts/adr.py`）

```bash
python scripts/adr.py list                 # 现行判据（默认只看现行；--all 含已退出）
python scripts/adr.py show <NNNN>          # 单条全文
python scripts/adr.py search <关键词>       # 全文匹配（含已退出；多词 AND）
python scripts/adr.py audit                # 只读体检（**不是闸**）
python scripts/adr.py hygiene              # 可数体检：空壳 / 近义簇 / 未毕业 / 今日新增
python scripts/adr.py refs --check         # 全仓断链巡检
python scripts/adr.py doctor               # Step 1 一键编排（audit + hygiene + 断链）

# 写（先答 6 问 → 贴进对话给用户看 → 用户决策"同意" → 才加 --user-approved 落盘）
python scripts/adr.py add --title "…" --conclusion "…" --user-approved
python scripts/adr.py status <NNNN> --to 已取代 --by <新号> --note "…"
python scripts/adr.py index --write        # 重生成下方索引块
```

## 一条 ADR 该长什么样（**判据 + SOP，不写推理**）

| 要 | 不要（写了就是"推理过程"，抽走） |
| --- | --- |
| **判据**：一句话能照做的规则 | 试错过程 · 探针输出 · A/B/C 比选 |
| **SOP**：发现违反后怎么做（命令 + 期望输出 + 处置） | "所以要小心…""希望后续…" |
| **边界表**：与谁互补 / 与谁不重叠 | 大段背景叙述 |
| **落点**：`文件:行` / 唯一入口 | 只有文档编号（= 未实现） |

- **结论 ≤120 字**（进索引）· **整个文件 ≤80 行** · 六节齐（背景 / 判据 / 决议 / 后果 / Lessons Learned / 落点）。
- **过程一律归 `daily/架构日志/`**（推理过程不是废话 —— 它只是**落错了地方**）。
- **修一条 ADR = 正文 + 结论句两处一起改**；只改正文 = **没打上**（AI 只读结论那一行）。

## 本项目与判据的其他落点（分工）

| 问 | 落点 |
| --- | --- |
| 它凭什么成立？被谁推翻过？ | **本目录 `docs/adr/`** |
| 物理红线 / 铁律（撬不动） | [`Agent.md`](../../Agent.md) §4（P1–P6 反掩盖 / X / F / G / L / 复权） |
| 需求级约束（K/F/G/L/P） | `data-layer/docs/PRD/` |
| 这条链路怎么走 / 施工记录 | `data-layer/docs/plan/` · `backtest/docs/design/` |

> 详细规程见 [`.codebuddy/commands/ADR守护者.md`](../../.codebuddy/commands/ADR守护者.md)。

---

<!-- ADR-INDEX:BEGIN（由 python scripts/adr.py index --write 生成 · 禁手改） -->
| # | 标题 | 状态 | 结论一句话 |
| --- | --- | --- | --- |
<!-- ADR-INDEX:END -->
