#!/usr/bin/env python3
"""
Neural-Rip v4.0 — Origami Protocol
qBittorrent post-processor: ANE hydration + zstd multi-threaded compression.

Usage (called by qBittorrent post-processing hook):
    python neural_rip.py --torrent-name <name> --save-path <path> [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
    from rich import print as rprint
    _RICH = True
except ImportError:
    _RICH = False

__version__ = "4.0.0"

BANNER = r"""
 _   _                      _   ____  _
| \ | | ___ _   _ _ __ __ _| | |  _ \(_)_ __
|  \| |/ _ \ | | | '__/ _` | | | |_) | | '_ \
| |\  |  __/ |_| | | | (_| | | |  _ <| | |_) |
|_| \_|\___|\__,_|_|  \__,_|_| |_| \_\_| .__/
                                         |_|
  v4.0 ⟡ Origami Protocol ⟡ ANE + zstd ⟡ 2026
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="neural_rip",
        description="Neural-Rip v4.0 — ANE hydration & zstd post-processor",
    )
    p.add_argument("--torrent-name", required=True, help="Torrent name (from qBittorrent)")
    p.add_argument("--save-path", required=True, type=Path, help="Download save path")
    p.add_argument("--model", type=Path, default=Path("models/upscale_4k.mlpackage"),
                   help="CoreML model bundle to use for hydration")
    p.add_argument("--zstd-threads", type=int, default=0,
                   help="zstd worker threads (0 = auto-detect all P-cores)")
    p.add_argument("--dry-run", action="store_true", help="Parse args and exit without processing")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def print_banner(console: "Console | None") -> None:
    if console:
        console.print(Panel(BANNER, style="bold cyan", border_style="bright_black"))
    else:
        print(BANNER)


def run(args: argparse.Namespace) -> int:
    console = Console() if _RICH else None
    print_banner(console)

    msg = f"[bold green]>>[/] Torrent : [cyan]{args.torrent_name}[/]"
    (console.print if console else print)(msg)

    msg2 = f"[bold green]>>[/] Path    : [cyan]{args.save_path}[/]"
    (console.print if console else print)(msg2)

    if args.dry_run:
        msg3 = "[yellow]>> DRY-RUN mode — exiting before processing.[/]"
        (console.print if console else print)(msg3)
        return 0

    # ── Phase 1: Hydration (CoreML / ANE) ────────────────────────────────────
    try:
        from scripts.hydrate import HydrationPipeline
        pipeline = HydrationPipeline(model_path=args.model)
        pipeline.run(save_path=args.save_path)
    except ImportError:
        _warn(console, "scripts.hydrate not yet implemented — skipping hydration phase.")

    # ── Phase 2: Compression (zstd) ───────────────────────────────────────────
    try:
        from scripts.compress import compress_tree
        compress_tree(root=args.save_path, threads=args.zstd_threads)
    except ImportError:
        _warn(console, "scripts.compress not yet implemented — skipping compression phase.")

    msg4 = "[bold green]>> [DONE][/] Neural-Rip complete."
    (console.print if console else print)(msg4)
    return 0


def _warn(console: "Console | None", text: str) -> None:
    msg = f"[yellow]>> [WARN][/] {text}"
    (console.print if console else print)(msg)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
