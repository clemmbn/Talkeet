# Milestone 9 — Parameters panel + WhisperX integration

**Goal:** The user can adjust all processing parameters from the UI and trigger re-analysis or transcription.

## Deliverables

- [ ] Parameters panel (sidebar or sheet) with:
  - [ ] Silence detection: threshold slider (dB), min silence duration, pre/post padding
  - [ ] Transcription: model size selector, language selector
- [ ] "Re-analyze" button: re-runs silence detection with current parameters, updates segments and timeline
- [ ] "Transcribe" button: calls `POST /transcribe`, populates word-level data in the segment list and on the timeline
- [ ] Progress indicator via WebSocket for transcription (model download + processing)
- [ ] Subtitle text editable inline in the segment list

## Test

Adjust threshold slider, re-analyze, verify segment boundaries change. Transcribe a clip and verify word timestamps appear on the timeline.
