# Example run (synthetic)

This folder is a **fully synthetic** end-to-end example. The video, its captions,
and every artifact under `run/` are produced by [`make_demo.py`](make_demo.py):
a fictional 40-candle price path, burned-in captions, no audio, no network access,
no third-party footage, no cookies, no personal data, no real market data.

It exists so you can see what the pipeline actually emits before running it.

## What's here

| Path | What it is |
|---|---|
| `make_demo.py` | Regenerates `demo.mp4` and `run/cues.json` (deterministic, fixed timings) |
| `demo.mp4` | 96 s, 960x540, 6 fps, silent, ~260 KB |
| `run/cues.json` | 18 authored cues, same schema as `fetch_subs.py` output |
| `run/keyframes/keyframes.json` | 62 difference-based keyframes with timings |
| `run/keyframes/frames/` | The 62 keyframes (jpeg, 640 px wide, ~18 KB each) |
| `run/keyframes/overview_0*.jpg` | 2 overview sheets, 12 cells each |
| `run/keyframes/subtitles_0*.jpg` | 2 caption-strip sheets (`--subtitles`) |
| `run/frame_plan.md` | Complete semantic plan as a table (16 unique frames) |
| `run/frame_plan_read.json` / `frame_plan_top.md` | Default reading list (budget 4) |
| `run/coverage.json` | Plan coverage vs read coverage |

## Measured numbers

| | This run |
|---|---|
| Duration | 96 s |
| Cues / cues with a visual need | 18 / 17 |
| Keyframe pool | 62 frames |
| Plan | 16 unique frames, needs 17/17, bars 8/8, high priority 11/11 |
| Default read budget | `max(ceil(96/60), ceil(16 × 0.25))` = **4** frames -> needs 4/17, bars 4/8 |
| `--read-budget 8` | needs 8/17, bars **8/8**, high priority 8/11 |

The plan is the index, the reading list is a token budget. The default budget is
the larger of one frame per minute and a quarter of the plan, so a 96-second video
gets 4 frames. Raise it with `--read-budget` when the question needs more.

## Reproduce

From the repository root:

```bash
python examples/make_demo.py

python scripts/extract_keyframes.py examples/demo.mp4 -o examples/run/keyframes \
  --cols 3 --sheet-max-cells 12 --sheet-width 1600 --sheet-max-sheets 2 \
  --max-width 640 --quality 78 \
  --subtitles --subtitle-band 0.16 --subtitle-max-sheets 2 \
  -q --json-summary

python scripts/semantic_frames.py examples/run/cues.json \
  examples/run/keyframes/keyframes.json -o examples/run --json-summary
```

A fresh run writes **absolute** paths into the JSON files; the committed copies
were rewritten to repo-relative form (`examples/run/keyframes/...`) so they stay
portable. That rewrite is the only difference from the raw output.

## What this example does and does not exercise

- **Does**: the visual layer (`extract_keyframes.py`), the alignment layer
  (`semantic_frames.py`), the coverage report, and the reading-budget logic.
- **Does not**: the text layer (`fetch_subs.py`). The captions here are authored
  inside `make_demo.py`, not fetched from Bilibili or YouTube, so no platform
  cookies or network access are involved.

## Provenance and sanitization

- Every byte comes from `make_demo.py`; the price path is a sine formula, not
  sampled or real data.
- No cookies, tokens, proxy settings, usernames, or absolute paths are included.
- `demo.mp4` has no audio track; the only format tag is the ffmpeg encoder string.
- Real runs are deliberately excluded: publishing another creator's frames and
  transcript under this repository's GPL-3.0 license would be their content, and
  a real run also carries your cookie paths. To see real output, run the
  pipeline on a video you own.

Re-running `make_demo.py` after editing the captions is the intended way to
produce a different example; keep it synthetic.
