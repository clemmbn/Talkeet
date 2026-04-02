# Milestone 4 — Export endpoints

**Goal:** The backend can produce all target export files from a finalized segments list and transcription data.

## Deliverables

- [ ] `POST /export/edl` — CMX 3600 EDL
- [ ] `POST /export/fcpxml` — Final Cut Pro XML
- [ ] `POST /export/premiere` — Premiere Pro XML
- [ ] `POST /export/srt` — SubRip subtitle file
- [ ] Input (all endpoints): original file metadata + validated segments array + words array
- [ ] Each endpoint returns the file as a download or writes it to a specified output path

## Test

Import each generated file into its target NLE and verify cuts and (where applicable) subtitles are correct.
