import SwiftUI
import AppKit

// ── View model ────────────────────────────────────────────────────────────────

@MainActor
final class SetupViewModel: ObservableObject {

    struct Check: Identifiable {
        let id   = UUID()
        var label:  String
        var status: Status = .pending

        enum Status { case pending, ok, warning, failed }
    }

    @Published var step         = 0
    @Published var checks:      [Check] = []
    @Published var ollamaFound  = false
    @Published var storagePath  = "\(NSHomeDirectory())/SVRN"
    @Published var storageError: String?
    @Published var freeSpaceLabel: String?
    @Published var isWorking    = false
    @Published var setupError:  String?

    var onComplete: () -> Void

    init(onComplete: @escaping () -> Void) {
        self.onComplete = onComplete
    }

    // ── Step 1: system checks ─────────────────────────────────────────────────

    func runChecks() {
        checks = [
            Check(label: "macOS version"),
            Check(label: "Architecture"),
            Check(label: "Bundled Python"),
            Check(label: "Ollama (AI engine)"),
        ]

        Task.detached(priority: .userInitiated) {
            // macOS version
            let v     = ProcessInfo.processInfo.operatingSystemVersion
            let major = v.majorVersion
            let macOSResult: (String, Check.Status) = major >= 13
                ? ("macOS \(major).\(v.minorVersion)", .ok)
                : ("macOS \(major) — requires 13 or later", .failed)

            // Architecture
            #if arch(arm64)
            let archResult = ("Apple Silicon (M-series)", Check.Status.ok)
            #else
            let archResult = ("Intel Mac", Check.Status.ok)
            #endif

            // Bundled Python
            let pyPath = Bundle.main.resourceURL?
                .appendingPathComponent("python/bin/python3").path ?? ""
            let pyResult: (String, Check.Status) =
                FileManager.default.isExecutableFile(atPath: pyPath)
                ? ("Python 3.12 (bundled)", .ok)
                : ("Bundled Python not found — please reinstall SVRN", .failed)

            // Ollama
            let ollama = findOllama()
            let ollamaResult: (String, Check.Status) = ollama != nil
                ? ("Ollama found", .ok)
                : ("Ollama not installed (optional)", .warning)

            await MainActor.run { [weak self] in
                guard let self else { return }
                self.checks[0].label  = macOSResult.0;  self.checks[0].status  = macOSResult.1
                self.checks[1].label  = archResult.0;   self.checks[1].status  = archResult.1
                self.checks[2].label  = pyResult.0;     self.checks[2].status  = pyResult.1
                self.checks[3].label  = ollamaResult.0; self.checks[3].status  = ollamaResult.1
                self.ollamaFound      = ollama != nil
            }
        }
    }

    var checksAllDone: Bool {
        !checks.isEmpty && checks.allSatisfy { $0.status != .pending }
    }

    var checksFailed: Bool {
        checks.contains { $0.status == .failed }
    }

    // ── Step 2: storage path ──────────────────────────────────────────────────

    func refreshStorageInfo() {
        storageError   = nil
        freeSpaceLabel = nil

        let expanded = storagePath.hasPrefix("~")
            ? NSHomeDirectory() + storagePath.dropFirst()
            : storagePath

        guard !expanded.isEmpty else { return }

        let url    = URL(fileURLWithPath: expanded)
        let parent = url.deletingLastPathComponent()

        guard FileManager.default.fileExists(atPath: parent.path) else {
            storageError = "Parent directory \"\(parent.lastPathComponent)\" does not exist"
            return
        }

        if let attrs = try? FileManager.default.attributesOfFileSystem(forPath: parent.path),
           let free  = attrs[.systemFreeSize] as? Int64 {
            let gb = Double(free) / 1e9
            if gb < 0.5 {
                storageError = String(format: "Only %.1f GB free — 1 GB minimum recommended", gb)
            } else {
                freeSpaceLabel = String(format: "%.1f GB available", gb)
            }
        }
    }

    func browseForFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles        = false
        panel.canChooseDirectories  = true
        panel.canCreateDirectories  = true
        panel.prompt                = "Choose"
        panel.message               = "Choose a folder to store SVRN libraries, maps, and notes"
        if panel.runModal() == .OK, let url = panel.url {
            storagePath = url.path
            refreshStorageInfo()
        }
    }

    // ── Step 3: write config and create directories ───────────────────────────

    func runSetup() {
        isWorking  = true
        setupError = nil
        step       = 3

        let raw = storagePath.hasPrefix("~")
            ? NSHomeDirectory() + storagePath.dropFirst()
            : storagePath

        Task.detached(priority: .userInitiated) {
            do {
                let url = URL(fileURLWithPath: raw)
                let fm  = FileManager.default
                for sub in ["zims", "maps", "notes", "chat"] {
                    try fm.createDirectory(
                        at: url.appendingPathComponent(sub),
                        withIntermediateDirectories: true
                    )
                }
                Config.setStorageRoot(url)

                await MainActor.run { [weak self] in
                    guard let self else { return }
                    self.isWorking = false
                    self.step      = 4
                }
            } catch {
                await MainActor.run { [weak self] in
                    guard let self else { return }
                    self.isWorking  = false
                    self.setupError = error.localizedDescription
                }
            }
        }
    }
}

// ── Root view ─────────────────────────────────────────────────────────────────

struct SetupWizard: View {
    @StateObject var vm: SetupViewModel

    var body: some View {
        VStack(spacing: 0) {
            switch vm.step {
            case 0:  WelcomeStep(vm: vm)
            case 1:  ChecksStep(vm: vm)
            case 2:  StorageStep(vm: vm)
            case 3:  WorkingStep(vm: vm)
            default: DoneStep(vm: vm)
            }
        }
        .frame(width: 520, height: 420)
    }
}

// ── Step views ────────────────────────────────────────────────────────────────

private struct WelcomeStep: View {
    @ObservedObject var vm: SetupViewModel

    var body: some View {
        VStack(spacing: 20) {
            Spacer()
            Text("◉")
                .font(.system(size: 72, weight: .thin))
                .foregroundStyle(.primary)
            Text("Welcome to SVRN")
                .font(.largeTitle.weight(.semibold))
            Text("Offline Knowledge & AI for Mac")
                .font(.title3)
                .foregroundStyle(.secondary)
            Text("This setup takes about 30 seconds and only runs once.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Spacer()
            Button("Get Started") {
                vm.step = 1
                vm.runChecks()
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .keyboardShortcut(.defaultAction)
            Spacer().frame(height: 16)
        }
        .padding(48)
    }
}

private struct ChecksStep: View {
    @ObservedObject var vm: SetupViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Checking your system")
                .font(.title2.weight(.semibold))
                .padding(.bottom, 28)

            VStack(alignment: .leading, spacing: 16) {
                ForEach(vm.checks) { check in
                    HStack(spacing: 14) {
                        statusIcon(check.status)
                            .frame(width: 18, height: 18)
                        Text(check.label)
                            .font(.body)
                        Spacer()
                    }
                }
            }

            Spacer()

            if vm.checksFailed {
                Text("One or more required checks failed. Please reinstall SVRN.")
                    .font(.callout)
                    .foregroundStyle(.red)
                    .padding(.bottom, 8)
            }

            HStack {
                Spacer()
                if vm.checksAllDone && !vm.checksFailed {
                    Button("Continue") {
                        vm.step = 2
                        vm.refreshStorageInfo()
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(48)
    }

    @ViewBuilder
    private func statusIcon(_ status: SetupViewModel.Check.Status) -> some View {
        switch status {
        case .pending:
            ProgressView().controlSize(.small)
        case .ok:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .warning:
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.yellow)
        case .failed:
            Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
        }
    }
}

private struct StorageStep: View {
    @ObservedObject var vm: SetupViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Choose a storage location")
                .font(.title2.weight(.semibold))
                .padding(.bottom, 8)

            Text("SVRN stores your offline libraries, maps, notes,\nand chat history here.")
                .font(.body)
                .foregroundStyle(.secondary)
                .padding(.bottom, 28)

            HStack(spacing: 8) {
                TextField("Path", text: $vm.storagePath)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: vm.storagePath) { _ in vm.refreshStorageInfo() }
                Button("Browse…") { vm.browseForFolder() }
            }

            Group {
                if let err = vm.storageError {
                    Label(err, systemImage: "exclamationmark.circle")
                        .foregroundStyle(.red)
                } else if let label = vm.freeSpaceLabel {
                    Label(label, systemImage: "internaldrive")
                        .foregroundStyle(.secondary)
                }
            }
            .font(.callout)
            .padding(.top, 6)

            if !vm.ollamaFound {
                Divider().padding(.vertical, 20)
                HStack(alignment: .top, spacing: 14) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.yellow)
                        .padding(.top, 2)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Ollama not installed")
                            .font(.callout.weight(.medium))
                        Text("AI chat requires Ollama. SVRN works fully without it — you can add it later.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Get Ollama") {
                        NSWorkspace.shared.open(URL(string: "https://ollama.ai")!)
                    }
                    .controlSize(.small)
                }
            }

            Spacer()

            HStack {
                Button("Back") { vm.step = 1 }
                    .controlSize(.large)
                Spacer()
                Button("Set Up SVRN") { vm.runSetup() }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .keyboardShortcut(.defaultAction)
                    .disabled(vm.storageError != nil || vm.storagePath.isEmpty)
            }
        }
        .padding(48)
    }
}

private struct WorkingStep: View {
    @ObservedObject var vm: SetupViewModel

    var body: some View {
        VStack(spacing: 24) {
            Spacer()
            if vm.isWorking {
                ProgressView().controlSize(.large)
                Text("Setting up SVRN…")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            } else if let err = vm.setupError {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 56))
                    .foregroundStyle(.red)
                Text("Setup failed")
                    .font(.title2.weight(.semibold))
                Text(err)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                Button("Try Again") { vm.step = 2 }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
            }
            Spacer()
        }
        .padding(48)
    }
}

private struct DoneStep: View {
    @ObservedObject var vm: SetupViewModel

    var body: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 64))
                .foregroundStyle(.green)
            Text("SVRN is ready")
                .font(.largeTitle.weight(.semibold))
            Text("Your dashboard is opening in your browser.\nThe menu bar icon gives you quick access anytime.")
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Spacer()
            Button("Open Dashboard") { vm.onComplete() }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)
            Spacer().frame(height: 16)
        }
        .padding(48)
    }
}
