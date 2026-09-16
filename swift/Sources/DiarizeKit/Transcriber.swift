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
        // that we can sample whenever the callback fires. WhisperKit only replaces
        // `wk.progress` with a fresh Progress object *after* a transcribe() call
        // finishes (to reset for the next one) - never mid-call - so capturing the
        // object once here, before this call starts, still reflects it live on every
        // tick; it just can't be `wk` itself; `WhisperKit` isn't Sendable, and `wk` is
        // actor-isolated state, so the @Sendable callback below can't capture it
        // directly. ProgressBox exists only to tell the compiler that reading this
        // particular Progress reference from another thread is fine (mirroring
        // LastReportedFraction below) - it doesn't add any synchronization itself,
        // since Progress's own property reads are already safe to do cross-thread.
        let progressBox = ProgressBox(wk.progress)
        let lastReported = LastReportedFraction()
        let results: [TranscriptionResult] = try await wk.transcribe(
            audioPath: audioURL.path,
            callback: { _ in
                let fraction = progressBox.fractionCompleted
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

/// Holds a WhisperKit `Progress` reference for reading from inside a `@Sendable`
/// callback. `Progress` isn't annotated `Sendable`, but reading its properties from
/// any thread is safe by design (it's a KVO-observable, cross-thread-safe Foundation
/// type) - this box only tells the compiler that, it adds no locking of its own.
private final class ProgressBox: @unchecked Sendable {
    private let progress: Progress
    init(_ progress: Progress) { self.progress = progress }
    var fractionCompleted: Double { progress.fractionCompleted }
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
