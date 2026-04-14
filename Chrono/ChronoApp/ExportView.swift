import SwiftUI
import UIKit

/// Wraps UIActivityViewController so the user can share or save the exported
/// folder (and its manifest.toml sidecar) via AirDrop, Files, or any other
/// system share extension.
struct ExportView: UIViewControllerRepresentable {

    var exportURL: URL

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let vc = UIActivityViewController(
            activityItems: [exportURL],
            applicationActivities: nil)
        // Exclude activities that don't make sense for a folder
        vc.excludedActivityTypes = [
            .assignToContact,
            .addToReadingList,
            .openInIBooks,
            .postToFlickr,
            .postToVimeo,
        ]
        return vc
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController,
                                 context: Context) {}
}
