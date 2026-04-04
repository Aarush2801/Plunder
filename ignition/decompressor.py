import os
import threading
from pathlib import Path

import pyzstd


class StreamingDecompressor:
    """Watches for .zst files and decompresses as data arrives."""

    def __init__(self, watch_dir: str):
        self._watch_dir = watch_dir
        self._lock = threading.Lock()
        self._active: dict[str, tuple[pyzstd.ZstdDecompressor, object]] = {}

    def is_zst_file(self, filename: str) -> bool:
        return filename.endswith(".zst")

    def on_piece_complete(self, filepath: str, offset: int, data: bytes) -> str | None:
        """Feed a completed piece into the streaming decompressor. Returns a log message or None."""
        if not self.is_zst_file(filepath):
            return None

        out_path = filepath[:-4]  # strip .zst
        is_new = False
        with self._lock:
            if filepath not in self._active:
                self._active[filepath] = (pyzstd.ZstdDecompressor(), open(out_path, "wb"))
                is_new = True
            decompressor, out_file = self._active[filepath]

        try:
            chunk = decompressor.decompress(data)
            if chunk:
                out_file.write(chunk)
                out_file.flush()
            name = Path(filepath).name
            if is_new:
                return f"[zstd] streaming decompression started → {Path(out_path).name}"
            return f"[zstd] piece decompressed — {len(chunk):,} bytes → {Path(out_path).name}"
        except Exception:
            return None

    def close_all(self):
        with self._lock:
            for _, (_, f) in self._active.items():
                try:
                    f.close()
                except Exception:
                    pass
            self._active.clear()
