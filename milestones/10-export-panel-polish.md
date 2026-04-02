# Milestone 10 — Export panel + final polish

**Goal:** The user can export their edit and subtitles in their chosen format.

## Deliverables

- [ ] Export panel: NLE selector (DaVinci / FCP / Premiere), output path picker
- [ ] Calls the appropriate export endpoint, saves files to disk
- [ ] SRT always exported alongside the XML/EDL
- [ ] Basic error states throughout the app (backend unreachable, ffmpeg missing, export failure)
- [ ] App icon
- [ ] README: full installation guide, WhisperX setup, first-run walkthrough
- [ ] GitHub release with a signed `.dmg` (or instructions for unsigned distribution)

## Test

Full end-to-end run on a real 5-minute video. Import EDL into DaVinci Resolve, import SRT, verify the edit is correct.
