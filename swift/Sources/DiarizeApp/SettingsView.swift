import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var state: AppState

    // Local copy so changes are only committed on Save
    @State private var whisperkitModel: String = ""
    @State private var language: String = ""
    @State private var vaultPath: String = ""
    @State private var vaultSubdir: String = ""
    @State private var vaultFilenameTemplate: String = ""
    @State private var anthropicAPIKey: String = ""
    @State private var outputDir: String = ""
    @State private var transcriptTitle: String = ""

    var body: some View {
        Form {
            Section("Transcription") {
                TextField("WhisperKit model", text: $whisperkitModel)
                TextField("Language", text: $language)
            }
            Section("Obsidian") {
                TextField("Vault path", text: $vaultPath)
                TextField("Subdirectory", text: $vaultSubdir)
                TextField("Filename template", text: $vaultFilenameTemplate)
            }
            Section("Claude name-guessing") {
                SecureField("Anthropic API key", text: $anthropicAPIKey)
            }
            Section("Output") {
                TextField("Output directory", text: $outputDir)
                TextField("Transcript title", text: $transcriptTitle)
            }
        }
        .formStyle(.grouped)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") {
                    state.config.whisperkitModel = whisperkitModel
                    state.config.language = language
                    state.config.vaultPath = vaultPath
                    state.config.vaultSubdir = vaultSubdir
                    state.config.vaultFilenameTemplate = vaultFilenameTemplate
                    state.config.anthropicAPIKey = anthropicAPIKey
                    state.config.outputDir = outputDir
                    state.config.transcriptTitle = transcriptTitle
                    state.saveConfig()
                    state.screen = .drop
                }
            }
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancel") { state.screen = .drop }
            }
        }
        .navigationTitle("Settings")
        .frame(minWidth: 480, minHeight: 420)
        .onAppear {
            whisperkitModel = state.config.whisperkitModel
            language = state.config.language
            vaultPath = state.config.vaultPath
            vaultSubdir = state.config.vaultSubdir
            vaultFilenameTemplate = state.config.vaultFilenameTemplate
            anthropicAPIKey = state.config.anthropicAPIKey
            outputDir = state.config.outputDir
            transcriptTitle = state.config.transcriptTitle
        }
    }
}
