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
        "vault_subdir": "Transcripts", "vault_filename_template": "{audio_stem}.md"
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
