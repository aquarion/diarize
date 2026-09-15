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
        // that we can sample whenever the callback fires. Read `wk.progress` fresh on
        // each tick (rather than capturing it once up front) so this can't end up
        // reading a stale Progress object if a caller ever reuses a WhisperKitTranscriber
        // across more than one transcribe() call.
        let lastReported = LastReportedFraction()
        let results: [TranscriptionResult] = try await wk.transcribe(
            audioPath: audioURL.path,
            callback: { _ in
                let fraction = wk.progress.fractionCompleted
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
/// for different windows can run out of order. Only forwarding strictly increasing
/// fractions both dedups repeats within a window and stops an out-of-order callback
/// (e.g. one for an earlier window that got scheduled late) from reporting progress
/// moving backwards.
private final class LastReportedFraction: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Double = -1

    func update(to newValue: Double) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard newValue > value else { return false }
        value = newValue
        return true
    }
}
