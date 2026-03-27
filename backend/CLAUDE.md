# CLAUDE.md — Talkeet Backend

## Scope

This is the Python/FastAPI backend for Talkeet, a native macOS video editor. The backend handles all heavy processing (silence detection, waveform extraction, transcription, export) and exposes a REST + WebSocket API consumed by the SwiftUI frontend over `localhost:8742`.

**Current milestone: Milestone 1 — Backend scaffold + silence detection.**
All other milestones are documented for context but must not be implemented until explicitly requested.

---

## Package Layout

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI app, lifespan, GET /health
│   ├── config.py             # Settings: ffmpeg path, port, model cache dir
│   ├── routers/
│   │   ├── __init__.py
│   │   └── analyze.py        # POST /analyze/silence  (Milestone 1)
│   │                         # POST /analyze/waveform (Milestone 3)
│   └── services/
│       ├── __init__.py
│       └── silence.py        # Silence detection logic
├── tests/
│   ├── __init__.py
│   └── test_silence.py
├── resources/                # Reference files — do NOT import from here
│   ├── video_to_edl.py       # Reference implementation for silence detection
│   └── whisperx.md           # WhisperX API reference (Milestone 2)
├── pyproject.toml
└── README.md
```

---

## ffmpeg Path Resolution

The backend expects ffmpeg to be bundled with the `.app`. **Never use `shutil.which("ffmpeg")` in production code.**

Resolution order in `app/config.py`:
1. `FFMPEG_PATH` environment variable (set this for local development)
2. `../Resources/ffmpeg` relative to `sys.executable` (final `.app` bundle layout)

```python
import os, sys
from pathlib import Path

def resolve_ffmpeg() -> str:
    if path := os.environ.get("FFMPEG_PATH"):
        return path
    bundled = Path(sys.executable).parent.parent / "Resources" / "ffmpeg"
    if bundled.exists():
        return str(bundled)
    raise RuntimeError(
        "ffmpeg not found. Set the FFMPEG_PATH environment variable "
        "to the ffmpeg binary (e.g. FFMPEG_PATH=$(which ffmpeg))."
    )
```

For local development:
```bash
FFMPEG_PATH=$(which ffmpeg) uv run uvicorn app.main:app --port 8742 --reload
```

---

## Milestone 1 — `POST /analyze/silence`

### Request body

```json
{
  "file_path": "/absolute/path/to/video.mp4",
  "threshold_db": -25.0,
  "min_silence_duration": 0.3,
  "pre_padding": 0.05,
  "post_padding": 0.05
}
```

All parameters except `file_path` are optional with the defaults above.

### Response

```json
[
  { "start": 0.0,  "end": 1.23, "type": "silence" },
  { "start": 1.23, "end": 4.56, "type": "speech"  },
  { "start": 4.56, "end": 5.10, "type": "silence" }
]
```

- **Both** speech and silence segments are returned; they must be contiguous and cover the full file duration without gaps or overlaps.
- Segments shorter than 5 frames at the file's native FPS are dropped after padding is applied.

### Error responses

| Condition | HTTP status | Detail string |
|-----------|-------------|---------------|
| `file_path` does not exist | 404 | `"File not found: <path>"` |
| Unsupported extension (not `.mp4` / `.mov`) | 422 | `"Unsupported file type: <ext>"` |
| ffmpeg subprocess failure | 500 | `"ffmpeg error: <stderr excerpt>"` |
| ffmpeg binary not found | 500 | `"ffmpeg not found. Set FFMPEG_PATH..."` |

### Implementation — `app/services/silence.py`

Port the logic from `resources/video_to_edl.py`. The functions to carry over (adapted):

1. **`extract_audio(input_path, ffmpeg_path) -> str`** — extracts 16 kHz mono WAV to a `tempfile.mktemp` path; raises `RuntimeError` on failure; caller is responsible for cleanup.

2. **`get_audio_duration(wav_path, ffmpeg_path) -> float`** — uses `ffprobe` (same directory as ffmpeg) to get duration in seconds.

3. **`detect_silences(wav_path, threshold_db, min_duration, ffmpeg_path) -> list[tuple[float, float]]`** — runs `ffmpeg silencedetect`, parses `silence_start` / `silence_end` from stderr; handles trailing open silence (no `silence_end`).

4. **`build_segments(silences, audio_duration, pre_padding, post_padding, fps) -> list[Segment]`** — inverts silence intervals to speech intervals, applies asymmetric padding (pre_padding before speech start, post_padding after speech end), clamps to midpoint between adjacent intervals, drops sub-5-frame segments, then re-derives silence segments to fill the gaps. Returns a flat list covering the full duration.

The padding clamping logic from `compute_edl_intervals` in the reference script applies but must be split into pre/post:
- `pad_start = max(raw_start - pre_padding, 0.0, prev_padded_end)`
- `pad_end = raw_end + post_padding`; clamped to midpoint with next speech start if applicable

### `GET /health`

Returns `{"status": "ok"}`. No authentication. Used by the SwiftUI layer to poll readiness on startup.

---

## `app/config.py`

Use `pydantic-settings` for configuration:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    port: int = 8742
    ffmpeg_path: str = ""           # resolved at startup if empty
    model_cache_dir: str = ""       # ~/Library/Application Support/Talkeet/models/

    model_config = {"env_prefix": "TALKEET_"}
```

Resolve `ffmpeg_path` at app startup (lifespan), not at import time, so missing ffmpeg fails fast with a clear log message rather than at the first request.

---

## `pyproject.toml`

```toml
[project]
name = "talkeet-backend"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic-settings>=2.0",
]

[dependency-groups]
transcription = [
    "torch",
    "torchaudio",
    "whisperx @ git+https://github.com/m-bain/whisperX.git",
]
dev = [
    "pytest>=8.0",
    "httpx>=0.27",
]

[tool.uv]
package = false

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Install base dependencies:
```bash
uv sync
```

Install transcription dependencies (Milestone 2+):
```bash
uv sync --group transcription
```

Install dev dependencies:
```bash
uv sync --group dev
```

---

## Running Locally

```bash
cd backend
uv sync --group dev
FFMPEG_PATH=$(which ffmpeg) uv run uvicorn app.main:app --port 8742 --reload
```

Health check:
```bash
curl http://localhost:8742/health
```

Test silence detection:
```bash
curl -s -X POST http://localhost:8742/analyze/silence \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/path/to/video.mp4"}' | python3 -m json.tool
```

---

## Testing

Tests live in `tests/`. Run with:
```bash
FFMPEG_PATH=$(which ffmpeg) uv run pytest -v
```

### `tests/test_silence.py`

Use FastAPI's `TestClient` (via `httpx`). Two test categories:

**Unit tests** (no real video needed) — mock `subprocess.run` to return pre-canned ffmpeg stderr and verify the parsing and segment-building logic:
- `test_detect_silences_parses_stderr` — feed known stderr, assert correct `(start, end)` tuples
- `test_build_segments_covers_full_duration` — given known silences + duration, assert segments are contiguous from 0 to duration
- `test_build_segments_pre_post_padding` — verify pre/post padding applied asymmetrically
- `test_build_segments_drops_short_segments` — sub-5-frame segments are excluded

**Integration tests** (require `TEST_VIDEO` env var pointing to a real `.mp4`):
- `test_analyze_silence_returns_200` — full round-trip; skip if `TEST_VIDEO` not set
- `test_analyze_silence_segments_contiguous` — assert no gaps between segments
- `test_analyze_silence_covers_duration` — first segment starts at 0, last ends at ≈ file duration

```python
import pytest, os
TEST_VIDEO = os.environ.get("TEST_VIDEO")
requires_video = pytest.mark.skipif(not TEST_VIDEO, reason="TEST_VIDEO not set")
```

---

## WebSocket Progress Events (Milestone 2+)

For the `/transcribe` endpoint (not in Milestone 1), progress is streamed as JSON messages over a WebSocket connection at `ws://localhost:8742/ws/progress/{job_id}`.

Stage sequence:
```json
{"stage": "downloading_model"}
{"stage": "transcribing"}
{"stage": "aligning"}
{"stage": "done"}
```

On error:
```json
{"stage": "error", "detail": "..."}
```

---

## WhisperX Notes (Milestone 2+)

Full API reference: `resources/whisperx.md`.

Key constraints:
- **Device:** Always `"cpu"` on Apple Silicon — MPS is not supported by CTranslate2.
- **Compute type:** Use `"int8"` on CPU for best performance with minimal accuracy loss.
- **Model cache:** Set `download_root` (in `load_model`) and `model_dir` (in `load_align_model`) to `~/Library/Application Support/Talkeet/models/`.
- **Word timestamps:** `word["start"]` and `word["end"]` may be absent after alignment — always use `.get("start")`.
- **Language detection:** runs on first 30 s; pass `language=` explicitly if audio starts with silence.

---

## Future Milestones (reference only — do not implement until requested)

| Milestone | Endpoint(s) |
|-----------|-------------|
| 2 — Transcription | `POST /transcribe`, `WS /ws/progress/{job_id}` |
| 3 — Waveform | `POST /analyze/waveform` |
| 4 — Export | `POST /export/edl`, `/export/fcpxml`, `/export/premiere`, `/export/srt` |
