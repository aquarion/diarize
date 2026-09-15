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

    func testMaskSecretKeepsLastFourCharacters() {
        XCTAssertEqual(ConfigLoader.maskSecret(key: "anthropic_api_key", value: "sk-ant-1234"), "*******1234")
    }

    func testMaskSecretFullyMasksShortValues() {
        XCTAssertEqual(ConfigLoader.maskSecret(key: "anthropic_api_key", value: "abc"), "***")
    }

    func testMaskSecretLeavesNonSecretKeysUntouched() {
        XCTAssertEqual(ConfigLoader.maskSecret(key: "language", value: "en"), "en")
    }

    func testMaskSecretsOnlyMasksKnownSecretFields() {
        let raw: [String: Any] = ["anthropic_api_key": "sk-ant-1234", "language": "en"]
        let masked = ConfigLoader.maskSecrets(raw)
        XCTAssertEqual(masked["anthropic_api_key"] as? String, "*******1234")
        XCTAssertEqual(masked["language"] as? String, "en")
    }

    func testValidKeysCoversAllConfigFields() {
        let expected: Set<String> = [
            "language", "whisperkit_model", "anthropic_api_key", "output_dir",
            "transcript_title", "vault_path", "vault_subdir", "vault_filename_template",
        ]
        XCTAssertEqual(AppConfig.validKeys, expected)
    }

    func testValidKeysRejectsUnknownKey() {
        XCTAssertFalse(AppConfig.validKeys.contains("not_a_real_key"))
    }

    /// Returns the repo root with symlinks already resolved (e.g. macOS's
    /// /var -> /private/var) so it matches what `searchUpwardForRepoFile`'s
    /// own `resolvingSymlinksInPath()` call will produce - otherwise these
    /// tests would compare an unresolved path against a resolved one.
    private func makeRepoCheckout() throws -> URL {
        let repoRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("repo_defaults_\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: repoRoot.appendingPathComponent("config"), withIntermediateDirectories: true)
        try "{}".write(
            to: repoRoot.appendingPathComponent("config/defaults.json"), atomically: true, encoding: .utf8)
        return repoRoot.resolvingSymlinksInPath()
    }

    func testSearchUpwardFindsDefaultsFromPlainSwiftPMBinaryLayout() throws {
        let repoRoot = try makeRepoCheckout()
        defer { try? FileManager.default.removeItem(at: repoRoot) }

        let binaryPath = repoRoot.appendingPathComponent("swift/.build/release/diarize").path
        let found = ConfigLoader.searchUpwardForRepoFile(from: binaryPath, filename: "config/defaults.json")
        XCTAssertEqual(found?.path, repoRoot.appendingPathComponent("config/defaults.json").path)
    }

    func testSearchUpwardFindsDefaultsFromAssembledAppBundleLayout() throws {
        let repoRoot = try makeRepoCheckout()
        defer { try? FileManager.default.removeItem(at: repoRoot) }

        let appExecutablePath = repoRoot
            .appendingPathComponent("swift/.build/release/DiarizeApp.app/Contents/MacOS/DiarizeApp").path
        let found = ConfigLoader.searchUpwardForRepoFile(from: appExecutablePath, filename: "config/defaults.json")
        XCTAssertEqual(found?.path, repoRoot.appendingPathComponent("config/defaults.json").path)

        let embeddedCLIPath = repoRoot
            .appendingPathComponent("swift/.build/release/DiarizeApp.app/Contents/Resources/diarize").path
        let foundFromResources = ConfigLoader.searchUpwardForRepoFile(
            from: embeddedCLIPath, filename: "config/defaults.json")
        XCTAssertEqual(foundFromResources?.path, repoRoot.appendingPathComponent("config/defaults.json").path)
    }

    func testSearchUpwardResolvesInstalledCLISymlink() throws {
        let repoRoot = try makeRepoCheckout()
        defer { try? FileManager.default.removeItem(at: repoRoot) }

        let realCLIDir = repoRoot
            .appendingPathComponent("swift/.build/release/DiarizeApp.app/Contents/Resources")
        try FileManager.default.createDirectory(at: realCLIDir, withIntermediateDirectories: true)
        let realCLIPath = realCLIDir.appendingPathComponent("diarize")
        try "".write(to: realCLIPath, atomically: true, encoding: .utf8)

        let symlinkDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("bin_\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: symlinkDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: symlinkDir) }
        let symlinkPath = symlinkDir.appendingPathComponent("diarize")
        try FileManager.default.createSymbolicLink(at: symlinkPath, withDestinationURL: realCLIPath)

        let found = ConfigLoader.searchUpwardForRepoFile(from: symlinkPath.path, filename: "config/defaults.json")
        XCTAssertEqual(found?.path, repoRoot.appendingPathComponent("config/defaults.json").path)
    }

    func testSearchUpwardReturnsNilWhenNoRepoFileFound() {
        let found = ConfigLoader.searchUpwardForRepoFile(
            from: "/tmp/nonexistent-diarize-checkout-xyz/swift/.build/release/diarize",
            filename: "config/defaults.json")
        XCTAssertNil(found)
    }

    func testRepoDefaultsURLFindsRealCheckoutConfigViaProductionPath() {
        // Unlike the layout tests above, this drives repoDefaultsURL() itself
        // with no synthetic path: it resolves the real xctest executable's
        // path via currentExecutablePath() and walks up from there, so it
        // actually exercises the code path a build of the CLI or app runs.
        // `swift test` runs with cwd == swift/, so the cwd-relative check
        // (which looks for ./config/defaults.json) doesn't short-circuit
        // this - config/defaults.json lives at the repo root, one level up.
        guard let url = ConfigLoader.repoDefaultsURL() else {
            XCTFail("Expected repoDefaultsURL() to find the repo checkout's config/defaults.json")
            return
        }
        XCTAssertTrue(url.path.hasSuffix("config/defaults.json"))
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
    }
}
