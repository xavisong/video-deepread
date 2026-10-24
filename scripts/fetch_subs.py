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
    """
    优先用 `python -m yt_dlp`（python 环境里的模块），而不是 PATH 里的 yt-dlp.exe。

    2026-10 实测：本机 PATH 的 yt-dlp.exe 是 2026.06.09，比 python 模块（2026.07.04）
    旧一个版本，拉 B 站网页会 `RemoteDisconnected`；换模块同一个 URL 立刻成功。
    YouTube 侧 skill 文档也已明确要 `python -m yt_dlp`（n-challenge）。
    """
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        pass
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


def _as_netscape(path: Path, work_dir: Path) -> Path:
    """
    Cookie 文件格式兜底：浏览器插件默认导的是 **JSON**（Cookie-Editor 的 Export），
    yt-dlp 只认 **Netscape**。实测用户按插件默认导出 → 直接喂 yt-dlp 会报
    "does not look like a Netscape format cookies file"。这里自动转换，别让人手工转。

    返回可用于 yt-dlp 的路径（若是 JSON，写一份到 work_dir 下的临时文件）。
    """
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return path
    if not raw or raw[0] not in "[{":
        return path
    try:
        data = json.loads(raw)
    except ValueError:
        return path
    rows = data if isinstance(data, list) else (data.get("cookies") or [])
    if not rows:
        return path
    lines = ["# Netscape HTTP Cookie File"]
    for c in rows:
        if not isinstance(c, dict) or "name" not in c:
            continue
        exp = "0" if c.get("session") else str(int(float(c.get("expirationDate") or 0)))
        domain = c.get("domain", "")
        if not c.get("hostOnly"):
            domain = domain.lstrip(".")
        lines.append("\t".join([
            domain, "TRUE", c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE", exp,
            c["name"], str(c.get("value", "")).replace("\t", "").replace("\n", ""),
        ]))
    tmp = work_dir / ".cookies_tmp.txt"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp


def cookie_arg(cookies: Path, work_dir: Path) -> tuple[Path, Path | None]:
    """
    yt-dlp 的 --cookies 语义是 "read cookies from and **dump** cookie jar in"：
    退出时会把 cookie jar 写回同一个文件。cookies 通常放在 ~/.claude/cookies/，
    在受限环境（只读文件系统 / 沙箱 / 只读账户）里没有写权限 → yt-dlp 在 close()
    时抛 PermissionError、退出码非 0，于是**字幕明明抓到了却被判失败**。

    所以：能写就原样用；写不了就复制一份到输出目录，用完删掉（副本不长期落盘）。
    JSON 导出的 cookie 先自动转成 Netscape（同样是临时文件）。
    返回 (传给 yt-dlp 的路径, 需要清理的临时文件或 None)。
    """
    converted = _as_netscape(cookies, work_dir)
    if converted != cookies:
        return converted, converted
    try:
        with open(cookies, "r+b"):
            pass
        return cookies, None
    except OSError:
        tmp = work_dir / ".cookies_tmp.txt"
        shutil.copy2(cookies, tmp)
        return tmp, tmp


def probe_page(work_dir: Path, url: str, cookies: Path | None,
               cookies_from_browser: str | None,
               proxy: str | None) -> tuple[bool, str]:
    """
    便宜的"页面到底能不能拉"探测（只取标题，不下载任何东西）。

    存在的理由：YouTube 用 `--write-auto-subs` 抓字幕时，bot-check 会伪装成
    `[info] There are no subtitles for the requested languages` 并且**退出码 0**，
    光看退出码分不清"这个视频真没字幕"和"页面被风控拦了"。两者处理方式完全不同
    （一个换 --langs，一个必须换 cookie），所以必须探一次。
    返回 (页面可用?, 诊断片段)。探测本身超时不算认证失败，避免误报。
    """
    cmd = find_ytdlp() + ["--no-warnings", "--no-update", "--no-playlist",
                          "--skip-download", "--print", "%(title)s", url]
    cookie_tmp = None
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    elif cookies:
        use, cookie_tmp = cookie_arg(cookies, work_dir)
        cmd += ["--cookies", str(use)]
    if proxy:
        cmd += ["--proxy", proxy]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return True, "probe timeout"
    finally:
        if cookie_tmp is not None:
            try:
                cookie_tmp.unlink()
            except OSError:
                pass
    out = ((res.stdout or "") + (res.stderr or "")).strip()
    low = out.lower()
    bot = ("sign in to confirm" in low) or ("not a bot" in low)
    last = out.splitlines()[-1][:300] if out else f"rc={res.returncode}"
    if res.returncode != 0 or bot:
        return False, last
    return True, last


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

    # 字幕轨存在 ≠ 有内容：实测 B 站有些视频的 ai-zh 只有 3 条「♪ 音乐 ♪」，
    # 却有 5 分钟时长。这种"稀疏轨"如果按 ok 返回，语义层会拿 3 句歌词生成一份
    # 装着 100% 覆盖率的假计划——比没有字幕更危险，所以按 nosub 处理。
    text = "".join(c["text"] for c in primary)
    chars = len(re.sub(r"\s", "", text))
    span = float(primary[-1]["end"]) if primary else 0.0
    rate = (chars / span) if span > 0 else 0.0
    sparse = span > 60 and (chars < 100 or rate < 0.3)

    status = "nosub" if sparse else "ok"
    meta = {"status": status, "platform": platform, "id": vid,
            "lang": primary_lang, "count": len(primary), "cues": primary,
            "available": {l: len(c) for l, c in parsed.items()},
            "chars": chars, "chars_per_sec": round(rate, 2),
            "span": round(span, 1)}
    if sparse:
        meta["reason"] = "track_too_sparse"
        meta["hint"] = (f"字幕轨只有 {len(primary)} 条 / {chars} 字（{rate:.2f} 字/秒），"
                        "等于没有内容：改走纯画面，或另做 ASR / 硬字幕 OCR")
    (out_dir / "cues.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "transcript.txt").write_text(text, encoding="utf-8")
    return meta


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
    cookie_tmp = None
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    elif cookies:
        use_cookies, cookie_tmp = cookie_arg(cookies, out_dir)
        cmd += ["--cookies", str(use_cookies)]
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
    finally:
        if cookie_tmp is not None:
            try:
                cookie_tmp.unlink()
            except OSError:
                pass
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
            # YouTube 的 bot-check 在 --write-auto-subs 路径上表现为
            # 「There are no subtitles for the requested languages」+ 退出码 0，
            # 和"真没字幕"长得一模一样。这里补一次便宜探测把两者分开：
            # 页面都拉不到 = 认证问题（cookie 过期/出口不一致），必须让人去换 cookie；
            # 页面能拉 = 才是真的没这几种语言的字幕，换 --langs 再试。
            if platform == "youtube" and meta.get("status") == "nosub":
                ok, detail = probe_page(out_dir, args.target, cookies,
                                        args.cookies_from_browser, args.proxy)
                if not ok:
                    meta["status"] = "auth_required"
                    meta["reason"] = "youtube_bot_check"
                    meta["detail"] = detail
                    meta["hint"] = (
                        "YouTube 不是没字幕，而是页面被风控拦了（Sign in to confirm "
                        "you're not a bot）→ 重新导出 cookies（Chrome 走哪个出口导出，"
                        "yt-dlp 就带同一个 --proxy），导出后立刻用；cookie 只有十几分钟寿命。"
                        "也行：关掉 Chrome 后 --cookies-from-browser chrome。")

    summary = {
        "status": meta.get("status"), "platform": platform, "id": vid,
        "lang": meta.get("lang"), "count": meta.get("count", 0),
        "available": meta.get("available", {}), "out_dir": str(out_dir),
        "cached": bool(meta.get("cached")),
        "chars": meta.get("chars"), "chars_per_sec": meta.get("chars_per_sec"),
    }
    for k in ("reason", "hint", "detail"):
        if meta.get(k):
            summary[k] = meta[k]
    if meta.get("status") == "nosub":
        summary.setdefault("hint", "没有软字幕；改走纯画面，或另做 ASR/硬字幕 OCR")
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
