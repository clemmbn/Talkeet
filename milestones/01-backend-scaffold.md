# Milestone 1 — Backend scaffold + silence detection

**Status: ✅ COMPLETE**

**Goal:** A working FastAPI server that accepts a video file and returns a list of segments (keep/cut) based on silence detection.

---

## Deliverables

- [x] FastAPI app with a single `POST /analyze/silence` endpoint
- [x] Input: path to MP4 file + silence parameters (threshold, min duration, padding)
- [x] Output: JSON array of segments `[{ start, end, type: "speech" | "silence" }]`
- [x] Basic error handling (file not found, ffmpeg failure)
- [x] README section: how to install dependencies and run the server manually

---

## Setup

Install base + dev dependencies:
```bash
cd backend
uv sync --no-group transcription --group dev
```

Run the server:
```bash
FFMPEG_PATH=$(which ffmpeg) uv run uvicorn app.main:app --port 8742 --reload
```

---

## Files created in this milestone

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI app, lifespan, GET /health
│   ├── config.py             # pydantic-settings: port, ffmpeg_path, model_cache_dir
│   ├── routers/
│   │   ├── __init__.py
│   │   └── analyze.py        # POST /analyze/silence
│   └── services/
│       ├── __init__.py
│       └── silence.py        # extract_audio, get_audio_duration, detect_silences, build_segments
├── tests/
│   ├── __init__.py
│   └── test_silence.py
├── pyproject.toml
└── README.md
```

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
    "whisperx",
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

> **Note:** The `transcription` group introduces conflicting torch index sources — always install it in isolation from the base deps. Never run `uv sync --group transcription` alongside a lock that includes base deps.

---

## `app/config.py`

Use `pydantic-settings` with the `TALKEET_` env prefix:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    port: int = 8742
    ffmpeg_path: str = ""           # resolved at startup if empty
    model_cache_dir: str = ""       # ~/Library/Application Support/Talkeet/models/

    model_config = {"env_prefix": "TALKEET_"}
```

Resolve `ffmpeg_path` at app startup (lifespan), not at import time, so a missing binary fails fast with a clear log message rather than silently on the first request.

---

## ffmpeg Path Resolution

**Never use `shutil.which("ffmpeg")` in production code.** The app bundles its own ffmpeg binary.

Resolution order implemented in `app/config.py`:
1. `FFMPEG_PATH` environment variable (for local development)
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

The resolved path is stored on `app.state.ffmpeg_path` at startup so every request handler can access it without re-resolving.

---

## `GET /health`

Returns `{"status": "ok"}`. No authentication. Used by the SwiftUI layer to poll readiness on startup.

---

## `POST /analyze/silence`

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

All parameters except `file_path` are optional with the defaults shown above.

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

Port the logic from `resources/video_to_edl.py`. Four functions to implement:

1. **`extract_audio(input_path, ffmpeg_path) -> str`** — extracts 16 kHz mono WAV to a `tempfile.mktemp` path; raises `RuntimeError` on failure; caller is responsible for cleanup.

2. **`get_audio_duration(wav_path, ffmpeg_path) -> float`** — uses `ffprobe` (same directory as ffmpeg) to get duration in seconds.

3. **`detect_silences(wav_path, threshold_db, min_duration, ffmpeg_path) -> list[tuple[float, float]]`** — runs `ffmpeg silencedetect`, parses `silence_start` / `silence_end` from stderr; handles trailing open silence (no `silence_end`) by appending `(start, math.inf)`.

4. **`build_segments(silences, audio_duration, pre_padding, post_padding, fps) -> list[Segment]`** — inverts silence intervals to speech intervals, applies asymmetric padding, clamps, drops sub-5-frame segments, then re-derives silence segments to fill the gaps. Returns a flat list covering 0 → audio_duration.

**Padding clamping logic** (applied per speech interval):
- `pad_start = max(raw_start - pre_padding, 0.0, prev_padded_end)`
- `pad_end = raw_end + post_padding`; if a next speech start exists, clamp to the midpoint between this end and the next raw start

---

## Tests — `tests/test_silence.py`

Use FastAPI's `TestClient` (via `httpx`). Two categories:

**Unit tests** — mock `subprocess.run`, no real video or ffmpeg needed:
- `test_detect_silences_parses_stderr` — feed known stderr, assert correct `(start, end)` tuples
- `test_detect_silences_trailing_open` — trailing silence with no `silence_end` → `(start, inf)`
- `test_build_segments_covers_full_duration` — segments contiguous from 0 to duration
- `test_build_segments_pre_post_padding` — pre/post padding applied asymmetrically
- `test_build_segments_drops_short_segments` — sub-5-frame speech intervals excluded

**Integration tests** — require `TEST_VIDEO` env var pointing to a real `.mp4`:
```python
TEST_VIDEO = os.environ.get("TEST_VIDEO")
requires_video = pytest.mark.skipif(not TEST_VIDEO, reason="TEST_VIDEO not set")
```
- `test_analyze_silence_returns_200` — full round-trip, non-empty segment list
- `test_analyze_silence_segments_contiguous` — no gaps between segments
- `test_analyze_silence_covers_duration` — first starts at 0, last ends at ≈ file duration

---

## Verification

```bash
# Unit tests (no video needed)
cd backend
FFMPEG_PATH=$(which ffmpeg) uv run pytest -v

# Integration tests (optional)
TEST_VIDEO=/path/to/video.mp4 FFMPEG_PATH=$(which ffmpeg) uv run pytest -v

# Manual — health check
curl http://localhost:8742/health

# Manual — silence detection
curl -s -X POST http://localhost:8742/analyze/silence \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/path/to/video.mp4"}' | python3 -m json.tool
```

Confirm: segments are contiguous, first starts at 0.0, types alternate speech/silence.
