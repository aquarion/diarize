import Foundation
import SwiftAnthropic

/// Uses the Anthropic Claude API to guess real names for speaker labels from transcript context.
public enum ClaudeGuesser {

    /// Calls the Claude API to infer speaker names from diarized transcript segments.
    ///
    /// - Parameters:
    ///   - detectedLabels: The speaker label strings (e.g. ["SPEAKER_00", "SPEAKER_01"]).
    ///   - segments: All diarized segments; the prompt is capped at 2000 characters.
    ///   - recordingDate: The date/time the recording was made, included in the prompt.
    ///   - apiKey: Anthropic API key.
    /// - Returns: A dictionary mapping each speaker label to a guessed name.
    /// - Throws: `DiarizeError.claudeAPIFailed` if the API call or JSON parsing fails.
    public static func guess(
        detectedLabels: [String],
        segments: [Segment],
        recordingDate: Date,
        apiKey: String
    ) async throws -> [String: String] {
        var chars = 0
        var lines: [String] = []
        for seg in segments {
            let line = "\(Renderer.formatTimestamp(seg.start)) \(seg.speaker): \(seg.text)"
            chars += line.count + 1
            if chars > 2000 { break }
            lines.append(line)
        }
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd HH:mm:ss"
        let prompt = """
        The following is an excerpt from a diarized transcript. \
        Speaker labels: \(detectedLabels.joined(separator: ", ")).
        Guess the real name of each speaker from context clues. \
        Use a short descriptive label if unsure (e.g. 'Facilitator').
        Return ONLY a valid JSON object mapping each label to a name. No markdown.
        Recording date: \(fmt.string(from: recordingDate)).

        Transcript:
        \(lines.joined(separator: "\n"))
        """

        let service = AnthropicServiceFactory.service(
            apiKey: apiKey,
            betaHeaders: nil
        )

        let parameter = MessageParameter(
            model: .other("claude-sonnet-4-6"),
            messages: [
                MessageParameter.Message(
                    role: .user,
                    content: .text(prompt)
                )
            ],
            maxTokens: 256
        )

        let response: MessageResponse
        do {
            response = try await service.createMessage(parameter)
        } catch {
            throw DiarizeError.claudeAPIFailed(error.localizedDescription)
        }

        guard case .text(let text) = response.content.first else {
            throw DiarizeError.claudeAPIFailed("No text content in Claude response")
        }

        return try extractJSON(from: text, labels: detectedLabels)
    }

    /// Extracts a `[String: String]` JSON dictionary from Claude's response text.
    ///
    /// Handles three formats:
    /// 1. Plain JSON object
    /// 2. JSON wrapped in a markdown code block (```json ... ```)
    /// 3. Greedy first `{...}` extraction
    ///
    /// Filters the result to only include keys present in `labels` (if `labels` is non-empty).
    ///
    /// - Parameters:
    ///   - text: The raw text returned by Claude.
    ///   - labels: The allowed speaker label keys. Pass empty array to allow all keys.
    /// - Returns: A filtered dictionary of speaker label -> name.
    /// - Throws: `DiarizeError.claudeAPIFailed` if no valid JSON object can be parsed.
    public static func extractJSON(from text: String, labels: [String]) throws -> [String: String] {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)

        func decode(_ s: String) -> [String: String]? {
            guard let d = s.data(using: .utf8) else { return nil }
            return try? JSONDecoder().decode([String: String].self, from: d)
        }

        func filter(_ dict: [String: String]) -> [String: String] {
            labels.isEmpty ? dict : dict.filter { labels.contains($0.key) }
        }

        // 1. Try plain JSON
        if let m = decode(t) { return filter(m) }

        // 2. Strip markdown code block
        if let r = t.range(of: #"```(?:json)?\s*(\{[\s\S]*?\})\s*```"#, options: .regularExpression) {
            let inner = String(t[r])
                .replacingOccurrences(of: #"```(?:json)?"#, with: "", options: .regularExpression)
                .replacingOccurrences(of: "```", with: "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if let m = decode(inner) { return filter(m) }
        }

        // 3. Greedy first JSON object
        if let r = t.range(of: #"\{[^{}]*\}"#, options: .regularExpression) {
            if let m = decode(String(t[r])) { return filter(m) }
        }

        throw DiarizeError.claudeAPIFailed("Could not parse JSON from: \(t.prefix(100))")
    }
}
