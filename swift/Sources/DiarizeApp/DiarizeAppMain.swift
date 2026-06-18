import SwiftUI

@main
struct DiarizeAppApp: App {
    @StateObject private var state = AppState()
    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(state)
                .onAppear { state.loadConfig() }
        }
    }
}
