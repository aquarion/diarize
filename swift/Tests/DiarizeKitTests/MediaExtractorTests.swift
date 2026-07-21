import XCTest
@testable import DiarizeKit

/// Exercises MediaExtractor against real ffmpeg-generated fixtures (skipped
/// via XCTSkip if ffmpeg isn't on PATH) rather than mocking AVFoundation,
/// since the whole point is confirming AVFoundation's actual behavior for
/// real video/cover-art/audio files.
final class MediaExtractorTests: XCTestCase {
    private func runFFmpeg(_ args: [String]) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = ["ffmpeg", "-y", "-loglevel", "error"] + args
        do {
            try process.run()
        } catch {
            throw XCTSkip("ffmpeg not available: \(error)")
        }
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw XCTSkip("ffmpeg exited \(process.terminationStatus); skipping")
        }
    }

    private func tempURL(_ ext: String) -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "." + ext)
    }

    private func makeVideoWithAudio() throws -> URL {
        let url = tempURL("mp4")
        try runFFmpeg([
            "-f", "lavfi", "-i", "testsrc=duration=1:size=32x32:rate=5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-c:a", "aac", url.path,
        ])
        return url
    }

    private func makePureAudio() throws -> URL {
        let url = tempURL("wav")
        try runFFmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-ar", "44100", url.path])
        return url
    }

    /// Mirrors how ffmpeg itself (and many real-world tools) embed cover art
    /// on .m4a: a genuine video track, MJPEG-encoded, flagged attached_pic.
    /// This is the exact case that regressed from the Python original.
    private func makeAudioWithCoverArt() throws -> URL {
        let audio = try makePureAudio()
        defer { try? FileManager.default.removeItem(at: audio) }
        let cover = tempURL("png")
        defer { try? FileManager.default.removeItem(at: cover) }
        try runFFmpeg(["-f", "lavfi", "-i", "color=c=red:s=32x32", "-frames:v", "1", cover.path])
        let output = tempURL("m4a")
        try runFFmpeg([
            "-i", audio.path, "-i", cover.path,
            "-map", "0:a", "-map", "1:v",
            "-c:v", "mjpeg", "-c:a", "aac",
            "-disposition:v:0", "attached_pic",
            output.path,
        ])
        return output
    }

    func testHasVideoTrackTrueForRealVideo() async throws {
        let url = try makeVideoWithAudio()
        defer { try? FileManager.default.removeItem(at: url) }
        let result = try await MediaExtractor.hasVideoTrack(at: url)
        XCTAssertTrue(result)
    }

    func testHasVideoTrackFalseForPureAudio() async throws {
        let url = try makePureAudio()
        defer { try? FileManager.default.removeItem(at: url) }
        let result = try await MediaExtractor.hasVideoTrack(at: url)
        XCTAssertFalse(result)
    }

    func testHasVideoTrackFalseForCoverArt() async throws {
        let url = try makeAudioWithCoverArt()
        defer { try? FileManager.default.removeItem(at: url) }
        let result = try await MediaExtractor.hasVideoTrack(at: url)
        XCTAssertFalse(result)
    }

    func testExtractAudioProducesAudioOnlyFile() async throws {
        let url = try makeVideoWithAudio()
        defer { try? FileManager.default.removeItem(at: url) }
        let outDir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: outDir) }

        let dest = try await MediaExtractor.extractAudio(from: url, to: outDir)

        XCTAssertTrue(FileManager.default.fileExists(atPath: dest.path))
        let stillHasVideo = try await MediaExtractor.hasVideoTrack(at: dest)
        XCTAssertFalse(stillHasVideo)
    }

    func testExtractAudioOverwritesExistingDestination() async throws {
        let url = try makeVideoWithAudio()
        defer { try? FileManager.default.removeItem(at: url) }
        let outDir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: outDir) }
        try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

        _ = try await MediaExtractor.extractAudio(from: url, to: outDir)
        // Second extraction into the same destination must not throw despite
        // the file already existing from the first run.
        let dest = try await MediaExtractor.extractAudio(from: url, to: outDir)
        XCTAssertTrue(FileManager.default.fileExists(atPath: dest.path))
    }

    func testHasVideoTrackThrowsMediaExtractionFailedForMissingFile() async throws {
        let missing = tempURL("mp4")
        do {
            _ = try await MediaExtractor.hasVideoTrack(at: missing)
            XCTFail("expected an error for a nonexistent file")
        } catch let error as DiarizeError {
            guard case .mediaExtractionFailed = error else {
                XCTFail("expected .mediaExtractionFailed, got \(error)")
                return
            }
        }
    }
}
