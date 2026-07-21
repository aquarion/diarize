import json

import pytest

import config

# --- mask_secret ---


def test_mask_secret_keeps_last_four_chars():
    assert config.mask_secret("hf_token", "abcdefgh1234") == "********1234"


def test_mask_secret_fully_masks_short_values():
    assert config.mask_secret("hf_token", "abc") == "***"


def test_mask_secret_leaves_non_secret_keys_untouched():
    assert config.mask_secret("language", "en") == "en"


def test_mask_secret_leaves_empty_string_untouched():
    assert config.mask_secret("hf_token", "") == ""


def test_mask_secret_leaves_non_string_values_untouched():
    assert config.mask_secret("batch_size", 4) == 4


# --- coerce_config_value ---


def test_coerce_config_value_int_field():
    assert config.coerce_config_value("batch_size", "8") == 8


def test_coerce_config_value_int_field_invalid_raises():
    with pytest.raises(ValueError, match="batch_size must be an integer"):
        config.coerce_config_value("batch_size", "not-a-number")


def test_coerce_config_value_list_field_splits_and_strips():
    assert config.coerce_config_value("extra_path", "/a, /b ,/c") == ["/a", "/b", "/c"]


def test_coerce_config_value_list_field_empty_string_yields_empty_list():
    assert config.coerce_config_value("extra_path", "") == []


def test_coerce_config_value_string_field_passthrough():
    assert config.coerce_config_value("language", "fr") == "fr"


def test_coerce_config_value_unknown_key_passthrough_as_string():
    assert config.coerce_config_value("totally_unknown", "value") == "value"


# --- default_config_path ---


def test_default_config_path_linux(monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setattr(config.Path, "home", lambda: config.Path("/home/u"))
    assert config.default_config_path() == config.Path(
        "/home/u/.config/diarize/config.json"
    )


def test_default_config_path_darwin(monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", lambda: config.Path("/Users/u"))
    assert config.default_config_path() == config.Path(
        "/Users/u/Library/Application Support/diarize/config.json"
    )


def test_default_config_path_windows_uses_appdata(monkeypatch):
    # Forward slashes throughout: backslash literals would only parse as
    # separators on WindowsPath, breaking this test on POSIX test runners.
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setattr(config.os, "name", "nt")
    monkeypatch.setenv("APPDATA", "/AppData/Roaming")
    assert config.default_config_path() == config.Path(
        "/AppData/Roaming/diarize/config.json"
    )


def test_default_config_path_windows_falls_back_without_appdata(monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setattr(config.os, "name", "nt")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(config.Path, "home", lambda: config.Path("/Users/u"))
    assert config.default_config_path() == config.Path(
        "/Users/u/AppData/Roaming/diarize/config.json"
    )


# --- ensure_default_config ---


def test_ensure_default_config_creates_file_with_defaults(tmp_path):
    cfg_path = tmp_path / "sub" / "config.json"
    config.ensure_default_config(cfg_path)
    assert cfg_path.exists()
    assert json.loads(cfg_path.read_text()) == config.DEFAULTS


def test_ensure_default_config_does_not_overwrite_existing(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"language": "fr"}')
    config.ensure_default_config(cfg_path)
    assert json.loads(cfg_path.read_text()) == {"language": "fr"}


# --- load_config_data ---


def test_load_config_data_merges_defaults_repo_and_user(tmp_path, monkeypatch):
    repo_defaults = tmp_path / "repo_defaults.json"
    repo_defaults.write_text(
        json.dumps({"language": "fr", "vault_path": "/repo/vault"})
    )
    monkeypatch.setattr(config, "_REPO_DEFAULTS", repo_defaults)

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"vault_path": "/user/vault"}))

    merged = config.load_config_data(cfg_path)
    assert merged["language"] == "fr"  # from repo defaults, overriding DEFAULTS
    assert merged["vault_path"] == "/user/vault"  # user config wins over repo defaults
    assert merged["model"] == config.DEFAULTS["model"]  # untouched base default


def test_load_config_data_tolerates_missing_repo_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_REPO_DEFAULTS", tmp_path / "does_not_exist.json")
    cfg_path = tmp_path / "config.json"
    merged = config.load_config_data(cfg_path)
    assert merged == config.DEFAULTS


def test_load_config_data_tolerates_invalid_repo_defaults_json(tmp_path, monkeypatch):
    repo_defaults = tmp_path / "repo_defaults.json"
    repo_defaults.write_text("not json")
    monkeypatch.setattr(config, "_REPO_DEFAULTS", repo_defaults)
    cfg_path = tmp_path / "config.json"
    merged = config.load_config_data(cfg_path)
    assert merged == config.DEFAULTS


def test_load_config_data_missing_user_config_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_REPO_DEFAULTS", tmp_path / "does_not_exist.json")
    merged = config.load_config_data(tmp_path / "no_config.json")
    assert merged == config.DEFAULTS


def test_load_config_data_invalid_user_json_exits(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "_REPO_DEFAULTS", tmp_path / "does_not_exist.json")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{not valid json")
    with pytest.raises(SystemExit) as exc_info:
        config.load_config_data(cfg_path)
    assert exc_info.value.code == 2
    assert "Invalid config" in capsys.readouterr().err


def test_load_config_data_non_object_user_json_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_REPO_DEFAULTS", tmp_path / "does_not_exist.json")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("[1, 2, 3]")
    with pytest.raises(SystemExit) as exc_info:
        config.load_config_data(cfg_path)
    assert exc_info.value.code == 2


# --- save_config_data ---


def test_save_config_data_creates_parent_dirs_and_writes_json(tmp_path):
    cfg_path = tmp_path / "a" / "b" / "config.json"
    config.save_config_data(cfg_path, {"language": "en"})
    assert json.loads(cfg_path.read_text()) == {"language": "en"}


# --- prompt_for_required_config ---


def test_prompt_for_required_config_skips_already_set_values(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_REPO_DEFAULTS", tmp_path / "does_not_exist.json")
    cfg_path = tmp_path / "config.json"
    data = dict(config.DEFAULTS)
    data["vault_path"] = "/already/set"
    data["hf_token"] = "already-set-token"
    result = config.prompt_for_required_config(cfg_path, data, non_interactive=True)
    assert result["vault_path"] == "/already/set"
    assert result["hf_token"] == "already-set-token"


def test_prompt_for_required_config_non_interactive_missing_required_exits(tmp_path):
    cfg_path = tmp_path / "config.json"
    data = dict(config.DEFAULTS)
    with pytest.raises(SystemExit) as exc_info:
        config.prompt_for_required_config(cfg_path, data, non_interactive=True)
    assert exc_info.value.code == 2


def test_prompt_for_required_config_skip_transcription_only_requires_vault(tmp_path):
    cfg_path = tmp_path / "config.json"
    data = dict(config.DEFAULTS)
    data["vault_path"] = "/set"
    # hf_token stays empty, but skip_transcription=True means it's not required.
    result = config.prompt_for_required_config(
        cfg_path, data, skip_transcription=True, non_interactive=True
    )
    assert result["vault_path"] == "/set"


def test_prompt_for_required_config_assemblyai_backend_requires_its_key(tmp_path):
    cfg_path = tmp_path / "config.json"
    data = dict(config.DEFAULTS)
    data["backend"] = "assemblyai"
    data["vault_path"] = "/set"
    with pytest.raises(SystemExit):
        config.prompt_for_required_config(cfg_path, data, non_interactive=True)


def test_prompt_for_required_config_interactive_fills_and_saves(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    data = dict(config.DEFAULTS)
    data["vault_path"] = ""  # simulate genuinely missing, not just the shipped default
    data["hf_token"] = "already-set"

    monkeypatch.setattr("builtins.input", lambda _prompt: "/typed/vault")
    result = config.prompt_for_required_config(cfg_path, data, non_interactive=False)

    assert result["vault_path"] == "/typed/vault"
    assert json.loads(cfg_path.read_text())["vault_path"] == "/typed/vault"


def test_prompt_for_required_config_interactive_reprompts_on_empty_input(
    tmp_path, monkeypatch
):
    cfg_path = tmp_path / "config.json"
    data = dict(config.DEFAULTS)
    data["vault_path"] = ""
    data["hf_token"] = "already-set"

    responses = iter(["", "", "/typed/vault"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
    result = config.prompt_for_required_config(cfg_path, data, non_interactive=False)

    assert result["vault_path"] == "/typed/vault"


# --- _resolve_path ---


def test_resolve_path_absolute_passthrough(tmp_path):
    absolute = tmp_path / "some" / "file.txt"
    assert config._resolve_path(tmp_path, str(absolute)) == absolute


def test_resolve_path_relative_resolves_against_base(tmp_path):
    result = config._resolve_path(tmp_path, "sub/file.txt")
    assert result == (tmp_path / "sub" / "file.txt").resolve()


def test_resolve_path_expands_user(tmp_path, monkeypatch):
    # Path.expanduser() reads HOME/USERPROFILE directly - it does not go
    # through the public Path.home() classmethod, so that can't be mocked.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    result = config._resolve_path(tmp_path, "~/file.txt")
    assert result == tmp_path / "file.txt"


# --- load_config ---


def test_load_config_builds_appconfig_from_merged_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_REPO_DEFAULTS", tmp_path / "does_not_exist.json")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "hf_token": "tok",
                "vault_path": "/vault",
                "batch_size": 8,
                "extra_path": ["/a", "/b"],
            }
        )
    )
    cfg = config.load_config(cfg_path)
    assert isinstance(cfg, config.AppConfig)
    assert cfg.hf_token == "tok"
    assert cfg.vault_path == "/vault"
    assert cfg.batch_size == 8
    assert cfg.extra_path == ["/a", "/b"]
    assert cfg.model == config.DEFAULTS["model"]  # default applied for unset key


def test_load_config_missing_file_uses_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_REPO_DEFAULTS", tmp_path / "does_not_exist.json")
    cfg = config.load_config(tmp_path / "missing.json")
    assert cfg.backend == config.DEFAULTS["backend"]
    assert cfg.num_speakers == config.DEFAULTS["num_speakers"]
