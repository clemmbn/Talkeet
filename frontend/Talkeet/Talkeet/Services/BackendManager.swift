/*
 * BackendManager.swift
 *
 * Purpose: Manages the lifecycle of the Python/FastAPI backend subprocess.
 *
 * Responsibilities:
 *   - Locate and launch the backend process (dev via uv, prod via bundled binary).
 *   - Poll GET /health every 500ms until the server is ready or a 20s timeout elapses.
 *   - Terminate the process cleanly on app quit.
 *   - Expose a `status` property observed by SwiftUI views.
 *
 * Constraints:
 *   - App Sandbox must be disabled (ENABLE_APP_SANDBOX = NO) for Process to work.
 *   - Dev mode requires TALKEET_BACKEND_DEV_PATH env var set in the Xcode scheme.
 *   - FFMPEG_PATH must also be set in the scheme; it is forwarded to the child process
 *     automatically via environment inheritance.
 */

import Foundation
import OSLog
import Observation

// One logger for all backend lifecycle events — launch, polling, teardown.
private let log = Logger(subsystem: Bundle.main.bundleIdentifier!, category: "Backend")

// MARK: - BackendStatus

/// Represents the lifecycle state of the backend process.
enum BackendStatus: Equatable, Sendable {
    case idle
    case launching
    case ready
    case error(String)

    static func == (lhs: BackendStatus, rhs: BackendStatus) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.launching, .launching), (.ready, .ready):
            return true
        case (.error(let a), .error(let b)):
            return a == b
        default:
            return false
        }
    }
}

// MARK: - BackendManager

/// Observable manager for the backend subprocess lifecycle.
/// All members are @MainActor-isolated via the class annotation.
@Observable
@MainActor
final class BackendManager {

    // MARK: Public

    /// Current lifecycle state; observed by SwiftUI views.
    var status: BackendStatus = .idle

    // MARK: Private

    private var process: Process?
    private var pollingTask: Task<Void, Never>?

    private let backendPort = 8742
    private let healthURL = URL(string: "http://127.0.0.1:8742/health")!
    private let pollInterval: UInt64 = 500_000_000  // 500ms in nanoseconds
    private let pollTimeout: TimeInterval = 20

    // MARK: - Start

    /// Launches the backend process and begins health polling.
    /// No-op if not currently `.idle`.
    func start() {
        guard case .idle = status else {
            log.debug("start() called but status is not idle — ignoring")
            return
        }
        status = .launching
        log.info("Launching backend on port \(8742, privacy: .public)")

        do {
            let proc = Process()
            try configure(process: proc)
            try proc.run()
            process = proc
            log.info("Backend process started (pid \(proc.processIdentifier, privacy: .public))")
        } catch {
            let msg = "Failed to launch backend: \(error.localizedDescription)"
            log.error("\(msg, privacy: .public)")
            status = .error(msg)
            return
        }

        pollingTask = Task { [weak self] in
            await self?.pollHealth()
        }
    }

    // MARK: - Stop

    /// Terminates the backend process and cancels health polling.
    func stop() {
        pollingTask?.cancel()
        pollingTask = nil

        if let proc = process, proc.isRunning {
            log.info("Terminating backend process (pid \(proc.processIdentifier, privacy: .public))")
            proc.terminate()
        } else {
            log.debug("stop() called — no running process to terminate")
        }
        process = nil
        status = .idle
    }

    // MARK: - Process configuration

    /// Configures the Process for either dev or production mode.
    ///
    /// - Dev mode: `TALKEET_BACKEND_DEV_PATH` env var is set → runs
    ///   `uv run uvicorn app.main:app` in that directory.
    /// - Prod mode: runs the bundled `talkeet-backend` executable from the .app bundle.
    ///
    /// stdout and stderr are piped to prevent buffer deadlocks and console noise.
    /// The parent's environment is forwarded so FFMPEG_PATH and PATH are inherited.
    private func configure(process proc: Process) throws {
        // Pipe output to prevent the backend's stdout/stderr from deadlocking
        // when the pipe buffer fills, while keeping the Xcode console clean.
        proc.standardOutput = Pipe()
        proc.standardError = Pipe()

        // Inherit the full parent environment, then add/override backend-specific vars.
        var env = ProcessInfo.processInfo.environment
        env["TALKEET_PORT"] = "\(backendPort)"

        if let devPath = ProcessInfo.processInfo.environment["TALKEET_BACKEND_DEV_PATH"] {
            // Dev: launch via uv so the virtualenv and app module are resolved correctly.
            guard let uvPath = findUv() else {
                log.fault("uv binary not found — check ~/.local/bin, /opt/homebrew/bin, /usr/local/bin")
                throw BackendError.uvNotFound
            }
            log.info("Dev mode: using uv at \(uvPath, privacy: .public), backend dir \(devPath, privacy: .public)")
            proc.executableURL = URL(fileURLWithPath: uvPath)
            proc.arguments = [
                "run", "uvicorn", "app.main:app",
                "--port", "\(backendPort)",
                "--host", "127.0.0.1"
            ]
            proc.currentDirectoryURL = URL(fileURLWithPath: devPath)
        } else {
            // Prod: run the PyInstaller-bundled executable inside the .app bundle.
            let execURL = Bundle.main.bundleURL
                .appendingPathComponent("Contents/MacOS/talkeet-backend")
            guard FileManager.default.isExecutableFile(atPath: execURL.path) else {
                log.fault("Bundled backend binary not found at expected path — bundle may be malformed")
                throw BackendError.bundledBinaryNotFound(execURL.path)
            }
            log.info("Prod mode: using bundled binary at \(execURL.path, privacy: .public)")
            proc.executableURL = execURL
        }

        proc.environment = env
    }

    // MARK: - uv discovery

    /// Searches common install locations for the `uv` binary.
    /// Returns the first path where an executable file exists, or nil.
    private func findUv() -> String? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            "/opt/homebrew/bin/uv",
            "\(home)/.local/bin/uv",
            "/usr/local/bin/uv"
        ]
        return candidates.first {
            FileManager.default.isExecutableFile(atPath: $0)
        }
    }

    // MARK: - Health polling

    /// Polls GET /health every 500ms until the backend responds or the timeout elapses.
    /// Sets `.ready` on success, `.error` on timeout or unexpected process exit.
    private func pollHealth() async {
        let startTime = Date()
        log.debug("Health polling started (timeout \(Int(self.pollTimeout), privacy: .public)s)")

        while !Task.isCancelled {
            // Timeout guard
            if Date().timeIntervalSince(startTime) > pollTimeout {
                if case .launching = status {
                    let msg = "Backend did not become ready within \(Int(pollTimeout))s"
                    log.error("\(msg, privacy: .public)")
                    status = .error(msg)
                }
                return
            }

            // Detect unexpected process exit before the server became ready
            if let proc = process, !proc.isRunning {
                if case .launching = status {
                    let msg = "Backend process exited (code \(proc.terminationStatus))"
                    log.error("\(msg, privacy: .public)")
                    status = .error(msg)
                }
                return
            }

            if await checkHealth() {
                let elapsed = Date().timeIntervalSince(startTime)
                log.info("Backend ready after \(String(format: "%.2f", elapsed), privacy: .public)s")
                status = .ready
                return
            }

            try? await Task.sleep(nanoseconds: pollInterval)
        }

        log.debug("Health polling cancelled")
    }

    /// Performs a single GET /health request.
    /// Returns true if the response is HTTP 200 with `{"status": "ok"}`.
    private func checkHealth() async -> Bool {
        guard !Task.isCancelled else { return false }
        do {
            var request = URLRequest(url: healthURL)
            request.timeoutInterval = 2
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                return false
            }
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: String] {
                return json["status"] == "ok"
            }
            return false
        } catch {
            return false
        }
    }
}

// MARK: - BackendError

private enum BackendError: LocalizedError {
    case uvNotFound
    case bundledBinaryNotFound(String)

    var errorDescription: String? {
        switch self {
        case .uvNotFound:
            return "Could not find 'uv' at ~/.local/bin/uv, /opt/homebrew/bin/uv, or /usr/local/bin/uv"
        case .bundledBinaryNotFound(let path):
            return "Bundled backend binary not found at: \(path)"
        }
    }
}
