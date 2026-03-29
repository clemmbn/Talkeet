# Talkeet Backend

Python/FastAPI backend for Talkeet. Handles silence detection, waveform extraction, transcription, and export. Communicates with the SwiftUI frontend over `localhost:8742`.

---

## Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) — package manager
- ffmpeg — must be on your system for local development

Install ffmpeg via Homebrew if needed:

```bash
brew install ffmpeg
```

---

## Install Dependencies

```bash
cd backend
uv sync --no-group transcription --group dev
```

The `transcription` group (torch, WhisperX) is excluded here — it is only needed for Milestone 2.

---

## Run the Server

```bash
FFMPEG_PATH=$(which ffmpeg) uv run uvicorn app.main:app --port 8742 --reload
```

The server listens on `http://localhost:8742`. The `--reload` flag enables auto-restart on file changes during development.

**Health check:**

```bash
curl http://localhost:8742/health
# {"status":"ok"}
```

---

## Silence Detection

```bash
curl -s -X POST http://localhost:8742/analyze/silence \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/absolute/path/to/video.mp4"}' | python3 -m json.tool
```

All parameters are optional (defaults shown):

```json
{
  "file_path": "/absolute/path/to/video.mp4",
  "threshold_db": -25.0,
  "min_silence_duration": 0.3,
  "pre_padding": 0.05,
  "post_padding": 0.05
}
```

Response: a JSON array of contiguous segments covering the full file duration:

```json
[
  { "start": 0.0,  "end": 1.23, "type": "silence" },
  { "start": 1.23, "end": 4.56, "type": "speech"  },
  { "start": 4.56, "end": 5.10, "type": "silence" }
]
```

---

## Run Tests

Unit tests run without a real video file:

```bash
FFMPEG_PATH=$(which ffmpeg) uv run pytest -v
```

Integration tests require a real `.mp4` file and are skipped otherwise:

```bash
FFMPEG_PATH=$(which ffmpeg) TEST_VIDEO=/path/to/video.mp4 uv run pytest -v
```
