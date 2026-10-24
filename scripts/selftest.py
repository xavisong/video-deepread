#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py —— 三条改动的自检（不需要网络、不需要真实素材）

跑法：python scripts/selftest.py           （需要 ffmpeg 在 PATH）

覆盖：
  1. --subtitles 字幕条总览图：真的按底部比例裁、按行叠、行高不撑爆整图
  2. insufficient_visual_change：静帧视频为 True、真有变化的视频为 False
  3. summarize() / --json-summary：不含逐帧清单，体量比 --json 小一个数量级
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_keyframes import extract_keyframes, summarize  # noqa: E402

W, H = 640, 360
FPS = 1
SCENES = 8
SECS_PER_SCENE = 5          # 8 x 5 = 40 帧 / 40 秒（1fps）
STATIC_SECS = 70            # 静帧要比 STATIC_MIN_DURATION 长
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int):
    for c in FONT_CANDIDATES:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                pass
    return ImageFont.load_default()


def _slide(i: int) -> Image.Image:
    """造一张「讲课幻灯片」：大标题 + 底部字幕条（字幕就是改动 1 要裁的那条）。"""
    im = Image.new("RGB", (W, H), (245, 243, 236))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 46], fill=(28, 44, 72))
    d.text((16, 12), f"SCENE {i}  TITLE BAR {i * 7}", fill=(255, 255, 255), font=_font(22))
    d.text((16, 80), f"slide body {i}", fill=(40, 40, 40), font=_font(30))
    d.rectangle([0, int(H * 0.84), W, H], fill=(20, 20, 20))          # 字幕条底色
    d.text((16, int(H * 0.88)),
           f"subtitle line {i}: the quick brown fox jumps over 13 lazy dogs",
           fill=(255, 230, 120), font=_font(20))
    return im


def _make_video(frames: list[Image.Image], out: Path) -> Path:
    tmp = out.parent / (out.stem + "_frames")
    tmp.mkdir(parents=True, exist_ok=True)
    for n, im in enumerate(frames):
        im.save(tmp / f"f_{n:04d}.png")
    ff = shutil.which("ffmpeg")
    if not ff:
        raise SystemExit("找不到 ffmpeg，先装好并加进 PATH")
    subprocess.run(
        [ff, "-y", "-loglevel", "error", "-framerate", str(FPS),
         "-i", str(tmp / "f_%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-r", str(FPS), str(out)],
        check=True, creationflags=0x08000000 if sys.platform == "win32" else 0,
    )
    return out


def _work_root() -> Path:
    """找一个**真能写**的目录放合成素材：先 KF_SELFTEST_DIR，再系统临时区，最后脚本上一级。"""
    candidates = []
    if os.environ.get("KF_SELFTEST_DIR"):
        candidates.append(Path(os.environ["KF_SELFTEST_DIR"]).expanduser())
    candidates.append(Path(tempfile.gettempdir()) / "kf_selftest")
    candidates.append(Path(__file__).resolve().parent.parent / "_selftest")
    for c in candidates:
        try:
            if c.exists():
                shutil.rmtree(c)
            c.mkdir(parents=True)
            probe = c / ".probe"
            probe.write_bytes(b"ok")
            probe.unlink()
            return c
        except OSError:
            continue
    raise SystemExit("找不到可写的临时目录：设 KF_SELFTEST_DIR=<可写目录> 再跑（沙箱内常见）")


def main() -> int:
    out_root = _work_root()
    print(f"[tmp] {out_root}")

    # ① 有变化的视频：每个场景重复 5 帧，只有换场景时画面才变 → 场景数 ≈ 关键帧数
    changing = _make_video(
        [_slide(i) for i in range(SCENES) for _ in range(SECS_PER_SCENE)],
        out_root / "changing.mp4",
    )
    # ② 静帧视频：同一帧重复 70 秒 —— 信息全在声音里，正是要打标记的那种素材
    static = _make_video([_slide(0)] * STATIC_SECS, out_root / "static.mp4")

    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")

    print("\n[1] 有变化的视频 —— 字幕条总览 + 静帧标记应为 False")
    meta = extract_keyframes(changing, output=out_root / "changing_out",
                             subtitle_sheet=True, verbose=False,
                             cols=3, sheet_max_cells=12, sheet_width=1600)
    kc = meta["keyframe_count"]
    check("关键帧数量合理 6~16", 6 <= kc <= 16, f"keyframe_count={kc}")
    check("出了总览图", meta["overview_count"] >= 1, f"{meta['overview_count']} 张")
    check("出了字幕条图", meta["subtitle_count"] >= 1, f"{meta['subtitle_count']} 张")
    check("insufficient_visual_change 为 False", meta["insufficient_visual_change"] is False)

    si = meta["subtitle_info"]
    row_h, sheet_h = si["row_height"], si["size"][1]
    check("字幕条按底部 16% 裁（行高 = 96px * 缩放）", 30 <= row_h <= 140, f"row_h={row_h}")
    check("每张最多 10 行、行数正确", si["cells"] == min(10, meta["keyframe_count"]),
          f"cells={si['cells']}")
    # 整图别太高：长边超了会被读图工具压扁，叠再多行也白搭
    check("字幕条图高度不超宽度", sheet_h <= si["size"][0], f"{si['size'][0]}x{sheet_h}")
    # 字幕条图里的字幕区必须真有内容（不是全黑/全白）
    with Image.open(si["path"]) as im:
        px = im.convert("RGB").crop((im.width // 3, 0, im.width // 3 + 200, im.height))
        colors = len(px.getcolors(maxcolors=100000) or [])
        check("字幕条图不是空白", colors > 20, f"{colors} 种颜色")

    print("\n[3] --json-summary 的体积")
    s_json = json.dumps(summarize(meta), ensure_ascii=False)
    f_json = json.dumps(meta, ensure_ascii=False)
    check("summary 不含逐帧清单", "keyframes" not in summarize(meta))
    check("summary 保留 keyframe_count/overviews", "keyframe_count" in s_json and "overviews" in s_json)
    check("summary 明显更小", len(s_json) < len(f_json) / 2, f"{len(s_json)} < {len(f_json)} 字符")

    print("\n[2] 静帧视频 —— 必须打上 insufficient_visual_change")
    smeta = extract_keyframes(static, output=out_root / "static_out", verbose=False)
    check("关键帧 ≤ 2", smeta["keyframe_count"] <= 2, f"keyframe_count={smeta['keyframe_count']}")
    check("insufficient_visual_change 为 True", smeta["insufficient_visual_change"] is True,
          f"duration={smeta['duration']}s")
    check("带了提示文案", bool(smeta.get("visual_change_note")))
    check("summary 里也带提示", "visual_change_note" in summarize(smeta))

    print("\n[4] --sheet-only：帧走临时目录，图照出、帧不落地")
    only = extract_keyframes(changing, output=out_root / "sheetonly_out",
                             sheet_only=True, subtitle_sheet=True, verbose=False)
    check("出了总览图", only["overview_count"] >= 1)
    check("出了字幕条图", only["subtitle_count"] >= 1)
    check("不留单帧路径", all("path" not in k for k in only["keyframes"]))
    check("临时帧目录已清掉",
          not (out_root / "sheetonly_out" / ".kf_frames_tmp").exists())

    print("\n" + ("全部通过 ✔" if ok else "有 FAIL ✘"))
    print(f"产物：{out_root}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
