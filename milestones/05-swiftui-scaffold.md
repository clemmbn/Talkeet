# Milestone 5 — SwiftUI app scaffold + backend lifecycle management

**Goal:** A SwiftUI app that launches the Python backend on startup and shuts it down on quit.

## Deliverables

- [ ] SwiftUI app project (Xcode)
- [ ] On launch: starts the bundled Python backend process, polls `GET /health` until ready
- [ ] On quit: terminates the backend process cleanly
- [ ] Basic window with a "Drop MP4 here" area and an app status indicator (backend ready / loading / error)
- [ ] Backend binary path resolved from the app bundle

## Test

Launch and quit the app several times, verify the backend process starts and stops correctly (check Activity Monitor).
