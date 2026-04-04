from collections import deque

from textual.widget import Widget
from textual.widgets import Static
from textual.reactive import reactive

from ignition.engine import TorrentStatus
from ignition.ui.theme import MATRIX_GREEN, MATRIX_DIM, AMBER, WHITE


def fmt_bytes(n: int) -> str:
    if n < 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_rate(bps: int) -> str:
    if bps <= 0:
        return "  —  "
    mb = bps / 1_048_576
    return f"{mb:.1f} MB/s"


def fmt_eta(secs: int | None) -> str:
    if secs is None or secs <= 0:
        return "  —  "
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs//60}m{secs%60:02d}s"
    return f"{secs//3600}h{(secs%3600)//60}m"


def make_progress_bar(progress: float, width: int = 12) -> str:
    filled = int(progress * width)
    empty = width - filled
    return f"[green]{'█' * filled}[/][dim]{'░' * empty}[/]"


class PieceMapWidget(Static):
    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._status: TorrentStatus | None = None

    def update_status(self, status: TorrentStatus | None):
        self._status = status
        self._redraw()

    def _redraw(self):
        if self._status is None:
            self.update("[dim]No torrent selected[/]")
            return

        s = self._status
        name = s.name[:40] if s.name else "Unknown"
        header = f"[bold]PIECE MAP[/] [{name}]\n"

        piece_map = s.piece_map
        if not piece_map:
            # Show progress as a simple bar when no piece data
            bar_len = 60
            filled = int(s.progress * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            self.update(header + f"[green]{bar}[/]")
            return

        # Render piece map as a grid, max 64 pieces wide
        cols = 64
        rows_needed = max(1, (len(piece_map) + cols - 1) // cols)
        # Downsample to fit in cols
        total = len(piece_map)
        step = max(1, total // (cols * rows_needed))
        sampled = piece_map[::step][:cols * rows_needed]

        lines = []
        for row in range(rows_needed):
            chunk = sampled[row * cols:(row + 1) * cols]
            line = ""
            for p in chunk:
                line += "[green]█[/]" if p else "[dim]░[/]"
            lines.append(line)

        self.update(header + "\n".join(lines))


class ThroughputWidget(Static):
    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._history: deque[int] = deque(maxlen=20)
        self._peak = 0

    SPARK_CHARS = " ▁▂▃▄▅▆▇█"

    def update_rate(self, bps: int):
        self._history.append(bps)
        if bps > self._peak:
            self._peak = bps
        self._redraw()

    def _redraw(self):
        if not self._history:
            self.update("[dim]THROUGHPUT[/]\n—")
            return

        mx = max(self._history) or 1
        spark = ""
        for v in self._history:
            idx = int((v / mx) * (len(self.SPARK_CHARS) - 1))
            spark += f"[green]{self.SPARK_CHARS[idx]}[/]"

        current = self._history[-1] / 1_048_576
        peak = self._peak / 1_048_576
        text = (
            f"[bold]THROUGHPUT[/]\n"
            f"{spark}\n"
            f"now:  {current:.1f} MB/s\n"
            f"peak: {peak:.1f} MB/s"
        )
        self.update(text)


class LogWidget(Static):
    MAX_LINES = 200

    def __init__(self, **kwargs):
        super().__init__("", markup=True, **kwargs)
        self._lines: list[str] = []

    def append(self, messages: list[str]):
        if not messages:
            return
        self._lines.extend(messages)
        if len(self._lines) > self.MAX_LINES:
            self._lines = self._lines[-self.MAX_LINES:]
        visible = self._lines[-8:]
        text = "\n".join(f"[dim]>[/] {ln}" for ln in visible)
        self.update(text)
