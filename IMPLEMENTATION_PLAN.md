# RedditSurfer implementation plan

## 1. Product goal

Build a Python application with a local web UI and CLI that turns a public Reddit post into a vertical short-form video. The finished video combines:

- a user-provided, licensed Subway Surfers-style or Minecraft parkour gameplay clip;
- narration of the post, selected relevant comments, and useful replies written by the original poster (OP);
- short, word-timed captions that pop onto the screen as each phrase is spoken; and
- enough provenance in the build output to trace every narrated line back to its Reddit source.

The first release should produce a deterministic 1080 x 1920 MP4 from a Reddit submission URL and a local gameplay file. A user should be able to review sources, adjust the script and caption style, preview the result, and start the final render from a browser without manual video editing or hand-written subtitle timing. The CLI exposes the same underlying operations for automation and debugging.

## 2. Scope and non-goals

### MVP scope

1. Ingest one public Reddit submission and its comment tree through Reddit's supported API.
2. Normalize the title, post body, comments, authors, scores, and parent relationships into a local snapshot.
3. Create a narration script that fits a requested duration.
4. Include high-value comments, with special handling for comments to which OP replied.
5. Generate narration and word-level timestamps through a replaceable text-to-speech adapter.
6. Render animated phrase captions over a looped/cropped gameplay clip.
7. Mix narration with ducked gameplay audio or an optional music track.
8. Export the video, subtitles, script, source manifest, and build metadata.
9. Provide a local web UI for creating runs, reviewing selected post/comment material, editing narration, configuring captions, monitoring jobs, previewing video, and downloading artifacts.

### Explicit non-goals for the MVP

- Downloading or republishing gameplay owned by somebody else.
- Scraping Reddit HTML or bypassing authentication, rate limits, deleted content, or community restrictions.
- Automatically posting videos to social platforms.
- Cloning a Reddit author's or creator's voice.
- A multi-user hosted service, collaborative editing, accounts/billing, or a distributed job queue.
- Recreating the Subway Surfers or Minecraft trademarks, UI, logos, or game assets.

Users must supply footage they have permission to use. The background presets describe crop and composition behavior only.

## 3. Core user flow

```text
Browser or CLI
       |
       v
Reddit URL + gameplay.mp4 + config
                  |
                  v
       fetch and snapshot thread
                  |
                  v
    select story and comment chains
                  |
                  v
       build narration segments
                  |
                  v
    TTS audio + word timestamps
                  |
                  v
    caption cues + video timeline
                  |
                  v
        FFmpeg render and QC
                  |
                  v
 MP4 + SRT/ASS + script + manifest
```

Example target command:

```bash
uv run redditsurfer build \
  --url "https://www.reddit.com/r/example/comments/abc123/example/" \
  --background assets/gameplay/minecraft-parkour-01.mp4 \
  --preset minecraft \
  --duration 75 \
  --output runs/example/final.mp4
```

The command is a target interface. It does not exist until the corresponding phase below is implemented.

The corresponding browser flow is: create a project, upload/select gameplay, review the fetched thread and proposed comment exchanges, approve or edit the script, select voice/caption options, render a preview, acknowledge content/asset rights, and start the final render.

## 4. Architecture

Keep orchestration separate from provider integrations and pure timeline logic.

```text
src/redditsurfer/
  cli.py                  # CLI entry points and user-facing errors
  config.py               # validated YAML/env/CLI configuration
  domain.py               # provider-neutral dataclasses/models
  pipeline.py             # resumable build orchestration
  web/
    app.py                # FastAPI application factory and middleware
    routes.py             # HTML and JSON endpoints
    forms.py              # validated browser inputs
    jobs.py               # durable local job dispatch and progress events
    templates/            # Jinja pages and reusable fragments
    static/               # project CSS and small progressive-enhancement JS
  reddit/
    client.py             # supported Reddit API adapter
    normalize.py          # API response -> ThreadSnapshot
  editorial/
    clean.py              # URLs, markdown, pronunciation, safety cleanup
    select.py             # deterministic post/comment selection
    script.py             # selected sources -> NarrationScript
  speech/
    base.py               # SpeechProvider protocol
    provider.py           # first concrete TTS integration
    alignment.py          # timestamps/fallback alignment
  captions/
    chunk.py              # words -> readable phrase cues
    ass.py                # animated ASS subtitle generation
  media/
    probe.py              # ffprobe wrappers and validation
    timeline.py           # audio/video/caption timing model
    render.py             # safe FFmpeg command construction
    quality.py            # duration, streams, loudness, frame checks
  storage.py              # run directories, cache keys, manifests

tests/
  fixtures/               # small sanitized API snapshots and media fixtures
  unit/                   # selection, scripting, captions, timeline
  integration/            # fake providers and short FFmpeg renders
  web/                    # route, form, upload, progress, and browser-flow tests
```

Use FastAPI with server-rendered Jinja templates, HTMX-style partial updates, and minimal vanilla JavaScript for the MVP. This keeps the frontend in the Python project and avoids a separate Node build. The UI must call application services directly; it must never shell out to the CLI or duplicate editorial/rendering logic.

### Domain model

Start with typed, serializable models rather than passing provider dictionaries through the pipeline:

- `ThreadSnapshot`: submission metadata, `op_author_id`, retrieval time, and flattened comments.
- `Post`: source ID, title, body, author ID, score, permalink, and content flags.
- `Comment`: source ID, parent ID, author ID, body, score, depth, order, and content flags.
- `SourceRef`: source type, source ID, permalink, author role, and original text hash.
- `NarrationSegment`: stable ID, kind, speaker label, spoken text, source refs, and priority.
- `NarrationScript`: ordered segments, editorial decisions, target duration, and estimated duration.
- `SpokenWord`: text, normalized text, start/end milliseconds, and segment ID.
- `CaptionCue`: phrase text, start/end milliseconds, emphasized word, and style.
- `Timeline`: background spans, narration spans, audio levels, caption cues, and output settings.
- `BuildManifest`: input hashes, configuration, provider identifiers, source refs, artifacts, and status.

Author IDs should be hashed in intermediate artifacts by default. The final narration should say role labels such as "OP" and "a commenter," not usernames, unless explicitly enabled.

## 5. Reddit ingestion

Use an authenticated, supported Reddit API client behind a small interface. Credentials come from environment variables and are never written to manifests or logs.

Ingestion requirements:

- Accept canonical post URLs and submission IDs.
- Fetch submission fields and enough of the comment tree to support selection.
- Preserve comment `parent_id`, depth, score, author identity, and API order.
- Identify OP by stable author identity, not by display-text comparison.
- Represent deleted authors and deleted/removed bodies explicitly.
- Reject private, quarantined, unavailable, or age-restricted material unless a future policy explicitly permits it.
- Cache a normalized snapshot so script and render work can be repeated without another API call.
- Record fetch time and source permalinks in the manifest.
- Use bounded retries with jitter for transient errors and respect rate-limit responses.

Do not silently switch to HTML scraping if API access fails. A cached snapshot can be supplied for offline builds and tests.

## 6. Story and comment selection

Selection should be deterministic, explainable, and tested independently of narration. It operates on normalized data and returns source IDs plus reason codes.

### Post treatment

- Narrate the cleaned title first.
- Use the body as the main story.
- Remove markdown formatting while preserving list and paragraph pauses.
- Skip boilerplate edits, link trackers, repeated updates, and pronunciation-hostile URL text.
- Truncate only at sentence boundaries and record the omission in the script metadata.

### Eligible comments

A comment is eligible when it has usable text, is not deleted/removed, passes configured content filters, and is within the fetched depth/size limits. Very short reactions, bot messages, duplicate quotes, link-only comments, and obvious boilerplate receive a penalty or are excluded.

### OP-reply pairing

Treat a non-OP comment and its direct OP reply as an atomic `CommentExchange` candidate:

```text
Commenter: relevant question, correction, or observation
OP: direct answer or clarification
```

- Never narrate an OP reply without enough parent context to understand it.
- Give the pair a substantial priority bonus when the OP reply adds new information.
- If an OP reply answers a nested comment, include only the shortest necessary ancestor context.
- Do not include several near-identical OP replies; keep the clearest/highest-value exchange.
- A standalone OP comment may be selected when it is clearly an update or clarification.
- Preserve conversation order within a pair even when ranking candidates globally.

### Initial scoring heuristic

Use normalized components so one viral score cannot dominate the whole selection:

```text
candidate_score =
    0.30 * normalized_reddit_score
  + 0.20 * relevance_to_post
  + 0.15 * information_density
  + 0.10 * question_or_clarification_signal
  + 0.20 * op_reply_bonus
  + 0.05 * early_thread_bonus
  - duplicate_penalty
  - low_signal_penalty
  - excessive_length_penalty
```

For the MVP, relevance and information density should use deterministic lexical features. A later semantic-ranker adapter may improve ranking, but builds must retain scores and reason codes so selections remain auditable. Do not make a remote LLM a hard dependency.

### Duration budgeting

Estimate speech at a configurable words-per-minute rate before invoking TTS. A reasonable default allocation is:

- 8%: title and short introduction;
- 62%: post body;
- 25%: comments and OP exchanges;
- 5%: pauses and outro buffer.

Fill the comment budget with whole candidates. Prefer fewer complete exchanges over many cut-off comments. If the post alone exceeds the budget, preserve the opening, core event, and resolution when they can be found without inventing text; otherwise make a sentence-boundary extract and surface a warning for review.

## 7. Narration and speech

The editorial layer may clean and shorten source text, but must not fabricate claims, reactions, or an ending. Every narration segment carries one or more `SourceRef` values.

Script conventions:

- Announce transitions naturally: "Here is what OP wrote," "One commenter asked," and "OP replied."
- Strip usernames by default.
- Expand common abbreviations only through a reviewed pronunciation dictionary.
- Preserve meaningful emphasis without narrating markdown syntax.
- Insert explicit pause markers between the post and comment exchanges.
- Store both original excerpts and spoken text in the review artifact.

Define a `SpeechProvider` protocol that accepts segments and returns audio plus word timestamps. The concrete provider is configuration, not domain logic. Cache each segment by normalized text, voice settings, and provider identity. If a provider cannot return word timings, use a separate forced-alignment adapter and fail clearly when alignment confidence is too low.

Synthesize one segment at a time, then concatenate with controlled pauses. This allows a changed comment to reuse unchanged audio and makes timestamp failures easy to isolate.

## 8. Captions

Generate captions from actual word timing, not a characters-per-second estimate.

Caption behavior:

- Use phrases of roughly 2-5 words, with a configurable character limit.
- Break on punctuation, long pauses, speaker transitions, and safe syntactic boundaries.
- Keep a phrase on screen long enough to read, without extending beyond its spoken words by more than the configured tail.
- Highlight the currently spoken word or phrase.
- Animate entry with a short scale/opacity pop, settle quickly, and avoid constant motion after entry.
- Render inside a vertical-video safe area so platform controls do not cover text.
- Use a high-contrast font, outline/shadow, and at most two lines.
- Switch style or speaker label for commenter/OP transitions without moving the caption anchor.

ASS subtitles rendered by FFmpeg/libass are the initial implementation because they are inspectable, deterministic, and can express karaoke timing and transform tags. Also export plain SRT for accessibility and editing. Bundle or explicitly configure a font so rendering does not vary by machine.

## 9. Video and audio composition

### Background presets

Both presets consume a local video file:

- `subway`: center-weighted crop with optional horizontal offset for runner footage.
- `minecraft`: center-weighted crop with optional vertical offset for parkour footage.

Common behavior:

- Validate the source with `ffprobe` before expensive work.
- Scale and crop to 1080 x 1920 without stretching.
- Trim from a configurable start point and loop if it is shorter than narration.
- Prefer a clean cut when looping; a future enhancement may select low-motion loop points.
- Strip source audio by default. If retained, duck it under speech.
- Never upscale a severely undersized clip without warning.

### Output defaults

- MP4 container, H.264 video, AAC audio, `yuv420p` pixel format.
- 1080 x 1920, 30 fps, constant frame rate.
- Narration normalized to a configurable speech target; final true peak kept below the configured ceiling.
- Optional background music must be local/licensed and side-chain ducked beneath narration.
- Captions burned into the delivery MP4, with `.ass` and `.srt` sidecars retained.

All FFmpeg invocations should be built as argument arrays, never interpolated shell strings.

## 10. Run artifacts and resumability

Each build receives an immutable run directory:

```text
runs/<run-id>/
  manifest.json
  thread.json
  selection.json
  script.json
  script.txt
  speech/
  speech.json
  captions.json
  captions.ass
  captions.srt
  timeline-preview.json
  timeline-final.json
  preview.mp4
  final.mp4
  verification-preview.json
  verification.json
  logs/
```

Pipeline stages are `ingest`, `select`, `script`, `synthesize`, `caption`, profile-specific
`render`, and profile-specific `verify`. A stage can resume only when the hashes of its inputs,
relevant configuration, and declared outputs still match. Failed runs keep their review artifacts
and report the failed stage; they must not masquerade as successful output.

Large media, generated audio, run directories, credentials, and API snapshots containing real user content must be excluded from Git. Tests use deliberately sanitized fixtures.

## 11. Configuration and secrets

Use a checked-in example configuration and environment variables for secrets. Store non-secret per-run choices submitted by the web UI in the immutable run configuration. Expected configuration groups:

- Reddit credentials, fetch limits, and retry policy;
- target duration and words per minute;
- content filters and username policy;
- speech provider, voice, rate, and pronunciation dictionary;
- caption font, colors, position, chunk sizes, and animation timing;
- background crop offsets and audio policy;
- output resolution, frame rate, codec, and loudness targets.

Precedence should be CLI flags or validated web-form values, then project config, then defaults. Secrets have environment-only precedence and must never be accepted in a committed config file or browser form.

## 12. Safety, privacy, and rights

- Use public content only and retain source permalinks in private build metadata.
- Skip deleted/removed text and re-check availability before a publish-oriented final build.
- Support a configurable denylist for subreddits, authors, domains, and sensitive topics.
- Default to excluding NSFW, sexual content involving minors, personal data, targeted harassment, and doxxing indicators.
- Avoid reading usernames aloud and redact phone numbers, email addresses, physical addresses, and similar identifiers before TTS.
- Never imply that a generated voice is the Reddit author's real voice.
- Require an explicit `--acknowledge-rights` gate before final rendering; preview generation can remain ungated.
- Store the exact spoken-text/source mapping so a human can review attribution and transformations.

Automated filters reduce risk but do not replace review. The CLI should print the script and source report location before the final render step; the web UI should show the same source-linked review and require an explicit confirmation.

## 13. CLI surface

Planned commands:

```text
redditsurfer ingest URL                 fetch and snapshot a thread
redditsurfer select RUN                 score and select story material
redditsurfer script RUN                 build a reviewable narration script
redditsurfer narrate RUN                synthesize and align speech
redditsurfer preview RUN --background   render a short/low-resolution preview
redditsurfer render RUN --background    render the final video
redditsurfer verify RUN                 validate artifacts and output media
redditsurfer build URL --background     execute all applicable stages
```

Every command should support `--help`, return a non-zero exit status on failure, and print the run ID plus next useful action. `--dry-run` should show planned providers, duration budget, asset probes, and output paths without API or TTS charges.

## 14. Web UI

### Technology and deployment

The MVP web UI is a local-first FastAPI application launched with:

```bash
uv run redditsurfer web --host 127.0.0.1 --port 8000
```

Bind to loopback by default and show a warning when binding to a non-loopback address. Use server-rendered Jinja templates and progressive enhancement so core review actions still work without a large client bundle. A small durable local worker executes slow pipeline stages outside request handlers and records job state in SQLite plus the run manifest. This is a single-machine queue, not a distributed job system.

### Required screens

1. **Dashboard** - list runs with thumbnail/status, current stage, duration, creation time, and actions to resume, inspect, or download.
2. **New project** - Reddit URL, gameplay upload or approved local-asset selection, background preset, target duration, voice, and output profile.
3. **Source review** - display the post and selected comment candidates with scores/reason codes. Show commenter + OP reply as one visual exchange and enforce atomic include/exclude behavior.
4. **Script editor** - reorder whole exchanges, edit spoken text, compare spoken text with source excerpts, show estimated duration, and explicitly flag human edits. Any edit invalidates speech and downstream artifacts.
5. **Style and audio** - choose voice settings, caption theme/position, background crop offset, gameplay-audio policy, and optional licensed music.
6. **Preview and render** - show stage progress and logs, play the low-resolution preview, surface warnings, accept the rights acknowledgement, and start/cancel the final render.
7. **Artifacts** - play the final video and download MP4, ASS, SRT, script, selection report, and redacted manifest.

### HTTP surface

Keep HTML routes and a small JSON/SSE surface under stable prefixes:

```text
GET  /                         dashboard
GET  /runs/new                project form
POST /runs                    validate inputs and create run
GET  /runs/{run_id}           run overview
GET  /runs/{run_id}/sources   source and comment-exchange review
POST /runs/{run_id}/selection apply atomic selection changes
GET  /runs/{run_id}/script    script review/editor
POST /runs/{run_id}/script    save a new script revision
POST /runs/{run_id}/preview   enqueue preview build
POST /runs/{run_id}/render    acknowledge rights and enqueue final build
POST /runs/{run_id}/cancel    request cooperative cancellation
GET  /runs/{run_id}/events    server-sent progress events
GET  /runs/{run_id}/artifacts/{name}  allowlisted artifact download
```

Use POST/Redirect/GET for HTML forms. Return structured validation errors for enhanced requests. Job progress events should include stage, state, percentage when meaningful, and a safe human-readable message; the manifest remains the source of truth after reconnect or restart.

### Web security and file handling

- Treat the UI as untrusted input even when it runs locally.
- Use CSRF protection for state-changing browser requests and secure cookie settings appropriate to the deployment mode.
- Generate server-side run IDs; never accept filesystem paths as run IDs or artifact names.
- Allowlist upload extensions only as an early hint, then inspect file signatures and validate media with ffprobe.
- Stream uploads to a per-run staging directory with configurable byte and duration limits; never load full videos into memory.
- Sanitize display text, rely on template autoescaping, and never render Reddit HTML as trusted markup.
- Serve only allowlisted artifacts from resolved paths proven to remain inside that run directory, with HTTP range support for video playback.
- Do not expose secrets, source author hashes, raw environment data, unrestricted logs, or arbitrary local files through routes.
- Prevent duplicate paid work by making enqueue operations idempotent per run, stage, and input/config hash.
- Support cooperative cancellation between segments/processes and terminate owned FFmpeg child processes cleanly.

### UI behavior and accessibility

- Make stage status survive refresh and reconnect automatically to progress events.
- Disable actions whose prerequisites are stale or incomplete and explain the required next step.
- Clearly distinguish automatic selections from human edits and show which downstream artifacts will be invalidated before saving.
- Use keyboard-accessible controls, visible focus states, semantic labels, sufficient contrast, and text alternatives for status/thumbnail content.
- Make the review workflow usable at laptop and tablet widths; the generated 9:16 preview should fit without forcing page-height overflow.
- Do not rely on color alone to distinguish OP, commenter, selected, excluded, warning, or failure states.

## 15. Delivery phases

### Phase 0 - foundation

Status: implemented. The environment capability check currently reports that FFmpeg/ffprobe must be installed on this machine before rendering phases can run.

- Add package modules, typed domain models, configuration loading, structured errors, and run storage.
- Add development dependencies, lint/type/test commands, and sanitized fixtures.
- Detect required `ffmpeg`/`ffprobe` capabilities, including libass/font support.

Acceptance: a run can be created, validated configuration is recorded without secrets, and unit tests execute through `uv run pytest`.

### Phase 1 - Reddit snapshot and selection

Status: implemented with authenticated PRAW ingestion, normalized snapshots, deterministic heuristic ranking, atomic OP exchanges, CLI artifacts, and offline fixtures.

- Implement the Reddit adapter and URL parsing.
- Normalize post/comments and identify OP replies.
- Implement eligibility, candidate pairing, heuristic ranking, duration budgeting, and reason codes.
- Emit `thread.json` and `selection.json`.

Acceptance: fixture tests prove that a useful parent comment is included with its OP reply, deleted content is excluded, duplicate exchanges are suppressed, and the same input/config produces the same ordering.

### Phase 2 - script, TTS, and alignment

Status: implemented with source-linked review artifacts, deterministic cleanup and sentence-boundary editing, an Edge TTS word-boundary adapter, MP3-to-PCM normalization, per-segment WAV caching, absolute word alignment, and an offline fake provider.

- Implement text cleanup, source-linked script generation, speech adapter, per-segment caching, pauses, and timestamps.
- Emit a human-readable script/source report before network synthesis and support a fake offline speech provider in tests.

Acceptance: no spoken segment lacks a source reference; concatenated word timings are monotonic and remain within audio duration.

### Phase 3 - captions and media rendering

Status: implemented with word-timed deterministic cue chunking, animated ASS and plain SRT
sidecars, safe-area caption styles, Subway/Minecraft crop presets, validated local media probing,
timeline artifacts, narration/background audio policy, atomic FFmpeg preview rendering, and a
short synthetic integration render verified with ffprobe.

- Implement caption chunking, ASS/SRT export, background presets, audio mixing, timeline generation, and FFmpeg rendering.
- Add a small synthetic media fixture and a low-resolution integration render.

Acceptance: a fixture build produces a playable vertical MP4; captions stay in the safe area and within narration duration; `ffprobe` reports the intended streams, frame rate, and dimensions.

### Phase 4 - end-to-end CLI and quality checks

Status: implemented with checksum-validated stage reuse, cached normalized-thread builds,
failure/resume diagnostics, low-resolution preview and full-resolution final profiles, an
up-front final-render rights gate, persisted ffprobe quality reports, and interruption/cache
invalidation integration tests that prove completed speech is not requested again.

- Connect resumable stages to the CLI.
- Add preview/final profiles, rights acknowledgement, useful diagnostics, and final verification.
- Test interruption/resume and cache invalidation.

Acceptance: one command can build from a cached Reddit fixture and local gameplay, a failed stage resumes without repeating valid paid work, and a successful run contains every documented artifact.

### Phase 5 - local web UI

- Add the FastAPI application, server-rendered screens, validated forms, uploads, artifact streaming, and source/script review.
- Add the durable single-machine job worker, cancellation, reconnectable progress events, and restart recovery.
- Connect preview/final rendering to the same application services and cache rules used by the CLI.
- Add CSRF protection, path containment, upload limits, template escaping, and web accessibility checks.

Acceptance: a browser user can create a run, review an atomic commenter/OP exchange, revise the script, watch progress survive a refresh, play a preview, acknowledge rights, render the final video, and download allowlisted artifacts. Route tests prove path traversal and invalid uploads are rejected, duplicate submissions do not repeat paid work, and a server restart can recover job/run status.

### Phase 6 - optional improvements

- Drag-and-drop timeline editing and a richer visual caption/theme editor.
- Pluggable semantic ranking and summarization with strict source grounding.
- Automatic silence trimming, better loop-point selection, multiple caption themes, and landscape/square formats.
- Batch queue, authenticated multi-user deployment, and platform-specific export profiles.

These should not complicate the MVP interfaces until the end-to-end path is stable.

## 16. Test strategy

### Unit tests

- URL parsing and normalization.
- OP identification across deleted/missing authors.
- Parent/OP-reply atomic selection and ordering.
- Score normalization, reason codes, deduplication, and duration packing.
- Markdown/text cleanup and PII redaction.
- Caption chunk boundaries, timing monotonicity, and safe-area calculations.
- Timeline math, cache keys, and configuration precedence.

### Integration tests

- Recorded/sanitized API fixture through script generation.
- Fake TTS audio and timestamps through ASS/SRT generation.
- A 5-10 second synthetic FFmpeg render probed for expected media properties.
- Stage resume after a simulated provider failure.
- Web form through run creation using fake Reddit/TTS adapters.
- Job progress reconnect/recovery and idempotent preview/render enqueueing.
- Upload validation, CSRF, path traversal, template escaping, and artifact range requests.

### Manual acceptance checklist

- Review selected source excerpts and OP exchanges for context.
- Listen for mispronunciations, clipped words, bad transitions, and excessive pauses.
- Watch captions on a phone-sized preview for readability and control overlap.
- Confirm narration remains intelligible with any retained gameplay/music audio.
- Confirm footage/music rights and inspect the final source manifest.
- Refresh the UI during a job and verify that status/progress recovers correctly.
- Navigate the source/script workflow by keyboard and verify OP exchanges remain visually and semantically grouped.

## 17. Definition of done for the MVP

The MVP is done when a user with valid Reddit/TTS configuration and licensed local footage can complete the documented workflow from either the CLI or local web UI and receive:

1. a verified 1080 x 1920 MP4 of the requested approximate duration;
2. narration grounded in the title/body plus selected, contextual comments and OP replies;
3. synchronized popping captions with editable ASS and SRT sidecars;
4. a reviewable script and source manifest with no leaked credentials;
5. deterministic offline tests for selection and timeline logic;
6. clear failure/resume behavior for Reddit, TTS, alignment, and FFmpeg errors; and
7. a secure local browser workflow for source review, script/style changes, preview/final job progress, playback, and artifact downloads.
