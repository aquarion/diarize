import json
from unittest.mock import patch

import app
import pytest

# --- _normalize_argv ---


def test_normalize_argv_bare_wav_and_speakers_gets_transcribe_prefix():
    assert app._normalize_argv(["prog", "file.wav", "3"]) == [
        "prog",
        "transcribe",
        "file.wav",
        "3",
    ]


def test_normalize_argv_no_args_gets_transcribe_prefix():
    assert app._normalize_argv(["prog"]) == ["prog", "transcribe"]


def test_normalize_argv_explicit_transcribe_passthrough():
    argv = ["prog", "transcribe", "file.wav", "3"]
    assert app._normalize_argv(argv) == argv


def test_normalize_argv_config_passthrough():
    argv = ["prog", "config", "show"]
    assert app._normalize_argv(argv) == argv


def test_normalize_argv_help_flags_passthrough():
    assert app._normalize_argv(["prog", "-h"]) == ["prog", "-h"]
    assert app._normalize_argv(["prog", "--help"]) == ["prog", "--help"]


def test_normalize_argv_leading_flag_still_gets_transcribe_prefix():
    assert app._normalize_argv(["prog", "--claude-guess", "file.wav", "3"]) == [
        "prog",
        "transcribe",
        "--claude-guess",
        "file.wav",
        "3",
    ]


# --- parse_args ---


def test_parse_args_bare_invocation_defaults_to_transcribe():
    args = app.parse_args(["prog", "file.wav", "3"])
    assert args.command == "transcribe"
    assert args.wav == "file.wav"
    assert args.num_speakers == 3
    assert args.skip_whisperx is False
    assert args.claude_guess is False
    assert args.yes is False
    assert args.vault_output is None


def test_parse_args_transcribe_flags():
    args = app.parse_args(
        [
            "prog",
            "transcribe",
            "file.wav",
            "3",
            "--yes",
            "--claude-guess",
            "--skip-whisperx",
        ]
    )
    assert args.yes is True
    assert args.claude_guess is True
    assert args.skip_whisperx is True


def test_parse_args_transcribe_short_yes_flag():
    args = app.parse_args(["prog", "file.wav", "3", "-y"])
    assert args.yes is True


def test_parse_args_config_show():
    args = app.parse_args(["prog", "config", "show"])
    assert args.command == "config"
    assert args.config_command == "show"


def test_parse_args_config_get():
    args = app.parse_args(["prog", "config", "get", "model"])
    assert args.config_command == "get"
    assert args.key == "model"


def test_parse_args_config_set():
    args = app.parse_args(["prog", "config", "set", "model", "large-v3"])
    assert args.config_command == "set"
    assert args.key == "model"
    assert args.value == "large-v3"


def test_parse_args_config_without_subcommand_exits():
    with pytest.raises(SystemExit):
        app.parse_args(["prog", "config"])


def test_parse_args_transcribe_missing_required_args_exits():
    with pytest.raises(SystemExit):
        app.parse_args(["prog"])


# --- _relaunch_with_lib_path ---


def test_relaunch_noop_when_config_file_missing(tmp_path):
    argv = [
        "prog",
        "transcribe",
        "a.wav",
        "2",
        "--config",
        str(tmp_path / "missing.json"),
    ]
    with patch("app.os.execv") as mock_execv:
        app._relaunch_with_lib_path(argv)
    mock_execv.assert_not_called()


def test_relaunch_noop_when_no_extra_lib_path_configured(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"extra_lib_path": []}))
    argv = ["prog", "transcribe", "a.wav", "2", "--config", str(cfg_path)]
    with patch("app.os.execv") as mock_execv:
        app._relaunch_with_lib_path(argv)
    mock_execv.assert_not_called()


def test_relaunch_noop_when_lib_path_already_present(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"extra_lib_path": ["/opt/lib"]}))
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", f"/opt/lib{app.os.pathsep}/other")
    argv = ["prog", "transcribe", "a.wav", "2", "--config", str(cfg_path)]
    with patch("app.os.execv") as mock_execv:
        app._relaunch_with_lib_path(argv)
    mock_execv.assert_not_called()


def test_relaunch_execs_with_lib_path_prepended_on_linux(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"extra_lib_path": ["/opt/lib"]}))
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    argv = ["prog", "transcribe", "a.wav", "2", "--config", str(cfg_path)]

    with patch("app.os.execv") as mock_execv:
        app._relaunch_with_lib_path(argv)

    mock_execv.assert_called_once()
    assert app.os.environ["LD_LIBRARY_PATH"].startswith("/opt/lib")


def test_relaunch_uses_dyld_library_path_on_darwin(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"extra_lib_path": ["/opt/lib"]}))
    monkeypatch.setattr(app.sys, "platform", "darwin")
    monkeypatch.delenv("DYLD_LIBRARY_PATH", raising=False)
    argv = ["prog", "transcribe", "a.wav", "2", "--config", str(cfg_path)]

    with patch("app.os.execv"):
        app._relaunch_with_lib_path(argv)

    assert app.os.environ["DYLD_LIBRARY_PATH"].startswith("/opt/lib")


def test_relaunch_parses_config_equals_form(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"extra_lib_path": []}))
    argv = ["prog", "transcribe", "a.wav", "2", f"--config={cfg_path}"]
    with patch("app.os.execv") as mock_execv:
        app._relaunch_with_lib_path(argv)
    # No error means it found and parsed the file at the --config= path; the
    # empty extra_lib_path means it should still no-op rather than relaunch.
    mock_execv.assert_not_called()


def test_relaunch_uses_default_config_path_when_not_specified(monkeypatch, tmp_path):
    fallback = tmp_path / "default_config.json"  # deliberately left missing
    monkeypatch.setattr(app, "default_config_path", lambda: fallback)
    with patch("app.os.execv") as mock_execv:
        app._relaunch_with_lib_path(["prog", "transcribe", "a.wav", "2"])
    mock_execv.assert_not_called()


# --- config subcommand handlers ---


def test_config_path_prints_resolved_path(tmp_path, capsys):
    cfg_path = tmp_path / "config.json"
    args = app.parse_args(["prog", "config", "path", "--config", str(cfg_path)])
    exit_code = app._config_path(args)
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == str(cfg_path)


def test_config_show_prints_effective_config_with_secrets_masked(
    tmp_path, monkeypatch, capsys
):
    import config as config_module

    monkeypatch.setattr(
        config_module, "_REPO_DEFAULTS", tmp_path / "no_repo_defaults.json"
    )
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"hf_token": "abcdefgh1234", "language": "fr"}))

    args = app.parse_args(["prog", "config", "show", "--config", str(cfg_path)])
    exit_code = app._config_show(args)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert f"Config file: {cfg_path}" in out
    body = "\n".join(
        out.splitlines()[1:]
    )  # JSON body follows the "Config file: ..." line
    printed = json.loads(body)
    assert printed["language"] == "fr"
    assert printed["hf_token"] == "********1234"


def test_config_get_known_key(tmp_path, monkeypatch, capsys):
    import config as config_module

    monkeypatch.setattr(
        config_module, "_REPO_DEFAULTS", tmp_path / "no_repo_defaults.json"
    )
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"language": "fr"}))

    args = app.parse_args(
        ["prog", "config", "get", "language", "--config", str(cfg_path)]
    )
    exit_code = app._config_get(args)
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "fr"


def test_config_get_unknown_key_errors(tmp_path, capsys):
    cfg_path = tmp_path / "config.json"
    args = app.parse_args(
        ["prog", "config", "get", "not_a_real_key", "--config", str(cfg_path)]
    )
    exit_code = app._config_get(args)
    assert exit_code == 2
    assert "Unknown config key" in capsys.readouterr().err


def test_config_get_list_field_prints_json(tmp_path, monkeypatch, capsys):
    import config as config_module

    monkeypatch.setattr(
        config_module, "_REPO_DEFAULTS", tmp_path / "no_repo_defaults.json"
    )
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"extra_path": ["/a", "/b"]}))

    args = app.parse_args(
        ["prog", "config", "get", "extra_path", "--config", str(cfg_path)]
    )
    app._config_get(args)
    assert json.loads(capsys.readouterr().out) == ["/a", "/b"]


def test_config_set_writes_value_and_confirms(tmp_path, monkeypatch, capsys):
    import config as config_module

    monkeypatch.setattr(
        config_module, "_REPO_DEFAULTS", tmp_path / "no_repo_defaults.json"
    )
    cfg_path = tmp_path / "config.json"

    args = app.parse_args(
        ["prog", "config", "set", "model", "large-v3", "--config", str(cfg_path)]
    )
    exit_code = app._config_set(args)

    assert exit_code == 0
    assert json.loads(cfg_path.read_text())["model"] == "large-v3"
    assert "Set model = large-v3" in capsys.readouterr().out


def test_config_set_masks_secret_in_confirmation_output(tmp_path, monkeypatch, capsys):
    import config as config_module

    monkeypatch.setattr(
        config_module, "_REPO_DEFAULTS", tmp_path / "no_repo_defaults.json"
    )
    cfg_path = tmp_path / "config.json"

    args = app.parse_args(
        ["prog", "config", "set", "hf_token", "abcdefgh1234", "--config", str(cfg_path)]
    )
    app._config_set(args)

    out = capsys.readouterr().out
    assert "abcdefgh1234" not in out
    assert "********1234" in out
    # the raw value is still what's actually persisted to disk
    assert json.loads(cfg_path.read_text())["hf_token"] == "abcdefgh1234"


def test_config_set_unknown_key_errors_without_writing(tmp_path, capsys):
    cfg_path = tmp_path / "config.json"
    args = app.parse_args(
        ["prog", "config", "set", "not_a_real_key", "value", "--config", str(cfg_path)]
    )
    exit_code = app._config_set(args)
    assert exit_code == 2
    assert "Unknown config key" in capsys.readouterr().err
    assert not cfg_path.exists()


def test_config_set_invalid_int_value_errors(tmp_path, capsys):
    cfg_path = tmp_path / "config.json"
    args = app.parse_args(
        [
            "prog",
            "config",
            "set",
            "batch_size",
            "not-a-number",
            "--config",
            str(cfg_path),
        ]
    )
    exit_code = app._config_set(args)
    assert exit_code == 2
    assert "batch_size must be an integer" in capsys.readouterr().err


def test_config_set_list_field_coerces_from_comma_separated(tmp_path, monkeypatch):
    import config as config_module

    monkeypatch.setattr(
        config_module, "_REPO_DEFAULTS", tmp_path / "no_repo_defaults.json"
    )
    cfg_path = tmp_path / "config.json"

    args = app.parse_args(
        ["prog", "config", "set", "extra_path", "/a,/b", "--config", str(cfg_path)]
    )
    app._config_set(args)
    assert json.loads(cfg_path.read_text())["extra_path"] == ["/a", "/b"]


# --- run_config_command dispatch ---


def test_run_config_command_dispatches_to_correct_handler(tmp_path):
    cfg_path = tmp_path / "config.json"
    args = app.parse_args(["prog", "config", "path", "--config", str(cfg_path)])
    assert app.run_config_command(args) == 0


# --- main() top-level dispatch ---


def _record_call(called: dict, key: str, args) -> int:
    called[key] = True
    return 0


def test_main_dispatches_config_command(monkeypatch):
    called = {}
    monkeypatch.setattr(app, "_relaunch_with_lib_path", lambda argv: None)
    monkeypatch.setattr(
        app, "run_config_command", lambda args: _record_call(called, "config", args)
    )
    monkeypatch.setattr(
        app, "run_transcribe", lambda args: _record_call(called, "transcribe", args)
    )

    exit_code = app.main(["prog", "config", "path"])

    assert exit_code == 0
    assert called == {"config": True}


def test_main_dispatches_transcribe_by_default(monkeypatch):
    called = {}
    monkeypatch.setattr(app, "_relaunch_with_lib_path", lambda argv: None)
    monkeypatch.setattr(
        app, "run_config_command", lambda args: _record_call(called, "config", args)
    )
    monkeypatch.setattr(
        app, "run_transcribe", lambda args: _record_call(called, "transcribe", args)
    )

    exit_code = app.main(["prog", "file.wav", "3"])

    assert exit_code == 0
    assert called == {"transcribe": True}
