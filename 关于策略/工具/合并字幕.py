"""
把 Tugboat 字幕目录合并成一个大文件（每 90 字换行，便于分段通读）。
"""

import pathlib

D = pathlib.Path(__file__).resolve().parent.parent / "素材" / "Tugboat字幕"

parts = []
for f in sorted(D.glob("*.txt")):
    if f.name.startswith("_"):
        continue
    body = f.read_text(encoding="utf-8")
    body = "\n".join(body[i:i + 90] for i in range(0, len(body), 90))
    parts.append(f"\n\n{'=' * 90}\n### FILE: {f.stem}\n{'=' * 90}\n")
    parts.append(body)

out = D / "_全部字幕合并.txt"
out.write_text("".join(parts), encoding="utf-8")

txts = [f for f in D.glob("*.txt") if not f.name.startswith("_")]
lines = out.read_text(encoding="utf-8").splitlines()
print(f"txt 文件数：{len(txts)}")
print(f"合并文件行数：{len(lines):,}")
print(f"合并文件大小：{out.stat().st_size:,} 字节")
