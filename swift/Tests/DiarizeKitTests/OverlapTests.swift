import XCTest
@testable import DiarizeKit

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
