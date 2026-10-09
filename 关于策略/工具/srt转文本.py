"""
把 yt-dlp 下载的 .srt 自动字幕，转成干净文本（去时间轴 / 去滚动重复）。
用法：python srt转文本.py <字幕目录>
"""

import sys
import re
import pathlib


def clean_srt(path: pathlib.Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")

    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.isdigit():                 # 序号
            continue
        if "-->" in s:                  # 时间轴
            continue
        if s.upper().startswith("WEBVTT"):
            continue
        lines.append(s)

    # 自动字幕是"滚动式"的：相邻行大量重复 / 互为前后缀
    out = []
    for s in lines:
        if not out:
            out.append(s)
            continue
        last = out[-1]
        if s == last:
            continue
        if s.startswith(last):          # 新行是旧行的延伸 → 用新的替换
            out[-1] = s
            continue
        if last.startswith(s):          # 新行是旧行的前缀 → 丢掉
            continue
        out.append(s)

    text = "".join(out)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def main():
    d = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    files = sorted(d.glob("*.srt"))
    for f in files:
        t = clean_srt(f)
        out = f.with_suffix(".txt")
        out.write_text(t, encoding="utf-8")
        print(f"{f.name}  ->  {out.name}  ({len(t)} 字)")
    print(f"\n共 {len(files)} 个")


if __name__ == "__main__":
    main()
