import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, "/opt/homebrew/lib/python3.14/site-packages")
import libtorrent as lt

if TYPE_CHECKING:
    from ignition.decompressor import StreamingDecompressor


@dataclass
class TorrentStatus:
    id: str
    name: str
    progress: float
    download_rate: int
    upload_rate: int
    num_peers: int
    num_seeds: int
    total_size: int
    downloaded: int
    state: str
    eta_seconds: int | None
    piece_map: list[bool] = field(default_factory=list)


_STATE_MAP = {
    lt.torrent_status.checking_files: "checking",
    lt.torrent_status.downloading_metadata: "metadata",
    lt.torrent_status.downloading: "downloading",
    lt.torrent_status.finished: "seeding",
    lt.torrent_status.seeding: "seeding",
    lt.torrent_status.allocating: "allocating",
    lt.torrent_status.checking_resume_data: "checking",
}

_LOG_ALERTS = {
    "torrent_added_alert",
    "torrent_finished_alert",
    "peer_connect_alert",
    "listen_succeeded_alert",
    "tracker_reply_alert",
    "tracker_error_alert",
    "piece_finished_alert",
    "metadata_received_alert",
    "torrent_error_alert",
}


class TorrentEngine:
    def __init__(
        self,
        download_dir: str,
        port_range: tuple[int, int] = (6881, 6891),
        max_upload_speed: int = 0,
        max_download_speed: int = 0,
        seed_ratio: float = 0.0,
    ):
        self.download_dir = str(Path(download_dir).expanduser())
        Path(self.download_dir).mkdir(parents=True, exist_ok=True)

        settings = lt.default_settings()
        settings["listen_interfaces"] = f"0.0.0.0:{port_range[0]}"
        settings["enable_dht"] = True
        settings["enable_lsd"] = True
        settings["enable_upnp"] = True
        settings["enable_natpmp"] = True
        settings["announce_to_all_trackers"] = True
        settings["announce_to_all_tiers"] = True
        if max_upload_speed > 0:
            settings["upload_rate_limit"] = max_upload_speed
        if max_download_speed > 0:
            settings["download_rate_limit"] = max_download_speed
        self._session = lt.session(settings)
        self._seed_ratio = seed_ratio

        for host, port in [
            ("router.bittorrent.com", 6881),
            ("router.utorrent.com", 6881),
            ("dht.transmissionbt.com", 6881),
            ("dht.libtorrent.org", 25401),
        ]:
            self._session.add_dht_node((host, port))
        self._session.start_dht()

        self._handles: dict[str, lt.torrent_handle] = {}
        self._decompressor: "StreamingDecompressor | None" = None

        self._resume_dir = Path.home() / ".ignition" / "resume"
        self._resume_dir.mkdir(parents=True, exist_ok=True)
        self._load_session()

    def set_decompressor(self, decompressor: "StreamingDecompressor"):
        self._decompressor = decompressor

    # ------------------------------------------------------------------ #
    # Session persistence                                                  #
    # ------------------------------------------------------------------ #

    def _load_session(self):
        for resume_file in self._resume_dir.glob("*.resume"):
            try:
                params = lt.read_resume_data(resume_file.read_bytes())
                params.save_path = self.download_dir
                handle = self._session.add_torrent(params)
                handle.set_sequential_download(True)
                self._handles[str(handle.info_hash())] = handle
            except Exception:
                pass

    def _save_session(self):
        handles = [h for h in self._handles.values() if h.is_valid()]
        for h in handles:
            try:
                h.save_resume_data(
                    lt.torrent_handle.save_info_dict |
                    lt.torrent_handle.only_if_modified
                )
            except Exception:
                pass

        deadline = time.monotonic() + 5.0
        pending = len(handles)
        while pending > 0 and time.monotonic() < deadline:
            self._session.wait_for_alert(200)
            for a in self._session.pop_alerts():
                name = type(a).__name__
                if name == "save_resume_data_alert":
                    try:
                        ih = str(a.handle.info_hash())
                        data = lt.write_resume_data_buf(a.params)
                        (self._resume_dir / f"{ih}.resume").write_bytes(data)
                    except Exception:
                        pass
                    pending -= 1
                elif name == "save_resume_data_failed_alert":
                    pending -= 1

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def add_magnet(self, uri: str) -> str:
        params = lt.parse_magnet_uri(uri)
        params.save_path = self.download_dir
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        handle = self._session.add_torrent(params)
        handle.set_sequential_download(True)
        tid = str(handle.info_hash())
        self._handles[tid] = handle
        return tid

    def add_torrent_file(self, path: str) -> str:
        info = lt.torrent_info(str(Path(path).expanduser()))
        params = lt.add_torrent_params()
        params.ti = info
        params.save_path = self.download_dir
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        handle = self._session.add_torrent(params)
        handle.set_sequential_download(True)
        tid = str(handle.info_hash())
        self._handles[tid] = handle
        return tid

    def get_status(self) -> list[TorrentStatus]:
        statuses = []
        for tid, handle in list(self._handles.items()):
            if not handle.is_valid():
                continue
            try:
                s = handle.status()
                state = _STATE_MAP.get(s.state, "unknown")
                if s.paused:
                    state = "paused"

                eta = None
                if s.download_rate > 0 and s.total_wanted > s.total_wanted_done:
                    eta = int((s.total_wanted - s.total_wanted_done) / s.download_rate)

                piece_map: list[bool] = []
                try:
                    ti = handle.torrent_file()
                    if ti:
                        bitmask = handle.status(lt.torrent_handle.query_pieces).pieces
                        piece_map = list(bitmask)
                except Exception:
                    pass

                statuses.append(TorrentStatus(
                    id=tid,
                    name=s.name if s.name else tid[:16],
                    progress=s.progress,
                    download_rate=int(s.download_rate),
                    upload_rate=int(s.upload_rate),
                    num_peers=s.num_peers,
                    num_seeds=s.num_seeds,
                    total_size=s.total_wanted,
                    downloaded=s.total_wanted_done,
                    state=state,
                    eta_seconds=eta,
                    piece_map=piece_map,
                ))
            except Exception:
                pass
        return statuses

    def pause(self, torrent_id: str):
        if h := self._handles.get(torrent_id):
            h.pause()

    def resume(self, torrent_id: str):
        if h := self._handles.get(torrent_id):
            h.resume()

    def remove(self, torrent_id: str, delete_files: bool = False):
        if h := self._handles.get(torrent_id):
            flags = lt.session.delete_files if delete_files else 0
            self._session.remove_torrent(h, flags)
            del self._handles[torrent_id]

    # ------------------------------------------------------------------ #
    # Streaming server helpers                                            #
    # ------------------------------------------------------------------ #

    def get_files(self, info_hash: str) -> list[dict]:
        h = self._handles.get(info_hash)
        if not h or not h.is_valid():
            return []
        try:
            ti = h.torrent_file()
            if not ti:
                return []
            files = ti.files()
            result = []
            for i in range(files.num_files()):
                result.append({
                    "index": i,
                    "name": files.file_name(i),
                    "size": files.file_size(i),
                    "path": str(Path(self.download_dir) / files.file_path(i)),
                })
            return result
        except Exception:
            return []

    def get_file_info(self, info_hash: str, file_index: int) -> dict | None:
        h = self._handles.get(info_hash)
        if not h or not h.is_valid():
            return None
        try:
            ti = h.torrent_file()
            if not ti:
                return None
            files = ti.files()
            if file_index >= files.num_files():
                return None
            return {
                "size": files.file_size(file_index),
                "path": str(Path(self.download_dir) / files.file_path(file_index)),
            }
        except Exception:
            return None

    def have_piece(self, info_hash: str, piece_idx: int) -> bool:
        h = self._handles.get(info_hash)
        if not h or not h.is_valid():
            return False
        try:
            return h.have_piece(piece_idx)
        except Exception:
            return False

    def hint_piece_urgency(self, info_hash: str, piece_idx: int):
        h = self._handles.get(info_hash)
        if not h or not h.is_valid():
            return
        try:
            h.set_piece_deadline(piece_idx, 0)
        except Exception:
            pass

    def byte_to_piece(self, info_hash: str, file_index: int, byte_offset: int) -> int | None:
        h = self._handles.get(info_hash)
        if not h or not h.is_valid():
            return None
        try:
            ti = h.torrent_file()
            if not ti:
                return None
            peer_req = ti.map_file(file_index, byte_offset, 0)
            return peer_req.piece
        except Exception:
            return None

    def piece_length(self, info_hash: str) -> int:
        h = self._handles.get(info_hash)
        if not h or not h.is_valid():
            return 0
        try:
            ti = h.torrent_file()
            return ti.piece_length() if ti else 0
        except Exception:
            return 0

    def poll_alerts(self) -> list[str]:
        """Call from the main thread only. Drains libtorrent alerts and returns log strings."""
        msgs = []
        for a in self._session.pop_alerts():
            try:
                name = type(a).__name__
                msg = a.message()
                if name in _LOG_ALERTS:
                    msgs.append(msg)
                if name == "piece_finished_alert" and self._decompressor:
                    self._handle_piece(a)
            except Exception:
                pass
        return msgs

    def _handle_piece(self, a):
        try:
            handle = a.handle
            ti = handle.torrent_file()
            if not ti:
                return
            piece_idx = a.piece_index
            piece_size = ti.piece_size(piece_idx)
            piece_offset = piece_idx * ti.piece_length()
            files = ti.files()
            for i in range(files.num_files()):
                filepath = str(Path(self.download_dir) / files.file_path(i))
                if not self._decompressor.is_zst_file(filepath):
                    continue
                file_offset = files.file_offset(i)
                file_size = files.file_size(i)
                if piece_offset >= file_offset + file_size or piece_offset + piece_size <= file_offset:
                    continue
                local_offset = max(0, piece_offset - file_offset)
                with open(filepath, "rb") as f:
                    f.seek(local_offset)
                    data = f.read(piece_size)
                self._decompressor.on_piece_complete(filepath, local_offset, data)
        except Exception:
            pass

    def get_file_priorities(self, torrent_id: str) -> list[int]:
        """Returns per-file priority list (0 = skip, 4 = normal)."""
        h = self._handles.get(torrent_id)
        if not h or not h.is_valid():
            return []
        try:
            return list(h.get_file_priorities())
        except Exception:
            return []

    def set_file_priority(self, torrent_id: str, file_index: int, priority: int):
        """Set download priority for a single file (0 = skip, 4 = normal)."""
        h = self._handles.get(torrent_id)
        if not h or not h.is_valid():
            return
        try:
            h.file_priority(file_index, priority)
        except Exception:
            pass

    def enforce_ratios(self) -> list[str]:
        """Pause seeding torrents that have met or exceeded the seed ratio. Returns paused IDs."""
        if self._seed_ratio <= 0:
            return []
        paused = []
        for tid, handle in list(self._handles.items()):
            if not handle.is_valid():
                continue
            try:
                s = handle.status()
                if s.paused:
                    continue
                if _STATE_MAP.get(s.state, "unknown") != "seeding":
                    continue
                if s.all_time_download == 0:
                    continue
                if s.all_time_upload / s.all_time_download >= self._seed_ratio:
                    handle.pause()
                    paused.append(tid)
            except Exception:
                pass
        return paused

    def shutdown(self):
        self._save_session()
