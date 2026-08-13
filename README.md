# RedditSurfer

RedditSurfer is a Python pipeline for producing source-linked, narrated vertical videos from public Reddit posts and user-supplied gameplay footage. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the product roadmap.

Phases 0 through 4 provide the project foundation, authenticated Reddit ingestion,
deterministic selection of relevant comments and direct OP replies, Edge TTS narration,
word-timed captions, resumable preview/final rendering, and ffprobe-based quality checks.

## Setup

```bash
uv sync --group dev
cp config.example.yaml config.yaml
```

Set `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT` in the environment. The Reddit user agent should uniquely identify the application and operator. Do not commit credentials.

Narration uses Edge TTS and does not require an API key. Choose a voice in `config.yaml`; available voice names can be listed with `uv run edge-tts --list-voices`.

FFmpeg and ffprobe are system dependencies for caption burning and preview rendering. Check local readiness with:

```bash
uv run redditsurfer doctor
```

## Current CLI workflow

Run a complete preview build from Reddit:

```bash
uv run redditsurfer build \
  "https://www.reddit.com/r/example/comments/abc123/example/" \
  --background /path/to/licensed-gameplay.mp4 \
  --preset minecraft
```

For offline testing, replace the Reddit URL with `--thread-file path/to/thread.json`. Inspect a
plan without making Reddit, Edge TTS, or rendering calls with `--dry-run`.

If a build fails, its error includes the run ID. Resume only the invalid or incomplete stages:

```bash
uv run redditsurfer build --resume RUN_ID \
  --background /path/to/licensed-gameplay.mp4 \
  --preset minecraft
```

Completed stages are reused only when their input hash and every declared artifact checksum
still match. Edge TTS also retains its per-segment cache as a second layer of retry protection.

The equivalent reviewable stage-by-stage workflow starts by fetching and normalizing a thread:

Fetch and normalize a public thread:

```bash
uv run redditsurfer ingest "https://www.reddit.com/r/example/comments/abc123/example/"
```

The command prints a run ID. Score and select comment exchanges for that run:

```bash
uv run redditsurfer select RUN_ID
uv run redditsurfer script RUN_ID
```

Review `runs/<run-id>/script.txt` before starting network speech synthesis:

```bash
uv run redditsurfer narrate RUN_ID
uv run redditsurfer caption RUN_ID
```

Use a local gameplay video that you own or are licensed to use. `minecraft` applies the crop
offset vertically; `subway` applies it horizontally:

```bash
uv run redditsurfer preview RUN_ID \
  --background /path/to/licensed-gameplay.mp4 \
  --preset minecraft \
  --crop-offset 0
```

After reviewing the source report, preview, captions, and asset rights, create the full-resolution
delivery and verify it:

```bash
uv run redditsurfer render RUN_ID \
  --background /path/to/licensed-gameplay.mp4 \
  --preset minecraft \
  --acknowledge-rights

uv run redditsurfer verify RUN_ID --profile final
```

The acknowledgement confirms that the Reddit content, gameplay, music, fonts, and intended
output use are permitted. It is required before any one-command final build work begins.

Artifacts are written under `runs/<run-id>`, including `thread.json`, `selection.json`,
`script.json`, `script.txt`, `speech.json`, `speech/narration.wav`, `captions.json`,
`captions.ass`, `captions.srt`, `timeline-preview.json`, `preview.mp4`, and
`verification-preview.json`. A final render additionally creates `timeline-final.json`,
`final.mp4`, and `verification.json`. Captions are chunked from
Edge TTS word timestamps, not estimated reading speed. Gameplay audio is stripped by default;
enable `media.retain_background_audio` only when the audio is also licensed. Real snapshots and
generated artifacts are ignored by Git. Narration is synthesized one segment at a time and
cached, so unchanged completed segments are reused after a retry. The browser workflow remains
planned for Phase 5; the CLI and web UI will share these same application services.

## Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

Tests use synthetic, sanitized fixtures and do not call Reddit.
