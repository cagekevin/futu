# scripts/ —— 治理工具链（从 maomao 项目**Python 重写**搬入）

> 本目录只放**持续复用的治理工具**（被 `.codebuddy/commands/**` 与 `Agent.md` 引用）。
> 一次性 / 临时脚本放独立文件夹，禁止丢进本目录。
>
> **全部零第三方依赖**（只用标准库）⇒ 用哪个 Python 都行；本仓惯例：`data-layer/.venv/bin/python scripts/<x>.py`。

| 脚本 | 用途 | 谁在用 |
| --- | --- | --- |
| [`adr.py`](./adr.py) | **ADR 读写唯一入口** —— `docs/adr/` 各条决议 + `README.md` 索引（**索引是产物**）。含 `list/show/search/index/add/status/rm/audit/hygiene/stats/refs/doctor` | `ADR守护者.md` · `架构师改码7步法.md` |
| [`debt.py`](./debt.py) | **债务账本读写唯一入口** —— `daily/架构日志/债务.md`（待办）+ `债务-归档.md`（已完成）。含 `list/area/search/show/add/resolve/reanchor/edit/move/archive/audit/fix/stats` | `债务登记5步法.md` · `架构师改码7步法.md` |
| [`mv_sync_refs.py`](./mv_sync_refs.py) | **改名 / 移动 / 目录搬运 + 全库同步 import**（Python 版）+ `refs`（fan-in）/ `find-dead`（孤儿） | `写代码4步法.md` · `架构5步法.md` · `债务登记5步法.md` · `系统治理5步法.md` · `排查5步法.md` |
| [`probe.py`](./probe.py) | **「先红后绿」探针执行器** —— 临时注入 → 跑命令 → 精确断言 → 无条件还原（journal 兜底 + sha256 自校验 + 并发守卫） | `架构师改码7步法.md` §7.1 |

## 与源工具的差异（诚实声明）

源工具是 **Node `.mjs`**（`~/Documents/maomao/scripts/`）。本次按用户选择 **Python 重写**，行为等价、CLI 参数保持一致，差异只在**语言语义**处：

| 源工具 | 本版 | 差异原因 |
| --- | --- | --- |
| `adr.mjs` | `adr.py` | 路径常量改为本仓（`docs/adr/`；引用扫描根 = `docs` `data-layer` `backtest` `.codebuddy` `daily` `Agent.md`）；`doctor` 第 ④ 步（`extract-detours`）**未搬**，显式跳过不假装跑过 |
| `debt.mjs` | `debt.py` | 路径常量改为 `daily/架构日志/`；`--liveness` 的文件扩展名改为本仓实际（`.py` 等） |
| `probe.mjs` | `probe.py` | ⚠️ **关键差异**：JS `execSync` 非零退出会抛错，Python `subprocess.run` **不会** ⇒ 必须显式取 `returncode`（本版已处理；否则任何失败都会被当 exit=0 = 假绿） |
| `mv-sync-refs.mjs` | `mv_sync_refs.py` | 源工具是 TS 专用（babel/TS LanguageService/别名/后缀约定）。本版**只保留 Python 里有意义的命令**：`refs` / `find-dead` / `rename` / `move` / `move-dir` / `--dry` / `--undo`；**不含** `convert` / `plan` / `batch` / `rename-symbol` / `remove-unused-imports`（无对应物）。相对 import 按 Python 语义重算（`from . import read` → `from . import reader`） |

## 运行态文件（已 gitignore）

- `scripts/.probe/` —— 探针 journal（注入前原文全文；正常跑完即删，被中断时下次启动自愈还原）。
- `scripts/.move-dir-undo.json` —— `move-dir` 的一键回退记录。

## 维护约定

- **写入者唯一**：`docs/adr/**` 走 `adr.py`、`daily/架构日志/债务*.md` 走 `debt.py` —— **禁手写表格行**。
- **工具债不延后**：本目录脚本有缺陷 → **当场修**（`python -c "import ast;ast.parse(...)"` 自检 + 实跑 + 先红后绿证据）。
- 改脚本前先读其文件头（含「申诉口三问」：守什么 / 什么时候该改它 / 怎么改）。
