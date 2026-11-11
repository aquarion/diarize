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


# --- Windows file:// URIs (#70) ---


def _windows_stdout(uri: str) -> str:
    return f"    local transcript    : {_ESC}]8;;{uri}{_ESC}\\label{_ESC}]8;;{_ESC}\\\n"


def test_parse_python_windows_drive_uri(monkeypatch):
    import nturl2path

    import server

    monkeypatch.setattr(server, "url2pathname", nturl2path.url2pathname)
    uri = "file:///C:/Users/nicho/out/Record%202026-11-09/transcript.md"
    assert (
        parse_transcript_path(_windows_stdout(uri), "python")
        == "C:\\Users\\nicho\\out\\Record 2026-11-09\\transcript.md"
    )


def test_parse_python_windows_unc_uri(monkeypatch):
    import nturl2path

    import server

    monkeypatch.setattr(server, "url2pathname", nturl2path.url2pathname)
    uri = "file://server/share/out/transcript.md"
    assert (
        parse_transcript_path(_windows_stdout(uri), "python")
        == "\\\\server\\share\\out\\transcript.md"
    )


def test_parse_python_posix_uri_with_spaces():
    uri = "file:///tmp/my%20out/transcript.md"
    assert (
        parse_transcript_path(_windows_stdout(uri), "python")
        == "/tmp/my out/transcript.md"
    )


# --- secret scrubbing (#63) ---


def test_scrub_secrets_redacts_hf_tokens():
    from server import scrub_secrets

    tok = "hf_" + "A1b2C3d4E5" * 4
    text = f"Command '['whisperx', '--hf_token', '{tok}', '--x']' failed"
    out = scrub_secrets(text)
    assert tok not in out
    assert "whisperx" in out and "--x" in out


def test_scrub_secrets_leaves_ordinary_text_alone():
    from server import scrub_secrets

    assert scrub_secrets("exit code 1: no such file") == "exit code 1: no such file"
