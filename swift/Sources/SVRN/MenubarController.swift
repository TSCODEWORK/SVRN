import AppKit
import Foundation

/// Owns the menu bar status item and polls service health every 12 seconds.
@MainActor
final class MenubarController {

    private let statusItem:   NSStatusItem
    private let serviceManager: ServiceManager
    private var timer:        Timer?

    // Menu items updated during polling
    private let storageItem  = NSMenuItem(title: "💾  Storage: checking…", action: nil, keyEquivalent: "")
    private let ollamaItem   = NSMenuItem(title: "🤖  AI: checking…",      action: nil, keyEquivalent: "")
    private let libraryItem  = NSMenuItem(title: "📖  Library: checking…", action: nil, keyEquivalent: "")

    init(serviceManager: ServiceManager) {
        self.serviceManager = serviceManager
        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        buildMenu()
        updateStatus()
        startPolling()
    }

    // ── Menu construction ─────────────────────────────────────────────────────

    private func buildMenu() {
        let menu = NSMenu()

        let titleItem = NSMenuItem(title: "SVRN", action: nil, keyEquivalent: "")
        titleItem.isEnabled = false
        menu.addItem(titleItem)

        menu.addItem(storageItem)
        menu.addItem(ollamaItem)
        menu.addItem(libraryItem)
        menu.addItem(.separator())

        menu.addItem(makeItem("Open Dashboard",      selector: #selector(openDashboard)))
        menu.addItem(makeItem("Open AI Chat",        selector: #selector(openChat)))
        menu.addItem(.separator())
        menu.addItem(makeItem("Reload ZIM Libraries", selector: #selector(reloadZim)))
        menu.addItem(.separator())

        let quit = NSMenuItem(title: "Quit SVRN",
                              action: #selector(NSApplication.terminate(_:)),
                              keyEquivalent: "q")
        menu.addItem(quit)

        statusItem.menu   = menu
        applyScopeIcon(tint: nil)
    }

    /// Renders the menu bar glyph as a rifle-scope reticle (circle + crosshair
    /// ticks) using the SF Symbol "scope". `tint` recolors it to signal health
    /// without changing its shape — nil keeps the default template color.
    private func applyScopeIcon(tint: NSColor?) {
        let config = NSImage.SymbolConfiguration(pointSize: 14, weight: .medium)
        let image  = NSImage(systemSymbolName: "scope", accessibilityDescription: "SVRN")?
            .withSymbolConfiguration(config)
        image?.isTemplate = (tint == nil)
        statusItem.button?.image = image
        statusItem.button?.contentTintColor = tint
    }

    private func makeItem(_ title: String, selector: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        item.target = self
        return item
    }

    // ── Polling ───────────────────────────────────────────────────────────────

    private func startPolling() {
        timer = Timer.scheduledTimer(withTimeInterval: 12.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.updateStatus() }
        }
    }

    func updateStatus() {
        let dashPort   = Config.dashboardPort
        let kiwixPort  = Config.kiwixPort
        let ollamaPort = Config.ollamaPort
        let storageURL = Config.storageRoot

        Task.detached(priority: .utility) { [weak self] in
            let dashUp   = portReachable(dashPort)
            let kiwixUp  = portReachable(kiwixPort)
            let ollamaUp = portReachable(ollamaPort)

            // Ollama label
            let ollamaText: String
            if ollamaUp {
                if let tags   = fetchJSON(from: "http://127.0.0.1:\(ollamaPort)/api/tags"),
                   let models = tags["models"] as? [[String: Any]] {
                    let n = models.count
                    ollamaText = "🤖  AI: \(n) model\(n == 1 ? "" : "s") ready"
                } else {
                    ollamaText = "🤖  AI: running"
                }
            } else if findOllama() != nil {
                ollamaText = "🤖  AI: installed, not running"
            } else {
                ollamaText = "🤖  AI: Ollama not installed"
            }

            // Library label
            let libraryText: String
            if kiwixUp {
                if let arr = fetchArray(from: "http://127.0.0.1:\(kiwixPort)/api/archives") {
                    let n = arr.count
                    libraryText = "📖  Library: \(n) collection\(n == 1 ? "" : "s")"
                } else {
                    libraryText = "📖  Library: running"
                }
            } else {
                libraryText = "📖  Library: offline"
            }

            // Storage label
            let storageText: String
            if let root = storageURL,
               let attrs = try? FileManager.default.attributesOfFileSystem(forPath: root.path),
               let free  = attrs[.systemFreeSize] as? Int64 {
                storageText = String(format: "💾  %.1f GB free", Double(free) / 1e9)
            } else if storageURL != nil {
                storageText = "💾  Storage connected"
            } else {
                storageText = "💾  No storage configured"
            }

            // Keep the scope-reticle shape always; recolor to signal health.
            let tint: NSColor? = (dashUp && kiwixUp) ? nil
                                : (dashUp || kiwixUp) ? .systemOrange
                                : .systemRed

            await MainActor.run { [weak self] in
                guard let self else { return }
                self.applyScopeIcon(tint: tint)
                self.storageItem.title  = storageText
                self.ollamaItem.title   = ollamaText
                self.libraryItem.title  = libraryText
            }
        }
    }

    // ── Actions ───────────────────────────────────────────────────────────────

    @objc private func openDashboard() {
        open(path: "")
    }

    @objc private func openChat() {
        open(path: "/chat")
    }

    @objc private func reloadZim() {
        let port = Config.kiwixPort
        Task.detached {
            if let url = URL(string: "http://127.0.0.1:\(port)/reload") {
                _ = try? Data(contentsOf: url)
            }
        }
    }

    private func open(path: String) {
        let port = Config.dashboardPort
        if let url = URL(string: "http://localhost:\(port)\(path)") {
            NSWorkspace.shared.open(url)
        }
    }
}

// ── Network helpers (called from background tasks) ────────────────────────────

func portReachable(_ port: Int, timeout: TimeInterval = 0.6) -> Bool {
    guard let url = URL(string: "http://127.0.0.1:\(port)/") else { return false }
    var request = URLRequest(url: url, timeoutInterval: timeout)
    request.httpMethod = "HEAD"
    let sema = DispatchSemaphore(value: 0)
    var ok = false
    URLSession.shared.dataTask(with: request) { _, resp, _ in
        ok = (resp as? HTTPURLResponse) != nil
        sema.signal()
    }.resume()
    sema.wait()
    return ok
}

func fetchJSON(from urlString: String) -> [String: Any]? {
    guard let url = URL(string: urlString) else { return nil }
    let request = URLRequest(url: url, timeoutInterval: 2.0)
    let sema = DispatchSemaphore(value: 0)
    var result: [String: Any]?
    URLSession.shared.dataTask(with: request) { data, _, _ in
        if let data { result = try? JSONSerialization.jsonObject(with: data) as? [String: Any] }
        sema.signal()
    }.resume()
    sema.wait()
    return result
}

func fetchArray(from urlString: String) -> [[String: Any]]? {
    guard let url = URL(string: urlString) else { return nil }
    let request = URLRequest(url: url, timeoutInterval: 2.0)
    let sema = DispatchSemaphore(value: 0)
    var result: [[String: Any]]?
    URLSession.shared.dataTask(with: request) { data, _, _ in
        if let data { result = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] }
        sema.signal()
    }.resume()
    sema.wait()
    return result
}

func findOllama() -> String? {
    let candidates = [
        "/usr/local/bin/ollama",
        "/opt/homebrew/bin/ollama",
        "\(NSHomeDirectory())/.ollama/bin/ollama",
        "/Applications/Ollama.app/Contents/Resources/ollama",
    ]
    return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
}
