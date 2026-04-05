#!/usr/bin/env python3
import sys
import asyncio

# Add libtorrent to path (macOS brew install)
sys.path.insert(0, "/opt/homebrew/lib/python3.14/site-packages")

import click
from ignition.config import load as load_config, save_value, CONFIG_PATH, _SCALAR_FIELDS
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


@cli.command("config")
@click.option("--set", "set_kv", multiple=True, metavar="KEY=VALUE",
              help="Set a config value, e.g. --set seed_ratio=1.5")
def config_cmd(set_kv):
    """Show or modify configuration.

    \b
    Keys:  download_dir, port, http_port, max_upload_speed,
           max_download_speed, seed_ratio, rss_interval
    """
    if not set_kv:
        cfg = load_config()
        click.echo(f"Config file: {CONFIG_PATH}")
        click.echo("")
        fields = [
            "download_dir", "port", "http_port",
            "max_upload_speed", "max_download_speed",
            "seed_ratio", "rss_interval", "rss_feeds", "rss_patterns",
        ]
        for f in fields:
            click.echo(f"  {f:<22} = {getattr(cfg, f)!r}")
        return

    for kv in set_kv:
        if "=" not in kv:
            click.echo(f"[!] Invalid: {kv!r}  (expected KEY=VALUE)", err=True)
            continue
        k, v = kv.split("=", 1)
        k = k.strip()
        try:
            save_value(k, v.strip())
            click.echo(f"  {k} = {v.strip()!r}  ✓")
        except Exception as e:
            click.echo(f"[!] {k}: {e}", err=True)


@cli.command()
@click.option("--port", default=None, type=int, help="Override HTTP port")
def daemon(port: int | None):
    """Run engine headless (no TUI) — streaming server + RSS only."""
    from ignition.server import StreamServer
    from ignition.rss import RSSWatcher

    config = load_config()
    http_port = port or config.http_port
    engine = TorrentEngine(
        download_dir=config.download_dir,
        port_range=(config.port, config.port + 10),
        max_upload_speed=config.max_upload_speed,
        max_download_speed=config.max_download_speed,
        seed_ratio=config.seed_ratio,
    )
    decompressor = StreamingDecompressor(watch_dir=config.download_dir)
    engine.set_decompressor(decompressor)

    async def run():
        server = StreamServer(engine, port=http_port)
        await server.start()
        click.echo(f"[daemon] streaming at http://127.0.0.1:{http_port}/")

        rss = RSSWatcher(
            engine,
            feeds=config.rss_feeds,
            interval=config.rss_interval,
            patterns=config.rss_patterns,
        )
        await rss.start()

        try:
            while True:
                await asyncio.sleep(1)
                engine.poll_alerts()
                engine.enforce_ratios()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await rss.stop()
            await server.stop()
            engine.shutdown()
            decompressor.close_all()
            click.echo("[daemon] stopped")

    asyncio.run(run())


def _launch(initial_magnet: str | None):
    from ignition.rss import RSSWatcher
    config = load_config()
    engine = TorrentEngine(
        download_dir=config.download_dir,
        port_range=(config.port, config.port + 10),
        max_upload_speed=config.max_upload_speed,
        max_download_speed=config.max_download_speed,
        seed_ratio=config.seed_ratio,
    )
    decompressor = StreamingDecompressor(watch_dir=config.download_dir)
    engine.set_decompressor(decompressor)
    rss = RSSWatcher(
        engine,
        feeds=config.rss_feeds,
        interval=config.rss_interval,
        patterns=config.rss_patterns,
    )
    app = IgnitionApp(
        engine=engine,
        initial_magnet=initial_magnet,
        http_port=config.http_port,
        rss_watcher=rss,
    )
    try:
        app.run()
    finally:
        decompressor.close_all()


if __name__ == "__main__":
    cli()
