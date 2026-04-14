import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// A UIViewControllerRepresentable that presents a UIDocumentPickerViewController
/// configured to select a single folder. The selected URL is handed back via the
/// `onSelection` closure. Security-scoped resource access is started inside the
/// coordinator; the caller (ExportViewModel.folderSelected) creates the bookmark.
struct FolderPickerView: UIViewControllerRepresentable {

    var onSelection: (URL) -> Void

    // ── UIViewControllerRepresentable ──────────────────────────────────────

    func makeCoordinator() -> Coordinator {
        Coordinator(onSelection: onSelection)
    }

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(
            forOpeningContentTypes: [UTType.folder],
            asCopy: false)
        picker.allowsMultipleSelection = false
        picker.shouldShowFileExtensions = true
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIDocumentPickerViewController,
                                 context: Context) {}

    // ── Coordinator ────────────────────────────────────────────────────────

    final class Coordinator: NSObject, UIDocumentPickerDelegate {

        private let onSelection: (URL) -> Void

        init(onSelection: @escaping (URL) -> Void) {
            self.onSelection = onSelection
        }

        func documentPicker(_ controller: UIDocumentPickerViewController,
                            didPickDocumentsAt urls: [URL]) {
            guard let url = urls.first else { return }

            // Activate the security scope so the rest of the session
            // (including bookmark creation) has access.
            _ = url.startAccessingSecurityScopedResource()

            onSelection(url)
        }

        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
            // Nothing to do
        }
    }
}
