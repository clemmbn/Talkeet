/*
 * ContentView.swift
 *
 * Purpose: Main window content for Milestone 5. Shows a backend status indicator
 *          and a drop zone for selecting the video file to edit.
 *
 * Responsibilities:
 *   - Display backend status (idle / launching / ready / error) as a colored badge.
 *   - Accept .mp4 and .mov file drops and display the selected filename.
 *   - Disable drop zone interaction and dim it when the backend is not ready.
 *
 * Constraints:
 *   - Drop zone is non-functional while the backend is not .ready to prevent the user
 *     from starting a workflow before the backend can handle requests.
 *   - droppedFileURL is local @State for M5; it will move to a shared AppState in M6.
 */

import OSLog
import SwiftUI

// Logs user-facing interactions: file drops, backend readiness gate.
private let log = Logger(subsystem: Bundle.main.bundleIdentifier!, category: "UI")

struct ContentView: View {
    @Environment(BackendManager.self) private var backend
    @State private var droppedFileURL: URL?
    @State private var isTargeted = false

    var body: some View {
        VStack(spacing: 24) {
            statusBar
            dropZone
        }
        .padding(32)
        .frame(minWidth: 480, minHeight: 360)
    }

    // MARK: - Status bar

    private var statusBar: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(statusColor)
                .frame(width: 10, height: 10)
            Text(statusLabel)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Spacer()
        }
    }

    private var statusColor: Color {
        switch backend.status {
        case .idle:      return .gray
        case .launching: return .yellow
        case .ready:     return .green
        case .error:     return .red
        }
    }

    private var statusLabel: String {
        switch backend.status {
        case .idle:             return "Idle"
        case .launching:        return "Starting…"
        case .ready:            return "Ready"
        case .error(let msg):   return "Error: \(msg)"
        }
    }

    // MARK: - Drop zone

    private var isReady: Bool {
        if case .ready = backend.status { return true }
        return false
    }

    private var dropZone: some View {
        ZStack {
            // Background and border — accent-tinted when a file is being dragged over
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(
                    isTargeted ? Color.accentColor : Color.secondary.opacity(0.4),
                    style: StrokeStyle(lineWidth: 2, dash: [8, 4])
                )
                .background(
                    RoundedRectangle(cornerRadius: 16)
                        .fill(isTargeted ? Color.accentColor.opacity(0.06) : Color.clear)
                )

            VStack(spacing: 12) {
                Image(systemName: "film")
                    .font(.system(size: 44))
                    .foregroundStyle(isReady ? .secondary : .tertiary)

                if let url = droppedFileURL {
                    Text(url.lastPathComponent)
                        .font(.headline)
                    Text("File selected")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text("Drop an MP4 or MOV here")
                        .font(.headline)
                        .foregroundStyle(isReady ? .primary : .tertiary)
                    Text(isReady ? "Drag a video file to begin" : "Waiting for backend…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(32)
        }
        // Dim the zone while the backend is not ready to signal unavailability
        .opacity(isReady ? 1.0 : 0.5)
        .dropDestination(for: URL.self) { urls, _ in
            guard isReady else {
                log.debug("File drop ignored — backend not ready")
                return false
            }
            let accepted = urls.first {
                ["mp4", "mov"].contains($0.pathExtension.lowercased())
            }
            guard let url = accepted else {
                // Log rejected extensions so we can diagnose unexpected drop payloads.
                let exts = urls.map { $0.pathExtension }.joined(separator: ", ")
                log.info("File drop rejected — unsupported extension(s): \(exts, privacy: .public)")
                return false
            }
            log.info("File accepted: \(url.lastPathComponent, privacy: .public)")
            droppedFileURL = url
            return true
        } isTargeted: { targeted in
            // Only show hover state when the backend is actually ready to accept a file
            isTargeted = isReady && targeted
        }
        .animation(.easeInOut(duration: 0.15), value: isTargeted)
        .animation(.easeInOut(duration: 0.2), value: isReady)
    }
}

#Preview {
    ContentView()
        .environment(BackendManager())
}
