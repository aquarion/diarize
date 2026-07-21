import Foundation

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

    public static let jsonDefaults: [String: Any] = [
        "language": Defaults.language,
        "whisperkit_model": Defaults.whisperkitModel,
        "anthropic_api_key": Defaults.anthropicAPIKey,
        "output_dir": Defaults.outputDir,
        "transcript_title": Defaults.transcriptTitle,
        "vault_path": Defaults.vaultPath,
        "vault_subdir": Defaults.vaultSubdir,
        "vault_filename_template": Defaults.vaultFilenameTemplate,
    ]
}

public enum ConfigLoader {
    public static var configURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("diarize/config.json")
    }

    private static func repoDefaultsURL() -> URL? {
        let filename = "config/defaults.json"
        let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent(filename)
        if FileManager.default.fileExists(atPath: cwd.path) { return cwd }
        // Binary-relative: handles swift/.build/release/diarize in development
        if let arg = CommandLine.arguments.first {
            let candidate = URL(fileURLWithPath: arg).standardizedFileURL
                .deletingLastPathComponent()  // release
                .deletingLastPathComponent()  // .build
                .deletingLastPathComponent()  // swift
                .deletingLastPathComponent()  // repo root
                .appendingPathComponent(filename)
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
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
