import Foundation
import SpeakerKit
import WhisperKit

/// Wraps SpeakerKit's Pyannote-based on-device speaker diarization.
///
/// Usage:
/// ```swift
/// let diarizer = SpeakerKitDiarizer()
/// try await diarizer.loadModel()
/// let turns = try await diarizer.diarize(audioURL: url, numSpeakers: 2)
/// ```
@available(macOS 14, *)
public actor SpeakerKitDiarizer: DiarizerProtocol {
    private var speakerKit: SpeakerKit?

    public init() {}

    /// Downloads (if needed) and loads the Pyannote diarization models into memory.
    /// Must be called before `diarize(audioURL:numSpeakers:)`.
    public func loadModel() async throws {
        let config = PyannoteConfig(download: true, load: true, verbose: false)
        speakerKit = try await SpeakerKit(config)
    }

    public nonisolated func diarize(audioURL: URL, numSpeakers: Int) async throws -> [Turn] {
        guard let sk = await speakerKit else {
            throw DiarizeError.diarizationFailed("Call loadModel() before diarize()")
        }
        do {
            let audioArray = try AudioProcessor.loadAudioAsFloatArray(fromPath: audioURL.path)
            let options = PyannoteDiarizationOptions(numberOfSpeakers: numSpeakers > 0 ? numSpeakers : nil)
            let result = try await sk.diarize(audioArray: audioArray, options: options)
            return result.segments.map { seg in
                Turn(
                    start: Double(seg.startTime),
                    end: Double(seg.endTime),
                    speaker: seg.speaker.description
                )
            }
        } catch let error as DiarizeError {
            throw error
        } catch {
            throw DiarizeError.diarizationFailed(error.localizedDescription)
        }
    }
}
