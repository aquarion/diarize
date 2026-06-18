import SwiftUI

@main
struct DiarizeAppApp: App {
    @StateObject private var state = AppState()
    var body: some Scene {
        WindowGroup {
            Text("Loading…")
                .environmentObject(state)
                .onAppear { state.loadConfig() }
        }
    }
}
