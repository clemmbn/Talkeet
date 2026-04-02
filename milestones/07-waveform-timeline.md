# Milestone 7 — Waveform timeline

**Goal:** A visual timeline showing the audio waveform with segment boundaries overlaid.

## Deliverables

- [ ] Waveform rendered in a custom SwiftUI `Canvas` view from the backend float array
- [ ] Segment regions color-coded (speech = neutral, silence = highlighted for removal)
- [ ] Playhead position synced with AVKit player (bidirectional: scrub timeline → player seeks, player plays → playhead moves)
- [ ] Timeline is horizontally scrollable and zoomable

## Test

Scrub the timeline and verify the player follows. Play the video and verify the playhead tracks correctly.
