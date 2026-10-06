"""终端表格对齐 —— 中日韩全角字符占 **2 列**。

Python 的 `f"{s:<26}"` 只按**字符数**算宽度，中文实际占 2 列，结果必然错位。
打印含中文的列必须用 `pad()`。

    from engine.screener.table import pad
    print(pad("行业", 26) + pad("家数", 5, right=True))
"""

from __future__ import annotations

import unicodedata

__all__ = ["width", "pad", "md_table"]


def width(text: str) -> int:
    """显示宽度：全角（East Asian Width = W/F）算 2 列，其余算 1 列。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, n: int, right: bool = False) -> str:
    """按**显示宽度**补齐到 `n` 列。`right=True` 右对齐（数字列用）。

    `n=0` 表示最后一列，不补齐。
    """
    if n <= 0:
        return text
    spaces = " " * max(0, n - width(text))
    return spaces + text if right else text + spaces


def md_table(headers, rows):
    """Markdown 表格 —— 报告里到处要用，放共用层（原来在 `analyze.py` 里叫 `_md_table`）。"""
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)
