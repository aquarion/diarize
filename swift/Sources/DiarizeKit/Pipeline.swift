// swift/Sources/DiarizeKit/Pipeline.swift
import Foundation

public struct Pipeline {
    private let transcriber: any TranscriberProtocol
    private let diarizer: any DiarizerProtocol

    public init(transcriber: any TranscriberProtocol, diarizer: any DiarizerProtocol) {
        self.transcriber = transcriber
        self.diarizer = diarizer
    }

    public func run(
        audioURL: URL,
        numSpeakers: Int,
        config: AppConfig,
        progress: AsyncStream<PipelineProgress>.Continuation
    ) async throws -> PipelineResult {
        guard FileManager.default.fileExists(atPath: audioURL.path) else {
            throw DiarizeError.audioFileNotFound(audioURL)
        }

        let outputDir = try VaultExporter.makeOutputDirectory(for: audioURL, config: config)
        let stem = audioURL.deletingPathExtension().lastPathComponent
        let txCheckpoint = outputDir.appendingPathComponent("\(stem)_transcription.json")
        let diarCheckpoint = outputDir.appendingPathComponent("\(stem)_diarization.json")

        // --- Transcription ---
        let segments: [Segment]
        if FileManager.default.fileExists(atPath: txCheckpoint.path) {
            progress.yield(.init(stage: .transcribing, fraction: 1.0, message: "Resuming transcription checkpoint"))
            segments = try JSONDecoder().decode([Segment].self, from: Data(contentsOf: txCheckpoint))
        } else {
            progress.yield(.init(stage: .transcribing, fraction: 0.0, message: "Transcribing audio..."))
            let raw = try await transcriber.transcribe(audioURL: audioURL)
            try JSONEncoder().encode(raw).write(to: txCheckpoint)
            segments = raw
            progress.yield(.init(stage: .transcribing, fraction: 1.0, message: "Transcription complete"))
        }

        // --- Diarization ---
        let turns: [Turn]
        if FileManager.default.fileExists(atPath: diarCheckpoint.path) {
            progress.yield(.init(stage: .diarizing, fraction: 1.0, message: "Resuming diarization checkpoint"))
            turns = try JSONDecoder().decode([Turn].self, from: Data(contentsOf: diarCheckpoint))
        } else {
            progress.yield(.init(stage: .diarizing, fraction: 0.0, message: "Diarizing speakers..."))
            let raw = try await diarizer.diarize(audioURL: audioURL, numSpeakers: numSpeakers)
            try JSONEncoder().encode(raw).write(to: diarCheckpoint)
            turns = raw
            progress.yield(.init(stage: .diarizing, fraction: 1.0, message: "Diarization complete"))
        }

        // --- Assign & persist ---
        let labeled = SpeakerMapper.assignSpeakers(to: segments, from: turns)
        let jsonData = try JSONEncoder().encode(["segments": labeled])
        try jsonData.write(to: outputDir.appendingPathComponent("\(stem).json"))

        progress.yield(.init(stage: .complete, fraction: 1.0, message: "Pipeline complete"))
        progress.finish()

        return PipelineResult(segments: labeled, outputDirectoryURL: outputDir)
    }
}

extension Pipeline {
    public static func makeDefault(config: AppConfig) -> Pipeline {
        Pipeline(
            transcriber: WhisperKitTranscriber(),
            diarizer: SpeakerKitDiarizer()
        )
    }
}
