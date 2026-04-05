import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class Config:
    download_dir: str = str(Path.home() / "Downloads" / "Ignition" / "downloads")
    port: int = 6881
    http_port: int = 7889
    max_upload_speed: int = 0    # 0 = unlimited (bytes/sec)
    max_download_speed: int = 0  # 0 = unlimited (bytes/sec)
    seed_ratio: float = 2.0
    rss_feeds: List[str] = field(default_factory=list)
    rss_interval: int = 300      # seconds between RSS polls
    rss_patterns: List[str] = field(default_factory=list)  # regex filters (empty = accept all)
    http_username: str = ""      # basic auth (leave empty to disable)
    http_password: str = ""


def load() -> "Config":
    config_path = Path.home() / ".ignition" / "config.toml"
    if not config_path.exists():
        return Config()
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    return Config(
        download_dir=data.get("download_dir", Config.download_dir),
        port=data.get("port", Config.port),
        http_port=data.get("http_port", Config.http_port),
        max_upload_speed=data.get("max_upload_speed", Config.max_upload_speed),
        max_download_speed=data.get("max_download_speed", Config.max_download_speed),
        seed_ratio=data.get("seed_ratio", Config.seed_ratio),
        rss_feeds=data.get("rss_feeds", []),
        rss_interval=data.get("rss_interval", Config.rss_interval),
        rss_patterns=data.get("rss_patterns", []),
        http_username=data.get("http_username", Config.http_username),
        http_password=data.get("http_password", Config.http_password),
    )


CONFIG_PATH = Path.home() / ".ignition" / "config.toml"

_SCALAR_FIELDS = {
    "download_dir": str,
    "port": int,
    "http_port": int,
    "max_upload_speed": int,
    "max_download_speed": int,
    "seed_ratio": float,
    "rss_interval": int,
}


def save_value(key: str, value: str) -> None:
    """Write a single key=value to the config TOML file."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
    if key in _SCALAR_FIELDS:
        data[key] = _SCALAR_FIELDS[key](value)
    else:
        data[key] = value
    _write_toml(CONFIG_PATH, data)


def _write_toml(path: Path, data: dict) -> None:
    lines = []
    # scalars first, then lists
    for k, v in data.items():
        if isinstance(v, list):
            items = ", ".join(f'"{x}"' for x in v)
            lines.append(f"{k} = [{items}]")
        elif isinstance(v, str):
            lines.append(f'{k} = "{v}"')
        else:
            lines.append(f"{k} = {v}")
    path.write_text("\n".join(lines) + "\n")
