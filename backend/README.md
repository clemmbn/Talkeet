# Talkeet Backend

Python/FastAPI backend for Talkeet. Handles silence detection, waveform extraction, transcription, and export. Communicates with the SwiftUI frontend over `localhost:8742`.

---

## Prerequisites

- Python 3.11+
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

## Transcription

Install the transcription dependency group first (separate step — torch conflicts with the base lockfile):

```bash
uv sync --group transcription
```

Then open a WebSocket connection for progress events, and call `POST /transcribe`:

```bash
# Terminal 1 — listen for progress
websocat ws://localhost:8742/ws/progress/my-job-1

# Terminal 2 — start transcription
curl -s -X POST http://localhost:8742/transcribe \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/absolute/path/to/video.mp4",
    "job_id": "my-job-1",
    "model_size": "base"
  }'
```

The WebSocket receives stage events in order, ending with a `"done"` frame containing the word array:

```json
{"stage": "loading_audio"}
{"stage": "downloading_model"}
{"stage": "transcribing"}
{"stage": "aligning"}
{"stage": "done", "result": [{"word": "hello", "start": 0.12, "end": 0.45}]}
```

### Model sizes and RAM requirements

| Model | RAM (CPU) | Speed | Notes |
|-------|-----------|-------|-------|
| `tiny` | ~1 GB | Very fast | Low accuracy |
| `base` | ~1 GB | Fast | **Default — good balance** |
| `small` | ~2 GB | Moderate | Better accuracy |
| `medium` | ~5 GB | Slow | High accuracy |
| `large-v2` | ~10 GB | Very slow | Best quality |
| `large-v3` | ~10 GB | Very slow | Latest, best quality |
| `large-v3-turbo` | ~6 GB | Slow | Fastest large model |

**Apple Silicon constraint:** always `device="cpu"`. MPS is not supported by CTranslate2. Models are cached in `~/Library/Application Support/Talkeet/models/` after first download.

---

## Waveform Extraction

```bash
curl -s -X POST http://localhost:8742/analyze/waveform \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/absolute/path/to/video.mp4", "num_samples": 1000}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{len(d)} samples, max={max(d):.3f}')"
```

Parameters:

```json
{
  "file_path": "/absolute/path/to/video.mp4",
  "num_samples": 1000
}
```

Response: a JSON array of `num_samples` floats in `[0.0, 1.0]` representing RMS amplitude per time bucket. The array is normalized so the loudest bucket is always `1.0`.

### What the array represents

The array index is **not** time in seconds — it is a bucket index. The mapping to real time is:

```
time = (index / num_samples) × total_duration_seconds
```

So with `num_samples=1000` on a 120 s video: index 0 → 0 s, index 500 → 60 s, index 999 → ~120 s. The frontend is responsible for converting between pixel positions, bucket indices, and seconds using the video duration it already knows.

### SwiftUI rendering strategy (Milestone 7)

The frontend requests a high-resolution array once (e.g. 8000–10000 samples) and keeps it in memory. During zoom and pan, SwiftUI slices the relevant sub-range of the array and stretches it to fill the canvas width — no additional network requests needed. This keeps waveform zoom/pan at 60 fps without any backend round-trips.

```
Full view:   render allSamples[0 ..< 8000]       → 1200 px canvas
Zoomed 4×:  render allSamples[1000 ..< 3000]     → same 1200 px canvas
```

If the user zooms in further than the initial resolution allows, the frontend can re-request a fresh denser slice with explicit `start_time`/`end_time` parameters (not yet implemented — deferred to M7 if needed).

**Visual verification** (requires the server to be running):

```bash
python resources/visualize_waveform.py /path/to/video.mp4
# Opens a matplotlib waveform plot and saves waveform.png
```

Falls back to an ASCII chart if matplotlib is not installed.

---

## Export

All four export endpoints share the same request body. `output_path` is optional — omit it to receive the file as a download, or provide a path to write it directly to disk.

### EDL (DaVinci Resolve)

```bash
curl -s -X POST http://localhost:8742/export/edl \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/absolute/path/to/video.mp4",
    "segments": [
      {"start": 0.0,  "end": 1.0, "type": "silence"},
      {"start": 1.0,  "end": 4.5, "type": "speech"},
      {"start": 4.5,  "end": 6.0, "type": "silence"},
      {"start": 6.0,  "end": 9.2, "type": "speech"}
    ],
    "words": []
  }' -o edit.edl
```

In DaVinci Resolve: **File → Import Timeline → Import AAF, EDL, XML…** → select `edit.edl`.

### FCPXML (Final Cut Pro)

```bash
curl -s -X POST http://localhost:8742/export/fcpxml \
  -H "Content-Type: application/json" \
  -d '{"file_path": "...", "segments": [...], "words": []}' \
  -o edit.fcpxml
```

In Final Cut Pro: **File → Import → XML…** → select `edit.fcpxml`.

### XML (Premiere Pro)

```bash
curl -s -X POST http://localhost:8742/export/premiere \
  -H "Content-Type: application/json" \
  -d '{"file_path": "...", "segments": [...], "words": []}' \
  -o edit.xml
```

In Premiere Pro: **File → Import** → select `edit.xml`.

### SRT subtitles

Requires words from the transcription step. Each speech segment becomes one subtitle block.

```bash
curl -s -X POST http://localhost:8742/export/srt \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/absolute/path/to/video.mp4",
    "segments": [
      {"start": 1.0, "end": 4.5, "type": "speech"}
    ],
    "words": [
      {"word": "Hello", "start": 1.3, "end": 1.6},
      {"word": "world", "start": 1.7, "end": 2.0}
    ]
  }' -o subtitles.srt
```

### Write to disk instead of downloading

Add `"output_path"` to any export request to write the file directly:

```bash
curl -s -X POST http://localhost:8742/export/edl \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/path/to/video.mp4",
    "segments": [...],
    "words": [],
    "output_path": "/path/to/output/edit.edl"
  }'
# {"written_to": "/path/to/output/edit.edl"}
```

---

## Typical workflow

A full end-to-end session using all four milestones:

```bash
# 1. Detect silence → get segments
curl -s -X POST http://localhost:8742/analyze/silence \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/path/to/video.mp4"}' > segments.json

# 2. Fetch waveform for UI rendering (optional)
curl -s -X POST http://localhost:8742/analyze/waveform \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/path/to/video.mp4", "num_samples": 8000}' > waveform.json

# 3. Transcribe (optional — needed for SRT)
#    Open WebSocket first, then POST /transcribe (see Transcription section)

# 4. Export — pass segments (and words if transcribed) to the export endpoint
curl -s -X POST http://localhost:8742/export/edl \
  -H "Content-Type: application/json" \
  -d "{\"file_path\": \"/path/to/video.mp4\", \"segments\": $(cat segments.json), \"words\": []}" \
  -o edit.edl
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
