# Video Deepread（视频深读）

[English](README.md) | 中文

面向长视频的“字幕优先”理解管线。**不绑定 Claude Code**：脚本可独立运行，也可被
Claude Code、DSH（DeepSeek Harness）或任意 agent 框架调用。仓库里的 `SKILL.md`
同时写两个 harness 的用法，第八节列了它们之间仅有的三处差异（读图用 `read_image`
而非 `Read`、长下载走后台 job、沙箱下 cookie 文件只读）。

它把长视频压成一组小而有据可查的证据：带时间戳的字幕、基于画面差异的关键帧、
总览图，以及带读取预算的语义补帧计划。适合讲座、交易复盘、教程、图表讲解等
“信息主要在说话里、画面用来核对”的内容。

它不是原生视频模型：不逐帧观看，而是读一小批精选帧。因此在长视频上通常便宜
10–50 倍，同时保留时间戳和磁盘上的证据。

## 与多模态直接读视频的对比

多模态模型通常按 1 fps 采样帧序列；Video Deepread 读一小批精选证据。

| | Video Deepread | 多模态直接读视频 |
|---|---|---|
| 输入 | 字幕 + 精选关键帧 | 采样帧（+音频） |
| 30 分钟成本 | 约 5–7 万 tokens | 约 50 万–300 万 tokens |
| 时间定位 | 精确到 cue/帧 | 隐式，难引用 |
| 小字 | 原尺寸单帧可读 | 常被降采样 |
| 运动/语调 | 弱 | 强 |
| 可审计性 | 证据在磁盘 | 黑盒 |

**优点**：长视频成本低约 10–50 倍；时间戳和证据可回查；原尺寸帧能读小字；
产物可复用，有覆盖率报告。

**取舍**：看不到运动、手势和语调；短片或纯画面内容，多模态模型更合适。

## 工作方式

1. **文本层**：抓 B 站/YouTube 字幕（带时间戳）；YouTube 自动字幕是逐词的，
   用 `merge_rolling_cues.py` 合并成句子。
2. **视觉层**：基于画面差异抽关键帧（最多 500 张），生成 4 张总览（48 格）。
3. **对齐层**：每条字幕映射到最近关键帧，生成完整语义计划，再选出时间分散的
   读单（默认 1 分钟 1 张，最多 40 张）。
4. **读图**：读 4 张总览 + 读单；其余帧留在磁盘按需补读。

## 功能

- B 站 / YouTube 字幕。
- 画面差异抽帧 + 时间分散的总览。
- 语义补帧计划 + 读取预算 + 覆盖率报告。
- 逐词自动字幕合并成句子。
- 计划覆盖率与读图覆盖率分开报告。
- 离线自检：`python scripts/selftest.py`、`python scripts/selftest_semantic.py`。

## 依赖

- Python 3.10+
- `ffmpeg` / `ffprobe`
- `numpy`、`Pillow`、`yt-dlp`（见 `requirements.txt`）
- 可选：平台 cookies；被墙地区的代理。

## 安装

作为 Claude Code 技能：

```bash
git clone https://github.com/xavisong/video-deepread.git ~/.claude/skills/video-deepread
```

作为 DSH（DeepSeek Harness）技能 —— DSH 读自己的技能根，不读 `~/.claude`：

```bash
git clone https://github.com/xavisong/video-deepread.git ~/.agents/skills/video-deepread
```

Windows 下是 `%USERPROFILE%\.claude\skills\video-deepread` 和
`%USERPROFILE%\.agents\skills\video-deepread`。

两个 harness 想**共用一份**，克隆一次再给另一边建链接（Windows，无需管理员）：

```powershell
New-Item -ItemType Junction `
  -Path   $env:USERPROFILE\.agents\skills\video-deepread `
  -Target $env:USERPROFILE\.claude\skills\video-deepread
```

macOS / Linux：

```bash
ln -s ~/.claude/skills/video-deepread ~/.agents/skills/video-deepread
```

独立使用：

```bash
python -m pip install -r requirements.txt
```

## Harness 支持

- **Claude Code**：克隆到 `~/.claude/skills/video-deepread`，按 `SKILL.md` 使用。
- **DSH（DeepSeek Harness）**：克隆到 `~/.agents/skills/video-deepread`（或 junction
  到 Claude Code 那份）。`SKILL.md` 第八节列了两边只有三处差异：读图用
  `read_image` 而非 `Read`、长下载走后台 job、沙箱下 cookie 文件只读。
- **独立运行**：直接调用脚本，不需要任何 harness。

## 快速开始

### 1. 抓字幕

B 站：

```bash
python scripts/fetch_subs.py "<bilibili-url>" -o "<out-dir>"
```

YouTube（cookies 与代理可选，但通常需要）：

```bash
python scripts/fetch_subs.py "<youtube-url>" -o "<out-dir>" \
  --proxy "<proxy>" --cookies "<cookies-file>"
```

YouTube 返回 `nosub` 但 `--list-subs` 有自动字幕时：收窄到一种语言
（`--langs en`），用默认 client set。YouTube cookies 很短命：导出后立刻用，
bot-check 就重新导出。

合并逐词自动字幕：

```bash
python scripts/merge_rolling_cues.py "<out-dir>/cues.json" \
  -o "<out-dir>/cues_merged.json" \
  --txt "<out-dir>/transcript_merged.txt"
```

### 2. 抽关键帧

```bash
python scripts/extract_keyframes.py "<video-file>" \
  -o "<out-dir>/keyframes" \
  --cols 3 --sheet-max-cells 12 --sheet-width 1600 \
  --sheet-max-sheets 4 -q --json-summary
```

### 3. 生成语义计划与读单

```bash
python scripts/semantic_frames.py \
  "<out-dir>/cues_merged.json" \
  "<out-dir>/keyframes/keyframes.json" \
  -o "<out-dir>" --json-summary
```

输出：

- `frame_plan.json` / `frame_plan.md`：完整语义计划。
- `frame_plan_read.json` / `frame_plan_top.md`：默认读单。
- `coverage.json`：计划与读单覆盖率。

### 4. 读图

读 4 张总览 + `frame_plan_top.md`；用户追问具体 cue/帧时再查 `frame_plan.json`。

## 素材类型 → 走哪条路

四个离散信号决定路径——工具**不会**估算"文字/画面占比"：

| 信号 | 判据 | 结果 |
|---|---|---|
| 字幕 | `status: nosub` / `track_too_sparse` | 跳过语义层，纯视觉 |
| 画面是否变化 | `insufficient_visual_change: true`（>60s 且 <6 帧） | 纯文本，连 4 张总览都别读 |
| 视觉需求 | 字幕提到 bar / 价位 / 形态的 cue 映射到关键帧 | 决定读几张单帧（0 张 = 只读总览） |
| 认证 | `status: auth_required`（YouTube bot-check） | 停下换 cookie，不是降级 |

按素材类型：

| 素材 | 怎么判断 | 走哪条 |
|---|---|---|
| PPT / 课件 / 白板讲解 | **先读 1~2 张总览**：文字卡基本在复述口播 | `--text-only`，省掉 4 张总览 ≈6k token |
| 同上，但画面出现图表 / 公式 / 谱例 / 界面独有信息 | 这些是口播没说清的部分 | 默认路径——**只能从图里读**，字幕抄不出来 |
| 口播 + 静止配图（播客型） | 抽帧后 `insufficient_visual_change: true` | 自动转 text-only |
| 字幕稀疏 / 无字幕，信息在画面 | `status: nosub` 或 `track_too_sparse` | 纯视觉——**且读图量必须跟用户确认**（见下） |

**纯视觉模式有个硬上限。** 没有 cue 时语义读单是空的，默认只剩 4 张总览 = **48 格**，
而关键帧池往往远大于这个数（实测：127 帧 → 4 张总览覆盖 **48/127 ≈ 38%**）。
所以要把覆盖率摊给用户，并问清三选一：加总览张数（`--sheet-max-sheets 6` / `8`，每张 ≈+1.5k token）、
定向读单帧（1.5k/张）、还是先看这 4 张、需要细节再回来补。

## 模式

- **默认**：完整语义计划 + 读单 `min(40, max(1, ceil(时长分钟)))` 张。
- `--read-budget N`：改读单张数。
- `--max-frames N`：限制计划大小（时间分层）。
- `--text-only`：只抓字幕，不下载视频、不抽帧。

## 设计说明

- 计划是索引，读单是 token 预算。
- 500 是抽帧池上限，不是读取数量。
- 计划覆盖率与读单覆盖率分开报告。
- 字幕到帧是最近邻近似，不是逐 bar OCR。
- 多模态模型在运动、语调、纯视觉内容上更强；Video Deepread 适合密集口播 +
  针对性证据。

## 限制

- v1 只处理软字幕；不含 ASR 或硬字幕 OCR。
- YouTube 可能需要 fresh cookies、代理或 PO Token provider。
- 语义打标签是启发式（规则 + 可选 LLM）。
- 很短的视频或纯视觉内容，多模态模型可能更合适。

## 许可证

GPL-3.0，见 [LICENSE](LICENSE)。

视觉层（`scripts/extract_keyframes.py`、`scripts/selftest.py`、
`references/parameters.md`）来自
[ztough926/video-understanding](https://github.com/ztough926/video-understanding)，
同为 GPL-3.0。见 [NOTICE](NOTICE)。

## 隐私

仓库不含 cookies 或凭据；请自备 cookie 文件并不要提交。管线只在本地处理视频和
字幕，脚本不会上传。
