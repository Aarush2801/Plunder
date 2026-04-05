#!/usr/bin/env python3
import sys
import os

# Add libtorrent to path (macOS brew install)
sys.path.insert(0, "/opt/homebrew/lib/python3.14/site-packages")

import click
from ignition.config import load as load_config
from ignition.engine import TorrentEngine
from ignition.decompressor import StreamingDecompressor
from ignition.ui.app import IgnitionApp


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """PLUNDER — steal from the sea."""
    if ctx.invoked_subcommand is None:
        _launch(initial_magnet=None)


@cli.command()
@click.argument("magnet_or_path")
def add(magnet_or_path: str):
    """Add a magnet link or .torrent file and launch the dashboard."""
    _launch(initial_magnet=magnet_or_path)


def _launch(initial_magnet: str | None):
    config = load_config()
    engine = TorrentEngine(
        download_dir=config.download_dir,
        port_range=(config.port, config.port + 10),
        max_upload_speed=config.max_upload_speed,
        max_download_speed=config.max_download_speed,
    )
    decompressor = StreamingDecompressor(watch_dir=config.download_dir)
    engine.set_decompressor(decompressor)
    app = IgnitionApp(engine=engine, initial_magnet=initial_magnet, http_port=config.http_port)
    try:
        app.run()
    finally:
        decompressor.close_all()


if __name__ == "__main__":
    cli()
