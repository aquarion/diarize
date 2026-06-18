import SwiftUI

struct ResultView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 56))
                .foregroundColor(.green)
            Text("Complete").font(.title).bold()

            if let result = state.pipelineResult {
                let md = result.outputDirectoryURL.appendingPathComponent("transcript.md")
                Label(md.path, systemImage: "doc.text")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Button("Show in Finder") {
                    NSWorkspace.shared.selectFile(md.path, inFileViewerRootedAtPath: "")
                }
            }

            if let vault = state.vaultURL {
                Label(vault.path, systemImage: "book.closed")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Button("Open in Obsidian") {
                    let encoded = vault.path.addingPercentEncoding(
                        withAllowedCharacters: .urlPathAllowed) ?? ""
                    if let url = URL(string: "obsidian://open?path=\(encoded)") {
                        NSWorkspace.shared.open(url)
                    }
                }
            }

            Button("Process another file") { state.screen = .drop }
                .buttonStyle(.borderless)
        }
        .padding(40)
    }
}
