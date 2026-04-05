import threading
from pathlib import Path

import pyzstd

_FLUSH_THRESHOLD = 4 * 1024 * 1024  # flush to disk every 4MB


class _FileDecompressor:
    """Decompressor + buffered writer for a single .zst file."""

    def __init__(self, out_path: str):
        self._decompressor = pyzstd.ZstdDecompressor()
        self._file = open(out_path, "wb")
        self._buf = bytearray()

    def feed(self, data: bytes) -> int:
        """Decompress chunk, buffer output. Returns bytes written to buffer."""
        chunk = self._decompressor.decompress(data)
        if not chunk:
            return 0
        self._buf += chunk
        if len(self._buf) >= _FLUSH_THRESHOLD:
            self._flush()
        return len(chunk)

    def _flush(self):
        if self._buf:
            self._file.write(self._buf)
            self._file.flush()
            self._buf.clear()

    def close(self):
        self._flush()
        self._file.close()


class StreamingDecompressor:
    """Watches for .zst files and decompresses as data arrives."""

    def __init__(self, watch_dir: str):
        self._watch_dir = watch_dir
        self._lock = threading.Lock()
        self._active: dict[str, _FileDecompressor] = {}

    def is_zst_file(self, filename: str) -> bool:
        return filename.endswith(".zst")

    def on_piece_complete(self, filepath: str, offset: int, data: bytes) -> str | None:
        """Feed a completed piece into the streaming decompressor. Returns a log message or None."""
        if not self.is_zst_file(filepath):
            return None

        out_path = filepath[:-4]
        is_new = False
        with self._lock:
            if filepath not in self._active:
                self._active[filepath] = _FileDecompressor(out_path)
                is_new = True
            fd = self._active[filepath]

        try:
            n = fd.feed(data)
            out_name = Path(out_path).name
            if is_new:
                return f"[zstd] streaming decompression started → {out_name}"
            if n:
                return f"[zstd] {n:,} bytes decompressed → {out_name}"
            return None
        except Exception:
            return None

    def close_all(self):
        with self._lock:
            for fd in self._active.values():
                try:
                    fd.close()
                except Exception:
                    pass
            self._active.clear()
