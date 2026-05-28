import Foundation

/// Launches and supervises the Python dashboard and kiwix servers.
final class ServiceManager {

    private struct Service {
        let name:   String
        let script: URL
        var process:      Process?
        var restartCount: Int = 0
    }

    private let python:        URL
    private let resourcesPath: URL
    private var services:      [Service]
    private var monitorTimer:  Timer?

    init(resourcesPath: URL) {
        self.resourcesPath = resourcesPath
        self.python = resourcesPath.appendingPathComponent("python/bin/python3")
        self.services = [
            Service(name: "dashboard",
                    script: resourcesPath.appendingPathComponent("src/dashboard/server.py")),
            Service(name: "kiwix",
                    script: resourcesPath.appendingPathComponent("src/kiwix/server.py")),
        ]
    }

    // ── Public ────────────────────────────────────────────────────────────────

    func startAll() {
        for i in services.indices { launch(at: i) }
        startMonitor()
    }

    func stopAll() {
        monitorTimer?.invalidate()
        monitorTimer = nil
        for svc in services { svc.process?.terminate() }
    }

    // ── Private ───────────────────────────────────────────────────────────────

    private func launch(at index: Int) {
        let proc = Process()
        proc.executableURL = python
        proc.arguments     = [services[index].script.path]
        proc.environment   = {
            var env = ProcessInfo.processInfo.environment
            env["PYTHONPATH"] = resourcesPath.appendingPathComponent("src").path
            return env
        }()
        // Redirect output to per-service log files in ~/.config/svrn/logs/
        // This lets users (and support) diagnose startup failures on a fresh machine.
        let logsDir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/svrn/logs")
        try? FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
        let logFile = logsDir.appendingPathComponent("\(services[index].name).log")
        if FileManager.default.createFile(atPath: logFile.path, contents: nil) == false {
            _ = try? Data().write(to: logFile)  // ensure file exists
        }
        if let fh = try? FileHandle(forWritingTo: logFile) {
            fh.seekToEndOfFile()
            proc.standardOutput = fh
            proc.standardError  = fh
        } else {
            proc.standardOutput = FileHandle.nullDevice
            proc.standardError  = FileHandle.nullDevice
        }

        do {
            try proc.run()
            services[index].process = proc
            NSLog("[SVRN] Started %@ (pid %d)", services[index].name, proc.processIdentifier)
        } catch {
            NSLog("[SVRN] Failed to start %@: %@", services[index].name, error.localizedDescription)
        }
    }

    private func startMonitor() {
        // Poll on the main run loop; actual restarts are dispatched to a background queue
        monitorTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            self?.checkAll()
        }
    }

    private func checkAll() {
        for i in services.indices {
            guard let proc = services[i].process, !proc.isRunning else { continue }

            let count = services[i].restartCount
            let delay = min(5.0 * pow(2.0, Double(count)), 60.0)
            services[i].restartCount += 1

            NSLog("[SVRN] %@ exited — restart #%d in %.0fs", services[i].name, count + 1, delay)

            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + delay) { [weak self] in
                DispatchQueue.main.async { self?.launch(at: i) }
            }
        }
    }
}
