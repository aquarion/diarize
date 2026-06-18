import DiarizeKit
import SwiftUI

struct SpeakerLabelView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Name the speakers").font(.title2).bold()
            Text("Edit display names below. Press Continue when done.")
                .foregroundColor(.secondary)

            ScrollView {
                VStack(spacing: 12) {
                    ForEach(state.detectedSpeakers, id: \.self) { label in
                        SpeakerCard(
                            label: label,
                            name: nameBinding(for: label),
                            samples: samples(for: label)
                        )
                    }
                }
            }

            HStack {
                Spacer()
                Button("Continue") {
                    state.finishLabeling()
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
    }

    private func nameBinding(for label: String) -> Binding<String> {
        Binding(
            get: { state.speakerNames[label] ?? label },
            set: { state.speakerNames[label] = $0 }
        )
    }

    private func samples(for label: String) -> [(id: Int, text: String)] {
        guard let result = state.pipelineResult else { return [] }
        let segs = result.segments.filter {
            $0.speaker == label && !$0.text.trimmingCharacters(in: .whitespaces).isEmpty
        }
        guard !segs.isEmpty else { return [] }
        let n = segs.count
        let indices = Array(Set([n / 6, n / 2, (5 * n) / 6])).sorted()
        return indices.map { i in
            (id: i, text: "\(Renderer.formatTimestamp(segs[i].start)) \(segs[i].text.trimmingCharacters(in: .whitespaces))")
        }
    }
}

struct SpeakerCard: View {
    let label: String
    @Binding var name: String
    let samples: [(id: Int, text: String)]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label).font(.caption).foregroundColor(.secondary)
                Spacer()
                TextField("Display name", text: $name)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 200)
            }
            ForEach(samples, id: \.id) { s in
                Text(s.text)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }
        }
        .padding(10)
        .background(Color(NSColor.controlBackgroundColor))
        .cornerRadius(8)
    }
}
