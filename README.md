# StorySurfer

StorySurfer turns a public Reddit post, relevant comments, and direct OP replies into a narrated vertical video over licensed Minecraft parkour or Subway Surfers-style gameplay. It uses Edge TTS, word-timed pop-up captions, resumable rendering, and a local Gradio web UI.

## Requirements

- Python environment managed with `uv`
- FFmpeg and ffprobe
- Reddit API credentials
- Gameplay footage you own or have permission to use

## Setup

```bash
uv sync --group dev
cp config.example.yaml config.yaml
```

Set these environment variables:

```bash
export REDDIT_CLIENT_ID="..."
export REDDIT_CLIENT_SECRET="..."
export REDDIT_USER_AGENT="storysurfer/1.0 by your_reddit_username"
```

Do not commit credentials. Edge TTS requires no API key. Choose its voice in `config.yaml` or the web UI.

Check that the system is ready:

```bash
uv run storysurfer doctor
```

## Web UI

```bash
uv run storysurfer web
```

In the browser:

1. Enter a Reddit post URL, optional OP-authored update URLs, OP-exchange/comment limits, and upload licensed gameplay.
2. Review the complete post, relevant comments, and OP reply exchanges; revise narration in the table or centralized textarea.
3. Choose an Edge TTS voice and caption style.
4. Build and review a preview.
5. Confirm asset rights and render the final video.

If an existing run contains an old excerpt, click **Save selection and rebuild script** before rebuilding its preview.
Projects can be permanently removed from the Projects tab through its confirmation dialog.

## CLI

Build a preview directly:

```bash
uv run storysurfer build \
  "https://www.reddit.com/r/example/comments/abc123/example/" \
  --background /path/to/licensed-gameplay.mp4 \
  --preset minecraft
```

Use `--preset subway` for Subway Surfers footage, `--dry-run` to inspect a plan without external calls, or resume a failed build with:

```bash
uv run storysurfer build --resume RUN_ID \
  --background /path/to/licensed-gameplay.mp4 \
  --preset minecraft
```

Outputs are stored under `runs/<run-id>`. The pipeline preserves valid completed stages and cached TTS segments when retrying.

## Optional gameplay download

For a YouTube video you are authorized to reuse:

```bash
uv run python download.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --acknowledge-rights
```

## Development

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

Tests use sanitized fixtures and do not contact Reddit.
