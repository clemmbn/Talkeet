# Milestone 4 — Export endpoints

**Status: ✅ Complete**

**Goal:** The backend can produce all target export files from a finalized segments list and transcription data.

## Deliverables

- [x] `POST /export/edl` — CMX 3600 EDL
- [x] `POST /export/fcpxml` — Final Cut Pro XML
- [x] `POST /export/premiere` — Premiere Pro XML
- [x] `POST /export/srt` — SubRip subtitle file
- [x] Input (all endpoints): original file metadata + validated segments array + words array
- [x] Each endpoint returns the file as a download or writes it to a specified output path

## Files

```
app/routers/export.py         # POST /export/{edl,fcpxml,premiere,srt}
app/services/export.py        # get_video_info, generate_edl/srt/fcpxml/premiere_xml
tests/test_export.py          # 34 unit + router tests (integration tests skipped w/o TEST_VIDEO)
```

## Implementation notes

- All four endpoints share a single `ExportRequest` model (file_path, segments, words, optional output_path).
- If `output_path` is omitted, the file is streamed as a download attachment; if provided, it is written to disk and `{"written_to": path}` is returned.
- FPS and duration are queried from the source video via a single ffprobe call (`get_video_info`) — not extracted from the segment list — to ensure timecode accuracy.
- SRT groups words by speech segment (one block per segment with matching words); words with None timestamps are skipped; segments with no matched words are omitted.
- FCPXML uses rational time fractions (`{frames}/{fps}s`) as required by the spec.
- Premiere Pro XML (xmeml v4) uses integer frame counts; the file element is declared in the first clipitem and referenced by id in subsequent ones (Premiere convention).
- No new dependencies — only Python stdlib (`xml.etree.ElementTree`, `pathlib`, `urllib.parse`).

## Setup

No additional setup required beyond Milestone 1. The export endpoints use the same `FFMPEG_PATH` resolution as the rest of the backend.

## Test

```bash
# Unit tests (no video needed)
cd backend
FFMPEG_PATH=$(which ffmpeg) uv run pytest tests/test_export.py -v

# Manual — start server
FFMPEG_PATH=$(which ffmpeg) uv run uvicorn app.main:app --port 8742 --reload

# Manual — EDL download
curl -s -X POST http://localhost:8742/export/edl \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/path/to/video.mp4",
    "segments": [
      {"start": 0.0, "end": 1.0, "type": "silence"},
      {"start": 1.0, "end": 4.5, "type": "speech"},
      {"start": 4.5, "end": 6.0, "type": "silence"},
      {"start": 6.0, "end": 9.0, "type": "speech"}
    ],
    "words": []
  }' -o output.edl && cat output.edl

# Manual — SRT with words
curl -s -X POST http://localhost:8742/export/srt \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/path/to/video.mp4",
    "segments": [{"start": 1.0, "end": 4.5, "type": "speech"}],
    "words": [
      {"word": "Hello", "start": 1.3, "end": 1.6},
      {"word": "world", "start": 1.7, "end": 2.0}
    ]
  }'
```

Verify: import EDL into DaVinci Resolve, FCPXML into Final Cut Pro, XML into Premiere Pro, and SRT into any player to confirm cuts and subtitles are correct.
