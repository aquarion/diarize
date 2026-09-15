import Foundation
#if canImport(Darwin)
import Darwin
#endif

public struct AppConfig: Sendable {
    public enum Defaults {
        public static let language               = "en"
        public static let whisperkitModel        = "openai_whisper-large-v3_turbo"
        public static let anthropicAPIKey        = ""
        public static let outputDir              = "./out"
        public static let transcriptTitle        = "Session Transcript"
        public static let vaultPath              = "~/Obsidian"
        public static let vaultSubdir            = "Transcripts"
        public static let vaultFilenameTemplate  = "{audio_stem}.md"
    }

    public var language: String
    public var whisperkitModel: String
    public var anthropicAPIKey: String
    public var outputDir: String
    public var transcriptTitle: String
    public var vaultPath: String
    public var vaultSubdir: String
    public var vaultFilenameTemplate: String

    public init(
        language: String = Defaults.language,
        whisperkitModel: String = Defaults.whisperkitModel,
        anthropicAPIKey: String = Defaults.anthropicAPIKey,
        outputDir: String = Defaults.outputDir,
        transcriptTitle: String = Defaults.transcriptTitle,
        vaultPath: String = Defaults.vaultPath,
        vaultSubdir: String = Defaults.vaultSubdir,
        vaultFilenameTemplate: String = Defaults.vaultFilenameTemplate
    ) {
        self.language = language; self.whisperkitModel = whisperkitModel
        self.anthropicAPIKey = anthropicAPIKey; self.outputDir = outputDir
        self.transcriptTitle = transcriptTitle; self.vaultPath = vaultPath
        self.vaultSubdir = vaultSubdir; self.vaultFilenameTemplate = vaultFilenameTemplate
    }

    static let jsonDefaults: [String: Any] = [
        "language": Defaults.language,
        "whisperkit_model": Defaults.whisperkitModel,
        "anthropic_api_key": Defaults.anthropicAPIKey,
        "output_dir": Defaults.outputDir,
        "transcript_title": Defaults.transcriptTitle,
        "vault_path": Defaults.vaultPath,
        "vault_subdir": Defaults.vaultSubdir,
        "vault_filename_template": Defaults.vaultFilenameTemplate,
    ]

    /// The known config keys, for CLI validation. Derived from
    /// `jsonDefaults` rather than duplicated, and exposed as a clean
    /// `Set<String>` rather than the raw untyped defaults dict.
    public static var validKeys: Set<String> { Set(jsonDefaults.keys) }
}

public enum ConfigLoader {
    public static var configURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("diarize/config.json")
    }

    /// Where WhisperKit/SpeakerKit cache their downloaded Hugging Face models.
    ///
    /// Deliberately outside `~/Documents`: on machines where Documents is
    /// redirected into iCloud/OneDrive, cloud sync dehydrates the large model
    /// binaries into placeholder files, which breaks the Hub client's
    /// metadata validation (it can't read or delete a placeholder that's
    /// stuck mid-fetch).
    public static var modelCacheURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("diarize/huggingface")
    }

    /// Not private, so tests can exercise the real production lookup (driven
    /// by the actual running executable's path) rather than only the
    /// extracted `searchUpwardForRepoFile()` helper with synthetic paths.
    static func repoDefaultsURL() -> URL? {
        let filename = "config/defaults.json"
        let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent(filename)
        if FileManager.default.fileExists(atPath: cwd.path) { return cwd }
        guard let executablePath = currentExecutablePath() else { return nil }
        return searchUpwardForRepoFile(from: executablePath, filename: filename)
    }

    /// The path of the actual running executable, as opposed to
    /// `CommandLine.arguments.first` (argv[0]). When `diarize` is invoked
    /// via `PATH` (e.g. after "Install 'diarize' Command in Terminal"),
    /// argv[0] is commonly just the bare command name the user typed
    /// ("diarize"), not a path - resolving that starts a search from the
    /// current directory instead of the installed bundle. `_NSGetExecutablePath`
    /// returns the real path the OS loaded, which `searchUpwardForRepoFile`
    /// can then walk up from.
    private static func currentExecutablePath() -> String? {
        #if canImport(Darwin)
        var size: UInt32 = 0
        _NSGetExecutablePath(nil, &size)
        var buffer = [Int8](repeating: 0, count: Int(size))
        guard _NSGetExecutablePath(&buffer, &size) == 0 else { return nil }
        return String(cString: buffer)
        #else
        return CommandLine.arguments.first
        #endif
    }

    /// Walks up from `executablePath` looking for `filename`, handling both
    /// the plain SwiftPM binary layout (`swift/.build/release/diarize`) and
    /// the assembled `.app` bundle layout
    /// (`swift/.build/release/DiarizeApp.app/Contents/{MacOS,Resources}/...`),
    /// which nest the executable at different depths - a fixed number of
    /// `deletingLastPathComponent()` calls can't handle both at once, so this
    /// searches upward instead, bounded well past either case.
    ///
    /// `resolvingSymlinksInPath()` matters here: the "Install 'diarize'
    /// Command in Terminal" feature symlinks the bundled CLI to
    /// `/usr/local/bin/diarize`, and without resolving that symlink first,
    /// this walk would start under `/usr/local/bin` instead of the real
    /// bundle path inside the checkout.
    ///
    /// Not private, so tests can exercise both layouts directly without
    /// depending on `CommandLine.arguments`.
    static func searchUpwardForRepoFile(
        from executablePath: String, filename: String, fileManager: FileManager = .default
    ) -> URL? {
        var dir = URL(fileURLWithPath: executablePath).resolvingSymlinksInPath().deletingLastPathComponent()
        for _ in 0..<8 {
            let candidate = dir.appendingPathComponent(filename)
            if fileManager.fileExists(atPath: candidate.path) { return candidate }
            let parent = dir.deletingLastPathComponent()
            if parent.path == dir.path { break }
            dir = parent
        }
        return nil
    }

    public static func load(from url: URL = configURL) throws -> (AppConfig, [String: Any]) {
        var raw: [String: Any] = AppConfig.jsonDefaults
        if let repoURL = repoDefaultsURL(),
           let data = try? Data(contentsOf: repoURL),
           let repoDefaults = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            for (k, v) in repoDefaults { raw[k] = v }
        }
        if FileManager.default.fileExists(atPath: url.path) {
            let data = try Data(contentsOf: url)
            if let loaded = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
                for (k, v) in loaded { raw[k] = v }
            }
        }
        let config = AppConfig(
            language: raw["language"] as? String ?? AppConfig.Defaults.language,
            whisperkitModel: raw["whisperkit_model"] as? String ?? AppConfig.Defaults.whisperkitModel,
            anthropicAPIKey: raw["anthropic_api_key"] as? String ?? AppConfig.Defaults.anthropicAPIKey,
            outputDir: raw["output_dir"] as? String ?? AppConfig.Defaults.outputDir,
            transcriptTitle: raw["transcript_title"] as? String ?? AppConfig.Defaults.transcriptTitle,
            vaultPath: raw["vault_path"] as? String ?? AppConfig.Defaults.vaultPath,
            vaultSubdir: raw["vault_subdir"] as? String ?? AppConfig.Defaults.vaultSubdir,
            vaultFilenameTemplate: raw["vault_filename_template"] as? String ?? AppConfig.Defaults.vaultFilenameTemplate
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

    public static let secretKeys: Set<String> = ["anthropic_api_key"]

    /// Masks secret values for display, keeping just enough of the tail to
    /// confirm which one is loaded without exposing the whole thing.
    public static func maskSecret(key: String, value: String) -> String {
        guard secretKeys.contains(key), !value.isEmpty else { return value }
        guard value.count > 4 else { return String(repeating: "*", count: value.count) }
        return String(repeating: "*", count: value.count - 4) + String(value.suffix(4))
    }

    /// Masks all secret fields in a raw config dictionary, for display only.
    public static func maskSecrets(_ raw: [String: Any]) -> [String: Any] {
        var masked = raw
        for key in secretKeys {
            if let value = masked[key] as? String {
                masked[key] = maskSecret(key: key, value: value)
            }
        }
        return masked
    }
}
