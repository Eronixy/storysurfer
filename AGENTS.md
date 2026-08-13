# AGENTS.md

## Mission

Build RedditSurfer as a reliable, reviewable Python pipeline with CLI and local web interfaces that turns a public Reddit story and user-supplied gameplay footage into a narrated vertical video. The product requirements and phased roadmap live in `IMPLEMENTATION_PLAN.md`; read it before changing architecture or adding a pipeline stage.

The two product-critical behaviors are:

1. captions must be driven by actual speech timing and visibly pop in as narration progresses; and
2. relevant comments must retain context, especially when OP directly replied to them.

## Repository baseline

- Python 3.11+
- `uv` for dependency and command execution
- `src/redditsurfer` package layout
- FFmpeg/ffprobe for media inspection and rendering
- FastAPI, server-rendered Jinja templates, progressive enhancement, and minimal vanilla JavaScript for the local web UI
- One application-service layer shared by the CLI, web routes, and local job worker

The repository is initially minimal. Commands named below may not exist yet; introduce them in the phase that owns them and keep this file accurate.

## Working rules

- Preserve unrelated user changes. Inspect `git status` before and after edits.
- Make the smallest coherent change that completes the active phase or issue.
- Prefer typed, provider-neutral domain models over passing unvalidated dictionaries.
- Keep selection, caption chunking, timing, and timeline calculations pure and deterministic.
- Put network, TTS, filesystem, and subprocess behavior behind narrow adapters.
- Keep web route handlers thin; they validate/authorize input and call the same application services as the CLI.
- Build subprocess commands as argument lists. Never interpolate Reddit text or file paths into a shell command.
- Do not add a remote AI service as a requirement for the deterministic MVP path.
- Do not make paid/network calls in tests. Use fake adapters and sanitized fixtures.
- Do not commit generated media, run artifacts, real Reddit snapshots, credentials, or user data.
- Update `IMPLEMENTATION_PLAN.md` when an intentional product or architecture decision supersedes it.

## Required pipeline boundaries

Keep these stages independently runnable and resumable:

```text
ingest -> select -> script -> synthesize -> caption -> render -> verify
```

Each stage must:

- read explicit, versioned inputs;
- write its artifact atomically;
- include input/config hashes in the manifest;
- fail with a stage-specific, actionable error;
- avoid repeating valid paid or network work on resume; and
- never mark a partial artifact as successful.

Provider adapters may depend on provider SDK types internally. Those types must not leak into domain models or artifacts used by later stages.

## Web UI rules

- The browser interface is a first-class MVP workflow, but it must not duplicate pipeline or editorial logic.
- Do not invoke CLI commands from web request handlers. Call application services with typed inputs.
- Never run Reddit, TTS, alignment, or FFmpeg work inside the HTTP request lifecycle. Enqueue it through the durable local worker.
- Persist job/stage state before publishing progress. Server-sent events are a live view; manifests and the job store are authoritative after reconnect.
- Make enqueueing idempotent by run, stage, and relevant input/config hash so double-clicks cannot repeat paid work.
- A selection edit must keep commenter + OP reply atomic. A script/style edit must invalidate all affected downstream artifacts.
- Use POST/Redirect/GET for ordinary forms and return structured errors for progressively enhanced requests.
- Require CSRF protection for state-changing routes. Keep template autoescaping enabled and treat all Reddit/user text as untrusted.
- Generate run IDs server-side. Resolve and containment-check every upload/artifact path; never expose an arbitrary-path download endpoint.
- Stream uploads to a staging directory, enforce configurable size limits, and validate media content with ffprobe before promotion.
- Artifact endpoints use an allowlist and support byte ranges for video playback without exposing private/raw artifacts.
- Bind the development server to `127.0.0.1` by default. Non-loopback binding must show an explicit security warning and must not be presented as production-ready.
- Keep controls keyboard accessible, status understandable without color, and generated-video previews usable on laptop/tablet screens.

## Editorial invariants

- Every spoken segment must contain at least one `SourceRef`.
- Never invent facts, an ending, a reaction, or an OP response.
- Text may be cleaned or shortened, but material omissions must be recorded.
- Deleted/removed content is never reconstructed or narrated.
- Usernames are not spoken by default. Use "OP" and "a commenter."
- Redact likely personal contact/location data before text reaches TTS.
- Preserve the original source excerpt privately in the review artifact, with author IDs hashed by default.
- Selection must produce reason codes and component scores; avoid opaque ranking.

### Comment and OP-reply rules

- Model a selected commenter message plus its direct OP reply as one atomic exchange.
- Include the parent comment whenever an OP reply would otherwise lack context.
- Keep parent-before-reply order regardless of global ranking.
- Include only the shortest ancestor chain necessary for nested context.
- Prefer complete exchanges that fit the duration budget over clipped exchanges.
- Deduplicate near-identical questions and repeated OP answers.
- A standalone OP comment is valid only when it is a comprehensible update or clarification.
- Add or update fixture tests whenever selection behavior changes.

## Caption and timing invariants

- Generate production cues from TTS word timestamps or a validated forced-alignment result.
- Never estimate production caption timing from character count alone.
- Cue times must be monotonic, non-negative, and within the narration duration.
- Caption phrases should normally contain 2-5 words, at most two lines, and respect punctuation/speaker boundaries.
- Keep captions within the configured vertical-video safe area.
- Pop animation should settle quickly; readability is more important than motion.
- Export both animated ASS and plain SRT, and retain them beside the video.
- Rendering must use a bundled or explicitly configured font for reproducibility.

## Media and rights rules

- Gameplay, music, fonts, and other creative assets must be supplied or licensed for the intended use.
- Do not implement gameplay downloading, DRM bypasses, watermark removal, or scraping from video platforms.
- Treat `subway` and `minecraft` as composition presets for user-provided footage, not sources of branded assets.
- Probe input media before render and reject missing video streams or unsupported/corrupt inputs early.
- Strip gameplay audio by default; if retained, duck it beneath narration.
- Require the configured rights acknowledgement for a final render. Preview rendering may remain available for review.
- Never log API tokens, TTS keys, cookies, authorization headers, or full environment dumps.

## Code conventions

- Add type annotations to public functions and important internal boundaries.
- Use `pathlib.Path`, UTC-aware datetimes, and explicit UTF-8 text I/O.
- Prefer dataclasses or the project's chosen validated model library consistently.
- Use stable source IDs and hashes rather than list positions as identities.
- Pass a clock/random source into code that needs one so tests remain deterministic.
- Catch exceptions only where they can be enriched, retried, translated, or cleaned up.
- Include the failed stage, run ID, and practical remedy in user-facing errors.
- Keep functions focused. Media filter construction, editorial policy, and provider calls belong in separate modules.
- Comments should explain policy or non-obvious constraints, not restate code.

## Testing and verification

When available, run the narrowest relevant checks first and the full suite before handing off:

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

For web changes, add route/service tests using fake providers and temporary run storage. Test CSRF behavior, validation errors, template escaping, upload limits, path traversal rejection, idempotent job submission, restart recovery, and HTTP range responses where relevant. Do not require a live browser, Reddit, or TTS account for the default suite.

For media changes, also run the short integration render and inspect it with `ffprobe`. Do not claim visual correctness from a successful process exit alone; verify dimensions, streams, duration, timestamps, and caption boundaries. If a visual change is material, generate a low-resolution preview for human review.

Every bug fix should have a regression test unless the behavior cannot reasonably be automated. Mark tests requiring local FFmpeg separately from pure unit tests, but keep a short render in normal CI when feasible.

## Fixtures and generated files

- Reddit fixtures must be synthetic or irreversibly sanitized.
- Keep media fixtures tiny and generated from clearly redistributable sources.
- Test fixtures belong under `tests/fixtures`; transient outputs belong under the test temp directory.
- Runtime output belongs under `runs/<run-id>` and must remain ignored by Git.
- Never rewrite an existing successful run in place. Create a new run or a versioned artifact.

## Dependency policy

- Prefer the standard library when it keeps the code clear.
- Add a dependency only for a concrete current requirement, not a speculative future phase.
- Pin through `uv.lock`, check licenses, and avoid abandoned wrappers around core providers.
- Keep Reddit, TTS, and future ranking providers swappable behind local protocols.
- FFmpeg is an explicit system dependency; validate its required features at startup rather than hiding failures late in rendering.

## Completion checklist

Before declaring a change complete:

1. Confirm the change matches the current phase and does not weaken editorial, timing, privacy, or rights invariants.
2. Run relevant lint, type, and test commands that exist in the repository.
3. For pipeline changes, inspect emitted artifacts and resume/cache behavior.
4. For selection changes, verify parent plus OP-reply context with fixtures.
5. For caption/render changes, verify actual word timing and media metadata.
6. For web changes, verify stale-state invalidation, refresh/reconnect behavior, and the main keyboard-only path.
7. Check `git diff` and `git status`; report unrelated pre-existing changes without modifying them.
8. Update user-facing docs and example configuration when behavior or commands change.
