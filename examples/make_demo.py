#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the synthetic demo used in `examples/`.

Everything is generated here: a fictional price path, drawn captions, no audio,
no third-party footage, no network access. Nothing personal is embedded, so the
output is safe to publish.

    python examples/make_demo.py

Writes `examples/demo.mp4` and `examples/run/cues.json`. The rendering is
deterministic (fixed timings, no randomness), so re-running reproduces the same
frames. A CJK font is used when one is found; otherwise captions fall back to
DejaVu Sans and CJK glyphs may render as boxes.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BUILD = HERE / ".build"
VIDEO = HERE / "demo.mp4"
RUN = HERE / "run"

W, H = 960, 540
FPS = 6
DURATION = 96.0
N_CANDLES = 40
FIRST_CANDLE_T = 5.0
CANDLE_STEP = 1.95
GAP_INDEX = 31

BG = (14, 17, 21)
FG = (230, 237, 243)
MUTED = (139, 148, 158)
GRID = (38, 44, 52)
UP = (63, 185, 80)
DOWN = (248, 81, 73)
ACCENT = (88, 166, 255)

FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSans.ttf",
)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for name in (("C:/Windows/Fonts/msyhbd.ttc",) if bold else ()) + FONT_CANDIDATES:
        if Path(name).exists():
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def price_series(n: int = N_CANDLES) -> list[float]:
    out = []
    for i in range(n):
        p = 4120 + 5.4 * i + 6.5 * math.sin(i / 2.2) + 3.0 * math.sin(i * 1.7)
        if i >= GAP_INDEX:
            p += 34  # the gap the narration talks about
        out.append(round(p, 1))
    return out


CLOSE = price_series()
OPEN = [CLOSE[0]] + CLOSE[:-1]
LOW = [min(o, c) - (7 + 2 * math.sin(i * 2.0)) for i, (o, c) in enumerate(zip(OPEN, CLOSE))]
HIGH = [max(o, c) + (7 + 2 * math.sin(i * 3.0)) for i, (o, c) in enumerate(zip(OPEN, CLOSE))]

SUP = round(min(LOW[3:9]) / 10) * 10
RES = round(max(HIGH[8:17]) / 10) * 10
TARGET = round((RES + (RES - SUP)) / 10) * 10
SUP2 = round((CLOSE[-1] - 12) / 10) * 10
Y_MIN, Y_MAX = min(LOW) - 15, max(HIGH) + 15

CUES = [
    (0.0, 5.0, "这是 video-deepread 的合成示例：K 线和数据都由脚本生成，不含真实行情。"),
    (5.0, 10.0, f"开盘后价格在 {SUP} 到 {RES} 之间震荡，先看区间。"),
    (10.0, 15.0, "第 5 根之前没有明确方向，成交量也偏小。"),
    (15.0, 20.0, "第 8 根出现第一次回撤，看着像假突破。"),
    (20.0, 25.0, f"看这里，支撑位 {SUP} 被反复测试了两次。"),
    (25.0, 30.0, "第 12 根收盘价站上均线，结构开始转多。"),
    (30.0, 35.0, f"这条水平线 {RES} 就是前面的阻力。"),
    (35.0, 40.0, f"第 16 根放量突破 {RES}，突破点很干净。"),
    (40.0, 45.0, f"突破后回踩 {RES} 没有跌破，属于正常回撤。"),
    (45.0, 50.0, "第 21 根进入加速段，通道上轨被拉开。"),
    (50.0, 55.0, f"这里给出目标位 {TARGET}，阻力位的等距测量。"),
    (55.0, 60.0, "第 26 根冲到本段高点附近，接近目标区。"),
    (60.0, 65.0, f"随后进入盘整，支撑上移到 {SUP2} 一线。"),
    (65.0, 70.0, "第 31 根出现缺口，直接跳空高开。"),
    (70.0, 75.0, f"缺口回补，价格回落到 {SUP2} 附近。"),
    (75.0, 80.0, "第 36 根收出长上影，上方卖压偏重。"),
    (80.0, 84.0, f"最后几根回到 {SUP2} 支撑，结构没有破。"),
    (84.0, DURATION, "总结：一段趋势、三次回撤、一个缺口，全部都能在关键帧里核对。"),
]

CALLOUTS = [
    (5.5, 10.0, f"区间 {SUP}-{RES}", None),
    (15.5, 20.0, "回撤", None),
    (20.5, 25.0, f"支撑 {SUP}", SUP),
    (25.5, 30.0, "站上均线", None),
    (30.5, 35.0, f"阻力 {RES}", RES),
    (35.5, 40.0, f"突破 {RES}", RES),
    (40.5, 45.0, f"回踩 {RES}", RES),
    (45.5, 50.0, "加速段", None),
    (50.5, 55.0, f"目标 {TARGET}", TARGET),
    (60.5, 65.0, f"支撑 {SUP2}", SUP2),
    (65.5, 70.0, "缺口", None),
    (70.5, 75.0, f"回补 {SUP2}", SUP2),
    (75.5, 80.0, "长上影", None),
]

CH_X0, CH_X1 = 84, 906
CH_Y0, CH_Y1 = 96, 452
STEP_X = (CH_X1 - CH_X0 - 20) / (N_CANDLES - 1)


def x_of(i: int) -> float:
    return CH_X0 + 10 + i * STEP_X


def y_of(price: float) -> float:
    return CH_Y1 - (price - Y_MIN) / (Y_MAX - Y_MIN) * (CH_Y1 - CH_Y0)


def dashed(draw: ImageDraw.ImageDraw, x0: float, y: float, x1: float, color, width: int = 1):
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + 6, x1), y)], fill=color, width=width)
        x += 12


def candle_time(i: int) -> float:
    return FIRST_CANDLE_T + i * CANDLE_STEP


def caption_at(t: float) -> str:
    for start, end, text in CUES:
        if start <= t < end:
            return text
    return CUES[-1][2]


def render(t: float) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_small, f_label, f_cap, f_big = font(14), font(15), font(22, bold=True), font(26, bold=True)

    d.text((24, 16), "SYNTHETIC DEMO  ·  fictional prices  ·  make_demo.py",
           font=f_small, fill=MUTED)
    d.text((W - 24, 16), "video-deepread examples", font=f_small, fill=MUTED, anchor="ra")

    # price grid
    p = math.ceil(Y_MIN / 50) * 50
    while p <= Y_MAX:
        y = y_of(p)
        d.line([(CH_X0, y), (CH_X1, y)], fill=GRID, width=1)
        d.text((CH_X0 - 10, y), f"{int(p)}", font=f_small, fill=MUTED, anchor="rm")
        p += 50

    visible = [i for i in range(N_CANDLES) if candle_time(i) <= t]
    for i in visible:
        x = x_of(i)
        last = i == visible[-1]
        col = UP if CLOSE[i] >= OPEN[i] else DOWN
        if last:
            col = tuple(min(255, c + 40) for c in col)
        d.line([(x, y_of(HIGH[i])), (x, y_of(LOW[i]))], fill=col, width=2 if last else 1)
        top, bot = y_of(max(OPEN[i], CLOSE[i])), y_of(min(OPEN[i], CLOSE[i]))
        d.rectangle([x - 5, top, x + 5, max(bot, top + 2)], fill=col)
        if last:
            d.text((x + 9, y_of(CLOSE[i])), f"{CLOSE[i]:.0f}", font=f_small, fill=FG, anchor="lm")

    for start, end, label, level in CALLOUTS:
        if not (start <= t < end):
            continue
        if level is None:
            lvl = HIGH[min(N_CANDLES - 1, max(0, int((t - FIRST_CANDLE_T) / CANDLE_STEP)))]
        else:
            lvl = level
        y = y_of(lvl)
        dashed(d, CH_X0, y, CH_X1 - 120, ACCENT)
        box_w = 12 + 8 * len(label)
        bx = CH_X1 - box_w - 6
        d.rectangle([bx, y - 15, bx + box_w, y + 15], fill=(24, 32, 43), outline=ACCENT)
        d.text((bx + box_w / 2, y), label, font=f_label, fill=FG, anchor="mm")

    if t >= 84.0:
        d.rectangle([CH_X0 + 150, CH_Y0 + 90, CH_X1 - 30, CH_Y0 + 250], fill=(18, 24, 32), outline=GRID)
        d.text((CH_X0 + 330, CH_Y0 + 130), "END OF SYNTHETIC DEMO", font=f_big, fill=ACCENT, anchor="mm")
        d.text((CH_X0 + 330, CH_Y0 + 172), f"{int(DURATION)}s · {N_CANDLES} candles · no audio · no real data",
               font=f_label, fill=MUTED, anchor="mm")
        d.text((CH_X0 + 330, CH_Y0 + 210), "cues → keyframes → frame_plan.json",
               font=f_label, fill=MUTED, anchor="mm")

    # caption band
    d.rectangle([0, 466, W, 516], fill=(8, 10, 13))
    d.text((W / 2, 491), caption_at(t), font=f_cap, fill=FG, anchor="mm")

    # progress
    d.rectangle([0, 524, W, 528], fill=(28, 33, 40))
    d.rectangle([0, 524, W * min(1.0, t / DURATION), 528], fill=ACCENT)
    return img


def encode() -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ERROR: ffmpeg not found on PATH")
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(FPS), "-i", str(BUILD / "f_%05d.jpg"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "27",
        "-movflags", "+faststart", str(VIDEO),
    ]
    subprocess.run(cmd, check=True)


def main() -> int:
    BUILD.mkdir(exist_ok=True)
    RUN.mkdir(exist_ok=True)
    total = int(DURATION * FPS)
    for n in range(total):
        render(n / FPS).save(BUILD / f"f_{n:05d}.jpg", quality=88)
    encode()
    cues = {
        "source": "synthetic",
        "note": "Captions authored for the generated demo video; not fetched from any platform.",
        "duration": DURATION,
        "cues": [{"i": i + 1, "start": s, "end": e, "text": t} for i, (s, e, t) in enumerate(CUES)],
    }
    (RUN / "cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(BUILD, ignore_errors=True)
    print(json.dumps({"video": str(VIDEO), "cues": str(RUN / "cues.json"),
                      "cues_count": len(CUES), "frames": total,
                      "levels": {"support": SUP, "resistance": RES, "target": TARGET, "support2": SUP2}},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
