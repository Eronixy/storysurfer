# RedditSurfer

RedditSurfer is a Python pipeline for producing source-linked, narrated vertical videos from public Reddit posts and user-supplied gameplay footage. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the product roadmap.

Phases 0 and 1 provide the project foundation, authenticated Reddit ingestion, and deterministic selection of relevant comments and direct OP replies.

## Setup

```bash
uv sync --group dev
cp config.example.yaml config.yaml
```

Set `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT` in the environment. The user agent should uniquely identify the application and operator as required by Reddit's API guidance. Do not commit credentials.

FFmpeg and ffprobe are system dependencies for later rendering phases. Check local readiness with:

```bash
uv run redditsurfer doctor
```

## Phase 1 workflow

Fetch and normalize a public thread:

```bash
uv run redditsurfer ingest "https://www.reddit.com/r/example/comments/abc123/example/"
```

The command prints a run ID. Score and select comment exchanges for that run:

```bash
uv run redditsurfer select RUN_ID
```

Artifacts are written to `runs/<run-id>/thread.json`, `selection.json`, and `manifest.json`. Real snapshots and generated artifacts are ignored by Git.

## Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

Tests use synthetic, sanitized fixtures and do not call Reddit.
