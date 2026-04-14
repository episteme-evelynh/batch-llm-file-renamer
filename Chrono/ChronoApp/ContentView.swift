import SwiftUI
import UniformTypeIdentifiers
import os.log

private let logger = Logger(subsystem: "com.chrono.app", category: "ContentView")

@MainActor
final class ExportViewModel: ObservableObject {
    // ── Published state ────────────────────────────────────────────────────
    @Published var selectedFolderURL: URL?
    @Published var selectedFolderName: String = "No folder selected"
    @Published var isPickerPresented:  Bool   = false
    @Published var isExporting:        Bool   = false
    @Published var progress:           Double = 0.0
    @Published var statusMessage:      String = ""
    @Published var errorMessage:       String? = nil
    @Published var exportedFolderURL:  URL?
    @Published var isShareSheetPresented: Bool = false

    // ── Persistence key ────────────────────────────────────────────────────
    private let kBookmarkKey = "com.chrono.sourceFolderBookmark"

    // ── On-init: restore bookmark ──────────────────────────────────────────
    init() {
        restoreBookmarkIfAvailable()
    }

    // ── Folder selection ───────────────────────────────────────────────────

    func folderSelected(_ url: URL) {
        selectedFolderURL  = url
        selectedFolderName = url.lastPathComponent

        // Persist as security-scoped bookmark
        do {
            let bookmarkData = try url.bookmarkData(
                options: .withSecurityScope,
                includingResourceValuesForKeys: nil,
                relativeTo: nil)
            UserDefaults.standard.set(bookmarkData, forKey: kBookmarkKey)
        } catch {
            logger.warning("Could not create security-scoped bookmark: \(error)")
        }
    }

    func restoreBookmarkIfAvailable() {
        guard let data = UserDefaults.standard.data(forKey: kBookmarkKey) else { return }
        do {
            var isStale = false
            let url = try URL(
                resolvingBookmarkData: data,
                options: .withSecurityScope,
                relativeTo: nil,
                bookmarkDataIsStale: &isStale)

            if isStale {
                // Re-create the bookmark from the resolved URL
                let fresh = try url.bookmarkData(
                    options: .withSecurityScope,
                    includingResourceValuesForKeys: nil,
                    relativeTo: nil)
                UserDefaults.standard.set(fresh, forKey: kBookmarkKey)
            }

            selectedFolderURL  = url
            selectedFolderName = url.lastPathComponent
        } catch {
            logger.warning("Could not resolve bookmark: \(error)")
        }
    }

    // ── Export workflow ────────────────────────────────────────────────────

    func startExport() {
        guard let sourceURL = selectedFolderURL else { return }

        isExporting   = true
        progress      = 0.0
        errorMessage  = nil
        statusMessage = "Starting…"

        Task.detached(priority: .userInitiated) { [weak self] in
            guard let self else { return }
            await self.runExport(sourceURL: sourceURL)
        }
    }

    private func runExport(sourceURL: URL) async {
        // Activate security scope
        let scopeActive = sourceURL.startAccessingSecurityScopedResource()
        defer {
            if scopeActive { sourceURL.stopAccessingSecurityScopedResource() }
        }

        let engine = CHChronoEngine()

        // ── Enumerate ──────────────────────────────────────────────────────
        await update(status: "Scanning folder…", progress: 0.05)
        var enumerateError: NSError?
        guard let records = engine.enumerateFolder(sourceURL, error: &enumerateError) else {
            await fail(enumerateError?.localizedDescription ?? "Enumeration failed")
            return
        }

        if records.isEmpty {
            await fail("The selected folder contains no files.")
            return
        }

        // ── Prepare output folder ──────────────────────────────────────────
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let tag = formatter.string(from: Date())
            .replacingOccurrences(of: ":", with: "-")
            .replacingOccurrences(of: "+", with: "Z")
        let docsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let outputRoot = docsURL.appendingPathComponent("Chrono-Export-\(tag)", isDirectory: true)

        do {
            try FileManager.default.createDirectory(at: outputRoot, withIntermediateDirectories: true)
        } catch {
            await fail("Could not create output folder: \(error.localizedDescription)")
            return
        }

        // ── Duplicate files ────────────────────────────────────────────────
        await update(status: "Duplicating \(records.count) files…", progress: 0.1)
        let total  = Double(records.count)
        var failed = 0

        for (i, record) in records.enumerated() {
            let rel    = record.relativePath
            let srcURL = sourceURL.appendingPathComponent(rel)
            let dstURL = outputRoot.appendingPathComponent(rel)

            // Ensure intermediate directories exist in output
            let dstDir = dstURL.deletingLastPathComponent()
            try? FileManager.default.createDirectory(at: dstDir, withIntermediateDirectories: true)

            var dupError: NSError?
            let ok = engine.duplicateFile(at: srcURL,
                                          to: dstURL,
                                          applyTimestamps: record,
                                          error: &dupError)
            if !ok {
                logger.error("Duplicate failed for \(rel): \(dupError?.localizedDescription ?? "")")
                failed += 1
            }

            let pct = 0.1 + (Double(i + 1) / total) * 0.8
            await update(status: "Copying \(i+1)/\(records.count)…", progress: pct)
        }

        // ── Emit manifest ──────────────────────────────────────────────────
        await update(status: "Writing manifest…", progress: 0.92)
        var manifestError: NSError?
        let deviceName = await UIDevice.current.name
        guard let tomlString = engine.emitManifest(fromRecords: records,
                                                    sourceFolderPath: sourceURL.path,
                                                    deviceName: deviceName,
                                                    error: &manifestError) else {
            await fail(manifestError?.localizedDescription ?? "Manifest emission failed")
            return
        }

        let manifestURL = outputRoot.appendingPathComponent("manifest.toml")
        do {
            try tomlString.write(to: manifestURL, atomically: true, encoding: .utf8)
        } catch {
            await fail("Could not write manifest: \(error.localizedDescription)")
            return
        }

        // ── Finish ─────────────────────────────────────────────────────────
        let msg = failed == 0
            ? "Export complete: \(records.count) files."
            : "Export done with \(failed) error(s). See console for details."
        await MainActor.run {
            self.isExporting          = false
            self.progress             = 1.0
            self.statusMessage        = msg
            self.exportedFolderURL    = outputRoot
            self.isShareSheetPresented = true
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────

    private func update(status: String, progress: Double) async {
        await MainActor.run {
            self.statusMessage = status
            self.progress      = progress
        }
    }

    private func fail(_ message: String) async {
        await MainActor.run {
            self.isExporting   = false
            self.errorMessage  = message
            self.statusMessage = "Export failed."
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════ //

struct ContentView: View {
    @StateObject private var vm = ExportViewModel()

    var body: some View {
        NavigationStack {
            Form {
                // ── Source folder section ──────────────────────────────────
                Section("Source Folder") {
                    HStack {
                        Image(systemName: "folder")
                            .foregroundStyle(.tint)
                        Text(vm.selectedFolderName)
                            .foregroundStyle(vm.selectedFolderURL == nil
                                ? .secondary : .primary)
                        Spacer()
                    }

                    Button("Choose Folder…") {
                        vm.isPickerPresented = true
                    }
                }

                // ── Export section ─────────────────────────────────────────
                Section("Export") {
                    if vm.isExporting {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(vm.statusMessage)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                            ProgressView(value: vm.progress)
                        }
                        .padding(.vertical, 4)
                    } else {
                        Button {
                            vm.startExport()
                        } label: {
                            Label("Duplicate & Export", systemImage: "arrow.up.doc.on.clipboard")
                        }
                        .disabled(vm.selectedFolderURL == nil)

                        if !vm.statusMessage.isEmpty {
                            Text(vm.statusMessage)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("Chrono")
            .sheet(isPresented: $vm.isPickerPresented) {
                FolderPickerView { url in
                    vm.folderSelected(url)
                }
            }
            .sheet(isPresented: $vm.isShareSheetPresented) {
                if let url = vm.exportedFolderURL {
                    ExportView(exportURL: url)
                }
            }
            .alert("Export Error", isPresented: .init(
                get: { vm.errorMessage != nil },
                set: { if !$0 { vm.errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) { vm.errorMessage = nil }
            } message: {
                Text(vm.errorMessage ?? "")
            }
        }
    }
}
