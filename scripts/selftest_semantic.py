#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频深读 文本层 + 对齐层自检（离线，不联网、不读真实图片）"""

from __future__ import annotations

import json
import subprocess
import sys
import contextlib
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fetch_subs import _as_netscape, parse_subs, write_outputs  # noqa: E402
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


def _workdir():
    """
    自检用的可写目录。三级兜底，因为技能本体常常装在只读位置：

    1. 系统临时区（`mkdtemp`，**不删**——只有几 KB，换干净的输出）；
    2. 当前工作目录（DSH 里就是工作区，可写；沙箱只读临时区时这一级救命）；
    3. 技能目录（普通环境兜底）。

    任意一级都要先做**真实写入探测**再返回：临时区在受限环境里经常"建得出、写不进"。
    三级都不行就明确报错，别用 PermissionError 半死不活地崩。
    """
    candidates = [
        lambda: Path(tempfile.mkdtemp(prefix="deepread_selftest_")),
        lambda: Path.cwd() / ".deepread_selftest",
        lambda: Path(__file__).resolve().parent.parent / ".selftest_tmp",
    ]
    errs = []
    for make in candidates:
        try:
            d = make()
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".probe"
            probe.write_bytes(b"ok")
            probe.unlink()
            return contextlib.nullcontext(str(d))
        except OSError as e:
            errs.append(f"{e}")
    raise SystemExit("找不到可写目录跑自检（已试临时区/当前目录/技能目录）：" + " | ".join(errs))


def main() -> int:
    with _workdir() as td:
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

        # 稀疏字幕轨（实测 B 站有些 ai-zh 只有 3 条「♪ 音乐 ♪」却有 5 分钟时长）
        # 必须判成 nosub，否则语义层会拿几句歌词生成一份高覆盖率的假计划。
        sparse_srt = td / "sparse.ai-zh.srt"
        sparse_srt.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n♪ 音乐 ♪\n\n"
            "2\n00:02:00,000 --> 00:02:01,000\n音乐\n\n"
            "3\n00:05:10,000 --> 00:05:11,000\n♪ 音乐 ♪\n",
            encoding="utf-8")
        sparse_meta = write_outputs(td / "sparse_out", "bilibili", "BVsparse",
                                    {"ai-zh": sparse_srt})
        check("稀疏轨判 nosub", sparse_meta["status"] == "nosub", str(sparse_meta.get("status")))
        check("稀疏轨给 reason", sparse_meta.get("reason") == "track_too_sparse")
        check("稀疏轨带密度指标", sparse_meta.get("chars_per_sec", 1) < 0.3,
              str(sparse_meta.get("chars_per_sec")))
        # 正常密度的字幕不能被误判
        ok_meta = write_outputs(td / "ok_out", "bilibili", "BVok", {"ai-zh": srt})
        check("正常字幕仍为 ok", ok_meta["status"] == "ok", str(ok_meta.get("status")))
        # 浏览器插件默认导 JSON，yt-dlp 只认 Netscape —— 必须自动转（实测用户每次都踩）
        jck = td / "cookies.json"
        jck.write_text(
            '[{"domain": ".youtube.com", "hostOnly": false, "path": "/",'
            ' "secure": true, "expirationDate": 1827381740, "name": "__Secure-3PSID",'
            ' "value": "abc"}, {"domain": ".youtube.com", "secure": true,'
            ' "session": true, "name": "PREF", "value": "x"}]',
            encoding="utf-8")
        conv = _as_netscape(jck, td / "ckout")
        body = [l for l in conv.read_text(encoding="utf-8").splitlines() if l]
        check("JSON cookie 自动转 Netscape", body[0].startswith("# Netscape"), body[0][:20])
        check("JSON cookie 条目数", len(body) == 3, str(len(body) - 1))
        check("会话 cookie 过期时间写 0", body[2].split("\t")[4] == "0", body[2].split("\t")[4])
        # Netscape 输入必须原样放行，不能被当成 JSON 再转一次
        check("Netscape 原样放行", _as_netscape(srt, td / "ckout2") == srt)

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
