import SwiftUI
import UniformTypeIdentifiers

struct DropView: View {
    @EnvironmentObject var state: AppState
    @State private var numSpeakers: Int = 2
    @State private var isTargeted = false
    @State private var droppedURL: URL?

    var body: some View {
        VStack(spacing: 24) {
            Text("Diarize").font(.largeTitle).bold()

            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(
                        isTargeted ? Color.accentColor : Color.secondary.opacity(0.4),
                        style: StrokeStyle(lineWidth: 2, dash: [8])
                    )
                    .frame(height: 160)
                VStack(spacing: 8) {
                    Image(systemName: "waveform")
                        .font(.system(size: 40))
                        .foregroundColor(.secondary)
                    if let url = droppedURL {
                        Text(url.lastPathComponent).font(.headline)
                    } else {
                        Text("Drop a WAV file here").foregroundColor(.secondary)
                    }
                }
            }
            .onDrop(of: [.audio, .fileURL], isTargeted: $isTargeted) { providers in
                providers.first?.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
                    if let data = item as? Data,
                       let url = URL(dataRepresentation: data, relativeTo: nil) {
                        DispatchQueue.main.async { droppedURL = url }
                    }
                }
                return true
            }

            HStack {
                Text("Speakers:")
                Stepper("\(numSpeakers)", value: $numSpeakers, in: 1...10)
            }

            Toggle("Ask Claude to guess speaker names", isOn: $state.claudeGuess)
                .disabled(state.config.anthropicAPIKey.isEmpty)

            Button("Transcribe & Diarize") {
                guard let url = droppedURL else { return }
                state.startPipeline(audioURL: url, numSpeakers: numSpeakers)
            }
            .buttonStyle(.borderedProminent)
            .disabled(droppedURL == nil)

            Button("Settings") { state.screen = .settings }
                .buttonStyle(.borderless)
                .foregroundColor(.secondary)
        }
        .padding(32)
    }
}
