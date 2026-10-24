#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频深读 文本层 + 对齐层自检（离线，不联网、不读真实图片）"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fetch_subs import parse_subs, write_outputs  # noqa: E402
from semantic_frames import (  # noqa: E402
    bars_in, levels_in, tag_cue, nearest_kf, merge_need, select_frames,
)

SRT = """1
00:00:00,000 --> 00:00:02,000
大家好

2
00:00:02,000 --> 00:00:05,000
第7根K线是空头突破棒

3
00:00:05,000 --> 00:00:08,000
第12和13根在6800附近的阻力位
"""


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if not cond else ""))
    if not cond:
        raise SystemExit(1)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        srt = td / "test.ai-zh.srt"
        srt.write_text(SRT, encoding="utf-8")

        cues = parse_subs(srt)
        check("srt parse count", len(cues) == 3, str(len(cues)))
        check("srt parse text", cues[1]["text"] == "第7根K线是空头突破棒")
        check("srt parse time", abs(cues[2]["start"] - 5.0) < 1e-6)

        vtt = td / "test.zh-Hans.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<c>hello</c>\n",
            encoding="utf-8")
        check("vtt parse", len(parse_subs(vtt)) == 1)

        out = td / "out"
        meta = write_outputs(out, "bilibili", "BVtest", {"ai-zh": srt})
        check("write cues.json", (out / "cues.json").exists())
        check("write transcript.txt", (out / "transcript.txt").exists())
        check("write count", meta["count"] == 3, str(meta.get("count")))

        check("bars cn", bars_in("第七根和第八根K线的低点", {}) == [7, 8],
              str(bars_in("第七根和第八根K线的低点", {})))
        check("bars cn+ar", bars_in("第12和13根在阻力位", {}) == [12, 13])
        check("bars range", bars_in("第3~6根K线", {}) == [3, 4, 5, 6])
        check("bars asr fix", bars_in("D师傅跟K线是L2", {"D师傅跟": 14}) == [14])
        check("bars tolerant", bars_in("第11肋根", {}) == [11])

        terms, prices = levels_in("6800附近的50%回撤，2026年3月17日")
        check("level terms", "回撤" in terms, str(terms))
        check("price 6800", "6800" in prices, str(prices))
        check("price 50%", "50%" in prices, str(prices))
        check("year excluded", "2026" not in prices, str(prices))
        _, nprices = levels_in("ASD-STE100 有约900个许可词")
        check("non-price numbers ignored", nprices == [], str(nprices))
        _, pprices = levels_in("价格在6800附近")
        check("price with context", "6800" in pprices, str(pprices))

        check("tag bar", tag_cue("第7根K线是空头突破棒", {})["type"] == "bar")
        check("tag bar+level", tag_cue("第14根在阻力位", {})["type"] == "bar+level")
        check("tag level", tag_cue("价格在均线附近", {})["type"] == "level")
        check("tag price", tag_cue("6800附近", {})["type"] == "price")
        check("tag none", tag_cue("大家好", {})["type"] == "none")
        check("tag english level",
              tag_cue("Price is testing resistance near this level", {})["type"] == "level")
        check("tag english draw",
              "look at" in tag_cue("Look at this chart", {})["draws"])

        check("nearest kf", nearest_kf([0.0, 10.0, 20.0], 11.0) == 1)
        check("nearest kf end", nearest_kf([0.0, 10.0, 20.0], 16.0) == 2)

        rule = {"type": "bar", "bars": [7], "terms": [], "prices": []}
        llm = {"type": "bar+level", "bars": [7], "terms": ["阻力"],
               "prices": [], "confidence": 0.8}
        merged = merge_need(rule, llm)
        check("merge type", merged["type"] == "bar+level", str(merged))
        check("merge terms", "阻力" in merged["terms"], str(merged))
        low = merge_need(rule, {**llm, "confidence": 0.3, "type": "bar"})
        check("low conf keeps rule", low["type"] == "bar", str(low))

        rule56 = {"type": "bar", "bars": [56], "terms": [], "prices": []}
        llm56 = {"type": "bar", "bars": [5, 6], "terms": [], "prices": [],
                 "confidence": 0.8}
        m56 = merge_need(rule56, llm56)
        check("high conf replaces bars", m56["bars"] == [5, 6], str(m56))
        m56u = merge_need(rule56, {**llm56, "confidence": 0.5})
        check("low conf unions bars", m56u["bars"] == [5, 6, 56], str(m56u))

        groups = [
            {"kf_path": "a.jpg", "kf_time": 0.0, "score_type": "bar",
             "watch": [], "bars": [1], "terms": [], "prices": [],
             "cues": [1], "texts": ["a"]},
            {"kf_path": "b.jpg", "kf_time": 20.0, "score_type": "bar",
             "watch": [7], "bars": [7], "terms": [], "prices": [],
             "cues": [2], "texts": ["b"]},
            {"kf_path": "c.jpg", "kf_time": 40.0, "score_type": "level",
             "watch": [], "bars": [], "terms": ["阻力"], "prices": [],
             "cues": [3], "texts": ["c"]},
            {"kf_path": "d.jpg", "kf_time": 60.0, "score_type": "price",
             "watch": [], "bars": [], "terms": [], "prices": ["6800"],
             "cues": [4], "texts": ["d"]},
        ]
        picked = select_frames(groups, 3, 15.0, {7})
        check("select cap", len(picked) <= 3, str(len(picked)))
        check("select watch", any(g["kf_time"] == 20.0 for g in picked))

        # CLI 端到端
        cues_path = out / "cues.json"
        kf_path = td / "keyframes.json"
        kf_path.write_text(json.dumps({
            "keyframes": [
                {"n": 1, "time": 0.5, "path": str(td / "f1.jpg")},
                {"n": 2, "time": 3.0, "path": str(td / "f2.jpg")},
                {"n": 3, "time": 6.0, "path": str(td / "f3.jpg")},
                {"n": 4, "time": 9.0, "path": str(td / "f4.jpg")},
            ]
        }), encoding="utf-8")
        res = subprocess.run(
            [sys.executable, str(HERE / "semantic_frames.py"),
             str(cues_path), str(kf_path), "-o", str(out), "--max-frames", "3"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("cli exit", res.returncode == 0, res.stderr[-300:])
        plan = json.loads((out / "frame_plan.json").read_text(encoding="utf-8"))
        check("cli plan cap", len(plan) <= 3, str(len(plan)))
        check("cli needs file", (out / "semantic_needs.json").exists())
        check("cli plan md", (out / "frame_plan.md").exists())
        check("cli top plan", (out / "frame_plan_top.md").exists())
        check("cli plan score", bool(plan) and "score" in plan[0])
        cov_path = out / "coverage.json"
        check("cli coverage file", cov_path.exists())
        cov = json.loads(cov_path.read_text(encoding="utf-8"))
        check("coverage has bars", "bars_mentioned" in cov and "bars_covered" in cov)
        check("coverage needs total", cov.get("needs_total", 0) > 0,
              str(cov.get("needs_total")))
        check("cli read json", (out / "frame_plan_read.json").exists())
        check("cli read budget", cov.get("read_budget") == 1,
              str(cov.get("read_budget")))
        check("cli read coverage", "read_bar_coverage" in cov)

        # --full：完整计划（所有相关帧），而不是 20/40 预算
        full_out = td / "full"
        res_full = subprocess.run(
            [sys.executable, str(HERE / "semantic_frames.py"),
             str(cues_path), str(kf_path), "-o", str(full_out), "--full"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("full cli exit", res_full.returncode == 0, res_full.stderr[-300:])
        plan_full = json.loads((full_out / "frame_plan.json").read_text(encoding="utf-8"))
        check("full selects all relevant frames", len(plan_full) == 2, str(len(plan_full)))
        cov_full = json.loads((full_out / "coverage.json").read_text(encoding="utf-8"))
        check("full mode flag", cov_full.get("mode") == "full", str(cov_full.get("mode")))
        check("full bar coverage", cov_full.get("bar_coverage") == "3/3",
              str(cov_full.get("bar_coverage")))
        check("full top plan", (full_out / "frame_plan_top.md").exists())

    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
