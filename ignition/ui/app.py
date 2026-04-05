from __future__ import annotations

import subprocess
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static, DataTable, Input, Label
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.binding import Binding

from ignition.engine import TorrentEngine, TorrentStatus
from ignition.server import StreamServer
from ignition.ui.banner import IGNITION_ASCII
from ignition.ui.rain import MatrixRain
from ignition.ui.theme import CSS
from ignition.ui.widgets import (
    PieceMapWidget, ThroughputWidget, LogWidget,
    fmt_bytes, fmt_rate, fmt_eta,
)

COMPLETE_ART = """
██████╗ ███████╗ ██████╗ ██████╗ ███╗   ██╗███████╗████████╗██████╗ ██╗   ██╗ ██████╗████████╗██╗ ██████╗ ███╗   ██╗
██╔══██╗██╔════╝██╔════╝██╔═══██╗████╗  ██║██╔════╝╚══██╔══╝██╔══██╗██║   ██║██╔════╝╚══██╔══╝██║██╔═══██╗████╗  ██║
██████╔╝█████╗  ██║     ██║   ██║██╔██╗ ██║███████╗   ██║   ██████╔╝██║   ██║██║        ██║   ██║██║   ██║██╔██╗ ██║
██╔══██╗██╔══╝  ██║     ██║   ██║██║╚██╗██║╚════██║   ██║   ██╔══██╗██║   ██║██║        ██║   ██║██║   ██║██║╚██╗██║
██║  ██║███████╗╚██████╗╚██████╔╝██║ ╚████║███████║   ██║   ██║  ██║╚██████╔╝╚██████╗   ██║   ██║╚██████╔╝██║ ╚████║
╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝  ╚═════╝   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═══╝
                                C O M P L E T E
"""


class DownloadCompleteScreen(ModalScreen):
    def __init__(self, torrent_name: str):
        super().__init__()
        self._name = torrent_name

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold bright_green]{COMPLETE_ART}[/]\n[green]{self._name}[/]",
            id="complete-banner",
            markup=True,
        )

    def on_mount(self):
        self.set_timer(3.0, self._auto_dismiss)

    def _auto_dismiss(self):
        self.dismiss()

    def on_click(self):
        self.dismiss()

    def key_escape(self):
        self.dismiss()


class AddMagnetScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, prefill: str = ""):
        super().__init__()
        self._prefill = prefill

    def compose(self) -> ComposeResult:
        hint = " [dim][clipboard detected][/]" if self._prefill else ""
        with Vertical(id="dialog"):
            yield Label(f"[bold green]ADD TORRENT[/]  (magnet link or .torrent path){hint}", markup=True)
            yield Input(value=self._prefill, placeholder="magnet:?xt=... or /path/to/file.torrent", id="magnet-input")
            yield Label("[dim]Press Enter to add, Esc to cancel[/]", markup=True)

    def on_input_submitted(self, event: Input.Submitted):
        self.dismiss(event.value.strip())


class IgnitionApp(App):
    CSS = CSS
    TITLE = "PLUNDER"
    BINDINGS = [
        Binding("a", "add_torrent", "Add"),
        Binding("p", "pause_selected", "Pause"),
        Binding("r", "resume_selected", "Resume"),
        Binding("d", "delete_selected", "Delete"),
        Binding("q", "quit", "Quit"),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
    ]

    def __init__(self, engine: TorrentEngine, initial_magnet: str | None = None, http_port: int = 7889):
        super().__init__()
        self.engine = engine
        self._initial_magnet = initial_magnet
        self._selected_idx = 0
        self._statuses: list[TorrentStatus] = []
        self._completed: set[str] = set()
        self._stream_server = StreamServer(engine, port=http_port)
        self._http_port = http_port
        self._watch_dir = Path.home() / ".ignition" / "watch"
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._watched: set[str] = set()

    def compose(self) -> ComposeResult:
        yield MatrixRain()
        yield Static(IGNITION_ASCII, id="banner", markup=False)
        yield Static("─── ACTIVE TRANSFERS ───────────────────────────────────────", id="transfers-label", markup=False)
        with Vertical(id="table-container"):
            table = DataTable(id="downloads-table", show_cursor=True)
            table.add_columns("NAME", "SIZE", "PROGRESS", "%", "DOWN", "UP", "PEERS", "ETA", "STATE")
            yield table
        with Horizontal(id="bottom-row"):
            yield PieceMapWidget(id="piece-panel")
            yield ThroughputWidget(id="throughput-panel")
        yield LogWidget(id="log-panel")
        yield Static(
            "  [a]dd   [p]ause   [r]esume   [d]elete   [q]uit  ",
            id="keybinds", markup=False,
        )

    async def on_mount(self):
        if self._initial_magnet:
            try:
                if self._initial_magnet.startswith("magnet:"):
                    tid = self.engine.add_magnet(self._initial_magnet)
                else:
                    tid = self.engine.add_torrent_file(self._initial_magnet)
                self.query_one("#log-panel", LogWidget).append([f"[+] Added: {tid[:16]}..."])
            except Exception as e:
                self.query_one("#log-panel", LogWidget).append([f"[!] Error: {e}"])
        await self._stream_server.start()
        self.query_one("#log-panel", LogWidget).append(
            [f"[stream] http://127.0.0.1:{self._http_port}/"]
        )
        self.set_interval(0.5, self._refresh)

    def _poll_watch_dir(self):
        for torrent_file in self._watch_dir.glob("*.torrent"):
            key = str(torrent_file)
            if key not in self._watched:
                self._watched.add(key)
                try:
                    tid = self.engine.add_torrent_file(str(torrent_file))
                    self.query_one("#log-panel", LogWidget).append(
                        [f"[watch] auto-added: {torrent_file.name}"]
                    )
                    torrent_file.rename(torrent_file.with_suffix(".torrent.added"))
                except Exception as e:
                    self.query_one("#log-panel", LogWidget).append(
                        [f"[watch] failed to add {torrent_file.name}: {e}"]
                    )

    def _refresh(self):
        self._poll_watch_dir()
        self._statuses = self.engine.get_status()
        alerts = self.engine.poll_alerts()

        # Detect newly completed torrents
        for s in self._statuses:
            if s.state == "seeding" and s.id not in self._completed and s.progress >= 1.0:
                self._completed.add(s.id)
                self.push_screen(DownloadCompleteScreen(s.name))

        table = self.query_one("#downloads-table", DataTable)
        table.clear()

        total_rate = 0
        for s in self._statuses:
            total_rate += s.download_rate
            progress_chars = int(s.progress * 10)
            prog_str = "█" * progress_chars + "░" * (10 - progress_chars)
            size_str = fmt_bytes(s.total_size) if s.total_size else "..."
            table.add_row(
                s.name[:30],
                size_str,
                prog_str,
                f"{s.progress*100:5.1f}%",
                fmt_rate(s.download_rate),
                fmt_rate(s.upload_rate),
                str(s.num_peers),
                fmt_eta(s.eta_seconds),
                s.state.upper(),
            )

        if self._statuses:
            self._selected_idx = min(self._selected_idx, len(self._statuses) - 1)
            try:
                self.query_one("#downloads-table", DataTable).move_cursor(row=self._selected_idx)
            except Exception:
                pass

        piece_widget = self.query_one("#piece-panel", PieceMapWidget)
        if self._statuses and 0 <= self._selected_idx < len(self._statuses):
            piece_widget.update_status(self._statuses[self._selected_idx])
        else:
            piece_widget.update_status(None)

        self.query_one("#throughput-panel", ThroughputWidget).update_rate(total_rate)

        if alerts:
            self.query_one("#log-panel", LogWidget).append(alerts)

    def action_add_torrent(self):
        clipboard = self._read_clipboard()
        prefill = clipboard if clipboard and clipboard.startswith("magnet:") else ""

        def on_result(value: str | None):
            if not value:
                return
            try:
                if value.startswith("magnet:"):
                    tid = self.engine.add_magnet(value)
                else:
                    tid = self.engine.add_torrent_file(value)
                self.query_one("#log-panel", LogWidget).append([f"[+] Added: {tid[:16]}..."])
            except Exception as e:
                self.query_one("#log-panel", LogWidget).append([f"[!] Error: {e}"])

        self.push_screen(AddMagnetScreen(prefill=prefill), on_result)

    def _read_clipboard(self) -> str:
        try:
            result = subprocess.run(
                ["pbpaste"], capture_output=True, text=True, timeout=1
            )
            return result.stdout.strip()
        except Exception:
            return ""

    def action_pause_selected(self):
        if self._statuses and 0 <= self._selected_idx < len(self._statuses):
            self.engine.pause(self._statuses[self._selected_idx].id)

    def action_resume_selected(self):
        if self._statuses and 0 <= self._selected_idx < len(self._statuses):
            self.engine.resume(self._statuses[self._selected_idx].id)

    def action_delete_selected(self):
        if self._statuses and 0 <= self._selected_idx < len(self._statuses):
            self.engine.remove(self._statuses[self._selected_idx].id)
            self._selected_idx = max(0, self._selected_idx - 1)

    def action_move_up(self):
        self._selected_idx = max(0, self._selected_idx - 1)

    def action_move_down(self):
        if self._statuses:
            self._selected_idx = min(len(self._statuses) - 1, self._selected_idx + 1)

    async def action_quit(self):
        await self._stream_server.stop()
        self.engine.shutdown()
        self.exit()
