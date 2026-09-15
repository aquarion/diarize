import Foundation
import WhisperKit

public actor WhisperKitTranscriber: TranscriberProtocol {
    private var whisperKit: WhisperKit?

    public init() {}

    public func loadModel(_ model: String) async throws {
        whisperKit = try await WhisperKit(model: model, downloadBase: ConfigLoader.modelCacheURL)
    }

    public func transcribe(audioURL: URL, onProgress: (@Sendable (Double) -> Void)?) async throws -> [Segment] {
        guard let wk = whisperKit else {
            throw DiarizeError.transcriptionFailed("Call loadModel() before transcribe()")
        }
        // WhisperKit doesn't hand back a fraction in its per-token callback, but it
        // maintains its own Foundation `Progress` (updated once per ~30s decode window)
        // that we can sample whenever the callback fires. Dedup identical reads so we
        // don't flood the pipeline's progress stream with hundreds of repeats between
        // window boundaries.
        let taskProgress = wk.progress
        let lastReported = LastReportedFraction()
        let results: [TranscriptionResult] = try await wk.transcribe(
            audioPath: audioURL.path,
            callback: { _ in
                let fraction = taskProgress.fractionCompleted
                if lastReported.update(to: fraction) {
                    onProgress?(fraction)
                }
                return true
            }
        )
        let rawSegs = results.flatMap { $0.segments }
        guard !rawSegs.isEmpty else {
            throw DiarizeError.noSegmentsProduced
        }
        return rawSegs.compactMap { seg -> Segment? in
            let text = seg.text.trimmingCharacters(in: .whitespaces)
            guard !text.isEmpty else { return nil }
            return Segment(start: Double(seg.start), end: Double(seg.end), text: text)
        }
    }
}

/// WhisperKit invokes its transcription callback from `Task.detached`, so callbacks
/// can race each other; this guards the last-seen fraction so we only forward changes.
private final class LastReportedFraction: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Double = -1

    func update(to newValue: Double) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard newValue != value else { return false }
        value = newValue
        return true
    }
}
