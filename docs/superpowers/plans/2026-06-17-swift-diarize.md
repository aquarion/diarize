# Swift Diarize App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a macOS Swift package (`swift/`) with a shared `DiarizeKit` framework, a `diarize` CLI tool, and a SwiftUI `DiarizeApp`, replicating the Python diarization pipeline using WhisperKit (transcription) and SpeakerKit (on-device diarization).

**Architecture:** Swift Package Manager package at `swift/` defines `DiarizeKit` (all business logic), `DiarizeCLI` (ArgumentParser executable), and `DiarizeKitTests`. A separate Xcode project at `swift/DiarizeApp/` imports `DiarizeKit` as a local package for the SwiftUI GUI.

**Tech Stack:** WhisperKit (`argmaxinc/WhisperKit`), SpeakerKit (`argmaxinc/argmax-oss-swift`), swift-argument-parser (`apple/swift-argument-parser`), Anthropic Swift SDK (`anthropics/anthropic-swift-sdk`), XCTest, SwiftUI, macOS 14+.

---

## File Map

```
swift/
├── Package.swift
├── Sources/
│   ├── DiarizeKit/
│   │   ├── Models.swift          # Segment, Block, Turn, PipelineProgress, PipelineResult, DiarizeError
│   │   ├── Config.swift          # AppConfig struct + ConfigLoader (load/save/update JSON)
│   │   ├── Renderer.swift        # renderMarkdown / renderSRT / renderVTT / renderTXT + timestamp helpers
│   │   ├── SpeakerMapper.swift   # assignSpeakers / coalesce / loadMapping / saveMapping
│   │   ├── Transcriber.swift     # WhisperKitTranscriber conforming to TranscriberProtocol
│   │   ├── Diarizer.swift        # SpeakerKitDiarizer conforming to DiarizerProtocol
│   │   ├── VaultExporter.swift   # makeOutputDirectory / makeVaultTarget / writeOutputs
│   │   ├── ClaudeGuesser.swift   # guess() using Anthropic SDK + extractJSON helper
│   │   └── Pipeline.swift        # Pipeline struct with run() + checkpoint helpers
│   └── DiarizeCLI/
│       ├── DiarizeCommand.swift  # @main AsyncParsableCommand
│       └── InteractiveLabeler.swift  # label() reading from stdin
├── Tests/
│   └── DiarizeKitTests/
│       ├── RendererTests.swift
│       ├── SpeakerMapperTests.swift
│       ├── ConfigTests.swift
│       └── OverlapTests.swift
└── DiarizeApp/
    ├── DiarizeApp.xcodeproj/
    └── DiarizeApp/
        ├── DiarizeAppApp.swift
        ├── AppState.swift        # ObservableObject driving the state machine
        ├── ContentView.swift     # Top-level router view
        ├── DropView.swift        # File drop + num_speakers input
        ├── ProcessingView.swift  # Live progress display
        ├── SpeakerLabelView.swift
        ├── ResultView.swift
        └── SettingsView.swift
```

---

## Task 1: Package scaffold + dependency verification

**Files:**
- Create: `swift/Package.swift`
- Create: `swift/Sources/DiarizeKit/.gitkeep`
- Create: `swift/Sources/DiarizeCLI/.gitkeep`
- Create: `swift/Tests/DiarizeKitTests/.gitkeep`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p swift/Sources/DiarizeKit swift/Sources/DiarizeCLI swift/Tests/DiarizeKitTests swift/DiarizeApp
```

- [ ] **Step 2: Write Package.swift**

```swift
// swift/Package.swift
// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Diarize",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "DiarizeKit", targets: ["DiarizeKit"]),
        .executable(name: "diarize", targets: ["DiarizeCLI"]),
    ],
    dependencies: [
        .package(url: "https://github.com/argmaxinc/WhisperKit.git", from: "0.9.0"),
        .package(url: "https://github.com/argmaxinc/argmax-oss-swift.git", branch: "main"),
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.3.0"),
        .package(url: "https://github.com/anthropics/anthropic-swift-sdk.git", from: "0.7.0"),
    ],
    targets: [
        .target(
            name: "DiarizeKit",
            dependencies: [
                .product(name: "WhisperKit", package: "WhisperKit"),
                .product(name: "SpeakerKit", package: "argmax-oss-swift"),
                .product(name: "Anthropic", package: "anthropic-swift-sdk"),
            ]
        ),
        .executableTarget(
            name: "DiarizeCLI",
            dependencies: [
                "DiarizeKit",
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
            ]
        ),
        .testTarget(
            name: "DiarizeKitTests",
            dependencies: ["DiarizeKit"]
        ),
    ]
)
```

- [ ] **Step 3: Verify SpeakerKit and Anthropic product names**

```bash
cd swift && swift package resolve
# Inspect resolved packages to confirm product names:
cat .build/checkouts/argmax-oss-swift/Package.swift | grep -A 5 "products:"
cat .build/checkouts/anthropic-swift-sdk/Package.swift | grep -A 5 "products:"
```

If the product name for SpeakerKit is different (e.g. `"ArgmaxSpeakerKit"`), update `Package.swift` accordingly. Same for the Anthropic product name.

- [ ] **Step 4: Add placeholder source files so the package builds**

Create `swift/Sources/DiarizeKit/Models.swift` with just `import Foundation` for now. Create `swift/Sources/DiarizeCLI/DiarizeCommand.swift` with:

```swift
import Foundation
@main struct DiarizeCommand { static func main() {} }
```

- [ ] **Step 5: Verify the package compiles**

```bash
cd swift && swift build
```

Expected: Build succeeds (warnings are fine, errors are not).

- [ ] **Step 6: Commit**

```bash
git add swift/
git commit -m "feat: add Swift package scaffold with DiarizeKit and DiarizeCLI targets"
```

---

## Task 2: Core models

**Files:**
- Modify: `swift/Sources/DiarizeKit/Models.swift`

- [ ] **Step 1: Write Models.swift**

```swift
// swift/Sources/DiarizeKit/Models.swift
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

    public var errorDescription: String? {
        switch self {
        case .audioFileNotFound(let u): return "Audio file not found: \(u.path)"
        case .transcriptionFailed(let m): return "Transcription failed: \(m)"
        case .diarizationFailed(let m): return "Diarization failed: \(m)"
        case .noSegmentsProduced: return "No speech segments were produced"
        case .vaultWriteFailed(let u, let e): return "Failed to write vault at \(u.path): \(e)"
        case .claudeAPIFailed(let m): return "Claude API error: \(m)"
        case .configMissing(let k): return "Required config missing: \(k)"
        }
    }
}

public protocol TranscriberProtocol: Sendable {
    func transcribe(audioURL: URL) async throws -> [Segment]
}

public protocol DiarizerProtocol: Sendable {
    func diarize(audioURL: URL, numSpeakers: Int) async throws -> [Turn]
}
```

- [ ] **Step 2: Verify it compiles**

```bash
cd swift && swift build 2>&1 | grep -E "error:|Build complete"
```

Expected: `Build complete!`

- [ ] **Step 3: Commit**

```bash
git add swift/Sources/DiarizeKit/Models.swift
git commit -m "feat(DiarizeKit): add core model types and protocol definitions"
```

---

## Task 3: Config

**Files:**
- Create: `swift/Sources/DiarizeKit/Config.swift`
- Create: `swift/Tests/DiarizeKitTests/ConfigTests.swift`

- [ ] **Step 1: Write the failing tests**

```swift
// swift/Tests/DiarizeKitTests/ConfigTests.swift
import XCTest
@testable import DiarizeKit

final class ConfigTests: XCTestCase {
    func testDefaultsAppliedForMissingKeys() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("empty_config.json")
        try "{}".write(to: url, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: url) }

        let (config, _) = try ConfigLoader.load(from: url)
        XCTAssertEqual(config.language, "en")
        XCTAssertEqual(config.whisperkitModel, "openai_whisper-large-v3-turbo")
        XCTAssertEqual(config.vaultFilenameTemplate, "{audio_stem}.md")
    }

    func testUnknownKeysRoundTrip() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("roundtrip_config.json")
        let original = #"{"language":"fr","hf_token":"abc123","whisperx_bin":"whisperx"}"#
        try original.write(to: url, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: url) }

        let (config, var raw) = try ConfigLoader.load(from: url)
        XCTAssertEqual(config.language, "fr")
        XCTAssertEqual(raw["hf_token"] as? String, "abc123")   // unknown key preserved

        ConfigLoader.update(config, in: &raw)
        let saveURL = FileManager.default.temporaryDirectory.appendingPathComponent("saved_config.json")
        defer { try? FileManager.default.removeItem(at: saveURL) }
        try ConfigLoader.save(raw, to: saveURL)

        let (reloaded, reloadedRaw) = try ConfigLoader.load(from: saveURL)
        XCTAssertEqual(reloaded.language, "fr")
        XCTAssertEqual(reloadedRaw["hf_token"] as? String, "abc123")
        XCTAssertEqual(reloadedRaw["whisperx_bin"] as? String, "whisperx")
    }

    func testAudioStemTemplateSubstitution() {
        let config = AppConfig()
        let result = config.vaultFilenameTemplate.replacingOccurrences(of: "{audio_stem}", with: "meeting_2026-06-17")
        XCTAssertEqual(result, "meeting_2026-06-17.md")
    }
}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd swift && swift test --filter ConfigTests 2>&1 | tail -5
```

Expected: compile error (Config types not defined yet).

- [ ] **Step 3: Write Config.swift**

```swift
// swift/Sources/DiarizeKit/Config.swift
import Foundation

public struct AppConfig: Sendable {
    public var language: String
    public var whisperkitModel: String
    public var anthropicAPIKey: String
    public var outputDir: String
    public var transcriptTitle: String
    public var vaultPath: String
    public var vaultSubdir: String
    public var vaultFilenameTemplate: String

    public init(
        language: String = "en",
        whisperkitModel: String = "openai_whisper-large-v3-turbo",
        anthropicAPIKey: String = "",
        outputDir: String = "./out",
        transcriptTitle: String = "Session Transcript",
        vaultPath: String = "~/Obsidian",
        vaultSubdir: String = "Transcripts",
        vaultFilenameTemplate: String = "{audio_stem}.md"
    ) {
        self.language = language; self.whisperkitModel = whisperkitModel
        self.anthropicAPIKey = anthropicAPIKey; self.outputDir = outputDir
        self.transcriptTitle = transcriptTitle; self.vaultPath = vaultPath
        self.vaultSubdir = vaultSubdir; self.vaultFilenameTemplate = vaultFilenameTemplate
    }

    static let jsonDefaults: [String: Any] = [
        "language": "en", "whisperkit_model": "openai_whisper-large-v3-turbo",
        "anthropic_api_key": "", "output_dir": "./out",
        "transcript_title": "Session Transcript", "vault_path": "~/Obsidian",
        "vault_subdir": "Transcripts", "vault_filename_template": "{audio_stem}.md",
    ]
}

public enum ConfigLoader {
    public static var configURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("diarize/config.json")
    }

    public static func load(from url: URL = configURL) throws -> (AppConfig, [String: Any]) {
        var raw: [String: Any] = AppConfig.jsonDefaults
        if FileManager.default.fileExists(atPath: url.path) {
            let data = try Data(contentsOf: url)
            if let loaded = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
                for (k, v) in loaded { raw[k] = v }
            }
        }
        let config = AppConfig(
            language: raw["language"] as? String ?? "en",
            whisperkitModel: raw["whisperkit_model"] as? String ?? "openai_whisper-large-v3-turbo",
            anthropicAPIKey: raw["anthropic_api_key"] as? String ?? "",
            outputDir: raw["output_dir"] as? String ?? "./out",
            transcriptTitle: raw["transcript_title"] as? String ?? "Session Transcript",
            vaultPath: raw["vault_path"] as? String ?? "~/Obsidian",
            vaultSubdir: raw["vault_subdir"] as? String ?? "Transcripts",
            vaultFilenameTemplate: raw["vault_filename_template"] as? String ?? "{audio_stem}.md"
        )
        return (config, raw)
    }

    public static func update(_ config: AppConfig, in raw: inout [String: Any]) {
        raw["language"] = config.language
        raw["whisperkit_model"] = config.whisperkitModel
        raw["anthropic_api_key"] = config.anthropicAPIKey
        raw["output_dir"] = config.outputDir
        raw["transcript_title"] = config.transcriptTitle
        raw["vault_path"] = config.vaultPath
        raw["vault_subdir"] = config.vaultSubdir
        raw["vault_filename_template"] = config.vaultFilenameTemplate
    }

    public static func save(_ raw: [String: Any], to url: URL = configURL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        let data = try JSONSerialization.data(withJSONObject: raw,
                                              options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url)
    }
}
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd swift && swift test --filter ConfigTests 2>&1 | tail -5
```

Expected: `Test Suite 'ConfigTests' passed`

- [ ] **Step 5: Commit**

```bash
git add swift/Sources/DiarizeKit/Config.swift swift/Tests/DiarizeKitTests/ConfigTests.swift
git commit -m "feat(DiarizeKit): add AppConfig and ConfigLoader with JSON round-trip"
```

---

## Task 4: Renderer

**Files:**
- Create: `swift/Sources/DiarizeKit/Renderer.swift`
- Create: `swift/Tests/DiarizeKitTests/RendererTests.swift`

- [ ] **Step 1: Write the failing tests**

```swift
// swift/Tests/DiarizeKitTests/RendererTests.swift
import XCTest
@testable import DiarizeKit

final class RendererTests: XCTestCase {
    func testFormatTimestamp() {
        XCTAssertEqual(Renderer.formatTimestamp(0), "[00:00:00]")
        XCTAssertEqual(Renderer.formatTimestamp(3661), "[01:01:01]")
        XCTAssertEqual(Renderer.formatTimestamp(-1), "[00:00:00]")
    }

    func testSRTTimestamp() {
        XCTAssertEqual(Renderer.srtTimestamp(1.5), "00:00:01,500")
        XCTAssertEqual(Renderer.srtTimestamp(3661.123), "01:01:01,123")
    }

    func testVTTTimestamp() {
        XCTAssertEqual(Renderer.vttTimestamp(1.5), "00:00:01.500")
    }

    func testRenderMarkdown() {
        let blocks = [
            Block(speaker: "Alice", start: 0, text: "Hello world."),
            Block(speaker: "Bob", start: 5, text: "Hi there."),
        ]
        let md = Renderer.renderMarkdown(blocks: blocks, title: "Test", audioPath: "/tmp/audio.wav")
        XCTAssertTrue(md.contains("# Test"))
        XCTAssertTrue(md.contains("**Alice** [00:00:00]"))
        XCTAssertTrue(md.contains("Hello world."))
        XCTAssertTrue(md.contains("**Bob** [00:00:05]"))
        XCTAssertTrue(md.hasSuffix("\n"))
    }

    func testRenderSRT() {
        let segs = [
            Segment(start: 0, end: 2, text: "Hello.", speaker: "SPEAKER_00"),
        ]
        let srt = Renderer.renderSRT(segments: segs)
        XCTAssertTrue(srt.contains("1\n"))
        XCTAssertTrue(srt.contains("[SPEAKER_00] Hello."))
        XCTAssertTrue(srt.contains("-->"))
        XCTAssertTrue(srt.hasSuffix("\n"))
    }

    func testRenderVTT() {
        let segs = [Segment(start: 1, end: 3, text: "Test.", speaker: "SPEAKER_01")]
        let vtt = Renderer.renderVTT(segments: segs)
        XCTAssertTrue(vtt.hasPrefix("WEBVTT\n"))
        XCTAssertTrue(vtt.contains("[SPEAKER_01] Test."))
    }

    func testRenderTXT() {
        let segs = [
            Segment(start: 0, end: 1, text: "Line one.", speaker: "A"),
            Segment(start: 1, end: 2, text: "Line two.", speaker: "B"),
        ]
        XCTAssertEqual(Renderer.renderTXT(segments: segs), "Line one.\nLine two.\n")
    }
}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd swift && swift test --filter RendererTests 2>&1 | tail -3
```

Expected: compile error.

- [ ] **Step 3: Write Renderer.swift**

```swift
// swift/Sources/DiarizeKit/Renderer.swift
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
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd swift && swift test --filter RendererTests 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add swift/Sources/DiarizeKit/Renderer.swift swift/Tests/DiarizeKitTests/RendererTests.swift
git commit -m "feat(DiarizeKit): add Renderer with markdown/SRT/VTT/TXT output"
```

---

## Task 5: SpeakerMapper

**Files:**
- Create: `swift/Sources/DiarizeKit/SpeakerMapper.swift`
- Create: `swift/Tests/DiarizeKitTests/SpeakerMapperTests.swift`

- [ ] **Step 1: Write the failing tests**

```swift
// swift/Tests/DiarizeKitTests/SpeakerMapperTests.swift
import XCTest
@testable import DiarizeKit

final class SpeakerMapperTests: XCTestCase {
    func testAssignSpeakersMaxOverlap() {
        let segments = [Segment(start: 1.0, end: 3.0, text: "Hello", speaker: "UNKNOWN")]
        let turns = [
            Turn(start: 0.0, end: 2.0, speaker: "SPEAKER_00"),
            Turn(start: 1.5, end: 4.0, speaker: "SPEAKER_01"),
        ]
        // SPEAKER_00 overlaps [1.0, 2.0] = 1.0s; SPEAKER_01 overlaps [1.5, 3.0] = 1.5s
        let result = SpeakerMapper.assignSpeakers(to: segments, from: turns)
        XCTAssertEqual(result[0].speaker, "SPEAKER_01")
    }

    func testAssignSpeakersNoOverlapKeepsUnknown() {
        let segments = [Segment(start: 10.0, end: 12.0, text: "Hello", speaker: "UNKNOWN")]
        let turns = [Turn(start: 0.0, end: 5.0, speaker: "SPEAKER_00")]
        let result = SpeakerMapper.assignSpeakers(to: segments, from: turns)
        XCTAssertEqual(result[0].speaker, "UNKNOWN")
    }

    func testCoalesceConsecutiveSameSpeaker() {
        let segments = [
            Segment(start: 0, end: 1, text: "Hello", speaker: "SPEAKER_00"),
            Segment(start: 1, end: 2, text: "world.", speaker: "SPEAKER_00"),
            Segment(start: 2, end: 3, text: "Bye.", speaker: "SPEAKER_01"),
        ]
        let mapping = ["SPEAKER_00": "Alice", "SPEAKER_01": "Bob"]
        let blocks = SpeakerMapper.coalesce(segments: segments, mapping: mapping)
        XCTAssertEqual(blocks.count, 2)
        XCTAssertEqual(blocks[0].speaker, "Alice")
        XCTAssertEqual(blocks[0].text, "Hello world.")
        XCTAssertEqual(blocks[1].speaker, "Bob")
    }

    func testCoalesceSkipsEmptySegments() {
        let segments = [
            Segment(start: 0, end: 1, text: "  ", speaker: "SPEAKER_00"),
            Segment(start: 1, end: 2, text: "Hello.", speaker: "SPEAKER_00"),
        ]
        let blocks = SpeakerMapper.coalesce(segments: segments, mapping: [:])
        XCTAssertEqual(blocks.count, 1)
        XCTAssertEqual(blocks[0].text, "Hello.")
    }

    func testSpeakerMappingRoundTrip() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("speakers_test.json")
        defer { try? FileManager.default.removeItem(at: url) }
        let mapping = ["SPEAKER_00": "Alice", "SPEAKER_01": "Bob"]
        try SpeakerMapper.saveMapping(mapping, to: url)
        let loaded = SpeakerMapper.loadMapping(from: url)
        XCTAssertEqual(loaded["SPEAKER_00"], "Alice")
        XCTAssertEqual(loaded["SPEAKER_01"], "Bob")
        XCTAssertNil(loaded["_comment"])
    }
}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd swift && swift test --filter SpeakerMapperTests 2>&1 | tail -3
```

- [ ] **Step 3: Write SpeakerMapper.swift**

```swift
// swift/Sources/DiarizeKit/SpeakerMapper.swift
import Foundation

public enum SpeakerMapper {
    public static func assignSpeakers(to segments: [Segment], from turns: [Turn]) -> [Segment] {
        segments.map { seg in
            var best = "UNKNOWN"; var bestOverlap = 0.0
            for turn in turns {
                let overlap = max(0, min(seg.end, turn.end) - max(seg.start, turn.start))
                if overlap > bestOverlap { bestOverlap = overlap; best = turn.speaker }
            }
            return Segment(start: seg.start, end: seg.end, text: seg.text, speaker: best)
        }
    }

    public static func coalesce(segments: [Segment], mapping: [String: String]) -> [Block] {
        var blocks: [Block] = []
        for seg in segments {
            let text = seg.text.trimmingCharacters(in: .whitespaces)
            guard !text.isEmpty else { continue }
            let name = mapping[seg.speaker] ?? seg.speaker
            if blocks.last?.speaker == name {
                blocks[blocks.count - 1].text += " " + text
            } else {
                blocks.append(Block(speaker: name, start: seg.start, text: text))
            }
        }
        return blocks
    }

    public static func loadMapping(from url: URL) -> [String: String] {
        guard let data = try? Data(contentsOf: url),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return [:]
        }
        return json.compactMapValues { $0 as? String }.filter { !$0.key.hasPrefix("_") }
    }

    public static func saveMapping(_ mapping: [String: String], to url: URL) throws {
        var payload: [String: Any] = ["_comment": "Speaker mapping generated by DiarizeKit."]
        for (k, v) in mapping.sorted(by: { $0.key < $1.key }) { payload[k] = v }
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url)
    }
}
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd swift && swift test --filter SpeakerMapperTests 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add swift/Sources/DiarizeKit/SpeakerMapper.swift swift/Tests/DiarizeKitTests/SpeakerMapperTests.swift
git commit -m "feat(DiarizeKit): add SpeakerMapper with overlap assignment and segment coalescing"
```

---

## Task 6: Transcriber (WhisperKit wrapper)

**Files:**
- Create: `swift/Sources/DiarizeKit/Transcriber.swift`

No unit tests — wraps a CoreML model. Build verification only.

- [ ] **Step 1: Check the WhisperKit API**

```bash
cat swift/.build/checkouts/WhisperKit/Sources/WhisperKit/Core/WhisperKit.swift | head -80
```

Look for the `transcribe(audioPath:)` or `transcribe(audioURL:)` signature and the `TranscriptionSegment` type. Confirm field names (`start`, `end`, `text`).

- [ ] **Step 2: Write Transcriber.swift**

```swift
// swift/Sources/DiarizeKit/Transcriber.swift
import Foundation
import WhisperKit

public actor WhisperKitTranscriber: TranscriberProtocol {
    private var whisperKit: WhisperKit?

    public init() {}

    public func loadModel(_ model: String) async throws {
        whisperKit = try await WhisperKit(model: model)
    }

    public nonisolated func transcribe(audioURL: URL) async throws -> [Segment] {
        // Actor hop: call the isolated loadModel first, then transcribe
        guard let wk = await whisperKit else {
            throw DiarizeError.transcriptionFailed("Call loadModel() before transcribe()")
        }
        let results = try await wk.transcribe(audioPath: audioURL.path)
        guard let result = results.first, let rawSegs = result.segments, !rawSegs.isEmpty else {
            throw DiarizeError.noSegmentsProduced
        }
        return rawSegs.compactMap { seg -> Segment? in
            let text = seg.text.trimmingCharacters(in: .whitespaces)
            guard !text.isEmpty else { return nil }
            return Segment(start: Double(seg.start), end: Double(seg.end), text: text)
        }
    }
}
```

> **Note:** If the WhisperKit `transcribe` API differs (e.g. returns `[TranscriptionResult]` vs something else, or uses `audioURL` instead of `audioPath`), adjust accordingly based on Step 1.

- [ ] **Step 3: Verify it builds**

```bash
cd swift && swift build 2>&1 | grep -E "error:|Build complete"
```

- [ ] **Step 4: Commit**

```bash
git add swift/Sources/DiarizeKit/Transcriber.swift
git commit -m "feat(DiarizeKit): add WhisperKitTranscriber wrapping WhisperKit transcription"
```

---

## Task 7: Diarizer (SpeakerKit wrapper)

**Files:**
- Create: `swift/Sources/DiarizeKit/Diarizer.swift`

No unit tests — wraps a CoreML model. Build verification only.

- [ ] **Step 1: Check the SpeakerKit API**

```bash
find swift/.build/checkouts/argmax-oss-swift -name "*.swift" | xargs grep -l "diarize\|Diarize\|SpeakerDiarization" 2>/dev/null | head -5
```

Open the most relevant file found and read the public API. Look for:
- The main diarization class name
- Its initializer (async or sync?)
- The method signature that returns speaker segments
- The return type's fields (start, end, label/speaker)

- [ ] **Step 2: Write Diarizer.swift** (template — adapt API from Step 1)

```swift
// swift/Sources/DiarizeKit/Diarizer.swift
import Foundation
import SpeakerKit   // adjust import name if different

// ADAPT THIS: replace SpeakerDiarization, diarize(url:numSpeakers:), .segments, .start/.end/.label
// with the actual SpeakerKit types found in Step 1.
public actor SpeakerKitDiarizer: DiarizerProtocol {
    private var pipeline: SpeakerDiarization?

    public init() {}

    public func loadModel() async throws {
        pipeline = try await SpeakerDiarization()
    }

    public nonisolated func diarize(audioURL: URL, numSpeakers: Int) async throws -> [Turn] {
        guard let p = await pipeline else {
            throw DiarizeError.diarizationFailed("Call loadModel() before diarize()")
        }
        let result = try await p.diarize(url: audioURL, numSpeakers: numSpeakers)
        return result.segments.map { seg in
            Turn(start: seg.start, end: seg.end, speaker: seg.label)
        }
    }
}
```

- [ ] **Step 3: Verify it builds**

```bash
cd swift && swift build 2>&1 | grep -E "error:|Build complete"
```

Fix any API mismatches found in Step 1 until the build is clean.

- [ ] **Step 4: Commit**

```bash
git add swift/Sources/DiarizeKit/Diarizer.swift
git commit -m "feat(DiarizeKit): add SpeakerKitDiarizer wrapping on-device diarization"
```

---

## Task 8: VaultExporter

**Files:**
- Create: `swift/Sources/DiarizeKit/VaultExporter.swift`
- Create: `swift/Tests/DiarizeKitTests/OverlapTests.swift` (add VaultExporter tests here)

- [ ] **Step 1: Write the failing tests**

```swift
// swift/Tests/DiarizeKitTests/OverlapTests.swift
import XCTest
@testable import DiarizeKit

final class VaultExporterTests: XCTestCase {
    func testMakeVaultTarget() {
        let config = AppConfig(
            vaultPath: "/tmp/vault",
            vaultSubdir: "Meetings",
            vaultFilenameTemplate: "{audio_stem}.md"
        )
        let url = VaultExporter.makeVaultTarget(config: config, audioStem: "standup_2026-06-17")
        XCTAssertEqual(url.path, "/tmp/vault/Meetings/standup_2026-06-17.md")
    }

    func testMakeVaultTargetNoSubdir() {
        let config = AppConfig(vaultPath: "/tmp/vault", vaultSubdir: "", vaultFilenameTemplate: "{audio_stem}.md")
        let url = VaultExporter.makeVaultTarget(config: config, audioStem: "test")
        XCTAssertEqual(url.path, "/tmp/vault/test.md")
    }

    func testWriteOutputsCreatesFiles() throws {
        let tmpDir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let vaultURL = tmpDir.appendingPathComponent("vault/transcript.md")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let audioURL = tmpDir.appendingPathComponent("audio.wav")
        try Data().write(to: audioURL)
        let outDir = tmpDir.appendingPathComponent("out")
        try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

        let segments = [Segment(start: 0, end: 2, text: "Hello.", speaker: "SPEAKER_00")]
        let blocks = [Block(speaker: "Alice", start: 0, text: "Hello.")]
        let config = AppConfig()

        try VaultExporter.writeOutputs(
            segments: segments, blocks: blocks, config: config,
            audioURL: audioURL, outputDir: outDir, vaultURL: vaultURL
        )

        XCTAssertTrue(FileManager.default.fileExists(atPath: outDir.appendingPathComponent("transcript.md").path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: outDir.appendingPathComponent("audio.srt").path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: outDir.appendingPathComponent("audio.vtt").path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: outDir.appendingPathComponent("audio.txt").path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: vaultURL.path))
    }
}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd swift && swift test --filter VaultExporterTests 2>&1 | tail -3
```

- [ ] **Step 3: Write VaultExporter.swift**

```swift
// swift/Sources/DiarizeKit/VaultExporter.swift
import Foundation

public enum VaultExporter {
    public static func makeVaultTarget(config: AppConfig, audioStem: String) -> URL {
        let root = URL(fileURLWithPath: (config.vaultPath as NSString).expandingTildeInPath)
        let dir = config.vaultSubdir.isEmpty ? root : root.appendingPathComponent(config.vaultSubdir)
        let filename = config.vaultFilenameTemplate.replacingOccurrences(of: "{audio_stem}", with: audioStem)
        return dir.appendingPathComponent(filename)
    }

    public static func makeOutputDirectory(for audioURL: URL, config: AppConfig) throws -> URL {
        let attrs = try FileManager.default.attributesOfItem(atPath: audioURL.path)
        let ctime = (attrs[.creationDate] as? Date) ?? Date()
        let fmt = DateFormatter(); fmt.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        let dateStr = fmt.string(from: ctime)
        let stem = audioURL.deletingPathExtension().lastPathComponent

        let baseURL: URL
        let raw = config.outputDir
        if raw.hasPrefix("/") || raw.hasPrefix("~") {
            baseURL = URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
        } else {
            baseURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent(raw)
        }
        let outDir = baseURL.appendingPathComponent("\(stem)_\(dateStr)")
        try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
        return outDir
    }

    public static func writeOutputs(
        segments: [Segment], blocks: [Block], config: AppConfig,
        audioURL: URL, outputDir: URL, vaultURL: URL
    ) throws {
        let stem = audioURL.deletingPathExtension().lastPathComponent
        let md = Renderer.renderMarkdown(blocks: blocks, title: config.transcriptTitle, audioPath: audioURL.path)

        let localMD = outputDir.appendingPathComponent("transcript.md")
        try md.write(to: localMD, atomically: true, encoding: .utf8)
        try FileManager.default.createDirectory(at: vaultURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try md.write(to: vaultURL, atomically: true, encoding: .utf8)
        try Renderer.renderTXT(segments: segments)
            .write(to: outputDir.appendingPathComponent("\(stem).txt"), atomically: true, encoding: .utf8)
        try Renderer.renderSRT(segments: segments)
            .write(to: outputDir.appendingPathComponent("\(stem).srt"), atomically: true, encoding: .utf8)
        try Renderer.renderVTT(segments: segments)
            .write(to: outputDir.appendingPathComponent("\(stem).vtt"), atomically: true, encoding: .utf8)
    }
}
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd swift && swift test --filter VaultExporterTests 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add swift/Sources/DiarizeKit/VaultExporter.swift swift/Tests/DiarizeKitTests/OverlapTests.swift
git commit -m "feat(DiarizeKit): add VaultExporter for output directory and file writing"
```

---

## Task 9: ClaudeGuesser

**Files:**
- Create: `swift/Sources/DiarizeKit/ClaudeGuesser.swift`
- Modify: `swift/Tests/DiarizeKitTests/OverlapTests.swift` (add extractJSON tests)

- [ ] **Step 1: Check the Anthropic Swift SDK API**

```bash
find swift/.build/checkouts/anthropic-swift-sdk -name "*.swift" | xargs grep -l "messages\|create\|Message" 2>/dev/null | head -5
```

Look for the client class name, the `messages.create(...)` method signature, and the response content type.

- [ ] **Step 2: Add extractJSON tests to OverlapTests.swift**

Append to `swift/Tests/DiarizeKitTests/OverlapTests.swift`:

```swift
final class ClaudeGuesserTests: XCTestCase {
    func testExtractJSONFromPlainResponse() throws {
        let text = #"{"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}"#
        let result = try ClaudeGuesser.extractJSON(from: text, labels: ["SPEAKER_00", "SPEAKER_01"])
        XCTAssertEqual(result["SPEAKER_00"], "Alice")
        XCTAssertEqual(result["SPEAKER_01"], "Bob")
    }

    func testExtractJSONFromMarkdownCodeBlock() throws {
        let text = "```json\n{\"SPEAKER_00\": \"Alice\"}\n```"
        let result = try ClaudeGuesser.extractJSON(from: text, labels: ["SPEAKER_00"])
        XCTAssertEqual(result["SPEAKER_00"], "Alice")
    }

    func testExtractJSONFiltersUnknownLabels() throws {
        let text = #"{"SPEAKER_00": "Alice", "SPEAKER_99": "Ghost"}"#
        let result = try ClaudeGuesser.extractJSON(from: text, labels: ["SPEAKER_00"])
        XCTAssertNil(result["SPEAKER_99"])
    }

    func testExtractJSONThrowsOnGarbage() {
        XCTAssertThrowsError(try ClaudeGuesser.extractJSON(from: "not json at all", labels: []))
    }
}
```

- [ ] **Step 3: Run to confirm failure**

```bash
cd swift && swift test --filter ClaudeGuesserTests 2>&1 | tail -3
```

- [ ] **Step 4: Write ClaudeGuesser.swift** (adapt API from Step 1)

```swift
// swift/Sources/DiarizeKit/ClaudeGuesser.swift
import Foundation
import Anthropic   // adjust if product name differs

public enum ClaudeGuesser {
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
        let fmt = DateFormatter(); fmt.dateFormat = "yyyy-MM-dd HH:mm:ss"
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

        // ADAPT: replace Anthropic(...) and .messages.create(...) with actual SDK call from Step 1
        let client = Anthropic(apiKey: apiKey)
        let response = try await client.messages.create(
            model: .claude_sonnet_4_6,
            maxTokens: 256,
            messages: [.init(role: .user, content: prompt)]
        )
        guard case .text(let text) = response.content.first else {
            throw DiarizeError.claudeAPIFailed("No text in response")
        }
        return try extractJSON(from: text, labels: detectedLabels)
    }

    public static func extractJSON(from text: String, labels: [String]) throws -> [String: String] {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)

        func decode(_ s: String) -> [String: String]? {
            guard let d = s.data(using: .utf8) else { return nil }
            return try? JSONDecoder().decode([String: String].self, from: d)
        }

        if let m = decode(t) { return m.filter { labels.isEmpty || labels.contains($0.key) } }

        // Strip markdown code block
        if let r = t.range(of: #"```(?:json)?\s*(\{[\s\S]*?\})\s*```"#, options: .regularExpression) {
            let inner = String(t[r]).replacingOccurrences(of: #"```(?:json)?"#, with: "", options: .regularExpression)
                .replacingOccurrences(of: "```", with: "").trimmingCharacters(in: .whitespacesAndNewlines)
            if let m = decode(inner) { return m.filter { labels.isEmpty || labels.contains($0.key) } }
        }

        // Greedy first JSON object
        if let r = t.range(of: #"\{[^{}]*\}"#, options: .regularExpression) {
            if let m = decode(String(t[r])) { return m.filter { labels.isEmpty || labels.contains($0.key) } }
        }

        throw DiarizeError.claudeAPIFailed("Could not parse JSON from: \(t.prefix(100))")
    }
}
```

- [ ] **Step 5: Run tests and confirm they pass**

```bash
cd swift && swift test --filter ClaudeGuesserTests 2>&1 | tail -3
```

- [ ] **Step 6: Commit**

```bash
git add swift/Sources/DiarizeKit/ClaudeGuesser.swift swift/Tests/DiarizeKitTests/OverlapTests.swift
git commit -m "feat(DiarizeKit): add ClaudeGuesser for Anthropic SDK speaker name inference"
```

---

## Task 10: Pipeline orchestration

**Files:**
- Create: `swift/Sources/DiarizeKit/Pipeline.swift`

No unit tests for the full pipeline (requires real models). The test target already confirms compilation. A manual smoke test is described at the end.

- [ ] **Step 1: Write Pipeline.swift**

```swift
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
```

- [ ] **Step 2: Verify it builds**

```bash
cd swift && swift build 2>&1 | grep -E "error:|Build complete"
```

- [ ] **Step 3: Verify all tests still pass**

```bash
cd swift && swift test 2>&1 | tail -5
```

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add swift/Sources/DiarizeKit/Pipeline.swift
git commit -m "feat(DiarizeKit): add Pipeline with async transcription+diarization and checkpointing"
```

---

## Task 11: DiarizeCLI

**Files:**
- Modify: `swift/Sources/DiarizeCLI/DiarizeCommand.swift`
- Create: `swift/Sources/DiarizeCLI/InteractiveLabeler.swift`

- [ ] **Step 1: Write InteractiveLabeler.swift**

```swift
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
```

- [ ] **Step 2: Write DiarizeCommand.swift**

```swift
// swift/Sources/DiarizeCLI/DiarizeCommand.swift
import ArgumentParser
import DiarizeKit
import Foundation

@main
struct DiarizeCommand: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "diarize",
        abstract: "Transcribe and diarize a WAV file, export to Obsidian."
    )

    @Argument(help: "Path to the WAV audio file") var wav: String
    @Argument(help: "Number of speakers in the recording") var numSpeakers: Int

    @Flag(name: .long, help: "Ask Claude to guess speaker names") var claudeGuess = false
    @Flag(name: [.customShort("y"), .long], help: "Non-interactive: accept all defaults") var yes = false
    @Option(name: .long, help: "Override vault output path for this file") var vaultOutput: String?
    @Option(name: .long, help: "Path to config JSON file") var config: String?

    mutating func run() async throws {
        let configURL = config.map { URL(fileURLWithPath: $0) } ?? ConfigLoader.configURL
        var (cfg, raw) = try ConfigLoader.load(from: configURL)

        if !yes && cfg.vaultPath == "~/Obsidian" {
            print("\n==> Config: vault_path (Obsidian vault root)")
            print("  Path [~/Obsidian]: ", terminator: "")
            let input = readLine()?.trimmingCharacters(in: .whitespaces) ?? ""
            if !input.isEmpty { cfg.vaultPath = input }
        }
        ConfigLoader.update(cfg, in: &raw)
        try ConfigLoader.save(raw, to: configURL)

        let audioURL = URL(fileURLWithPath: (wav as NSString).expandingTildeInPath)
        guard FileManager.default.fileExists(atPath: audioURL.path) else {
            fputs("!! Input file not found: \(audioURL.path)\n", stderr)
            throw ExitCode(2)
        }

        // Load models before pipeline so we can report progress accurately
        let transcriber = WhisperKitTranscriber()
        print("==> Loading WhisperKit model: \(cfg.whisperkitModel)")
        try await transcriber.loadModel(cfg.whisperkitModel)

        let diarizer = SpeakerKitDiarizer()
        print("==> Loading SpeakerKit model")
        try await diarizer.loadModel()

        let pipeline = Pipeline(transcriber: transcriber, diarizer: diarizer)
        let (progressStream, continuation) = AsyncStream<PipelineProgress>.makeStream()

        async let result: PipelineResult = pipeline.run(
            audioURL: audioURL, numSpeakers: numSpeakers,
            config: cfg, progress: continuation
        )
        for await p in progressStream {
            print("==> \(p.message)")
        }
        let pipelineResult = try await result

        // Speaker labeling
        let mappingURL = pipelineResult.outputDirectoryURL.appendingPathComponent("speakers.json")
        var mapping = SpeakerMapper.loadMapping(from: mappingURL)
        let detected = Array(Set(pipelineResult.segments.map(\.speaker))).sorted()

        if claudeGuess && !cfg.anthropicAPIKey.isEmpty {
            print("==> Asking Claude to guess speaker names...")
            let attrs = try? FileManager.default.attributesOfItem(atPath: audioURL.path)
            let date = (attrs?[.creationDate] as? Date) ?? Date()
            if let guesses = try? await ClaudeGuesser.guess(
                detectedLabels: detected, segments: pipelineResult.segments,
                recordingDate: date, apiKey: cfg.anthropicAPIKey
            ) {
                for (k, v) in guesses where mapping[k] == nil { mapping[k] = v }
                print("    Guesses: \(guesses)")
            }
        }

        let finalMapping: [String: String]
        if yes {
            for label in detected where mapping[label] == nil { mapping[label] = label }
            finalMapping = mapping
        } else {
            finalMapping = InteractiveLabeler.label(
                detected: detected, existing: mapping, segments: pipelineResult.segments
            )
        }
        try SpeakerMapper.saveMapping(finalMapping, to: mappingURL)

        let blocks = SpeakerMapper.coalesce(segments: pipelineResult.segments, mapping: finalMapping)
        let stem = audioURL.deletingPathExtension().lastPathComponent
        let vaultTarget = vaultOutput.map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) }
            ?? VaultExporter.makeVaultTarget(config: cfg, audioStem: stem)

        try VaultExporter.writeOutputs(
            segments: pipelineResult.segments, blocks: blocks, config: cfg,
            audioURL: audioURL, outputDir: pipelineResult.outputDirectoryURL, vaultURL: vaultTarget
        )

        print("\n==> Complete")
        print("    speaker map : \(mappingURL.path)")
        print("    local       : \(pipelineResult.outputDirectoryURL.appendingPathComponent("transcript.md").path)")
        print("    vault       : \(vaultTarget.path)")
    }
}
```

- [ ] **Step 3: Verify it builds and `--help` works**

```bash
cd swift && swift build && .build/debug/diarize --help
```

Expected output includes `USAGE: diarize <wav> <num-speakers>` and the flag descriptions.

- [ ] **Step 4: Commit**

```bash
git add swift/Sources/DiarizeCLI/
git commit -m "feat(DiarizeCLI): implement CLI with ArgumentParser, interactive labeling, and Obsidian export"
```

---

## Task 12: DiarizeApp — Xcode project scaffold

**Files:**
- Create: `swift/DiarizeApp/` Xcode project

- [ ] **Step 1: Create the Xcode project**

Open Xcode → File → New → Project → macOS → App. Set:
- Product Name: `DiarizeApp`
- Team: your developer team (or None for local use)
- Organization Identifier: `com.aquarion` (or similar)
- Language: Swift
- Interface: SwiftUI
- Save location: `swift/DiarizeApp/`

- [ ] **Step 2: Add DiarizeKit as a local package dependency**

In Xcode, select the `DiarizeApp` project → Package Dependencies → `+` → Add Local → select `swift/` (the directory containing `Package.swift`).

Then add `DiarizeKit` to the DiarizeApp target's "Frameworks, Libraries, and Embedded Content".

- [ ] **Step 3: Set minimum deployment target**

In the DiarizeApp target's General settings, set macOS Deployment Target to 14.0.

- [ ] **Step 4: Verify it builds in Xcode**

Cmd+B in Xcode. Expected: Build succeeds.

- [ ] **Step 5: Commit**

```bash
git add swift/DiarizeApp/
git commit -m "feat(DiarizeApp): create Xcode project with local DiarizeKit dependency"
```

---

## Task 13: DiarizeApp — views and state machine

**Files:**
- Create/Modify: all files in `swift/DiarizeApp/DiarizeApp/`

- [ ] **Step 1: Write AppState.swift**

```swift
// swift/DiarizeApp/DiarizeApp/AppState.swift
import DiarizeKit
import Foundation
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    enum Screen { case drop, processing, labeling, result, settings }

    @Published var screen: Screen = .drop
    @Published var progressFraction: Double = 0
    @Published var progressMessage: String = ""
    @Published var detectedSpeakers: [String] = []
    @Published var speakerNames: [String: String] = [:]
    @Published var pipelineResult: PipelineResult?
    @Published var vaultURL: URL?
    @Published var errorMessage: String?
    @Published var claudeGuess: Bool = false

    var config: AppConfig = AppConfig()
    var rawConfig: [String: Any] = [:]
    private var pipeline: Pipeline?

    func loadConfig() {
        if let (c, r) = try? ConfigLoader.load() { config = c; rawConfig = r }
    }

    func saveConfig() {
        ConfigLoader.update(config, in: &rawConfig)
        try? ConfigLoader.save(rawConfig)
    }

    func startPipeline(audioURL: URL, numSpeakers: Int) {
        screen = .processing
        Task {
            do {
                let transcriber = WhisperKitTranscriber()
                try await transcriber.loadModel(config.whisperkitModel)
                let diarizer = SpeakerKitDiarizer()
                try await diarizer.loadModel()
                let p = Pipeline(transcriber: transcriber, diarizer: diarizer)
                pipeline = p

                let (stream, cont) = AsyncStream<PipelineProgress>.makeStream()
                async let result = p.run(audioURL: audioURL, numSpeakers: numSpeakers,
                                         config: config, progress: cont)
                for await prog in stream {
                    progressFraction = prog.fraction
                    progressMessage = prog.message
                }
                let r = try await result
                pipelineResult = r
                detectedSpeakers = Array(Set(r.segments.map(\.speaker))).sorted()
                speakerNames = SpeakerMapper.loadMapping(
                    from: r.outputDirectoryURL.appendingPathComponent("speakers.json"))

                if claudeGuess && !config.anthropicAPIKey.isEmpty {
                    let attrs = try? FileManager.default.attributesOfItem(atPath: audioURL.path)
                    let date = (attrs?[.creationDate] as? Date) ?? Date()
                    if let guesses = try? await ClaudeGuesser.guess(
                        detectedLabels: detectedSpeakers, segments: r.segments,
                        recordingDate: date, apiKey: config.anthropicAPIKey) {
                        for (k, v) in guesses where speakerNames[k] == nil { speakerNames[k] = v }
                    }
                }
                screen = .labeling
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    func finishLabeling(audioURL: URL) {
        guard let result = pipelineResult else { return }
        Task {
            do {
                let mappingURL = result.outputDirectoryURL.appendingPathComponent("speakers.json")
                try SpeakerMapper.saveMapping(speakerNames, to: mappingURL)
                let blocks = SpeakerMapper.coalesce(segments: result.segments, mapping: speakerNames)
                let stem = audioURL.deletingPathExtension().lastPathComponent
                let vault = VaultExporter.makeVaultTarget(config: config, audioStem: stem)
                try VaultExporter.writeOutputs(
                    segments: result.segments, blocks: blocks, config: config,
                    audioURL: audioURL, outputDir: result.outputDirectoryURL, vaultURL: vault)
                vaultURL = vault
                screen = .result
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
```

- [ ] **Step 2: Write DiarizeAppApp.swift**

```swift
// swift/DiarizeApp/DiarizeApp/DiarizeAppApp.swift
import SwiftUI

@main
struct DiarizeAppApp: App {
    @StateObject private var state = AppState()

    var body: some Scene {
        WindowGroup {
            ContentView().environmentObject(state)
                .onAppear { state.loadConfig() }
        }
        .commands {
            CommandGroup(after: .appSettings) {
                Button("Settings…") { state.screen = .settings }
                    .keyboardShortcut(",", modifiers: .command)
            }
        }
    }
}
```

- [ ] **Step 3: Write ContentView.swift**

```swift
// swift/DiarizeApp/DiarizeApp/ContentView.swift
import SwiftUI

struct ContentView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Group {
            switch state.screen {
            case .drop:        DropView()
            case .processing:  ProcessingView()
            case .labeling:    SpeakerLabelView()
            case .result:      ResultView()
            case .settings:    SettingsView()
            }
        }
        .frame(minWidth: 500, minHeight: 400)
        .alert("Error", isPresented: .constant(state.errorMessage != nil)) {
            Button("OK") { state.errorMessage = nil; state.screen = .drop }
        } message: {
            Text(state.errorMessage ?? "")
        }
    }
}
```

- [ ] **Step 4: Write DropView.swift**

```swift
// swift/DiarizeApp/DiarizeApp/DropView.swift
import SwiftUI
import UniformTypeIdentifiers

struct DropView: View {
    @EnvironmentObject var state: AppState
    @State private var numSpeakers: Int = 2
    @State private var isTargeted = false
    @State private var droppedURL: URL?

    var body: some View {
        VStack(spacing: 24) {
            Text("Diarize").font(.largeTitle).bold()

            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(isTargeted ? Color.accentColor : Color.secondary.opacity(0.4),
                                  style: StrokeStyle(lineWidth: 2, dash: [8]))
                    .frame(height: 160)
                VStack(spacing: 8) {
                    Image(systemName: "waveform").font(.system(size: 40)).foregroundColor(.secondary)
                    if let url = droppedURL {
                        Text(url.lastPathComponent).font(.headline)
                    } else {
                        Text("Drop a WAV file here").foregroundColor(.secondary)
                    }
                }
            }
            .onDrop(of: [.audio, .fileURL], isTargeted: $isTargeted) { providers in
                providers.first?.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
                    if let data = item as? Data, let url = URL(dataRepresentation: data, relativeTo: nil) {
                        DispatchQueue.main.async { droppedURL = url }
                    }
                }
                return true
            }

            HStack {
                Text("Speakers:")
                Stepper("\(numSpeakers)", value: $numSpeakers, in: 1...10)
            }

            Toggle("Ask Claude to guess speaker names", isOn: $state.claudeGuess)
                .disabled(state.config.anthropicAPIKey.isEmpty)

            Button("Transcribe & Diarize") {
                guard let url = droppedURL else { return }
                state.startPipeline(audioURL: url, numSpeakers: numSpeakers)
            }
            .buttonStyle(.borderedProminent)
            .disabled(droppedURL == nil)

            Button("Settings") { state.screen = .settings }
                .buttonStyle(.borderless).foregroundColor(.secondary)
        }
        .padding(32)
    }
}
```

- [ ] **Step 5: Write ProcessingView.swift**

```swift
// swift/DiarizeApp/DiarizeApp/ProcessingView.swift
import SwiftUI

struct ProcessingView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 20) {
            ProgressView(value: state.progressFraction)
                .progressViewStyle(.linear)
                .frame(maxWidth: 400)
            Text(state.progressMessage).foregroundColor(.secondary)
        }
        .padding(40)
    }
}
```

- [ ] **Step 6: Write SpeakerLabelView.swift**

```swift
// swift/DiarizeApp/DiarizeApp/SpeakerLabelView.swift
import DiarizeKit
import SwiftUI

struct SpeakerLabelView: View {
    @EnvironmentObject var state: AppState
    @State private var audioURL: URL?   // set from environment or stored in AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Name the speakers").font(.title2).bold()
            Text("Edit display names below. Press Continue when done.")
                .foregroundColor(.secondary)

            ScrollView {
                VStack(spacing: 12) {
                    ForEach(state.detectedSpeakers, id: \.self) { label in
                        SpeakerCard(label: label,
                                    name: binding(for: label),
                                    samples: samples(for: label))
                    }
                }
            }

            HStack {
                Spacer()
                Button("Continue") {
                    // audioURL is stored in AppState by the pipeline kick-off
                    if let url = state.audioDroppedURL {
                        state.finishLabeling(audioURL: url)
                    }
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
    }

    private func binding(for label: String) -> Binding<String> {
        Binding(
            get: { state.speakerNames[label] ?? label },
            set: { state.speakerNames[label] = $0 }
        )
    }

    private func samples(for label: String) -> [String] {
        guard let result = state.pipelineResult else { return [] }
        let segs = result.segments.filter { $0.speaker == label && !$0.text.trimmingCharacters(in: .whitespaces).isEmpty }
        guard !segs.isEmpty else { return [] }
        let n = segs.count
        return [segs[n/6], segs[n/2], segs[(5*n)/6]].map {
            "\(Renderer.formatTimestamp($0.start)) \($0.text.trimmingCharacters(in: .whitespaces))"
        }
    }
}

struct SpeakerCard: View {
    let label: String
    @Binding var name: String
    let samples: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label).font(.caption).foregroundColor(.secondary)
                Spacer()
                TextField("Display name", text: $name)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 200)
            }
            ForEach(samples, id: \.self) { s in
                Text(s).font(.caption2).foregroundColor(.secondary).lineLimit(2)
            }
        }
        .padding(10)
        .background(Color(NSColor.controlBackgroundColor))
        .cornerRadius(8)
    }
}
```

> **Note:** Add `var audioDroppedURL: URL?` to `AppState` and set it in `startPipeline()`.

- [ ] **Step 7: Write ResultView.swift**

```swift
// swift/DiarizeApp/DiarizeApp/ResultView.swift
import SwiftUI

struct ResultView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 56)).foregroundColor(.green)
            Text("Complete").font(.title).bold()

            if let result = state.pipelineResult {
                let md = result.outputDirectoryURL.appendingPathComponent("transcript.md")
                Label(md.path, systemImage: "doc.text").font(.caption).foregroundColor(.secondary)
                Button("Show in Finder") { NSWorkspace.shared.selectFile(md.path, inFileViewerRootedAtPath: "") }
            }

            if let vault = state.vaultURL {
                Label(vault.path, systemImage: "book.closed").font(.caption).foregroundColor(.secondary)
                Button("Open in Obsidian") {
                    if let url = URL(string: "obsidian://open?path=\(vault.path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? "")") {
                        NSWorkspace.shared.open(url)
                    }
                }
            }

            Button("Process another file") { state.screen = .drop }
                .buttonStyle(.borderless)
        }
        .padding(40)
    }
}
```

- [ ] **Step 8: Write SettingsView.swift**

```swift
// swift/DiarizeApp/DiarizeApp/SettingsView.swift
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        Form {
            Section("Transcription") {
                TextField("WhisperKit model", text: $state.config.whisperkitModel)
                TextField("Language", text: $state.config.language)
            }
            Section("Obsidian") {
                TextField("Vault path", text: $state.config.vaultPath)
                TextField("Subdirectory", text: $state.config.vaultSubdir)
                TextField("Filename template", text: $state.config.vaultFilenameTemplate)
            }
            Section("Claude name-guessing") {
                SecureField("Anthropic API key", text: $state.config.anthropicAPIKey)
            }
            Section("Output") {
                TextField("Output directory", text: $state.config.outputDir)
                TextField("Transcript title", text: $state.config.transcriptTitle)
            }
        }
        .formStyle(.grouped)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") { state.saveConfig(); state.screen = .drop }
            }
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancel") { state.screen = .drop }
            }
        }
        .navigationTitle("Settings")
        .frame(minWidth: 480, minHeight: 420)
    }
}
```

- [ ] **Step 9: Build in Xcode and fix any compiler errors**

Cmd+B. Fix any errors — likely `audioDroppedURL` missing from `AppState`, or minor API adjustments.

Add to `AppState`:
```swift
@Published var audioDroppedURL: URL?
```

Update `startPipeline` to set it:
```swift
func startPipeline(audioURL: URL, numSpeakers: Int) {
    audioDroppedURL = audioURL
    // ... rest of existing code
}
```

- [ ] **Step 10: Smoke test the app**

Run the app in Xcode (Cmd+R). Verify:
1. Drop zone accepts a WAV file
2. Settings opens and saves correctly
3. Transcription begins when "Transcribe & Diarize" is clicked (may take a while on first model download)
4. Speaker label sheet appears after pipeline completes
5. Result view shows correct output paths

- [ ] **Step 11: Commit**

```bash
git add swift/DiarizeApp/
git commit -m "feat(DiarizeApp): implement SwiftUI macOS app with drop zone, progress, speaker labeling, and Obsidian export"
```

---

## Self-Review Notes

- **Spec coverage:** All six spec sections covered: repository layout (Task 1), models+pipeline (Tasks 2, 10), config (Task 3), speaker labeling (Tasks 5, 11, 13), output+vault (Tasks 8, 11), error handling (Task 2 DiarizeError, propagated throughout).
- **SpeakerKit API:** Tasks 7 requires verifying the actual SpeakerKit API — a `# ADAPT THIS` comment marks the exact lines to adjust.
- **Anthropic SDK API:** Task 9 similarly marks the client call for adaptation.
- **Type consistency:** `Segment`, `Turn`, `Block`, `PipelineResult`, `PipelineProgress`, `DiarizeError` defined once in `Models.swift` and referenced consistently throughout.
- **`audioDroppedURL`:** Required in `AppState` — noted in Task 13 Step 9; must be added before the app compiles.
