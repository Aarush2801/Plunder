"""Tests for ignition.config — load, save, TOML round-trip."""
import pytest
from pathlib import Path


def test_defaults_when_no_file(tmp_path, monkeypatch):
    """Config returns sensible defaults when config.toml doesn't exist."""
    import ignition.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")

    from ignition.config import load
    cfg = load()
    assert cfg.port == 6881
    assert cfg.http_port == 7889
    assert cfg.seed_ratio == 2.0
    assert cfg.max_upload_speed == 0
    assert cfg.max_download_speed == 0
    assert cfg.rss_feeds == []
    assert cfg.http_username == ""


def test_save_and_reload_scalar(tmp_path, monkeypatch):
    """save_value writes a scalar and load reads it back."""
    import ignition.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")

    from ignition.config import save_value, load
    save_value("port", "9999")
    cfg = load()
    assert cfg.port == 9999


def test_save_multiple_values(tmp_path, monkeypatch):
    """Multiple save_value calls accumulate in the same file."""
    import ignition.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")

    from ignition.config import save_value, load
    save_value("seed_ratio", "1.5")
    save_value("max_upload_speed", "512000")
    cfg = load()
    assert cfg.seed_ratio == 1.5
    assert cfg.max_upload_speed == 512000


def test_unknown_key_stored_as_string(tmp_path, monkeypatch):
    """An unrecognised key is stored as a string without crashing."""
    import ignition.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")

    from ignition.config import save_value
    save_value("future_option", "hello")  # should not raise


def test_load_existing_toml(tmp_path, monkeypatch):
    """load() picks up values written directly to the TOML file."""
    import ignition.config as cfg_mod
    config_path = tmp_path / "config.toml"
    config_path.write_text('port = 7000\nseed_ratio = 0.5\n')
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", config_path)

    from ignition.config import load
    cfg = load()
    assert cfg.port == 7000
    assert cfg.seed_ratio == 0.5
