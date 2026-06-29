# Diarize

Transcribes an audio file, detects and labels each speaker, and exports a clean markdown transcript to an Obsidian vault. Two independent implementations are provided — pick the one that fits your setup.

| | Swift | Python |
|---|---|---|
| **Platform** | macOS 14+ (Apple Silicon) | macOS, Linux, Windows |
| **Setup** | `swift build` — no Python env | pip install + Hugging Face token |
| **Engine** | WhisperKit + SpeakerKit | WhisperX or mlx-whisper + pyannote |
| **Interface** | macOS app + CLI | CLI |
| **CUDA** | — | Yes |

## Swift (macOS)

No Python environment required. Uses on-device WhisperKit and SpeakerKit models.

**Requirements:** macOS 14+, Xcode command line tools or Xcode.

**Build:**

```bash
cd swift
swift build -c release
```

**CLI:**

```bash
.build/release/diarize /path/to/audio.wav <num_speakers>
```

Options match the Python CLI: `--claude-guess`, `--yes`, `--vault-output`, `--config`.

**macOS app:**

Open `swift/` in Xcode and run the `DiarizeApp` scheme, or build it from the command line:

```bash
swift build -c release --product DiarizeApp
```

Drag a WAV file onto the app window to start processing.

**Config** is stored at `~/Library/Application Support/diarize/config.json` and created on first run.

## Python

Cross-platform CLI with WhisperX (CUDA-capable) and an mlx-whisper path for Apple Silicon. Full setup, configuration reference, and usage examples:

**[python/README.md](python/README.md)**
