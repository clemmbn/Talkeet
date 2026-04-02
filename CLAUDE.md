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

See `milestones/` for individual specs:

- Milestone 1 — Backend scaffold + silence detection: @milestones/01-backend-scaffold.md
- Milestone 2 — WhisperX transcription endpoint: @milestones/02-whisperx-transcription.md
- Milestone 3 — Waveform extraction endpoint: @milestones/03-waveform-extraction.md
- Milestone 4 — Export endpoints: @milestones/04-export-endpoints.md
- Milestone 5 — SwiftUI app scaffold + backend lifecycle management: @milestones/05-swiftui-scaffold.md
- Milestone 6 — Video player + segment list: @milestones/06-video-player-segment-list.md
- Milestone 7 — Waveform timeline: @milestones/07-waveform-timeline.md
- Milestone 8 — Segment editor + keep/cut decisions: @milestones/08-segment-editor.md
- Milestone 9 — Parameters panel + WhisperX integration: @milestones/09-parameters-panel.md
- Milestone 10 — Export panel + final polish: @milestones/10-export-panel-polish.md

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
