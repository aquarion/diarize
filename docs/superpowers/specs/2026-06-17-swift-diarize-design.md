# Swift Diarize App — Design Spec

**Date:** 2026-06-17
**Status:** Approved

## Overview

A macOS Swift port of the Python diarization pipeline. Accepts an audio file and speaker count, transcribes with WhisperKit, diarizes with SpeakerKit (CoreML Pyannote v4), prompts the user to name each detected speaker, and outputs a markdown transcript + Obsidian vault export.

Delivered as two frontends sharing a single framework:
- `diarize` — CLI tool (mirrors the Python app's interface)
- `DiarizeApp` — SwiftUI macOS app (drag-drop, visual progress, speaker labeling UI)

---

## 1. Repository Layout and Targets

Lives at `swift/` within the existing `aquarion/diarize` repo.

```
swift/
├── Package.swift
├── Sources/
│   ├── DiarizeKit/       # Core framework — all business logic
│   ├── DiarizeCLI/       # CLI executable (arg parsing + prompts only)
│   └── DiarizeApp/       # SwiftUI macOS app (views only)
└── Tests/
    └── DiarizeKitTests/
```

Neither `DiarizeCLI` nor `DiarizeApp` contains business logic — both import `DiarizeKit` exclusively.

### Dependencies (Package.swift)

| Package | Purpose |
|---|---|
| `argmaxinc/WhisperKit` | On-device speech transcription |
| `argmaxinc/argmax-oss-swift` | SpeakerKit — CoreML Pyannote v4 diarization |
| `apple/swift-argument-parser` | CLI flag/argument parsing |
| `anthropics/anthropic-swift-sdk` | Claude name-guessing (replaces Python's `claude` CLI subprocess) |

---

## 2. Core Data Model and Pipeline

### Types

```swift
struct Segment {
    var start: Double
    var end: Double
    var text: String
    var speaker: String   // e.g. "SPEAKER_00", "UNKNOWN"
}

struct Block {            // coalesced consecutive same-speaker segments
    var speaker: String   // display name after mapping
    var start: Double
    var text: String
}

struct PipelineProgress {
    enum Stage { case transcribing, diarizing, complete }
    var stage: Stage
    var fraction: Double  // 0.0–1.0
    var message: String
}

struct PipelineResult {
    var blocks: [Block]
    var segments: [Segment]
    var outputDirectoryURL: URL
}
```

### Pipeline interface

```swift
struct Pipeline {
    func run(
        audioURL: URL,
        numSpeakers: Int,
        speakerNames: [String: String],    // label → display name, may be empty
        progress: AsyncStream<PipelineProgress>.Continuation
    ) async throws -> PipelineResult
}
```

Both CLI and GUI subscribe to the same `AsyncStream<PipelineProgress>`. The CLI prints to stdout; the GUI updates a progress view.

### Checkpointing

Mirrors the Python app: after transcription, `{stem}_transcription.json` is written; after diarization, `{stem}_diarization.json` is written. On re-run with existing checkpoints, those stages are skipped. Segment-to-speaker assignment uses maximum time overlap (identical algorithm to the Python app).

---

## 3. Configuration

Config path: `~/Library/Application Support/diarize/config.json` — same file as the Python app. Unknown keys (WhisperX/CUDA/mlx-specific) are preserved on write so switching back to the Python app doesn't break anything.

### Fields used by the Swift app

| Key | Default | Notes |
|---|---|---|
| `language` | `"en"` | Passed to WhisperKit |
| `whisperkit_model` | `"openai_whisper-large-v3-turbo"` | New key; ignored by Python app |
| `anthropic_api_key` | `""` | For Claude name-guessing; new key |
| `output_dir` | `"./out"` | Same as Python |
| `transcript_title` | `"Session Transcript"` | Same as Python |
| `vault_path` | `"~/Obsidian"` | Same as Python |
| `vault_subdir` | `"Transcripts"` | Same as Python |
| `vault_filename_template` | `"{audio_stem}.md"` | Same as Python |

A `Config` struct in `DiarizeKit` loads/saves via `JSONSerialization`. Unknown keys round-trip untouched.

**First run:** GUI opens a settings sheet for required fields (`vault_path`). CLI prompts interactively, matching the Python app's behaviour.

---

## 4. Speaker Labeling and Claude Name-Guessing

After the pipeline produces raw segments with speaker labels, a labeling step runs before rendering.

### CLI

Interactive prompts identical to the Python app: shows three sample quotes per speaker, asks for a display name, accepts Enter to keep the default. `speakers.json` saved in the output directory.

### GUI

A `SpeakerLabelView` sheet appears after the pipeline completes. Each speaker gets a card with three sample quotes and a text field pre-filled with the label. User edits names and clicks Continue.

### Claude name-guessing

Uses the Anthropic Swift SDK directly (no subprocess). Sends the same prompt as the Python app: first 2000 chars of transcript, speaker labels, recording date. Returns `[String: String]` pre-filling the labeling step — user can still override.

- CLI flag: `--claude-guess`
- GUI: toggle in the processing options panel
- Requires `anthropic_api_key` in config; if missing, step is silently skipped with a warning

### speakers.json format

Identical to the Python app — mappings are shared between both tools.

---

## 5. Output and Obsidian Export

### Output directory

Named `{stem}_{YYYY-MM-DD_HH-MM-SS}/` based on audio file creation time, under `output_dir`. Matches Python app exactly.

```
out/audio_2026-06-17_10-30-00/
  audio.json                   # raw segments with speaker labels
  audio_transcription.json     # transcription checkpoint
  audio_diarization.json       # diarization checkpoint
  audio.txt                    # plain text
  audio.srt                    # SRT subtitles
  audio.vtt                    # WebVTT
  transcript.md                # rendered markdown
  speakers.json                # speaker name mapping
```

### Markdown format

Identical to the Python app (`**Name** [HH:MM:SS]` blocks) so existing Obsidian notes and templates continue to work.

### Obsidian export

Writes the same markdown to `{vault_path}/{vault_subdir}/{filename_template}` using `{audio_stem}` substitution.

- **GUI:** Shows a Finder-revealed path and an "Open in Obsidian" button via `obsidian://` URL scheme.
- **CLI:** Prints a clickable OSC 8 hyperlink, matching the Python app.
- **`--vault-output PATH`** override supported in CLI; GUI has an "Override vault path" field.

---

## 6. Error Handling and Testing

### Errors

```swift
enum DiarizeError: Error {
    case audioFileNotFound(URL)
    case transcriptionFailed(String)
    case diarizationFailed(String)
    case noSegmentsProduced
    case vaultWriteFailed(URL, any Error)
    case claudeAPIFailed(String)
    case configMissing(String)   // required field empty
}
```

- **CLI:** Prints to stderr with `!!` prefix (matching Python style), exits code 2.
- **GUI:** Alert sheet with message and "Try Again" button.

### Tests (`DiarizeKitTests`)

| Test | What it covers |
|---|---|
| `testMarkdownRendering` | Output matches expected string for known `[Block]` input |
| `testCoalesceSegments` | Consecutive same-speaker segments merge; boundaries preserved |
| `testConfigRoundTrip` | Unknown keys preserved; defaults applied for missing fields |
| `testAudioStemTemplate` | `{audio_stem}` substitution in `vault_filename_template` |
| `testOverlapAssignment` | Segment gets the speaker label with highest time overlap |

No integration tests against real WhisperKit/SpeakerKit models (too slow for CI). Manual smoke tests cover the full pipeline end-to-end.
