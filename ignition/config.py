import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    download_dir: str = str(Path.home() / "Downloads" / "Ignition" / "downloads")
    port: int = 6881
    max_upload_speed: int = 0  # 0 = unlimited (bytes/sec)
    seed_ratio: float = 2.0


def load() -> Config:
    config_path = Path.home() / ".ignition" / "config.toml"
    if not config_path.exists():
        return Config()
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    return Config(
        download_dir=data.get("download_dir", Config.download_dir),
        port=data.get("port", Config.port),
        max_upload_speed=data.get("max_upload_speed", Config.max_upload_speed),
        seed_ratio=data.get("seed_ratio", Config.seed_ratio),
    )
