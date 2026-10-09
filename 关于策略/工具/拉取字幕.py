"""
拉取 YouTube 频道 / 视频的中文字幕，存成纯文本。

字幕走 youtube-transcript-api（绕开 yt-dlp 字幕接口的 429 限流）；
逐个视频下载 + 间隔 + 失败退避重试。

用法：
    python 拉取字幕.py [频道或视频URL] [间隔秒] [最多N个]

默认：
    URL   = https://www.youtube.com/@可乐AI/videos
    间隔  = 30 秒
    最多  = 全部

产物：
    关于策略/素材/可乐AI实验室字幕/<标题> [<视频id>].txt
    已下载的自动跳过
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import time

from youtube_transcript_api import YouTubeTranscriptApi

HERE = pathlib.Path(__file__).resolve().parent          # 关于策略/工具/
OUT = HERE.parent / "素材" / "可乐AI实验室字幕"

LANGS = ["zh-Hans", "zh-Hant", "zh"]
DEFAULT_URL = "https://www.youtube.com/@可乐AI/videos"
DEFAULT_INTERVAL = 30        # 每个视频之间的间隔（秒）
MAX_TRIES = 3                # 单个视频最多重试次数
BACKOFF = 30                 # 失败后基础等待（秒），逐次翻倍

# 这些是永久性失败（视频无字幕 / 不可播放），重试也没用，直接跳过
PERMANENT = {
    "TranscriptsDisabled", "NoTranscriptFound", "VideoUnavailable",
    "VideoUnplayable", "AgeRestricted", "NotTranslatable",
    "TranslationLanguageNotAvailable",
}

API = YouTubeTranscriptApi()


def safe(name: str) -> str:
    """把标题清成合法文件名。"""
    return re.sub(r'[/\\:*?"<>|\n\r\t]', "_", name)[:120].strip()


def join_snips(texts: list[str]) -> str:
    """拼接字幕片段，并去掉自动字幕的滚动重复。"""
    out = []
    for s in texts:
        s = s.strip()
        if not s:
            continue
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
    return re.sub(r"\s+", " ", "".join(out)).strip()


def list_videos(url: str) -> list[tuple[str, str]]:
    """用 yt-dlp 只取频道的 (视频id, 标题) 清单。"""
    r = subprocess.run(
        ["yt-dlp", "--no-update", "--flat-playlist",
         "--print", "%(id)s\t%(title)s", url],
        capture_output=True, text=True)
    vids = []
    for line in r.stdout.splitlines():
        if "\t" in line:
            vid, title = line.split("\t", 1)
            vids.append((vid.strip(), title.strip()))
    return vids


def fetch_one(vid: str) -> str:
    t = API.fetch(vid, languages=LANGS)
    return join_snips([s.text for s in t.snippets])


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_INTERVAL
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else None

    OUT.mkdir(parents=True, exist_ok=True)
    vids = list_videos(url)
    if limit:
        vids = vids[:limit]
    print(f"待处理 {len(vids)} 个视频，间隔 {interval}s\n", flush=True)

    done = 0
    for n, (vid, title) in enumerate(vids, 1):
        dest = OUT / f"{safe(title)} [{vid}].txt"
        if dest.exists():
            print(f"[{n}/{len(vids)}] 已有，跳过  {title}", flush=True)
            done += 1
            continue

        text = ""
        for i in range(MAX_TRIES):
            try:
                text = fetch_one(vid)
                break
            except Exception as e:
                name = type(e).__name__
                if name in PERMANENT:               # 无字幕 / 不可播放 → 直接跳过
                    print(f"[{n}/{len(vids)}] ✗ 无字幕（{name}）  {title}", flush=True)
                    break
                wait = BACKOFF * (2 ** i)
                print(f"[{n}/{len(vids)}] 失败 {i + 1}/{MAX_TRIES}，"
                      f"等 {wait}s … {name}: {str(e)[:120]}", flush=True)
                time.sleep(wait)

        if text:
            dest.write_text(text, encoding="utf-8")
            print(f"[{n}/{len(vids)}] ✓ {len(text)} 字  {title}", flush=True)
            done += 1
        else:
            print(f"[{n}/{len(vids)}] ✗ 放弃  {title}", flush=True)

        time.sleep(interval)

    print(f"\n完成：{done}/{len(vids)} 个字幕 → {OUT}", flush=True)


if __name__ == "__main__":
    main()
