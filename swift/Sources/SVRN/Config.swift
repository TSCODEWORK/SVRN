import Foundation

/// Reads and writes ~/.config/svrn/config.json and ports.json.
enum Config {

    private static let dir = FileManager.default
        .homeDirectoryForCurrentUser
        .appendingPathComponent(".config/svrn")

    static let configFile = dir.appendingPathComponent("config.json")
    static let portsFile  = dir.appendingPathComponent("ports.json")

    // ── Load / save ───────────────────────────────────────────────────────────

    private static func load() -> [String: Any] {
        guard
            let data = try? Data(contentsOf: configFile),
            let obj  = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [:] }
        return obj
    }

    private static func save(_ dict: [String: Any]) {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        guard let data = try? JSONSerialization.data(
            withJSONObject: dict, options: [.prettyPrinted, .sortedKeys]
        ) else { return }
        try? data.write(to: configFile, options: .atomic)
    }

    // ── Storage root ──────────────────────────────────────────────────────────

    static var storageRoot: URL? {
        guard let path = load()["storage_root"] as? String else { return nil }
        return URL(fileURLWithPath: path)
    }

    static func setStorageRoot(_ url: URL) {
        var cfg = load()
        cfg["storage_root"] = url.path
        save(cfg)
    }

    static var isFirstRun: Bool { storageRoot == nil }

    // ── Ports ─────────────────────────────────────────────────────────────────

    static func port(for service: String, fallback: Int) -> Int {
        guard
            let data = try? Data(contentsOf: portsFile),
            let obj  = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let port = obj[service] as? Int
        else { return fallback }
        return port
    }

    static var dashboardPort: Int { port(for: "dashboard", fallback: 3333) }
    static var kiwixPort:     Int { port(for: "kiwix",     fallback: 8888) }

    // ── Ollama port (user-configurable, default 11434) ────────────────────────

    static var ollamaPort: Int { (load()["ollama_port"] as? Int) ?? 11434 }
}
