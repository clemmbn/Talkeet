# Milestone 3 — Waveform extraction endpoint

**Goal:** The backend can extract audio amplitude data suitable for rendering a waveform in the UI.

## Deliverables

- [ ] `POST /analyze/waveform` endpoint
- [ ] Input: path to MP4 file + desired resolution (number of samples)
- [ ] Output: JSON array of floats (normalized amplitude per sample bucket)
- [ ] Performance target: under 5 seconds for a 10-minute file

## Test

Call the endpoint and plot the returned array with a quick Python script or paste it into an online visualizer to verify it matches the audio.
