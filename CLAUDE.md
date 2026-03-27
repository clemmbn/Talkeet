# CLAUDE.md — Talkeet

## Project Overview

A native macOS application that streamlines the editing of talking-head videos. It automates silence removal, provides a visual interface to review and adjust cuts, transcribes speech segment by segment using WhisperX, and exports to the formats used by DaVinci Resolve, Final Cut Pro, and Premiere Pro.

The app is intended to be distributed publicly on GitHub. All processing must run fully locally (no cloud APIs).

---

## Architecture

The project is split into two independent layers that communicate over localhost.

### Backend — Python / FastAPI

- Handles all heavy processing: silence detection, audio analysis, waveform extraction, WhisperX transcription
- Exposes a REST API for discrete operations and WebSockets for real-time progress feedback
- Bundled as a self-contained executable (PyInstaller) inside the macOS `.app` bundle
- Launched and terminated automatically by the SwiftUI layer on app start/quit

### Frontend — Swift / SwiftUI

- Native macOS UI
- Video playback via AVKit
- Waveform and timeline rendered from data provided by the backend
- Communicates with the backend via URLSession (REST) and URLSessionWebSocketTask (progress streaming)

### Data flow

```
[MP4 file]
    → Backend: silence detection → segments JSON
    → Backend: waveform extraction → float array JSON
    → Backend: WhisperX transcription → words + timestamps JSON
    → Frontend: user reviews and edits segments
    → Backend: export to EDL / FCPXML / Premiere XML + SRT
```

---

## Tech Stack


| Layer                  | Technology                      |
| ------------------------ | --------------------------------- |
| Backend language       | Python 3.14+                    |
| Backend framework      | FastAPI + Uvicorn               |
| Audio/video processing | ffmpeg (subprocess, bundled binary) |
| Transcription          | WhisperX (local)                |
| Backend packaging      | uv                              |
| Frontend language      | Swift 6.2+                      |
| Frontend framework     | SwiftUI (macOS 26+)             |
| Video playback         | AVKit                           |
| HTTP client            | URLSession                      |

---

## Export Formats


| Target NLE      | Format         | Notes                                  |
| ----------------- | ---------------- | ---------------------------------------- |
| DaVinci Resolve | EDL (CMX 3600) | Primary target                         |
| Final Cut Pro   | FCPXML         | Secondary                              |
| Premiere Pro    | Premiere XML   | Secondary                              |
| Subtitles (all) | SRT            | Exported separately, imported manually |

---

## Silence Detection Parameters (exposed in UI)

- Volume threshold (dB)
- Minimum silence duration (ms)
- Pre-cut padding (ms)
- Post-cut padding (ms)
- Playback speed (for future use)

Additional parameters from `auto-editor` may be added after review.

---

## Project Milestones

Each milestone produces a concrete, testable deliverable. The project can be paused after any milestone.

---

### Milestone 1 — Backend scaffold + silence detection

**Goal:** A working FastAPI server that accepts a video file and returns a list of segments (keep/cut) based on silence detection.

**Deliverables:**

- FastAPI app with a single `POST /analyze/silence` endpoint
- Input: path to MP4 file + silence parameters (threshold, min duration, padding)
- Output: JSON array of segments `[{ start, end, type: "speech" | "silence" }]`
- Basic error handling (file not found, ffmpeg failure)
- README section: how to install dependencies and run the server manually

**Test:** Call the endpoint via `curl` or a REST client with a local MP4 file and verify the returned segments make sense.

---

### Milestone 2 — WhisperX transcription endpoint

**Goal:** The backend can transcribe a video file and return word-level timestamps.

**Deliverables:**

- `POST /transcribe` endpoint
- Input: path to MP4 file, Whisper model size, optional list of segments to restrict transcription scope
- Output: JSON array of words `[{ word, start, end, speaker }]`
- Model download handled gracefully on first run (progress via WebSocket)
- README section: supported model sizes, RAM requirements, Apple Silicon setup (always CPU — MPS not supported)

**Test:** Transcribe a short clip and verify word timestamps align with the audio when played back.

---

### Milestone 3 — Waveform extraction endpoint

**Goal:** The backend can extract audio amplitude data suitable for rendering a waveform in the UI.

**Deliverables:**

- `POST /analyze/waveform` endpoint
- Input: path to MP4 file + desired resolution (number of samples)
- Output: JSON array of floats (normalized amplitude per sample bucket)
- Performance target: under 5 seconds for a 10-minute file

**Test:** Call the endpoint and plot the returned array with a quick Python script or paste it into an online visualizer to verify it matches the audio.

---

### Milestone 4 — Export endpoints

**Goal:** The backend can produce all target export files from a finalized segments list and transcription data.

**Deliverables:**

- `POST /export/edl` — CMX 3600 EDL
- `POST /export/fcpxml` — Final Cut Pro XML
- `POST /export/premiere` — Premiere Pro XML
- `POST /export/srt` — SubRip subtitle file
- Input (all endpoints): original file metadata + validated segments array + words array
- Each endpoint returns the file as a download or writes it to a specified output path

**Test:** Import each generated file into its target NLE and verify cuts and (where applicable) subtitles are correct.

---

### Milestone 5 — SwiftUI app scaffold + backend lifecycle management

**Goal:** A SwiftUI app that launches the Python backend on startup and shuts it down on quit.

**Deliverables:**

- SwiftUI app project (Xcode)
- On launch: starts the bundled Python backend process, polls `GET /health` until ready
- On quit: terminates the backend process cleanly
- Basic window with a "Drop MP4 here" area and an app status indicator (backend ready / loading / error)
- Backend binary path resolved from the app bundle

**Test:** Launch and quit the app several times, verify the backend process starts and stops correctly (check Activity Monitor).

---

### Milestone 6 — Video player + segment list

**Goal:** The user can open a video, see it play, and view the list of detected segments alongside the player.

**Deliverables:**

- File open panel (drag-and-drop + file picker)
- AVKit video player embedded in the UI
- On file open: `POST /analyze/silence` is called with default parameters, result displayed as a scrollable segment list (speech / silence, timestamps)
- Clicking a segment in the list seeks the player to that position
- Loading state while backend processes the file

**Test:** Open a real talking-head video, verify segments correspond to audible speech and silences, verify seek on click works.

---

### Milestone 7 — Waveform timeline

**Goal:** A visual timeline showing the audio waveform with segment boundaries overlaid.

**Deliverables:**

- Waveform rendered in a custom SwiftUI `Canvas` view from the backend float array
- Segment regions color-coded (speech = neutral, silence = highlighted for removal)
- Playhead position synced with AVKit player (bidirectional: scrub timeline → player seeks, player plays → playhead moves)
- Timeline is horizontally scrollable and zoomable

**Test:** Scrub the timeline and verify the player follows. Play the video and verify the playhead tracks correctly.

---

### Milestone 8 — Segment editor + keep/cut decisions

**Goal:** The user can toggle individual segments between keep and cut, and preview the result.

**Deliverables:**

- Each segment in the list and on the timeline has a keep/cut toggle
- "Preview cuts" button: plays only the kept segments in sequence using AVKit (via a generated in-memory playlist or seek logic)
- Segment list also displays transcription text per segment (populated after Milestone 3 is triggered)
- Manual segment split and merge (split at playhead position, merge two adjacent segments)

**Test:** Toggle several segments, preview, verify only kept segments play back.

---

### Milestone 9 — Parameters panel + WhisperX integration

**Goal:** The user can adjust all processing parameters from the UI and trigger re-analysis or transcription.

**Deliverables:**

- Parameters panel (sidebar or sheet) with:
  - Silence detection: threshold slider (dB), min silence duration, pre/post padding
  - Transcription: model size selector, language selector
- "Re-analyze" button: re-runs silence detection with current parameters, updates segments and timeline
- "Transcribe" button: calls `POST /transcribe`, populates word-level data in the segment list and on the timeline
- Progress indicator via WebSocket for transcription (model download + processing)
- Subtitle text editable inline in the segment list

**Test:** Adjust threshold slider, re-analyze, verify segment boundaries change. Transcribe a clip and verify word timestamps appear on the timeline.

---

### Milestone 10 — Export panel + final polish

**Goal:** The user can export their edit and subtitles in their chosen format.

**Deliverables:**

- Export panel: NLE selector (DaVinci / FCP / Premiere), output path picker
- Calls the appropriate export endpoint, saves files to disk
- SRT always exported alongside the XML/EDL
- Basic error states throughout the app (backend unreachable, ffmpeg missing, export failure)
- App icon
- README: full installation guide, WhisperX setup, first-run walkthrough
- GitHub release with a signed `.dmg` (or instructions for unsigned distribution)

**Test:** Full end-to-end run on a real 5-minute video. Import EDL into DaVinci Resolve, import SRT, verify the edit is correct.

---

## Constraints and Key Decisions

- **Minimum macOS version:** 26 (Tahoe) — required for latest SwiftUI APIs and AVKit features
- **WhisperX model storage:** models downloaded to `~/Library/Application Support/Talkeet/models/` on first use
- **ffmpeg dependency:** bundled inside the app (static binary) so users do not need to install it separately
- **ffmpeg path resolution:** backend resolves ffmpeg via `FFMPEG_PATH` env var first, then `../Resources/ffmpeg` relative to the executable (bundle layout); never uses system PATH in production
- **ffmpeg usage:** called via `subprocess` directly (no ffmpeg-python wrapper); reference implementation in `backend/resources/video_to_edl.py`
- **Silence detection:** uses ffmpeg's `silencedetect` filter; pre-cut and post-cut padding are separate parameters applied asymmetrically around speech intervals
- **WhisperX device:** always `"cpu"` on Apple Silicon — MPS is not supported by CTranslate2; use `compute_type="int8"` for best CPU performance
- **WebSocket progress events:** simple stage strings — `"downloading_model"` → `"transcribing"` → `"aligning"` → `"done"` (or `"error"`)
- **Backend Python structure:** package layout with `app/routers/` and `app/services/`; managed with `uv`
- **No cloud processing:** all inference runs locally; no API keys required
- **Backend port:** fixed at `localhost:8742` (unlikely to conflict); configurable via environment variable if needed
- **File handling:** the app works with the original file in place; it never copies or moves the source video

---

## Out of Scope (for now)

- Multi-track or multicam editing
- Real-time silence detection while recording
- Direct NLE plugin / extension
- Windows or Linux support
