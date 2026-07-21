// swift/Sources/DiarizeCLI/DiarizeCommand.swift
import ArgumentParser
import Darwin
import DiarizeKit
import Foundation

@main
struct DiarizeCommand: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "diarize",
        abstract: "Transcribe and diarize audio/video files, export to Obsidian.",
        subcommands: [Transcribe.self, ConfigCommand.self],
        defaultSubcommand: Transcribe.self
    )
}

struct Transcribe: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "transcribe",
        abstract: "Transcribe and diarize an audio or video file (default)."
    )

    @Argument(help: "Path to the audio or video file") var wav: String
    @Argument(help: "Number of speakers in the recording") var numSpeakers: Int

    @Flag(name: .long, help: "Ask Claude to guess speaker names") var claudeGuess = false
    @Flag(name: [.customShort("y"), .long], help: "Non-interactive: accept all defaults") var yes = false
    @Option(name: .long, help: "Override vault output path for this file") var vaultOutput: String?
    @Option(name: .long, help: "Path to config JSON file") var config: String?

    mutating func run() async throws {
        // Line-buffer stdout instead of the libc default of full block
        // buffering when not a tty - otherwise the MCP server (which pipes
        // this process's stdout to read "==> ..." progress lines live)
        // wouldn't see any output until the whole run finishes.
        setvbuf(stdout, nil, _IOLBF, 0)

        let configURL = config.map { URL(fileURLWithPath: $0) } ?? ConfigLoader.configURL
        var (cfg, raw) = try ConfigLoader.load(from: configURL)

        if !yes && cfg.vaultPath == "~/Obsidian" {
            print("\n==> Config: vault_path (Obsidian vault root)")
            print("  Path [~/Obsidian]: ", terminator: "")
            let input = readLine()?.trimmingCharacters(in: .whitespaces) ?? ""
            if !input.isEmpty { cfg.vaultPath = input }
        }
        ConfigLoader.update(cfg, in: &raw)
        try ConfigLoader.save(raw, to: configURL)

        let audioURL = URL(fileURLWithPath: (wav as NSString).expandingTildeInPath)
        guard FileManager.default.fileExists(atPath: audioURL.path) else {
            fputs("!! Input file not found: \(audioURL.path)\n", stderr)
            throw ExitCode(2)
        }

        // Load models before pipeline so we can report progress accurately
        let transcriber = WhisperKitTranscriber()
        print("==> Loading WhisperKit model: \(cfg.whisperkitModel)")
        try await transcriber.loadModel(cfg.whisperkitModel)

        let diarizer = SpeakerKitDiarizer()
        print("==> Loading SpeakerKit model")
        try await diarizer.loadModel()

        let pipeline = Pipeline(transcriber: transcriber, diarizer: diarizer)
        let (progressStream, continuation) = AsyncStream<PipelineProgress>.makeStream()

        async let result: PipelineResult = pipeline.run(
            audioURL: audioURL, numSpeakers: numSpeakers,
            config: cfg, progress: continuation
        )
        for await p in progressStream {
            print("==> \(p.message)")
        }
        let pipelineResult = try await result

        // Speaker labeling
        let mappingURL = pipelineResult.outputDirectoryURL.appendingPathComponent("speakers.json")
        var mapping = SpeakerMapper.loadMapping(from: mappingURL)
        let detected = Array(Set(pipelineResult.segments.map(\.speaker))).sorted()

        if claudeGuess && !cfg.anthropicAPIKey.isEmpty {
            print("==> Asking Claude to guess speaker names...")
            let attrs = try? FileManager.default.attributesOfItem(atPath: audioURL.path)
            let date = (attrs?[.creationDate] as? Date) ?? Date()
            if let guesses = try? await ClaudeGuesser.guess(
                detectedLabels: detected, segments: pipelineResult.segments,
                recordingDate: date, apiKey: cfg.anthropicAPIKey
            ) {
                for (k, v) in guesses where mapping[k] == nil { mapping[k] = v }
                print("    Guesses: \(guesses)")
            }
        }

        let finalMapping: [String: String]
        if yes {
            for label in detected where mapping[label] == nil { mapping[label] = label }
            finalMapping = mapping
        } else {
            finalMapping = InteractiveLabeler.label(
                detected: detected, existing: mapping, segments: pipelineResult.segments
            )
        }
        try SpeakerMapper.saveMapping(finalMapping, to: mappingURL)

        let blocks = SpeakerMapper.coalesce(segments: pipelineResult.segments, mapping: finalMapping)
        let stem = audioURL.deletingPathExtension().lastPathComponent
        let dirName = pipelineResult.outputDirectoryURL.lastPathComponent
        let dateStr = String(dirName.dropFirst(stem.count + 1))
        let vaultTarget = vaultOutput.map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) }
            ?? VaultExporter.makeVaultTarget(config: cfg, audioStem: "\(dateStr)_\(stem)")

        try VaultExporter.writeOutputs(
            segments: pipelineResult.segments, blocks: blocks, config: cfg,
            audioURL: audioURL, outputDir: pipelineResult.outputDirectoryURL, vaultURL: vaultTarget
        )

        print("\n==> Complete")
        print("    speaker map : \(mappingURL.path)")
        print("    local       : \(pipelineResult.outputDirectoryURL.appendingPathComponent("transcript.md").path)")
        print("    vault       : \(vaultTarget.path)")
    }
}
