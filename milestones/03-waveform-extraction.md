# Milestone 3 — Waveform extraction endpoint

**Status: ✅ Complete**

**Goal:** The backend can extract audio amplitude data suitable for rendering a waveform in the UI.

## Deliverables

- [x] `POST /analyze/waveform` endpoint
- [x] Input: path to MP4 file + desired resolution (number of samples)
- [x] Output: JSON array of floats (normalized amplitude per sample bucket)
- [x] Performance target: under 5 seconds for a 10-minute file

## Implementation notes

- Audio is piped from ffmpeg as raw 16-bit PCM (16 kHz mono) — no temp file written.
- RMS per bucket is used (not peak amplitude) for a perceptually smooth, editor-style waveform.
- Result is normalized to [0.0, 1.0]; silent files return all zeros without division errors.
- Default resolution: 1000 samples (configurable via `num_samples` in the request body).

## Array semantics and SwiftUI integration (decided post-implementation)

The array index is a **bucket index**, not time in seconds. The conversion is:

```
time_seconds = (index / num_samples) × total_duration_seconds
```

The frontend is responsible for this mapping using the duration it already has from the file metadata.

**Zoom/pan strategy for M7:** The SwiftUI `Canvas` view fetches the waveform once at high resolution (~8000–10000 samples) on file open, then renders a sub-slice of the array for the current viewport:

- Full view → render `allSamples[0 ..< count]` scaled to canvas width
- Zoomed in → render `allSamples[startIdx ..< endIdx]` scaled to canvas width
- Panning → shift the slice window

This avoids any backend round-trips during gesture handling, keeping zoom/pan at 60 fps. A `start_time`/`end_time` range parameter can be added to the endpoint in M7 if the single high-res fetch is not dense enough at maximum zoom, but this is not expected to be necessary for typical talking-head videos.

## Files

```
app/routers/analyze.py        # WaveformRequest model + POST /analyze/waveform
app/services/waveform.py      # extract_waveform() — core PCM → RMS pipeline
tests/test_waveform.py        # 11 unit tests + 3 integration tests (skipped w/o TEST_VIDEO)
resources/visualize_waveform.py  # manual verification script (matplotlib or ASCII)
```

## Test

```bash
# Unit tests (no video needed)
cd backend
FFMPEG_PATH=$(which ffmpeg) uv run pytest tests/test_waveform.py -v

# Integration tests (requires a real video)
TEST_VIDEO=/path/to/video.mp4 FFMPEG_PATH=$(which ffmpeg) uv run pytest tests/test_waveform.py -v

# Manual visual verification
FFMPEG_PATH=$(which ffmpeg) uv run uvicorn app.main:app --port 8742 --reload
# In another terminal:
python resources/visualize_waveform.py /path/to/video.mp4
# Opens a matplotlib waveform plot and saves waveform.png
```
