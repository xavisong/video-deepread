---
name: video-deepread
description: >
  视频深读：B 站/YouTube 字幕优先（B 站 cookies；YouTube 可选 cookies + 代理），
  配合 diff 抽帧与语义补帧，输出“字幕主干 + 画面证据”的结构化理解。
  默认生成完整语义计划（与字幕相关的帧全部入索引），但只读 4 张总览 +
  按视频时长 1 分钟 1 张的读单（最多 40 张）；支持 --text-only 仅字幕、
  --read-budget 覆盖读单、--max-frames 限制计划大小。
  适用于“深读/总结/分析这个视频”“这个 B 站视频讲了什么”“视频画面和字幕对照”等请求。
---

# video-deepread：视频深读（字幕主干 + 画面证据）

三层，缺一不可：

1. **文本层**：B 站 cookies 抓软字幕 → 带时间戳的 cue 列表。
2. **视觉层**：diff 抽帧 → 关键帧 + 总览图（原 `extract_keyframes.py`）。
3. **对齐层**：按字幕语义找出需要核对的画面 → 候选补帧清单。

核心原则：**字幕负责“说了什么”，画面负责“在哪里、长什么样”**。
默认轻量，深挖手动；不要把 500 张全读。

---

## 一、环境

- 视觉层：`ffmpeg` / `ffprobe`、`numpy`、`Pillow`（已就绪）。
- 文本层：`yt-dlp`（`yt-dlp` 或 `python -m yt_dlp` 均可）。
- B 站 cookies：默认 `~/.claude/cookies/bilibili.txt`（Netscape 格式）。
  没有 cookies 时软字幕通常抓不到，只会看到弹幕。
- YouTube cookies：可选，默认 `~/.claude/cookies/youtube.txt`；过期时用
  `--cookies-from-browser chrome`（需先关浏览器）。YouTube 通常还需要代理。
- 脚本（本技能目录下）：

```
scripts/fetch_subs.py          # 文本层（B 站 + YouTube）
scripts/merge_rolling_cues.py  # 合并 YouTube 逐词滚动自动字幕
scripts/extract_keyframes.py   # 视觉层（从原 video-understanding 复制）
scripts/semantic_frames.py     # 对齐层
scripts/selftest.py            # 视觉层自检
scripts/selftest_semantic.py   # 文本层 + 对齐层自检（离线）
```

自检：

```bash
python scripts/selftest.py
python scripts/selftest_semantic.py
```

---

## 二、默认行为与可选模式

| 模式 | 做什么 | 什么时候用 |
|---|---|---|
| `--text-only` | 只抓字幕，不下载视频、不抽帧 | 讲座、播客、复盘；只要内容不要画面 |
| **默认** | 字幕 + 4 张总览 + **完整语义计划**（全部相关帧）+ **读单**（1 分钟 1 张，最多 40） | 大多数视频 |
| `--max-frames N` | 可选：限制计划大小（时间分层），读单仍按分钟算 | 计划文件太大、只想看一个子集时 |
| `--read-budget N` | 可选：手动覆盖读单张数 | 用户明确说“少读几张/多读几张”时 |

三个数字别混：

- **500**：diff 抽帧的导出上限，是池子。
- **完整计划**：语义层把与字幕需求相关的唯一帧全部选进计划（通常 100–150 张），计划覆盖率 100%。
- **读单**：默认 `min(40, max(1, ceil(时长分钟)))` 张，按优先级 + 时间分散挑出；
  这才是实际送模型的图。计划是索引，读单是预算。默认读 4 张总览 + 读单，不读全部计划。

---

## 三、固定流程

### 1. 文本层（永远先跑）

```bash
# B 站（默认直连；需要 cookies）
python scripts/fetch_subs.py "<B站URL或BV>" \
  -o "<工作目录>/video_deepread/<BV>" --json-summary

# YouTube（通常需要代理 + 新鲜 cookies；自动字幕也支持）
python scripts/fetch_subs.py "<YouTube URL>" \
  -o "<工作目录>/video_deepread/<id>" \
  --proxy socks5://127.0.0.1:1080 \
  --cookies ~/.claude/cookies/youtube.txt --json-summary
```

平台默认语言：

- B 站：`ai-zh / zh-Hans / zh-CN / ai-en / en`
- YouTube：默认 `en / en-orig`；中文视频传 `--langs zh-Hans`

网络与 cookies：

- **YouTube 必须与 cookie 的导出出口一致**：本机 YouTube 走代理 `127.0.0.1:1080`，
  Chrome 导出 cookie 时也走这个代理，yt-dlp 带同一个 `--proxy socks5://127.0.0.1:1080`。
  不要硬编码“直连”或“代理”。
- **Cookie 是短命的**：会话/时间戳轮换、本机或代理出口 IP 变化，都会让几分钟前还能用的
  cookie 突然 bot-check。导出后立刻用；批量任务前重新导出；失败先换 cookie，不要反复重试。
- 报 “Sign in to confirm you're not a bot”：重新导出 fresh cookie（同一出口）；
  关掉 Chrome 后可试 `--cookies-from-browser chrome`。
- 报 “The page needs to be reloaded” / “no subtitles for the requested languages”：
  默认 client set 通常可用；下载时收窄为 `--sub-lang en --sub-format vtt`，
  不要一次请求多个语言；必要时再传 `--extractor-args` 指定 client。
- 2026-10-24 实测：fresh cookie + 代理 + `python -m yt_dlp --js-runtimes node
  --write-auto-subs --sub-lang en --sub-format vtt` 成功；约 15 分钟后同命令再次
  bot-check，说明是 cookie 短命/出口变化，不是命令错。
- 支持 SRT / VTT；YouTube 自动字幕是**逐词滚动**的，先跑
  `scripts/merge_rolling_cues.py` 合并成句子再做语义对齐。

输出：

- `subs/bili_<BV>.<lang>.srt`
- `cues.json`：主语言，`{i,start,end,text}`（YouTube 自动字幕可能是逐词粒度）
- `cues_<lang>.json`：每种可用语言一份（默认尝试 ai-zh / zh-Hans / zh-CN / ai-en / en）
- `transcript.txt`：纯文本
- YouTube 自动字幕另跑 `merge_rolling_cues.py` → `cues_merged.json` /
  `transcript_merged.txt`（实测 2484 条逐词 cue → 498 句）

规则：

- `cues.json` 已存在且未加 `--force` 时直接复用缓存。
- 返回 `status: "nosub"` 时**不要假装语义对齐可用**：明确告诉用户没有软字幕，
  继续只做视觉层，并建议另走 ASR 或硬字幕 OCR。
- 已有 SRT 时可离线复用：
  `python scripts/fetch_subs.py --from-srt <srt或vtt> -o <工作目录>`。
- YouTube 自动字幕下载成功后，先合并滚动字幕：
  `python scripts/merge_rolling_cues.py <工作目录>/cues.json -o <工作目录>/cues_merged.json --txt <工作目录>/transcript_merged.txt`
  （cues 少于 ~1000 时可跳过）。后续语义对齐用 `cues_merged.json`。

### 2. LLM 过字幕（默认做一次，可跳过）

读 `cues_merged.json`（YouTube 自动字幕）或 `cues.json`（B 站）；如果有多语言版本
（如 `cues_ai-en.json` / `cues_en.json`），一起给 LLM 交叉校正（中文 ASR 常把
“空头/熊旗/H2/信号棒”读错，英文版可帮助判断）。

**只让 LLM 标 cue，不让它决定帧号。** 输出严格 JSON，保存为
`<工作目录>/needs_llm.json`：

```json
{
  "cues": [
    {"cue": 12, "type": "bar+level", "bars": [7], "terms": ["阻力"],
     "prices": ["6800"], "draws": [], "confidence": 0.8,
     "why": "第7根在阻力位，需核对形态"}
  ]
}
```

`type` 只能取 `bar+level` / `bar` / `level` / `price` / `none`。
规则：

- 只有提到**具体 bar 号、价位、均线/通道/支撑阻力/突破点/回撤/跳空**，
  或“画/这条线/这里”时才标。
- 不要发明 bar 号和价位；乱码处用英文版交叉校正，仍不确定就降低 `confidence`。
- 不要输出帧号、时间戳、图片路径。
- 纯观点、情绪、过渡语标 `none`（可直接省略）。
- cue 数超过 1500 时按时间分 2–3 批标注，再合并。

脚本会校验；`--needs` 解析失败时自动回退纯规则，不会硬失败。

### 3. 视觉层（需要画面时）

先按字幕判断是否需要画面：`--text-only` 时跳过这一步。

下载视频：B 站默认 1080p；YouTube 默认 **720p**（代理带宽有限）。

```bash
# B 站：1080p
python -m yt_dlp --no-playlist \
  -f "bv*[height<=1080]+ba/b[height<=1080]" \
  --merge-output-format mp4 -o "<工作目录>/%(id)s.%(ext)s" "<B站URL>"

# YouTube：720p（代理带宽有限）；带 list= 参数时 --no-playlist 只下当前视频
python -m yt_dlp --no-playlist --proxy socks5://127.0.0.1:1080 \
  --cookies ~/.claude/cookies/youtube.txt \
  -f "bv*[height<=720]+ba/b[height<=720]" \
  --merge-output-format mp4 -o "<工作目录>/%(id)s.%(ext)s" "<YouTube URL>"
```

抽帧（固定参数，给模型读的安全档）：

```bash
python scripts/extract_keyframes.py "<视频路径>" \
  -o "<工作目录>/video_understanding/<视频名>" \
  --cols 3 --sheet-max-cells 12 --sheet-width 1600 \
  --sheet-max-sheets 4 -q --json-summary
```

- 默认导出全部关键帧（最多 500 张），总览最多 4 张（48 格）。
- 抽帧只负责建池子；语义计划默认完整，不需要 `--full`（保留为兼容别名）。
- 如果 `insufficient_visual_change: true`：**改走 text-only**，连 4 张总览都别读。
- 下载/抽帧预计超过 10 分钟时，先向用户确认。

### 4. 对齐层

```bash
# 默认：完整计划 + 自动读单（1 分钟 1 张，最多 40）
# YouTube 自动字幕用 cues_merged.json；B 站用 cues.json
python scripts/semantic_frames.py \
  "<工作目录>/cues_merged.json（YouTube）或 cues.json（B站）" \
  "<工作目录>/video_understanding/<视频名>/keyframes.json" \
  -o "<工作目录>" \
  --needs "<工作目录>/needs_llm.json" \
  --json-summary

# 可选：限制计划大小（读单仍按分钟算）
... --max-frames 40 ...

# 可选：手动指定读单张数
... --read-budget 25 ...
```

- 可选 `--asr-fixes fixes.json`：视频专属错字映射（如 `{"D师傅跟": 14}`），
  不要写死在脚本里。
- 可选 `--watch 7,14,19`：必须覆盖的重点 bar。
- 输出 `semantic_needs.json`、`frame_plan.json`（完整计划）、
  `frame_plan_read.json`（读单）、`frame_plan.md`（计划 + 覆盖率）、
  `frame_plan_top.md`（默认读单）、`coverage.json`（计划覆盖 + 读单覆盖）。

**这是候选帧，不是逐 bar 精确对齐。** 帧映射按 `cue.end + 0.5s` 找最近关键帧；
同一帧上出现多个 bar 号时，是相关 cue 的并集，不代表光标一定停在那根。

### 5. 读图

1. 先按 `overviews` 顺序读 **4 张总览**，看整体结构和高周期上下文。
2. 再按 `frame_plan_top.md` 的**读单**读单帧；读单默认
   `min(40, max(1, ceil(时长分钟)))` 张。计划本身是完整索引，
   用户追问某根 bar/某个画面时，从 `frame_plan.json` 按时间补读。
3. `coverage.json` 分开报**计划覆盖**和**读单覆盖**（bar / 需求 / 高优先级）。
   实际读图覆盖由你按真正读了多少张、哪些 bar 报告。
4. 如果读单为 0（没有语义需求）：只读 4 张总览，不硬补帧。
5. 总览里的小字不要硬读；价位、bar 号、标注一律看原尺寸单帧。
6. 字幕和画面冲突时，以画面为准，并在输出里标注“字幕说 X，画面显示 Y”。

### 6. 输出

像没看过视频的人读你的文字就能“看见”内容：

- 这段视频是什么、讲了哪几块；
- 字幕里的关键判断和推理链；
- 画面里的关键证据（价位、形态、标注、高周期位置）；
- 哪些地方是推断/不确定。

不写时间轴、帧序号、分辨率、帧率；用户要证据时再附 `frame_plan.md` 摘要。

---

## 四、硬规则

- **没字幕 → `nosub`**：不要用画面猜字幕，不要假装语义对齐可用。
- **静帧视频 → text-only**：`insufficient_visual_change: true` 时不读总览。
- **>60 分钟**：先建议 text-only 或 720p；抽帧可能超过 10 分钟，需确认。
- **默认读图预算**：4 张总览 + 读单（`min(40, max(1, ceil(时长分钟)))` 张）。
- **默认计划是完整的**：不自动切换、不自动加帧；计划覆盖率通常 100%。
- **计划 ≠ 已读**：输出必须分开写“计划覆盖”和“实际读图覆盖”；读单之外的帧是索引。
- **读单不够时**：用户明确要求再补读；不要自己把 100+ 张全读。
- **高优先级需求**（bar/价位/明确指代）是读单质量的诊断指标；
  全部需求覆盖率按 cue 计，天然偏低。
- **不默认读 500 张**：每张约 1.5k tokens，全读约 750k，收益递减。
- **LLM 只标 cue，不标帧**：帧映射必须由脚本按时间确定。

---

## 五、成本心算

| 模式 | 图片 | 额外时间 |
|---|---:|---|
| `--text-only` | 0 | 只抓字幕，秒级 |
| 默认 | 4 总览 + 读单（37 分钟 ≈ 38 张）≈ 63k tokens；2.5 分钟 ≈ 7 张 ≈ 10.5k | 下载 + 抽帧，约与视频时长成正比 |
| 读单上限 | 4 总览 + 40 张 ≈ 66k tokens | 读图按需 |

37 分钟视频实测：78MB / 1080p，抽帧约 10 分钟，原始候选 286 张（默认上限 500）；
完整计划 125 张，读单 38 张；读单覆盖 bar 48/54（89%）、高优先级 91/139（65%）。

---

## 六、边界（v1）

- **v1 支持 B 站 + YouTube 字幕**；其他平台仍用现有工具，不在本技能内。
- **只处理软字幕**；硬字幕 OCR、ASR 转写不在 v1 范围。
- 本地视频 + 单独 SRT 可用 `--from-srt`；本地视频无字幕时只走视觉层。
- 语义层是 best-effort：规则 + 可选 LLM；ASR 错字多时效果会下降。
- 不把视频内容用于训练或外发；字幕/画面只在本机处理。

---

## 七、来源与本机改动

- 视觉层 `extract_keyframes.py` / `selftest.py` / `references/parameters.md`
  来自 [ztough926/video-understanding](https://github.com/ztough926/video-understanding)（GPL-3.0）。
- 本技能新增：
  - `fetch_subs.py`：B 站 + YouTube 字幕抓取 + 带时间戳 cues。
    `fetch_bili_subs.py` 保留为兼容入口。
  - `semantic_frames.py`：按字幕语义挑补帧（规则 + 可选 LLM needs）。
  - `selftest_semantic.py`：离线自检。
- 原 `video-understanding` 技能先保留，验证稳定后再删除。
