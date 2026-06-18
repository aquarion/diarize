import DiarizeKit
import Foundation
import SwiftUI

@MainActor
public final class AppState: ObservableObject {
    public enum Screen { case drop, processing, labeling, result, settings }

    @Published public var screen: Screen = .drop
    @Published public var progressFraction: Double = 0
    @Published public var progressMessage: String = ""
    @Published public var detectedSpeakers: [String] = []
    @Published public var speakerNames: [String: String] = [:]
    @Published public var pipelineResult: PipelineResult?
    @Published public var audioDroppedURL: URL?
    @Published public var vaultURL: URL?
    @Published public var errorMessage: String?
    @Published public var claudeGuess: Bool = false

    public var config: AppConfig = AppConfig()
    public var rawConfig: [String: Any] = [:]

    public init() {}

    public func loadConfig() {
        if let (c, r) = try? ConfigLoader.load() { config = c; rawConfig = r }
    }

    public func saveConfig() {
        ConfigLoader.update(config, in: &rawConfig)
        try? ConfigLoader.save(rawConfig)
    }

    public func startPipeline(audioURL: URL, numSpeakers: Int) {
        audioDroppedURL = audioURL
        screen = .processing
        Task { @MainActor in
            do {
                let transcriber = WhisperKitTranscriber()
                try await transcriber.loadModel(config.whisperkitModel)
                let diarizer = SpeakerKitDiarizer()
                try await diarizer.loadModel()
                let pipeline = Pipeline(transcriber: transcriber, diarizer: diarizer)
                let (stream, cont) = AsyncStream<PipelineProgress>.makeStream()
                async let result = pipeline.run(
                    audioURL: audioURL, numSpeakers: numSpeakers,
                    config: config, progress: cont
                )
                for await prog in stream {
                    progressFraction = prog.fraction
                    progressMessage = prog.message
                }
                let r = try await result
                pipelineResult = r
                detectedSpeakers = Array(Set(r.segments.map(\.speaker))).sorted()
                speakerNames = SpeakerMapper.loadMapping(
                    from: r.outputDirectoryURL.appendingPathComponent("speakers.json"))
                if claudeGuess && !config.anthropicAPIKey.isEmpty {
                    let attrs = try? FileManager.default.attributesOfItem(atPath: audioURL.path)
                    let date = (attrs?[.creationDate] as? Date) ?? Date()
                    if let guesses = try? await ClaudeGuesser.guess(
                        detectedLabels: detectedSpeakers, segments: r.segments,
                        recordingDate: date, apiKey: config.anthropicAPIKey) {
                        for (k, v) in guesses where speakerNames[k] == nil { speakerNames[k] = v }
                    }
                }
                screen = .labeling
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    public func finishLabeling() {
        guard let audioURL = audioDroppedURL, let result = pipelineResult else { return }
        Task { @MainActor in
            do {
                let mappingURL = result.outputDirectoryURL.appendingPathComponent("speakers.json")
                try SpeakerMapper.saveMapping(speakerNames, to: mappingURL)
                let blocks = SpeakerMapper.coalesce(segments: result.segments, mapping: speakerNames)
                let stem = audioURL.deletingPathExtension().lastPathComponent
                let vault = VaultExporter.makeVaultTarget(config: config, audioStem: stem)
                try VaultExporter.writeOutputs(
                    segments: result.segments, blocks: blocks, config: config,
                    audioURL: audioURL, outputDir: result.outputDirectoryURL, vaultURL: vault)
                vaultURL = vault
                screen = .result
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
