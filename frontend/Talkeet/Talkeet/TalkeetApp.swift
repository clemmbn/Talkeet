/*
 * TalkeetApp.swift
 *
 * Purpose: Application entry point. Owns the BackendManager and drives its
 *          lifecycle from the SwiftUI scene phase.
 *
 * Responsibilities:
 *   - Instantiate BackendManager as @State so it lives for the full app lifetime.
 *   - Inject it into the environment for all descendant views.
 *   - Start the backend when the scene becomes active; stop it on background/quit.
 *
 * Constraints:
 *   - Backend starts on .active (not on init) to avoid a race with the window appearing.
 *   - Backend stops on .background only — .inactive fires when the app loses focus
 *     (e.g. user switches to another app) and must not kill the backend.
 *   - On macOS, Cmd+Q terminates the process before scenePhase reaches .background,
 *     so NSApplication.willTerminateNotification is also observed as a safety net.
 */

import AppKit
import OSLog
import SwiftUI

// One logger for the app entry point — lifecycle transitions only.
private let log = Logger(subsystem: Bundle.main.bundleIdentifier!, category: "App")

@main
struct TalkeetApp: App {
    @State private var backend = BackendManager()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup("Talkeet") {
            ContentView()
                .environment(backend)
                // Safety net: scenePhase never reaches .background on macOS Cmd+Q
                // because the process exits first. willTerminateNotification fires
                // synchronously during the quit sequence, guaranteeing cleanup.
                .onReceive(NotificationCenter.default.publisher(
                    for: NSApplication.willTerminateNotification)
                ) { _ in
                    log.info("App will terminate — stopping backend")
                    backend.stop()
                }
        }
        .onChange(of: scenePhase) { _, newPhase in
            switch newPhase {
            case .active:
                log.info("Scene became active — starting backend")
                backend.start()
            case .background:
                log.info("Scene moved to background — stopping backend")
                backend.stop()
            default:
                break
            }
        }
    }
}
