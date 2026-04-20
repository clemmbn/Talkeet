# CLAUDE.md — Talkeet Backend

## Scope

This is the Python/FastAPI backend for Talkeet, a native macOS video editor. It handles all heavy processing (silence detection, waveform extraction, transcription, export) and exposes a REST + WebSocket API consumed by the SwiftUI frontend over `localhost:8742`.

**Current milestone: Milestone 5 — SwiftUI app scaffold + backend lifecycle management.**
All other milestones must not be implemented until explicitly requested.

### Workflow for each milestone

1. Read the milestone file in `../milestones/` — it contains the full spec, implementation guide, and test requirements.
2. Implement each deliverable and check its box as you go.
3. Run the full test suite before marking the milestone done.
4. **Keep the milestone file in sync with reality.** If implementation diverges from the spec (different signatures, added error cases, changed defaults), update the milestone file immediately. Milestone files are the source of truth for what was built, not just what was planned.

---

## Package Layout

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI app, lifespan, GET /health           [M1]
│   ├── config.py             # pydantic-settings: port, ffmpeg_path, cache  [M1]
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── analyze.py        # POST /analyze/silence                        [M1]
│   │   │                     # POST /analyze/waveform                       [M3]
│   │   └── transcribe.py     # POST /transcribe, WS /ws/progress/{job_id}  [M2]
│   └── services/
│       ├── __init__.py
│       ├── silence.py        # Silence detection logic                      [M1]
│       ├── transcription.py  # WhisperX transcription logic                 [M2]
│       └── waveform.py       # Waveform extraction logic                    [M3]
├── tests/
│   ├── __init__.py
│   ├── test_silence.py                                                      [M1]
│   ├── test_transcription.py                                                [M2]
│   └── test_waveform.py                                                     [M3]
├── resources/                # Reference files — do NOT import from here
│   ├── video_to_edl.py       # Reference implementation for silence detection
│   └── whisperx.md           # WhisperX API reference
├── pyproject.toml
└── README.md
```

Files tagged `[M4]` do not exist yet — they are created when that milestone starts.

---

## Permanent Constraints

- **ffmpeg:** Never use `shutil.which("ffmpeg")` in production. Resolve via `FFMPEG_PATH` env var first, then `../Resources/ffmpeg` relative to `sys.executable`. Full implementation in `../milestones/01-backend-scaffold.md`.
- **Port:** Fixed at `localhost:8742`. Configurable via `TALKEET_PORT` env var.
- **Python:** 3.11+ (pinned to 3.11 in `.python-version` — required for ML dep compatibility)
- **Dependency groups:** Install `base + dev` for all milestones. The `transcription` group (M2) must be installed separately — it pins specific ML versions (torch==2.8.0, torchaudio==2.8.0, whisperx==3.8.4) that must not be mixed with the base lockfile.
- **No cloud APIs:** All inference runs locally; no API keys.
- **Waveform array semantics:** The `num_samples` array returned by `/analyze/waveform` uses bucket indices, not seconds. Mapping: `time = (index / num_samples) × duration`. The SwiftUI frontend (M7) fetches once at high resolution (~8000–10000 samples) and slices client-side during zoom/pan — no re-fetching during gestures. The endpoint does not currently support `start_time`/`end_time` range parameters; add them in M7 only if the single high-res fetch proves insufficient.

---

## Running Locally

```bash
cd backend
uv sync --no-group transcription --group dev
FFMPEG_PATH=$(which ffmpeg) uv run uvicorn app.main:app --port 8742 --reload
```

For M2+ (after installing transcription deps separately):
```bash
uv sync --group transcription
```

---

## Testing

```bash
cd backend
FFMPEG_PATH=$(which ffmpeg) uv run pytest -v
```

Integration tests that require a real video file are skipped unless `TEST_VIDEO=/path/to/file.mp4` is set.

---

## Backend Milestones

| Milestone | Endpoint(s) | Status | Spec |
|-----------|-------------|--------|------|
| 1 — Silence detection | `POST /analyze/silence`, `GET /health` | ✅ Complete | `../milestones/01-backend-scaffold.md` |
| 2 — Transcription | `POST /transcribe`, `WS /ws/progress/{job_id}` | ✅ Complete | `../milestones/02-whisperx-transcription.md` |
| 3 — Waveform | `POST /analyze/waveform` | ✅ Complete | `../milestones/03-waveform-extraction.md` |
| 4 — Export | `POST /export/edl`, `/export/fcpxml`, `/export/premiere`, `/export/srt` | ✅ Complete | `../milestones/04-export-endpoints.md` |
