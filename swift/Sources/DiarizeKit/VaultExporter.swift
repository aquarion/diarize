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
