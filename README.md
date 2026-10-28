# Video Deepread

English | [中文](README.zh.md)

A subtitle-first video understanding pipeline for long videos. It is
harness-agnostic: the scripts run standalone and can be driven by Claude
Code, DSH (DeepSeek Harness), or any agent framework. The included `SKILL.md`
covers both integrations; its §8 lists the three things that differ between
them (`read_image` instead of `Read`, long downloads as background jobs, and
read-only cookie files under a sandbox).

Video Deepread turns a long video into a small, auditable evidence set:
timestamped subtitle cues, difference-based keyframes, overview sheets,
and a semantic frame plan with a reading budget. It is designed for
lectures, trading recaps, tutorials, and other talking-head or chart
content where most of the information is in the speech and the visuals
are used to verify specific claims.

It is not a native video model. It reads a curated set of frames instead
of watching every frame, which makes it roughly 10-50x cheaper on long
videos while keeping timestamps and evidence on disk.

## Compared with native video models

Native video models watch a sampled frame sequence (often 1 fps).
Video Deepread reads a small, curated evidence set instead.

| | Video Deepread | Native video model |
|---|---|---|
| Input | subtitles + selected keyframes | sampled frames (+ audio) |
| Cost (30 min) | ~50-70k tokens | ~0.5-3M tokens |
| Timestamps | exact cue/frame references | implicit |
| Small text | full-resolution frames | often downsampled |
| Motion/tone | weak | strong |
| Auditability | evidence on disk | black box |

**Advantages:** roughly 10-50x cheaper on long videos; precise timestamps
and drill-down; readable full-resolution frames; reusable artifacts and
a coverage report.

**Trade-off:** it does not see motion, gestures, or tone. For short or
visual-first clips, a native video model is the better tool.

## How it works

1. **Text layer**: fetch subtitles from Bilibili or YouTube with
   timestamps. YouTube auto-captions are word-level; `merge_rolling_cues.py`
   merges them into sentences.
2. **Visual layer**: extract difference-based keyframes (up to 500) and
   build four overview sheets (48 cells).
3. **Alignment layer**: map each subtitle cue to its nearest keyframe,
   build a complete semantic plan, and select a time-diverse reading
   list (default: 1 frame per minute, capped at 40).
4. **Read**: the agent reads the four overviews plus the reading list.
   The rest of the plan stays on disk for drill-down.

## Examples

[`examples/`](examples/) is a fully synthetic 96-second run: generated video,
authored captions, 62 keyframes, the semantic plan and a coverage report, all
produced by [`examples/make_demo.py`](examples/make_demo.py). No third-party
footage, no cookies, no personal data. Start with
[`examples/run/frame_plan.md`](examples/run/frame_plan.md) and
[`examples/run/coverage.json`](examples/run/coverage.json).

## Features

- Bilibili and YouTube subtitle support.
- Difference-based keyframe extraction with time-aware overview sheets.
- Semantic frame plan with a reading budget and coverage report.
- Rolling auto-caption merge (word-level cues to sentences).
- Coverage split into **plan coverage** and **read coverage**.
- Offline self-tests: `python scripts/selftest.py` and
  `python scripts/selftest_semantic.py`.

## Requirements

- Python 3.10+
- `ffmpeg` and `ffprobe` on PATH
- Python packages: `numpy`, `Pillow`, `yt-dlp` (see `requirements.txt`)
- Optional: cookies for platforms that require them; a proxy for blocked
  regions.

## Install

As a Claude Code skill:

```bash
git clone https://github.com/xavisong/video-deepread.git ~/.claude/skills/video-deepread
```

As a DSH (DeepSeek Harness) skill — DSH reads its own skill root, not `~/.claude`:

```bash
git clone https://github.com/xavisong/video-deepread.git ~/.agents/skills/video-deepread
```

On Windows the two paths are `%USERPROFILE%\.claude\skills\video-deepread` and
`%USERPROFILE%\.agents\skills\video-deepread`.

To keep **one** checkout for both harnesses, clone once and link the other
(Windows, no admin required):

```powershell
New-Item -ItemType Junction `
  -Path   $env:USERPROFILE\.agents\skills\video-deepread `
  -Target $env:USERPROFILE\.claude\skills\video-deepread
```

macOS / Linux:

```bash
ln -s ~/.claude/skills/video-deepread ~/.agents/skills/video-deepread
```

The scripts can also be used standalone:

```bash
python -m pip install -r requirements.txt
```

## Harness support

- **Claude Code**: clone into `~/.claude/skills/video-deepread`; use `SKILL.md`.
- **DSH (DeepSeek Harness)**: clone into `~/.agents/skills/video-deepread`
  (or junction to the Claude Code copy). `SKILL.md` §8 lists the three harness
  differences: `read_image` instead of `Read`, long downloads as a background
  job, and read-only cookie files under the sandbox.
- **Standalone**: run the scripts directly; no harness required.

## Quick start

### 1. Fetch subtitles

Bilibili:

```bash
python scripts/fetch_subs.py "<bilibili-url>" -o "<out-dir>"
```

YouTube (cookies and a proxy are optional but often required):

```bash
python scripts/fetch_subs.py "<youtube-url>" -o "<out-dir>" \
  --proxy "<proxy>" --cookies "<cookies-file>"
```

If YouTube returns `nosub` but `--list-subs` shows automatic captions,
narrow the request to one language (`--langs en`) and use the default
client set. YouTube cookies are short-lived: export them, use them
immediately, and re-export if a bot check appears.

Merge word-level auto-captions:

```bash
python scripts/merge_rolling_cues.py "<out-dir>/cues.json" \
  -o "<out-dir>/cues_merged.json" \
  --txt "<out-dir>/transcript_merged.txt"
```

### 2. Extract keyframes

```bash
python scripts/extract_keyframes.py "<video-file>" \
  -o "<out-dir>/keyframes" \
  --cols 3 --sheet-max-cells 12 --sheet-width 1600 \
  --sheet-max-sheets 4 -q --json-summary
```

### 3. Build the semantic plan and reading list

```bash
python scripts/semantic_frames.py \
  "<out-dir>/cues_merged.json" \
  "<out-dir>/keyframes/keyframes.json" \
  -o "<out-dir>" --json-summary
```

Outputs:

- `frame_plan.json` / `frame_plan.md`: complete semantic plan.
- `frame_plan_read.json` / `frame_plan_top.md`: default reading list.
- `coverage.json`: plan and read coverage.

### 4. Read

Read the four overview images plus `frame_plan_top.md`. Drill down into
`frame_plan.json` only if the user asks about a specific cue or frame.

## Which path a video takes

Four discrete signals decide the path — the tool never estimates a "text vs image ratio":

| Signal | Test | Result |
|---|---|---|
| Subtitles | `status: nosub` / `track_too_sparse` | Skip the semantic layer, vision only |
| Motion | `insufficient_visual_change: true` (>60 s and <6 keyframes) | Text only; don't even read the 4 overview sheets |
| Visual demand | cues mentioning bars / prices / patterns, mapped to keyframes | How many single frames to read (0 = overviews only) |
| Auth | `status: auth_required` (YouTube bot-check) | Stop and refresh cookies — not a degradation |

Per material type:

| Material | How to tell | Path |
|---|---|---|
| Slides / deck / whiteboard talk | Read 1–2 overviews first: the on-screen text mostly echoes the narration | `--text-only`, saves the 4 overview sheets (~6k tokens) |
| Same, but the slides carry charts / formulas / music notation / screen-only info | That content is missing from the narration | Default path — **it can only be read from the images**, subtitles cannot recover it |
| Talking head over a static image (podcast) | `insufficient_visual_change: true` after extraction | Automatic text-only |
| Sparse or missing subtitles, information in the images | `status: nosub` or `track_too_sparse` | Vision only — **and the read budget has to be raised with the user** (below) |

**Vision-only has a hard ceiling.** With no cues the semantic reading list is empty, so the
default budget is 4 overview sheets = **48 cells**, while the keyframe pool is often far
larger (measured: 127 keyframes → 4 sheets cover **48/127 ≈ 38%**). Report that coverage and
ask the user whether to add sheets (`--sheet-max-sheets 6` / `8`, ≈+1.5k tokens each), read
targeted single frames (1.5k each), or start with the 4 sheets and come back for detail.

## Modes

- **Default**: complete semantic plan plus a reading budget of
  `min(40, max(1, ceil(duration_minutes), ceil(plan_frames * 0.25)))` frames.
- `--read-budget N`: override the reading list size.
- `--max-frames N`: limit the plan size (time-aware).
- `--text-only`: subtitles only; no video download or frames.

## Design notes

- The plan is an index; the reading list is a token budget.
- The 500-frame cap is the extraction pool, not the reading count.
- Coverage is reported separately for the plan and the reading list.
- Frame-to-cue mapping is approximate (nearest keyframe), not exact
  bar-level OCR.
- Native video models still win on motion, tone, and visual-first
  content. Video Deepread is for dense speech plus targeted evidence.

## Limitations

- Soft subtitles only in v1; no built-in ASR or hard-subtitle OCR.
- YouTube may require fresh cookies, a proxy, or a PO-token provider.
- Semantic tagging is heuristic (rules plus an optional LLM pass).
- Very short or visual-first videos may be better handled by a native
  multimodal model.

## License

GPL-3.0. See [LICENSE](LICENSE).

The visual layer (`scripts/extract_keyframes.py`, `scripts/selftest.py`,
and `references/parameters.md`) is derived from
[ztough926/video-understanding](https://github.com/ztough926/video-understanding),
also GPL-3.0. See [NOTICE](NOTICE) for attribution and modifications.

## Privacy

No cookies or credentials are included in this repository. Bring your
own cookie file and keep it out of version control. The pipeline processes
video and subtitle content locally; the scripts do not upload it.
