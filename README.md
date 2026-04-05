# PLUNDER

A terminal-based BitTorrent client with a Matrix-inspired TUI. Downloads magnet links and `.torrent` files with sequential piece ordering and streaming zstd decompression.

```
 ██████╗ ██╗     ██╗   ██╗███╗   ██╗██████╗ ███████╗██████╗
 ██╔══██╗██║     ██║   ██║████╗  ██║██╔══██╗██╔════╝██╔══██╗
 ██████╔╝██║     ██║   ██║██╔██╗ ██║██║  ██║█████╗  ██████╔╝
 ██╔═══╝ ██║     ██║   ██║██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗
 ██║     ███████╗╚██████╔╝██║ ╚████║██████╔╝███████╗██║  ██║
 ╚═╝     ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝
              [ S T E A L   F R O M   T H E   S E A ]
```

## What it does

- **Sequential downloading** — pieces arrive first-to-last so content is playable before the download finishes
- **Streaming zstd decompression** — `.zst` files decompress chunk-by-chunk as pieces land, not after
- **Live TUI dashboard** — piece map, throughput sparkline, peer count, ETA, scrolling log
- **Session persistence** — resume downloads across restarts via libtorrent's resume data
- **Matrix rain** — because why not

## Install

**macOS:**
```bash
brew install libtorrent-rasterbar
pip install textual pyzstd click
```

**Ubuntu/Debian:**
```bash
sudo apt install python3-libtorrent
pip install textual pyzstd click
```

Clone the repo:
```bash
git clone https://github.com/Aarush2801/Ignition.git
cd Ignition
```

## Usage

```bash
# Launch dashboard
python3 ignition.py

# Add a magnet link
python3 ignition.py add "magnet:?xt=urn:btih:..."

# Add a .torrent file
python3 ignition.py add /path/to/file.torrent
```

**Keybindings:**

| Key | Action |
|-----|--------|
| `a` | Add torrent or magnet link |
| `p` | Pause selected |
| `r` | Resume selected |
| `d` | Delete selected |
| `q` | Quit |
| `↑` / `↓` | Select torrent |

## Architecture

```
ignition/
├── ignition.py          # CLI entry point (click)
├── ignition/
│   ├── engine.py        # TorrentEngine — wraps libtorrent session
│   ├── decompressor.py  # StreamingDecompressor — pyzstd pipeline
│   ├── config.py        # ~/.ignition/config.toml loading
│   └── ui/
│       ├── app.py       # Textual App, keybindings, refresh loop
│       ├── widgets.py   # PieceMapWidget, ThroughputWidget, LogWidget
│       ├── rain.py      # MatrixRain background widget
│       ├── banner.py    # ASCII art
│       └── theme.py     # Matrix color scheme + CSS
```

**Engine** runs libtorrent with DHT, PEX, and LSD enabled. Alerts are drained on the main thread every 500ms alongside the UI refresh to avoid cross-thread issues with the Python/C++ bindings.

**Decompressor** maintains a per-file `pyzstd.ZstdDecompressor` instance. When a piece completes, the engine reads the bytes off disk and feeds them into the decompressor, which writes the output incrementally to the destination file.

**UI** polls engine state every 500ms via Textual's `set_interval`. The piece map samples the libtorrent piece bitmask and renders it as a unicode block grid. The throughput sparkline keeps a 20-sample rolling window.

## Config

Create `~/.ignition/config.toml` to override defaults:

```toml
download_dir = "~/Downloads"
port = 6881
max_upload_speed = 0   # bytes/sec, 0 = unlimited
seed_ratio = 2.0
```

## Why not just use qBittorrent?

qBittorrent uses random piece selection by default, which is correct for swarm health but means you can't use content until 100% complete. Plunder trades swarm efficiency for time-to-usable-data — useful when downloading large compressed archives where you want to start processing output before the transfer finishes.

## Dependencies

- [libtorrent-rasterbar](https://libtorrent.org/) — BitTorrent engine
- [Textual](https://github.com/Textualize/textual) — terminal UI framework
- [pyzstd](https://github.com/animalize/pyzstd) — streaming zstd decompression
- [click](https://click.palletsprojects.com/) — CLI argument parsing
