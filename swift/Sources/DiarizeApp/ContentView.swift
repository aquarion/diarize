import SwiftUI

struct ContentView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Group {
            switch state.screen {
            case .drop:       DropView()
            case .processing: ProcessingView()
            case .labeling:   SpeakerLabelView()
            case .result:     ResultView()
            case .settings:   SettingsView()
            }
        }
        .frame(minWidth: 500, minHeight: 400)
        .alert("Error", isPresented: .constant(state.errorMessage != nil)) {
            Button("OK") {
                state.errorMessage = nil
                state.screen = .drop
            }
        } message: {
            Text(state.errorMessage ?? "")
        }
    }
}
