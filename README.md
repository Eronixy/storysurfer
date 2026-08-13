# StorySurfer

StorySurfer is a Python pipeline for producing source-linked, narrated vertical videos from public Reddit posts and user-supplied gameplay footage. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the product roadmap.

Phases 0 through 5 provide the project foundation, authenticated Reddit ingestion,
deterministic selection of relevant comments and direct OP replies, Edge TTS narration,
word-timed captions, resumable preview/final rendering, ffprobe-based quality checks, and a
minimal local Gradio review interface.

## Setup

```bash
uv sync --group dev
cp config.example.yaml config.yaml
```

Set `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT` in the environment. The Reddit user agent should uniquely identify the application and operator. Do not commit credentials.

Narration uses Edge TTS and does not require an API key. Choose a voice in `config.yaml`;
available voice names can be listed with `uv run edge-tts --list-voices`. The Gradio UI loads
this live catalog into dropdowns and filters voices by their specialized language/locale.

FFmpeg and ffprobe are system dependencies for caption burning and preview rendering. Check local readiness with:

```bash
uv run storysurfer doctor
```

## Optional gameplay download

If you have permission to download and reuse a specific YouTube gameplay video, use the
standalone downloader:

```bash
uv run python download.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --max-height 1080 \
  --acknowledge-rights
```

Files are saved under `downloads/gameplay` by default. The downloader accepts one explicit
YouTube video, disables playlists, avoids overwriting existing files, and does not use browser
cookies or bypass protected media. You remain responsible for YouTube's terms and the creator's
license.

YouTube extraction requires a supported JavaScript runtime. The script automatically enables
Deno, Node.js, QuickJS, or Bun when found; Deno is preferred and Node.js 22+ is supported. Use
`--js-runtime node` to select Node explicitly.

## Current CLI workflow

Run a complete preview build from Reddit:

```bash
uv run storysurfer build \
  "https://www.reddit.com/r/example/comments/abc123/example/" \
  --background /path/to/licensed-gameplay.mp4 \
  --preset minecraft
```

For offline testing, replace the Reddit URL with `--thread-file path/to/thread.json`. Inspect a
plan without making Reddit, Edge TTS, or rendering calls with `--dry-run`.

If a build fails, its error includes the run ID. Resume only the invalid or incomplete stages:

```bash
uv run storysurfer build --resume RUN_ID \
  --background /path/to/licensed-gameplay.mp4 \
  --preset minecraft
```

Completed stages are reused only when their input hash and every declared artifact checksum
still match. Edge TTS also retains its per-segment cache as a second layer of retry protection.

The equivalent reviewable stage-by-stage workflow starts by fetching and normalizing a thread:

Fetch and normalize a public thread:

```bash
uv run storysurfer ingest "https://www.reddit.com/r/example/comments/abc123/example/"
```

The command prints a run ID. Score and select comment exchanges for that run:

```bash
uv run storysurfer select RUN_ID
uv run storysurfer script RUN_ID
```

Review `runs/<run-id>/script.txt` before starting network speech synthesis:

```bash
uv run storysurfer narrate RUN_ID
uv run storysurfer caption RUN_ID
```

Use a local gameplay video that you own or are licensed to use. `minecraft` applies the crop
offset vertically; `subway` applies it horizontally:

```bash
uv run storysurfer preview RUN_ID \
  --background /path/to/licensed-gameplay.mp4 \
  --preset minecraft \
  --crop-offset 0
```

After reviewing the source report, preview, captions, and asset rights, create the full-resolution
delivery and verify it:

```bash
uv run storysurfer render RUN_ID \
  --background /path/to/licensed-gameplay.mp4 \
  --preset minecraft \
  --acknowledge-rights

uv run storysurfer verify RUN_ID --profile final
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
cached, so unchanged completed segments are reused after a retry.

## Local web UI

The Gradio browser workflow uses the same resumable application services as the CLI. Start it on the
loopback interface (the default), then create a project with a Reddit URL and licensed MP4,
MOV, MKV, or WebM gameplay footage:

```bash
uv run storysurfer web
```

Review relevant comments and complete commenter/OP reply exchanges, revise the source-linked
narration, filter Edge TTS voices by language/locale, tune caption presentation, render a preview, acknowledge
asset rights for the final render, follow reconnectable progress, and download public
artifacts. Uploads and long-running Reddit, TTS, and FFmpeg work use a durable local worker.
Use `--no-browser` to skip opening a tab, or `--host`/`--port` to change the listener.
Non-loopback binding prints a security warning and is not presented as production-ready.

Web settings are under `web` in `config.yaml`; the job database defaults to
`.storysurfer/jobs.sqlite3`, while run artifacts remain under `runs/`.

## Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

Tests use synthetic, sanitized fixtures and do not call Reddit.
