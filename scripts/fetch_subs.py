#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_subs.py —— 字幕抓取（文本层，B 站 + YouTube）

B 站：cookies + yt-dlp 抓 ai-zh / zh-Hans / zh-CN / ai-en / en。
YouTube：cookies（可选）+ 代理（可选）+ 自动字幕，抓 zh-Hans / zh-Hant / zh / en。
输出带时间戳的 cues.json、每个语言的 cues_<lang>.json、纯文本 transcript.txt。

用法：
  python fetch_subs.py "https://www.bilibili.com/video/BV1AZwezbE1V" -o out
  python fetch_subs.py "https://www.youtube.com/watch?v=xxxx" -o out \
      --proxy socks5://127.0.0.1:1080 --cookies ~/.claude/cookies/youtube.txt
  python fetch_subs.py --from-srt C:/tmp/x.zh-Hans.vtt -o out   # 离线复用（SRT/VTT）
  python fetch_subs.py BV... --json-summary
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
YT_ID_RE = re.compile(r"[0-9A-Za-z_-]{11}")
TS_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)
LANGS = {
    "bilibili": ["ai-zh", "zh-Hans", "zh-CN", "ai-en", "en"],
    "youtube": ["en"],
}
DEFAULT_COOKIES = {
    "bilibili": Path.home() / ".claude" / "cookies" / "bilibili.txt",
    "youtube": Path.home() / ".claude" / "cookies" / "youtube.txt",
}


def detect_platform(s: str) -> str:
    s = s.strip()
    if "youtube.com" in s or "youtu.be" in s:
        return "youtube"
    if "bilibili.com" in s or BV_RE.search(s):
        return "bilibili"
    return "unknown"


def extract_id(s: str, platform: str) -> str:
    s = s.strip()
    if platform == "bilibili":
        m = BV_RE.search(s)
        if m:
            return m.group(1)
        if "b23.tv" in s:
            raise ValueError("b23.tv 短链请先展开成完整视频页 URL 再传进来")
        raise ValueError(f"无法从输入里提取 BV 号：{s!r}")
    if platform == "youtube":
        m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([0-9A-Za-z_-]{11})", s)
        if m:
            return m.group(1)
        if YT_ID_RE.fullmatch(s):
            return s
        raise ValueError(f"无法从输入里提取 YouTube ID：{s!r}")
    raise ValueError(f"不支持的平台/输入：{s!r}")


def find_ytdlp() -> list[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


def _ts(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_subs(path: Path) -> list[dict]:
    """解析 SRT / VTT；自动去重 YouTube 滚动字幕。"""
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    seen = set()
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [x for x in block.splitlines() if x.strip()]
        if not lines:
            continue
        # 找时间戳行，SRT/VTT 都可能带有 cue id / settings
        ti = next((i for i, x in enumerate(lines) if "-->" in x), None)
        if ti is None:
            continue
        m = TS_RE.search(lines[ti])
        if not m:
            continue
        text = " ".join(lines[ti + 1:]).strip()
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        # 滚动字幕会连续重复同一行
        key = (round(_ts(*m.groups()[:4]), 1), text)
        if text in seen and cues and cues[-1]["text"] == text:
            continue
        seen.add(text)
        cues.append({
            "i": len(cues) + 1,
            "start": round(_ts(*m.groups()[:4]), 3),
            "end": round(_ts(*m.groups()[4:]), 3),
            "text": text,
        })
    return cues


def parse_lang(path: Path) -> str:
    parts = path.name.split(".")
    return parts[-2] if len(parts) >= 3 else "unknown"


def write_outputs(out_dir: Path, platform: str, vid: str,
                  subs: dict[str, Path]) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    parsed = {lang: parse_subs(p) for lang, p in subs.items()}
    parsed = {lang: cues for lang, cues in parsed.items() if cues}
    if not parsed:
        meta = {"status": "nosub", "platform": platform, "id": vid,
                "cues": [], "count": 0}
        (out_dir / "cues.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    priority = LANGS.get(platform, [])
    primary_lang = next((l for l in priority if l in parsed), sorted(parsed)[0])
    primary = parsed[primary_lang]
    for lang, cues in parsed.items():
        (out_dir / f"cues_{lang}.json").write_text(
            json.dumps({"platform": platform, "id": vid, "lang": lang,
                        "count": len(cues), "cues": cues},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "cues.json").write_text(
        json.dumps({"platform": platform, "id": vid, "lang": primary_lang,
                    "count": len(primary), "cues": primary},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "transcript.txt").write_text(
        "\n".join(c["text"] for c in primary), encoding="utf-8")
    return {
        "status": "ok", "platform": platform, "id": vid,
        "lang": primary_lang, "count": len(primary), "cues": primary,
        "available": {l: len(c) for l, c in parsed.items()},
    }


def fetch(url: str, platform: str, out_dir: Path, cookies: Path | None,
          cookies_from_browser: str | None, proxy: str | None,
          js_runtimes: str | None, extractor_args: str | None,
          langs: list[str], direct: bool = False) -> dict[str, Path]:
    subs_dir = out_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)
    base = subs_dir / "ytdlp_%(id)s"
    cmd = find_ytdlp() + [
        "--no-warnings", "--no-update", "--no-playlist",
        "--skip-download", "--ignore-no-formats",
        "--sub-langs", ",".join(langs),
        "--sub-format", "vtt" if platform == "youtube" else "srt/vtt/best",
        "-o", str(base), url,
    ]
    # B 站 ai-zh 是普通字幕；YouTube 用自动字幕轨道
    if platform == "youtube":
        cmd += ["--write-auto-subs"]
    else:
        cmd += ["--write-subs"]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    elif cookies:
        cmd += ["--cookies", str(cookies)]
    if direct:
        # B 站默认直连：显式空 proxy 可绕开 Windows 系统代理
        cmd += ["--proxy", ""]
    elif proxy:
        cmd += ["--proxy", proxy]
    if js_runtimes:
        cmd += ["--js-runtimes", js_runtimes]
    if extractor_args:
        cmd += ["--extractor-args", extractor_args]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp 超过 300 秒未完成；检查网络/代理或 cookies")
    if res.returncode != 0:
        err = (res.stderr or "").strip().splitlines()[-10:]
        msg = "yt-dlp 抓字幕失败：\n" + "\n".join(err)
        if platform == "youtube":
            msg += ("\n提示：YouTube 通常需要 ① 代理 --proxy socks5://127.0.0.1:1080；"
                    "② 新鲜 cookies（--cookies 或 --cookies-from-browser chrome）；"
                    "③ --js-runtimes node。")
        raise RuntimeError(msg)
    found = {}
    for p in glob.glob(str(subs_dir / "ytdlp_*.*")):
        path = Path(p)
        if path.suffix.lower() not in {".srt", ".vtt"}:
            continue
        found[parse_lang(path)] = path
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="字幕抓取（B 站 + YouTube）")
    ap.add_argument("target", nargs="?",
                    help="视频 URL / BV / YouTube ID；配合 --from-srt 时可省略")
    ap.add_argument("-o", "--output", help="输出目录（默认 ./video_deepread/<id>）")
    ap.add_argument("--cookies", help="cookies 文件；B 站默认 ~/.claude/cookies/bilibili.txt")
    ap.add_argument("--no-cookies", action="store_true", help="不传 cookies")
    ap.add_argument("--cookies-from-browser", help="从浏览器读 cookies（如 chrome/edge/firefox）")
    ap.add_argument("--proxy", help="代理，如 socks5://127.0.0.1:1080")
    ap.add_argument("--js-runtimes", default="node",
                    help="JS runtime（YouTube 解 n-challenge 用，默认 node；传空字符串关闭）")
    ap.add_argument("--extractor-args", help="yt-dlp extractor args（默认不指定，"
                    "让 yt-dlp 用默认 client set）")
    ap.add_argument("--langs", help="字幕语言优先级，逗号分隔（默认按平台）")
    ap.add_argument("--from-srt", help="离线模式：解析已有 SRT/VTT，不联网")
    ap.add_argument("--force", action="store_true", help="忽略缓存，重新抓取")
    ap.add_argument("--json-summary", action="store_true", help="只打印精简 JSON")
    args = ap.parse_args(argv)

    from_srt = Path(args.from_srt).expanduser().resolve() if args.from_srt else None
    if from_srt and not from_srt.exists():
        print(f"ERROR: 字幕文件不存在：{from_srt}", file=sys.stderr)
        return 1

    src = args.target or (from_srt.stem if from_srt else "")
    platform = detect_platform(src)
    if platform == "unknown" and from_srt:
        platform = "bilibili" if "BV" in from_srt.name else "youtube"
    try:
        vid = extract_id(src, platform) if src else from_srt.stem.split(".")[0]
    except ValueError:
        vid = from_srt.stem.split(".")[0] if from_srt else "unknown"

    out_dir = Path(args.output).expanduser().resolve() if args.output else \
        Path.cwd() / "video_deepread" / vid

    if from_srt:
        meta = write_outputs(out_dir, platform, vid, {parse_lang(from_srt): from_srt})
    else:
        cached = out_dir / "cues.json"
        if cached.exists() and not args.force:
            meta = json.loads(cached.read_text(encoding="utf-8"))
            meta["cached"] = True
        else:
            langs = [x.strip() for x in args.langs.split(",") if x.strip()] \
                if args.langs else LANGS.get(platform, LANGS["bilibili"])
            cookies = None
            if not args.no_cookies and not args.cookies_from_browser:
                if args.cookies:
                    cookies = Path(args.cookies).expanduser()
                elif DEFAULT_COOKIES.get(platform) and DEFAULT_COOKIES[platform].exists():
                    cookies = DEFAULT_COOKIES[platform]
                elif platform == "bilibili":
                    print(f"ERROR: cookies 不存在：{DEFAULT_COOKIES['bilibili']}\n"
                          "B 站软字幕通常需要 cookies；可先导出 Netscape 格式的 "
                          "bilibili.txt，或用 --no-cookies 试公开视频。", file=sys.stderr)
                    return 1
            if cookies and not cookies.exists():
                print(f"ERROR: cookies 不存在：{cookies}", file=sys.stderr)
                return 1
            try:
                direct = (platform == "bilibili" and not args.proxy)
                extractor_args = args.extractor_args
                subs = fetch(args.target, platform, out_dir, cookies,
                             args.cookies_from_browser, args.proxy,
                             args.js_runtimes or None, extractor_args,
                             langs, direct=direct)
            except RuntimeError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            meta = write_outputs(out_dir, platform, vid, subs)

    summary = {
        "status": meta.get("status"), "platform": platform, "id": vid,
        "lang": meta.get("lang"), "count": meta.get("count", 0),
        "available": meta.get("available", {}), "out_dir": str(out_dir),
        "cached": bool(meta.get("cached")),
    }
    if meta.get("status") == "nosub":
        summary["hint"] = "没有软字幕；改走纯画面，或另做 ASR/硬字幕 OCR"
        print(json.dumps(summary, ensure_ascii=False) if args.json_summary
              else "nosub：没有抓到软字幕。", file=sys.stderr)
    elif args.json_summary:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"OK {platform}/{vid}  lang={summary['lang']}  cues={summary['count']}  "
              f"available={summary['available']}  -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
