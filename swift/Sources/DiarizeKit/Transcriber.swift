import Foundation
import WhisperKit

public actor WhisperKitTranscriber: TranscriberProtocol {
    private var whisperKit: WhisperKit?

    public init() {}

    public func loadModel(_ model: String) async throws {
        whisperKit = try await WhisperKit(model: model)
    }

    public func transcribe(audioURL: URL) async throws -> [Segment] {
        guard let wk = whisperKit else {
            throw DiarizeError.transcriptionFailed("Call loadModel() before transcribe()")
        }
        let results: [TranscriptionResult] = try await wk.transcribe(audioPath: audioURL.path)
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
