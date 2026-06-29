from server import parse_transcript_path

SWIFT_STDOUT = """\
==> Loading WhisperKit model: openai_whisper-large-v3-turbo
==> Complete
    speaker map : /tmp/out/audio_2026-06-29_10-00-00/speakers.json
    local       : /tmp/out/audio_2026-06-29_10-00-00/transcript.md
    vault       : /Users/me/Obsidian/Transcripts/audio.md
"""

# Python prints an OSC 8 hyperlink: ESC]8;;file:///abs/path ESC\ label ESC]8;; ESC\
_ESC = "\x1b"
_TPATH = "/tmp/out/audio_2026-06-29_10-00-00/transcript.md"
PYTHON_STDOUT = (
    "==> Complete\n"
    "    speaker map updated: /tmp/out/audio_2026-06-29_10-00-00/speakers.json\n"
    f"    local transcript    : {_ESC}]8;;file://{_TPATH}{_ESC}\\"
    f"out/audio_2026-06-29_10-00-00/transcript.md{_ESC}]8;;{_ESC}\\\n"
    "    vault transcript    : ...\n"
)


def test_parse_swift_stdout():
    path = parse_transcript_path(SWIFT_STDOUT, "swift")
    assert path == "/tmp/out/audio_2026-06-29_10-00-00/transcript.md"


def test_parse_python_stdout():
    path = parse_transcript_path(PYTHON_STDOUT, "python")
    assert path == "/tmp/out/audio_2026-06-29_10-00-00/transcript.md"


def test_returns_none_on_empty_swift():
    assert parse_transcript_path("", "swift") is None


def test_returns_none_on_empty_python():
    assert parse_transcript_path("", "python") is None


def test_returns_none_on_unrelated_output():
    assert (
        parse_transcript_path("==> Loading model\n==> Transcribing\n", "swift") is None
    )
