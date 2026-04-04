#!/usr/bin/env python3
"""Phase 1 smoke test — run this to verify engine downloads before building UI."""
import sys
import time

sys.path.insert(0, "/opt/homebrew/lib/python3.14/site-packages")

from ignition.engine import TorrentEngine

MAGNET = (
    "magnet:?xt=urn:btih:5765415f3c9e74b8e2c48d104f8b8d3e4b59b734"
    "&dn=ubuntu-24.04.2-desktop-amd64.iso"
)

engine = TorrentEngine(download_dir="~/Downloads/Ignition/downloads")
print("Engine started. Adding magnet...")
tid = engine.add_magnet(MAGNET)
print(f"Torrent ID: {tid}")

try:
    for i in range(120):  # run for 2 minutes
        time.sleep(2)
        statuses = engine.get_status()
        alerts = engine.poll_alerts()
        for msg in alerts:
            print(f"  {msg}")
        for s in statuses:
            rate_mb = s.download_rate / 1_048_576
            dl_mb = s.downloaded / 1_048_576
            total_mb = s.total_size / 1_048_576 if s.total_size else 0
            print(
                f"[{i*2:3d}s] {s.state:12s} | "
                f"{s.progress*100:5.1f}% | "
                f"{rate_mb:6.2f} MB/s | "
                f"{dl_mb:.1f}/{total_mb:.1f} MB | "
                f"peers={s.num_peers}"
            )
        if statuses and statuses[0].progress > 0.001:
            print("\nDownloading confirmed!")
            break
except KeyboardInterrupt:
    print("\nStopped.")
finally:
    engine.shutdown()
