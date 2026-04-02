# Milestone 6 — Video player + segment list

**Goal:** The user can open a video, see it play, and view the list of detected segments alongside the player.

## Deliverables

- [ ] File open panel (drag-and-drop + file picker)
- [ ] AVKit video player embedded in the UI
- [ ] On file open: `POST /analyze/silence` is called with default parameters, result displayed as a scrollable segment list (speech / silence, timestamps)
- [ ] Clicking a segment in the list seeks the player to that position
- [ ] Loading state while backend processes the file

## Test

Open a real talking-head video, verify segments correspond to audible speech and silences, verify seek on click works.
