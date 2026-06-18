import SwiftUI

struct ProcessingView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 20) {
            ProgressView(value: state.progressFraction)
                .progressViewStyle(.linear)
                .frame(maxWidth: 400)
            Text(state.progressMessage)
                .foregroundColor(.secondary)
        }
        .padding(40)
    }
}
