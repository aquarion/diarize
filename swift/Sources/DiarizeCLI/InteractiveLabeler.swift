// swift/Sources/DiarizeCLI/InteractiveLabeler.swift
import DiarizeKit
import Foundation

public enum InteractiveLabeler {
    public static func label(
        detected: [String],
        existing: [String: String],
        segments: [Segment]
    ) -> [String: String] {
        print("\n==> Speaker labeling")
        print("    Detected: \(detected.joined(separator: ", "))")
        print("    Press Enter to keep the default shown in [brackets].\n")

        // Build sample quotes per speaker: pick segments at ~17%, 50%, 83% through each speaker's utterances
        var speakerSegs: [String: [Segment]] = [:]
        for seg in segments where !seg.text.trimmingCharacters(in: .whitespaces).isEmpty {
            speakerSegs[seg.speaker, default: []].append(seg)
        }

        var result = existing
        for label in detected.sorted() {
            let segs = speakerSegs[label] ?? []
            if !segs.isEmpty {
                let n = segs.count
                let picks = [segs[n / 6], segs[n / 2], segs[(5 * n) / 6]]
                print("  Examples for \(label):")
                for p in picks {
                    print("    \(Renderer.formatTimestamp(p.start)) \(p.text.trimmingCharacters(in: .whitespaces))")
                }
            }
            let def = existing[label] ?? label
            print("  \(label) [\(def)]: ", terminator: "")
            let input = readLine()?.trimmingCharacters(in: .whitespaces) ?? ""
            result[label] = input.isEmpty ? def : input
        }
        return result
    }
}
