import XCTest
@testable import DiarizeKit

final class ConfigTests: XCTestCase {
    func testDefaultsAppliedForMissingKeys() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("empty_config.json")
        try "{}".write(to: url, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: url) }

        let (config, _) = try ConfigLoader.load(from: url)
        XCTAssertEqual(config.language, "en")
        XCTAssertEqual(config.whisperkitModel, "openai_whisper-large-v3_turbo")
        XCTAssertEqual(config.vaultFilenameTemplate, "{audio_stem}.md")
    }

    func testUnknownKeysRoundTrip() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("roundtrip_config.json")
        let original = #"{"language":"fr","hf_token":"abc123","whisperx_bin":"whisperx"}"#
        try original.write(to: url, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: url) }

        let (config, loadedRaw) = try ConfigLoader.load(from: url)
        var raw = loadedRaw
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
