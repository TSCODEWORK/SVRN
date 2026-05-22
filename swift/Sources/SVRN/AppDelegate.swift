import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {

    private var serviceManager:        ServiceManager?
    private var menubarController:     MenubarController?
    private var setupWindowController: NSWindowController?

    // ── Launch ────────────────────────────────────────────────────────────────

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Menubar-only app — no Dock icon, no app menu
        NSApp.setActivationPolicy(.accessory)

        let resources = resolveResourcesPath()
        serviceManager = ServiceManager(resourcesPath: resources)

        if Config.isFirstRun {
            showSetupWizard()
        } else {
            startNormally(openBrowser: true)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        serviceManager?.stopAll()
    }

    // ── Resource path resolution ──────────────────────────────────────────────

    /// Returns the Resources/ directory whether running inside an .app bundle
    /// or directly from `swift run` during development.
    private func resolveResourcesPath() -> URL {
        // Inside SVRN.app: Bundle.main.resourceURL is Contents/Resources/
        if let res = Bundle.main.resourceURL,
           FileManager.default.fileExists(atPath: res.appendingPathComponent("src").path) {
            return res
        }
        // Dev fallback: executable lives in .build/…, walk up to repo root
        let exe = URL(fileURLWithPath: CommandLine.arguments[0])
        var candidate = exe.deletingLastPathComponent()
        for _ in 0..<8 {
            if FileManager.default.fileExists(atPath: candidate.appendingPathComponent("src").path) {
                return candidate
            }
            candidate = candidate.deletingLastPathComponent()
        }
        return exe.deletingLastPathComponent() // best guess
    }

    // ── Normal startup ────────────────────────────────────────────────────────

    func startNormally(openBrowser: Bool) {
        serviceManager?.startAll()

        // MenubarController must be created on the main thread
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.menubarController = MenubarController(serviceManager: self.serviceManager!)
        }

        if openBrowser {
            // Give services a moment to bind their ports before opening the browser
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                let port = Config.dashboardPort
                if let url = URL(string: "http://localhost:\(port)") {
                    NSWorkspace.shared.open(url)
                }
            }
        }
    }

    // ── Setup wizard ──────────────────────────────────────────────────────────

    @MainActor private func showSetupWizard() {
        let vm = SetupViewModel { [weak self] in
            self?.setupDidComplete()
        }

        let hosting = NSHostingController(rootView: SetupWizard(vm: vm))

        let window = NSWindow(contentViewController: hosting)
        window.title                       = "SVRN Setup"
        window.styleMask                   = [.titled, .closable, .fullSizeContentView]
        window.titlebarAppearsTransparent  = true
        window.isMovableByWindowBackground = true
        window.setContentSize(NSSize(width: 520, height: 420))
        window.center()
        window.delegate = self

        setupWindowController = NSWindowController(window: window)
        setupWindowController?.showWindow(nil)

        // Bring to front — temporarily show in Dock so the window gets focus
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func setupDidComplete() {
        setupWindowController?.close()
        setupWindowController = nil
        NSApp.setActivationPolicy(.accessory)
        startNormally(openBrowser: true)
    }
}

// ── Window delegate — quit if setup window is closed without finishing ────────

extension AppDelegate: NSWindowDelegate {
    func windowWillClose(_ notification: Notification) {
        guard setupWindowController?.window === notification.object as? NSWindow else { return }
        // If setup was closed before completion and we have no menubar, quit cleanly
        if menubarController == nil {
            NSApp.terminate(nil)
        }
    }
}
