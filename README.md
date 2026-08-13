# RedditSurfer

RedditSurfer is a Python pipeline for producing source-linked, narrated vertical videos from public Reddit posts and user-supplied gameplay footage. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the product roadmap.

Phases 0 through 3 provide the project foundation, authenticated Reddit ingestion,
deterministic selection of relevant comments and direct OP replies, Edge TTS narration,
word-timed captions, and vertical FFmpeg preview rendering.

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

Artifacts are written under `runs/<run-id>`, including `thread.json`, `selection.json`,
`script.json`, `script.txt`, `speech.json`, `speech/narration.wav`, `captions.json`,
`captions.ass`, `captions.srt`, `timeline.json`, and `preview.mp4`. Captions are chunked from
Edge TTS word timestamps, not estimated reading speed. Gameplay audio is stripped by default;
enable `media.retain_background_audio` only when the audio is also licensed. Real snapshots and
generated artifacts are ignored by Git. Narration is synthesized one segment at a time and
cached, so unchanged completed segments are reused after a retry.

The Phase 3 command intentionally creates `preview.mp4`. Rights acknowledgement, final output,
resume verification, and the end-to-end build command belong to Phase 4.

## Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

Tests use synthetic, sanitized fixtures and do not call Reddit.
