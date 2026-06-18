import Foundation

public enum Renderer {
    public static func formatTimestamp(_ seconds: Double) -> String {
        let total = max(0, Int(seconds))
        let h = total / 3600; let m = (total % 3600) / 60; let s = total % 60
        return String(format: "[%02d:%02d:%02d]", h, m, s)
    }

    public static func srtTimestamp(_ seconds: Double) -> String {
        let ms = max(0, Int(seconds * 1000))
        return String(format: "%02d:%02d:%02d,%03d",
                      ms / 3_600_000, (ms % 3_600_000) / 60_000,
                      (ms % 60_000) / 1000, ms % 1000)
    }

    public static func vttTimestamp(_ seconds: Double) -> String {
        let ms = max(0, Int(seconds * 1000))
        return String(format: "%02d:%02d:%02d.%03d",
                      ms / 3_600_000, (ms % 3_600_000) / 60_000,
                      (ms % 60_000) / 1000, ms % 1000)
    }

    public static func renderMarkdown(blocks: [Block], title: String, audioPath: String) -> String {
        let fmt = DateFormatter(); fmt.dateFormat = "yyyy-MM-dd HH:mm"
        var lines = ["# \(title)", "", "- Source audio: `\(audioPath)`",
                     "- Generated: \(fmt.string(from: Date()))", ""]
        for b in blocks {
            lines += ["**\(b.speaker)** \(formatTimestamp(b.start))", "", b.text, ""]
        }
        return lines.joined(separator: "\n").trimmingCharacters(in: .newlines) + "\n"
    }

    public static func renderTXT(segments: [Segment]) -> String {
        segments.map(\.text).joined(separator: "\n") + "\n"
    }

    public static func renderSRT(segments: [Segment]) -> String {
        var lines: [String] = []
        for (i, s) in segments.enumerated() {
            lines += ["\(i+1)", "\(srtTimestamp(s.start)) --> \(srtTimestamp(s.end))",
                      "[\(s.speaker)] \(s.text)", ""]
        }
        return lines.joined(separator: "\n").trimmingCharacters(in: .newlines) + "\n"
    }

    public static func renderVTT(segments: [Segment]) -> String {
        var lines = ["WEBVTT", ""]
        for (i, s) in segments.enumerated() {
            lines += ["\(i+1)", "\(vttTimestamp(s.start)) --> \(vttTimestamp(s.end))",
                      "[\(s.speaker)] \(s.text)", ""]
        }
        return lines.joined(separator: "\n").trimmingCharacters(in: .newlines) + "\n"
    }
}
