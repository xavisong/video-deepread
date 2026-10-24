# Video Deepread

A subtitle-first video understanding pipeline for Claude Code.

Video Deepread turns a long video into a small, auditable evidence set:
timestamped subtitle cues, difference-based keyframes, overview sheets,
and a semantic frame plan with a reading budget. It is designed for
lectures, trading recaps, tutorials, and other talking-head or chart
content where most of the information is in the speech and the visuals
are used to verify specific claims.

It is not a native video model. It reads a curated set of frames instead
of watching every frame, which makes it roughly 10-50x cheaper on long
videos while keeping timestamps and evidence on disk.

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

On Windows, clone into `%USERPROFILE%\.claude\skills\video-deepread`.

The scripts can also be used standalone:

```bash
python -m pip install -r requirements.txt
```

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

## Modes

- **Default**: complete semantic plan plus a reading budget of
  `min(40, max(1, ceil(duration_minutes)))` frames.
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
