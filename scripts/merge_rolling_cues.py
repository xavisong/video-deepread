#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 YouTube 逐词滚动自动字幕合并成句子（按最长词级重叠去重）。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w).lower()


def merge(cues: list[dict]) -> list[dict]:
    words: list[tuple[str, float, float]] = []
    for c in cues:
        b = c["text"].split()
        a = [w for w, _, _ in words]
        k = 0
        for kk in range(min(len(a), len(b)), 0, -1):
            if [norm(x) for x in a[-kk:]] == [norm(x) for x in b[:kk]]:
                k = kk
                break
        new = b[k:]
        dur = (c["end"] - c["start"]) / max(1, len(b))
        for j, w in enumerate(new):
            ws = c["start"] + (j + k) * dur
            words.append((w, ws, min(c["end"], ws + dur)))
        # 末尾只差标点时，把标点补回最后一个词
        if k and not new and b:
            last_b, last_a = b[-1], words[-1][0]
            if norm(last_b) == norm(last_a) and last_b != last_a:
                words[-1] = (last_b, words[-1][1], words[-1][2])

    sentences: list[tuple[float, float, str]] = []
    cur: list[str] = []
    start = None
    last_end = None
    for w, ws, we in words:
        if start is None:
            start = ws
        if last_end is not None and ws - last_end > 1.5 and cur:
            sentences.append((start, last_end, " ".join(cur)))
            cur = []
            start = ws
        cur.append(w)
        last_end = we
        if re.search(r"[.!?]$", w) or len(cur) >= 45:
            if cur:
                sentences.append((start, last_end, " ".join(cur)))
                cur = []
                start = None
    if cur:
        sentences.append((start, last_end, " ".join(cur)))

    out = []
    for s, e, t in sentences:
        t = re.sub(r"\s+([.,!?;:])", r"\1", t)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            out.append({"i": len(out) + 1, "start": round(s or 0, 3),
                        "end": round(e or 0, 3), "text": t})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="合并 YouTube 逐词滚动自动字幕")
    ap.add_argument("cues", help="fetch_subs.py 产出的 cues.json")
    ap.add_argument("-o", "--output", help="输出 cues JSON（默认 cues_merged.json）")
    ap.add_argument("--txt", help="输出纯文本（默认 transcript_merged.txt）")
    args = ap.parse_args(argv)

    src = Path(args.cues)
    data = json.loads(src.read_text(encoding="utf-8"))
    cues = data.get("cues", data) if isinstance(data, dict) else data
    out = merge(cues)
    base = src.parent
    op = Path(args.output) if args.output else base / "cues_merged.json"
    tp = Path(args.txt) if args.txt else base / "transcript_merged.txt"
    payload = {
        "lang": data.get("lang") if isinstance(data, dict) else None,
        "count": len(out),
        "cues": out,
    }
    op.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tp.write_text("\n".join(x["text"] for x in out), encoding="utf-8")
    print(f"merged {len(cues)} -> {len(out)} sentences -> {op}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
