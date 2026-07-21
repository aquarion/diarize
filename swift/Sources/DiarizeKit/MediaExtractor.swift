// swift/Sources/DiarizeKit/MediaExtractor.swift
import AVFoundation
import CoreMedia
import Foundation

/// Detects video input and extracts its audio track before transcription,
/// using AVFoundation only — no external tools required.
///
/// AVFoundation reads QuickTime-family containers (.mp4, .mov, .m4v, ...)
/// natively but has no built-in Matroska (.mkv) support, so files from
/// tools that default to .mkv (e.g. OBS) need to be exported as .mp4/.mov
/// first for this to help.
public enum MediaExtractor {
    /// Video codecs used for still-image "attached picture" tracks (e.g.
    /// embedded cover art on .m4a/.mp3), as opposed to real video content.
    /// AVFoundation has no direct equivalent of ffprobe's `attached_pic`
    /// disposition flag, so this is the closest available signal.
    private static let stillImageCodecs: Set<CMVideoCodecType> = [kCMVideoCodecType_JPEG]

    /// Returns true if the asset has a real (non-cover-art) video track,
    /// meaning its audio needs to be extracted before transcription.
    public static func hasVideoTrack(at url: URL) async throws -> Bool {
        do {
            let asset = AVURLAsset(url: url)
            let videoTracks = try await asset.loadTracks(withMediaType: .video)
            for track in videoTracks {
                let formatDescriptions = try await track.load(.formatDescriptions)
                let isCoverArt = !formatDescriptions.isEmpty && formatDescriptions.allSatisfy { desc in
                    stillImageCodecs.contains(CMFormatDescriptionGetMediaSubType(desc))
                }
                if !isCoverArt {
                    return true
                }
            }
            return false
        } catch {
            throw DiarizeError.mediaExtractionFailed(
                "Could not inspect \(url.lastPathComponent): \(error.localizedDescription)"
            )
        }
    }

    /// Extracts the audio track from `src` into an .m4a file inside `outDir`.
    public static func extractAudio(from src: URL, to outDir: URL) async throws -> URL {
        let asset = AVURLAsset(url: src)
        guard let exportSession = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetAppleM4A) else {
            throw DiarizeError.mediaExtractionFailed(
                "Could not read \(src.lastPathComponent) — AVFoundation doesn't support this "
                + "container (e.g. .mkv isn't supported; re-export as .mp4/.mov first)"
            )
        }

        let dest = outDir.appendingPathComponent(src.deletingPathExtension().lastPathComponent + ".m4a")
        if FileManager.default.fileExists(atPath: dest.path) {
            do {
                try FileManager.default.removeItem(at: dest)
            } catch {
                throw DiarizeError.mediaExtractionFailed(
                    "Could not remove existing file at \(dest.path): \(error.localizedDescription)"
                )
            }
        }
        exportSession.outputURL = dest
        exportSession.outputFileType = .m4a

        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            exportSession.exportAsynchronously {
                switch exportSession.status {
                case .completed:
                    continuation.resume()
                case .failed, .cancelled:
                    let reason = exportSession.error?.localizedDescription ?? "unknown error"
                    continuation.resume(throwing: DiarizeError.mediaExtractionFailed(
                        "Audio extraction failed for \(src.lastPathComponent): \(reason)"
                    ))
                default:
                    continuation.resume(throwing: DiarizeError.mediaExtractionFailed(
                        "Audio extraction ended in an unexpected state for \(src.lastPathComponent)"
                    ))
                }
            }
        }
        return dest
    }
}
