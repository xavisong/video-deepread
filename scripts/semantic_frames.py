#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
semantic_frames.py —— 对齐层：按字幕语义挑补帧

输入：
  cues.json        fetch_bili_subs.py 的输出（带时间戳的字幕段）
  keyframes.json   extract_keyframes.py 的输出
可选：
  --needs          LLM 过字幕产出的视觉需求（严格 JSON；见 SKILL.md）
  --asr-fixes      视频专属 ASR 错字映射（JSON: {"D师傅跟": 14}）
  --watch          必须覆盖的 bar 号，逗号分隔（如 7,14,19）

输出：
  semantic_needs.json   所有带视觉需求的 cue
  frame_plan.json       选中的补帧（默认 ≤15 张）
  frame_plan.md         给人看的对照表

注意：这是“候选帧”，不是逐 bar 精确对齐；帧映射只按时间最近邻，不靠 LLM 猜帧号。
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import sys
from pathlib import Path

CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
LEVEL_TERMS = ["均线", "EMA", "通道", "趋势线", "支撑", "阻力", "买入区",
               "突破点", "回撤", "跳空", "缺口", "水平线", "收盘价", "高点", "低点",
               # English
               "support", "resistance", "level", "zone", "structure", "breakout",
               "retest", "supply", "demand", "trend", "range", "chart", "line",
               "order block", "liquidity", "imbalance"]
DRAW_TERMS = ["画", "这条线", "这里", "这个位置", "看这里", "上面", "下面",
              # English
              "this line", "this level", "this zone", "this chart", "look at",
              "here", "this area", "right here"]
PRICE_CONTEXT = ["价格", "点位", "止损", "目标", "支撑", "阻力", "上方", "下方",
                 "买入", "卖出", "收盘", "高点", "低点", "突破", "回撤", "点",
                 "附近", "一线", "关口",
                 # English
                 "price", "level", "support", "resistance", "target", "stop",
                 "above", "below", "around", "near", "zone", "point"]
NEED_ORDER = {"none": 0, "price": 1, "level": 2, "bar": 3, "bar+level": 4}


def cn2num(s: str):
    if s.isdigit():
        return int(s)
    total = cur = 0
    for ch in s:
        if ch in CN_DIGITS:
            cur = CN_DIGITS[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        else:
            return None
    return total + cur


def bars_in(text: str, fixes: dict[str, int]) -> list[int]:
    nums = []
    for bad, good in fixes.items():
        if bad in text and isinstance(good, int):
            nums.append(good)
    for pat in (
        r"第\s*(\d{1,2})\s*[根跟]",
        r"第\s*(\d{1,2})\s*K",
        r"(?:bar|Bar|BAR)\s*(\d{1,2})",
        r"第\s*(\d{1,2})\s*[^\s，。、]{0,3}?[根跟]",
    ):
        nums += [int(x) for x in re.findall(pat, text)]
    for s in re.findall(r"第\s*([一二三四五六七八九十两]{1,3})\s*[根跟]", text):
        n = cn2num(s)
        if n:
            nums.append(n)
    for a, b in re.findall(
        r"第\s*(\d{1,2})\s*和\s*(\d{1,2})\s*[根跟]", text):
        nums += [int(a), int(b)]
    for a, b in re.findall(
        r"第\s*([一二三四五六七八九十两]{1,3})\s*和\s*"
        r"([一二三四五六七八九十两]{1,3})\s*[根跟]", text):
        na, nb = cn2num(a), cn2num(b)
        if na and nb:
            nums += [na, nb]
    for a, b in re.findall(
        r"第?\s*([一二三四五六七八九十两]{1,3})\s*[~～\-到至]\s*"
        r"([一二三四五六七八九十两]{1,3})\s*[根跟]", text):
        na, nb = cn2num(a), cn2num(b)
        if na and nb and 0 < nb - na <= 20:
            nums += list(range(na, nb + 1))
    for a, b in re.findall(
        r"第?\s*(\d{1,2})\s*[~～\-到至]\s*(\d{1,2})\s*[根跟]", text):
        na, nb = int(a), int(b)
        if 0 < nb - na <= 20:
            nums += list(range(na, nb + 1))
    return sorted({n for n in nums if 1 <= n <= 100})


def _has(term: str, text: str) -> bool:
    """英文词用词边界匹配，中文按子串匹配。"""
    if re.search(r"[A-Za-z]", term):
        return re.search(r"\b" + re.escape(term.lower()) + r"\b",
                         text.lower()) is not None
    return term in text


def levels_in(text: str):
    terms = [t for t in LEVEL_TERMS if _has(t, text)]
    prices = []
    for m in re.finditer(r"(?<!\d)(\d{1,3})\s*%", text):
        prices.append(m.group(1) + "%")
    # 只把“有价格语境”的独立 3–5 位数当价位，避免把 STE100 / 900 个许可词
    # 这类普通数字误判成价格；同时排除嵌在英文/数字里的数（STE100）。
    has_ctx = any(_has(t, text) for t in PRICE_CONTEXT)
    if has_ctx:
        for m in re.finditer(r"(?<![A-Za-z0-9])(\d{3,5})(?![A-Za-z0-9])", text):
            n = int(m.group(1))
            if 1900 <= n <= 2100:  # 年份
                continue
            prices.append(str(n))
    return terms, prices


def tag_cue(text: str, fixes: dict[str, int]) -> dict:
    bars = bars_in(text, fixes)
    terms, prices = levels_in(text)
    draws = [t for t in DRAW_TERMS if _has(t, text)]
    if bars and (terms or prices or draws):
        typ = "bar+level"
    elif bars:
        typ = "bar"
    elif terms or draws:
        typ = "level"
    elif prices:
        typ = "price"
    else:
        typ = "none"
    return {"type": typ, "bars": bars, "terms": terms, "prices": prices,
            "draws": draws, "source": "rule", "confidence": None}


def load_cues(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cues = data.get("cues", []) if isinstance(data, dict) else data
    return [c for c in cues if isinstance(c, dict) and "start" in c and "text" in c]


def load_llm_needs(path: Path | None) -> dict[int, dict]:
    if not path:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("cues", []) if isinstance(data, dict) else data
        out = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            cue = r.get("cue")
            if not isinstance(cue, int):
                continue
            typ = r.get("type", "none")
            if typ not in NEED_ORDER:
                typ = "none"
            bars = [int(x) for x in (r.get("bars") or [])
                    if isinstance(x, (int, float)) and 1 <= int(x) <= 100]
            out[cue] = {
                "type": typ, "bars": sorted(set(bars)),
                "terms": [str(x) for x in (r.get("terms") or [])],
                "prices": [str(x) for x in (r.get("prices") or [])],
                "draws": [str(x) for x in (r.get("draws") or [])],
                "source": "llm",
                "confidence": r.get("confidence"),
                "why": r.get("why"),
            }
        return out
    except Exception as e:  # noqa: BLE001
        print(f"WARN: --needs 解析失败，回退纯规则：{e}", file=sys.stderr)
        return {}


def merge_need(rule: dict, llm: dict | None) -> dict:
    if not llm:
        return rule
    conf = llm.get("confidence")
    if conf is not None and conf < 0.5 and NEED_ORDER[rule["type"]] >= NEED_ORDER[llm["type"]]:
        return rule
    # 高置信度且给了 bars：以 LLM 为准（用于纠正 ASR 把 "5/6" 读成 "56" 这类误判）；
    # 否则与规则取并集，避免漏。
    merged = dict(llm)
    high = conf is not None and conf >= 0.7
    if high and llm.get("bars"):
        merged["bars"] = sorted(set(llm["bars"]))
    else:
        merged["bars"] = sorted(set(llm.get("bars", [])) | set(rule["bars"]))
    merged["terms"] = sorted(set(llm.get("terms", [])) | set(rule["terms"]))
    merged["prices"] = sorted(set(llm.get("prices", [])) | set(rule["prices"]))
    if NEED_ORDER[rule["type"]] > NEED_ORDER[merged["type"]]:
        merged["type"] = rule["type"]
    return merged


def nearest_kf(times: list[float], target: float) -> int:
    if not times:
        return -1
    idx = bisect.bisect_left(times, target)
    cand = [i for i in (idx - 1, idx, idx + 1) if 0 <= i < len(times)]
    return min(cand, key=lambda i: abs(times[i] - target))


def score_of(g: dict) -> int:
    # 同分时优先选“一帧覆盖多根 bar”的画面，提高覆盖率
    return (NEED_ORDER[g["score_type"]] + (2 if g["watch"] else 0)
            + min(2, len(g["watch"])) + min(3, len(g["bars"]) // 2))


def select_frames(groups: list[dict], max_frames: int, min_gap: float,
                  watch: set[int]) -> list[dict]:
    def score(g):
        return score_of(g)

    ordered = sorted(groups, key=lambda g: (-score(g), g["kf_time"]))
    picked, seen, covered, last_t = [], set(), set(), -999.0

    def add(g):
        if g["kf_path"] in seen or (max_frames and len(picked) >= max_frames):
            return False
        picked.append(g)
        seen.add(g["kf_path"])
        return True

    for g in ordered:
        w = set(g.get("watch") or [])
        if w - covered and add(g):
            covered |= w

    # 尾部覆盖：最后 8% 至少一帧，避免全片只看到前半段
    if ordered:
        t_min = min(g["kf_time"] for g in ordered)
        t_max = max(g["kf_time"] for g in ordered)
        span = t_max - t_min
        if span > 0:
            tail = [g for g in ordered if g["kf_time"] >= t_max - 0.08 * span]
            if tail:
                add(max(tail, key=score))

    # 时间桶覆盖：防止高分帧全挤在前半段，保证早/中/晚都有代表帧
    if ordered:
        t0 = min(g["kf_time"] for g in ordered)
        t1 = max(g["kf_time"] for g in ordered)
        n_buckets = max(1, min(10, max(4, max_frames or 15)))
        if t1 > t0 and n_buckets > 1:
            bucket_w = (t1 - t0) / n_buckets
            for b in range(n_buckets):
                lo = t0 + b * bucket_w
                hi = lo + bucket_w
                cand = [g for g in ordered if lo <= g["kf_time"] < hi]
                if cand:
                    add(max(cand, key=score))

    for g in ordered:
        if max_frames and len(picked) >= max_frames:
            break
        if picked and abs(g["kf_time"] - last_t) < min_gap and score(g) < 4:
            continue
        if add(g):
            last_t = g["kf_time"]

    picked.sort(key=lambda g: g["kf_time"])
    return picked


def why_text(g: dict) -> str:
    bits = []
    if g["bars"]:
        bits.append("bar " + ",".join(map(str, g["bars"])))
    if g["terms"]:
        bits.append(",".join(g["terms"]))
    if g["prices"]:
        bits.append(",".join(g["prices"]))
    return "; ".join(bits) or "相关画面"


def is_high_priority(n: dict) -> bool:
    return n.get("type") in {"bar+level", "bar", "price"} or bool(n.get("draws"))


def coverage_stats(needs: list[dict], selected_groups: list[dict]) -> dict:
    paths = {g["kf_path"] for g in selected_groups}
    covered = [n for n in needs if n["kf_path"] in paths]
    all_bars = sorted({b for n in needs for b in n["bars"]})
    cov_bars = sorted({b for n in covered for b in n["bars"]})
    missing = sorted(set(all_bars) - set(cov_bars))
    high = [n for n in needs if is_high_priority(n)]
    high_cov = [n for n in high if n["kf_path"] in paths]

    def pct(a, b):
        return round(a / b, 3) if b else None

    return {
        "selected_frames": len(selected_groups),
        "bars_mentioned": all_bars,
        "bars_covered": cov_bars,
        "bars_missing": missing,
        "bar_coverage": f"{len(cov_bars)}/{len(all_bars)}",
        "bar_coverage_pct": pct(len(cov_bars), len(all_bars)),
        "needs_total": len(needs),
        "needs_covered": len(covered),
        "need_coverage": f"{len(covered)}/{len(needs)}",
        "need_coverage_pct": pct(len(covered), len(needs)),
        "high_priority_total": len(high),
        "high_priority_covered": len(high_cov),
        "high_priority_coverage": f"{len(high_cov)}/{len(high)}",
        "high_priority_pct": pct(len(high_cov), len(high)),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="按字幕语义挑补帧")
    ap.add_argument("cues", help="cues.json")
    ap.add_argument("keyframes", help="keyframes.json")
    ap.add_argument("-o", "--output", help="输出目录（默认 keyframes.json 所在目录）")
    ap.add_argument("--needs", help="LLM 产出的视觉需求 JSON")
    ap.add_argument("--asr-fixes", help="ASR 错字映射 JSON")
    ap.add_argument("--watch", default="", help="必须覆盖的 bar 号，逗号分隔")
    ap.add_argument("--full", action="store_true",
                    help="兼容别名；默认就是完整计划（max-frames=0, min-gap=0）")
    ap.add_argument("--max-frames", type=int, default=None,
                    help="可选：限制计划帧数（默认 0 = 完整计划；>0 时时间分层）")
    ap.add_argument("--read-budget", type=int, default=None,
                    help="默认读单上限（默认取「时长分钟」与「计划帧数四分之一」的较大者，最多 40）")
    ap.add_argument("--min-gap", type=float, default=15.0,
                    help="读单/预算计划的非顶格帧最小间隔秒数")
    ap.add_argument("--json-summary", action="store_true", help="只打印精简 JSON")
    args = ap.parse_args(argv)

    cues = load_cues(Path(args.cues))
    kf_data = json.loads(Path(args.keyframes).read_text(encoding="utf-8"))
    kfs = kf_data.get("keyframes", [])
    if not cues or not kfs:
        print("ERROR: cues 或 keyframes 为空", file=sys.stderr)
        return 1

    fixes = {}
    if args.asr_fixes:
        try:
            fixes = {str(k): int(v) for k, v in
                     json.loads(Path(args.asr_fixes).read_text(encoding="utf-8")).items()}
        except Exception as e:  # noqa: BLE001
            print(f"WARN: --asr-fixes 解析失败：{e}", file=sys.stderr)

    llm = load_llm_needs(Path(args.needs)) if args.needs else {}
    times = [float(k["time"]) for k in kfs]
    paths = [k["path"] for k in kfs]
    watch = {int(x) for x in args.watch.split(",") if x.strip().isdigit()}

    needs = []
    for c in cues:
        rule = tag_cue(c["text"], fixes)
        need = merge_need(rule, llm.get(c["i"]))
        if need["type"] == "none":
            continue
        target = float(c["end"]) + 0.5
        ki = nearest_kf(times, target)
        needs.append({
            "cue": c["i"], "start": c["start"], "end": c["end"],
            "text": c["text"], **need,
            "kf_index": ki, "kf_time": round(times[ki], 3),
            "kf_path": paths[ki],
        })

    groups: dict[str, dict] = {}
    for n in needs:
        g = groups.setdefault(n["kf_path"], {
            "kf_index": n["kf_index"], "kf_time": n["kf_time"],
            "kf_path": n["kf_path"], "score_type": "none",
            "bars": set(), "watch": set(), "terms": set(), "prices": set(),
            "cues": [], "texts": [],
        })
        if NEED_ORDER[n["type"]] > NEED_ORDER[g["score_type"]]:
            g["score_type"] = n["type"]
        g["bars"].update(n["bars"])
        g["watch"].update(set(n["bars"]) & watch)
        g["terms"].update(n["terms"])
        g["prices"].update(n["prices"])
        g["cues"].append(n["cue"])
        g["texts"].append(n["text"])

    glist = []
    for g in groups.values():
        g = dict(g)
        g["bars"] = sorted(g["bars"])
        g["watch"] = sorted(g["watch"])
        g["terms"] = sorted(g["terms"])
        g["prices"] = sorted(g["prices"])
        g["score"] = score_of(g)
        glist.append(g)
    duration = float(kf_data.get("duration") or (cues[-1]["end"] if cues else 0))
    if args.full or args.max_frames is None:
        plan_max, plan_gap = 0, 0.0
    else:
        plan_max, plan_gap = max(0, args.max_frames), args.min_gap
    plan_groups = select_frames(glist, plan_max, plan_gap, watch)

    # 默认读单：时长与「计划帧数 × 25%」取大者，最多 40，且不超过计划大小。
    # 只看时长会让短片断崖：96 秒的片子计划 16 帧只分到 2 张 = 12%，而 24/37 分钟的
    # 片子本来就落在 23%~30%。取大者把短片拉回同一比例，长片几乎不动。
    auto_read = (min(40, max(1,
                             math.ceil(duration / 60.0),
                             math.ceil(len(plan_groups) * 0.25)))
                 if duration > 0 else 0)
    read_budget = auto_read if args.read_budget is None else max(0, args.read_budget)
    read_budget = min(read_budget, len(plan_groups))
    read_groups = select_frames(plan_groups, read_budget, args.min_gap, watch) \
        if read_budget > 0 else []

    out_dir = Path(args.output) if args.output else Path(args.keyframes).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "semantic_needs.json").write_text(
        json.dumps(needs, ensure_ascii=False, indent=2), encoding="utf-8")

    plan_cov = coverage_stats(needs, plan_groups)
    read_cov = coverage_stats(needs, read_groups)
    coverage = {
        "mode": "full" if plan_max == 0 else "budget",
        "duration": round(duration, 3),
        "plan_max_frames": plan_max,
        "plan_min_gap": plan_gap,
        "plan_selected": len(plan_groups),
        "read_budget": read_budget,
        "read_selected": len(read_groups),
        "plan": plan_cov,
        "read": read_cov,
        # 兼容旧字段：顶层 = 计划覆盖
        "selected_frames": plan_cov["selected_frames"],
        "bars_mentioned": plan_cov["bars_mentioned"],
        "bars_covered": plan_cov["bars_covered"],
        "bars_missing": plan_cov["bars_missing"],
        "bar_coverage": plan_cov["bar_coverage"],
        "bar_coverage_pct": plan_cov["bar_coverage_pct"],
        "needs_total": plan_cov["needs_total"],
        "needs_covered": plan_cov["needs_covered"],
        "need_coverage": plan_cov["need_coverage"],
        "need_coverage_pct": plan_cov["need_coverage_pct"],
        "high_priority_coverage": plan_cov["high_priority_coverage"],
        "high_priority_pct": plan_cov["high_priority_pct"],
        "read_bar_coverage": read_cov["bar_coverage"],
        "read_bar_coverage_pct": read_cov["bar_coverage_pct"],
        "read_need_coverage": read_cov["need_coverage"],
        "read_need_coverage_pct": read_cov["need_coverage_pct"],
        "read_high_priority_coverage": read_cov["high_priority_coverage"],
        "read_high_priority_pct": read_cov["high_priority_pct"],
        "read_bars_missing": read_cov["bars_missing"],
        "note": "plan = 完整计划覆盖；read = 默认读单的预期覆盖。"
                "实际读图覆盖取决于 agent 读了多少张。"
                "bar 归属按 cue→帧最近邻估算，不代表光标一定停在那根",
    }
    (out_dir / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")

    def entry(g):
        return {
            "score": g["score"],
            "kf_time": round(g["kf_time"], 3),
            "kf_index": g["kf_index"],
            "kf_path": g["kf_path"],
            "bars": g["bars"],
            "terms": g["terms"],
            "prices": g["prices"],
            "cue_count": len(g["cues"]),
            "cues": g["cues"][:5],
            "sample_text": g["texts"][0][:100] if g["texts"] else "",
            "why": why_text(g),
        }

    plan = [entry(g) for g in plan_groups]
    read_plan = [entry(g) for g in read_groups]
    (out_dir / "frame_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "frame_plan_read.json").write_text(
        json.dumps(read_plan, ensure_ascii=False, indent=2), encoding="utf-8")

    def table(rows):
        out = ["| score | time | frame | bars | terms | prices | why |",
               "|---:|---:|---|---|---|---|---|"]
        for p in rows:
            out.append(
                f"| {p['score']} | {p['kf_time']:.1f}s | `{Path(p['kf_path']).name}` | "
                f"{','.join(map(str, p['bars']))} | {','.join(p['terms'])} | "
                f"{','.join(p['prices'])} | {p['why']} |")
        return out

    def pct(v):
        return "无" if v is None else f"{v:.0%}"

    top_lines = [
        "# 默认读单（时间分散）",
        "",
        f"- 读单预算：{read_budget} 张（视频 {duration / 60:.1f} 分钟；"
        f"max(时长分钟, 计划 {len(plan_groups)} 帧 × 25%)，最多 40）",
        f"- 预期读图覆盖：bar {read_cov['bar_coverage']}（{pct(read_cov['bar_coverage_pct'])}）；"
        f"需求 {read_cov['need_coverage']}（{pct(read_cov['need_coverage_pct'])}）；"
        f"高优先级 {read_cov['high_priority_coverage']}（{pct(read_cov['high_priority_pct'])}）",
        "",
    ] + table(read_plan)
    (out_dir / "frame_plan_top.md").write_text(
        "\n".join(top_lines) + "\n", encoding="utf-8")

    lines = table(plan)
    lines += [
        "",
        "## 覆盖率",
        f"- 计划模式：{coverage['mode']}；计划帧数：{len(plan)}；"
        f"读单预算：{read_budget}；读单帧数：{len(read_plan)}",
        f"- **计划** bar 覆盖：{plan_cov['bar_coverage']}（{pct(plan_cov['bar_coverage_pct'])}）；"
        f"需求 {plan_cov['need_coverage']}（{pct(plan_cov['need_coverage_pct'])}）；"
        f"高优先级 {plan_cov['high_priority_coverage']}（{pct(plan_cov['high_priority_pct'])}）",
        f"- **读单** bar 覆盖：{read_cov['bar_coverage']}（{pct(read_cov['bar_coverage_pct'])}）；"
        f"需求 {read_cov['need_coverage']}（{pct(read_cov['need_coverage_pct'])}）；"
        f"高优先级 {read_cov['high_priority_coverage']}（{pct(read_cov['high_priority_pct'])}）",
        f"- 读单未覆盖 bar：{', '.join(map(str, read_cov['bars_missing'])) or '无'}",
        "- 实际读图覆盖由 agent 按读了哪些帧报告；计划是完整索引，读单是默认预算。",
        "- 说明：bar 归属按 cue→帧最近邻估算，不代表光标一定停在那根。",
    ]
    (out_dir / "frame_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    summary = {
        "mode": coverage["mode"],
        "duration": round(duration, 3),
        "cues": len(cues), "tagged": len(needs), "unique_frames": len(groups),
        "plan_selected": len(plan),
        "read_budget": read_budget,
        "read_selected": len(read_plan),
        "plan_bar_coverage": plan_cov["bar_coverage"],
        "plan_need_coverage": plan_cov["need_coverage"],
        "plan_high_priority_coverage": plan_cov["high_priority_coverage"],
        "read_bar_coverage": read_cov["bar_coverage"],
        "read_need_coverage": read_cov["need_coverage"],
        "read_high_priority_coverage": read_cov["high_priority_coverage"],
        "output_dir": str(out_dir),
        "plan": str(out_dir / "frame_plan.md"),
        "read_plan": str(out_dir / "frame_plan_top.md"),
        "coverage": str(out_dir / "coverage.json"),
        # 兼容旧字段
        "selected": len(plan),
        "bar_coverage": plan_cov["bar_coverage"],
        "need_coverage": plan_cov["need_coverage"],
        "missing_bars": plan_cov["bars_missing"][:40],
        "missing_bars_count": len(plan_cov["bars_missing"]),
    }
    if args.json_summary:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
