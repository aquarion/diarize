import Foundation

public struct Segment: Codable, Sendable {
    public var start: Double
    public var end: Double
    public var text: String
    public var speaker: String

    public init(start: Double, end: Double, text: String, speaker: String = "UNKNOWN") {
        self.start = start; self.end = end; self.text = text; self.speaker = speaker
    }
}

public struct Turn: Codable, Sendable {
    public var start: Double
    public var end: Double
    public var speaker: String

    public init(start: Double, end: Double, speaker: String) {
        self.start = start; self.end = end; self.speaker = speaker
    }
}

public struct Block: Sendable {
    public var speaker: String
    public var start: Double
    public var text: String

    public init(speaker: String, start: Double, text: String) {
        self.speaker = speaker; self.start = start; self.text = text
    }
}

public struct PipelineProgress: Sendable {
    public enum Stage: Sendable { case transcribing, diarizing, complete }
    public var stage: Stage
    public var fraction: Double
    public var message: String

    public init(stage: Stage, fraction: Double, message: String) {
        self.stage = stage; self.fraction = fraction; self.message = message
    }
}

public struct PipelineResult: Sendable {
    public var segments: [Segment]
    public var outputDirectoryURL: URL

    public init(segments: [Segment], outputDirectoryURL: URL) {
        self.segments = segments; self.outputDirectoryURL = outputDirectoryURL
    }
}

public enum DiarizeError: Error, LocalizedError {
    case audioFileNotFound(URL)
    case transcriptionFailed(String)
    case diarizationFailed(String)
    case noSegmentsProduced
    case vaultWriteFailed(URL, any Error)
    case claudeAPIFailed(String)
    case configMissing(String)
    case mediaExtractionFailed(String)

    public var errorDescription: String? {
        switch self {
        case .audioFileNotFound(let u): return "Audio file not found: \(u.path)"
        case .transcriptionFailed(let m): return "Transcription failed: \(m)"
        case .diarizationFailed(let m): return "Diarization failed: \(m)"
        case .noSegmentsProduced: return "No speech segments were produced"
        case .vaultWriteFailed(let u, let e): return "Failed to write vault at \(u.path): \(e)"
        case .claudeAPIFailed(let m): return "Claude API error: \(m)"
        case .configMissing(let k): return "Required config missing: \(k)"
        case .mediaExtractionFailed(let m): return "Media extraction failed: \(m)"
        }
    }
}

public protocol TranscriberProtocol: Sendable {
    func transcribe(audioURL: URL) async throws -> [Segment]
}

public protocol DiarizerProtocol: Sendable {
    func diarize(audioURL: URL, numSpeakers: Int) async throws -> [Turn]
}
