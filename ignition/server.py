"""
HTTP streaming server for Plunder.
Exposes in-progress torrent downloads as streamable HTTP endpoints.

Endpoints:
  GET /                            — browser UI (HTML dashboard)
  GET /api                         — torrent list as JSON
  GET /stream/<info_hash>/<idx>    — stream file with Range support (VLC-compatible)
  GET /transcode/<info_hash>/<idx> — re-encode via ffmpeg → fragmented MP4 (browser-native)
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

_CHUNK = 256 * 1024  # 256 KB read chunks
_PIECE_WAIT_INTERVAL = 0.5   # seconds between have_piece polls
_PIECE_WAIT_TIMEOUT = 120.0  # seconds before giving up

_MIME = {
    ".mkv": "video/x-matroska",
    ".mp4": "video/mp4",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".ts":  "video/mp2t",
    ".flv": "video/x-flv",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".iso": "application/octet-stream",
}

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PLUNDER</title>
<style>
  :root {{ --green: #00ff41; --dark: #0d0d0d; --mid: #1a1a1a; --dim: #555; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--dark); color: var(--green); font-family: 'Courier New', monospace;
         font-size: 14px; padding: 24px; }}
  h1 {{ font-size: 1.4em; letter-spacing: 4px; margin-bottom: 20px; border-bottom: 1px solid var(--green); padding-bottom: 8px; }}
  .torrent {{ background: var(--mid); border: 1px solid #222; border-radius: 4px;
              padding: 16px; margin-bottom: 16px; }}
  .torrent-name {{ font-size: 1.1em; font-weight: bold; margin-bottom: 8px; }}
  .meta {{ color: var(--dim); font-size: 0.85em; margin-bottom: 10px; }}
  .progress-bar {{ background: #111; border: 1px solid #333; height: 8px; border-radius: 2px; margin-bottom: 10px; }}
  .progress-fill {{ background: var(--green); height: 100%; border-radius: 2px; transition: width 0.5s; }}
  .files {{ list-style: none; }}
  .files li {{ padding: 4px 0; border-top: 1px solid #222; }}
  .files a {{ color: var(--green); text-decoration: none; }}
  .files a:hover {{ text-decoration: underline; }}
  .badge {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 0.75em;
            border: 1px solid; margin-left: 8px; vertical-align: middle; }}
  .badge-seeding {{ color: #00ff41; border-color: #00ff41; }}
  .badge-downloading {{ color: #ffaa00; border-color: #ffaa00; }}
  .badge-paused {{ color: #555; border-color: #555; }}
  .transcode-note {{ color: #555; font-size: 0.8em; margin-left: 8px; }}
  footer {{ margin-top: 32px; color: var(--dim); font-size: 0.8em; }}
</style>
</head>
<body>
<h1>⬛ PLUNDER</h1>
{content}
<footer>PLUNDER streaming server &mdash; <a style="color:#555" href="/api">/api</a> for JSON</footer>
<script>setTimeout(() => location.reload(), 5000);</script>
</body>
</html>
"""


def _content_type(filepath: str) -> str:
    return _MIME.get(Path(filepath).suffix.lower(), "application/octet-stream")


def _state_badge(state: str) -> str:
    cls = {"seeding": "badge-seeding", "downloading": "badge-downloading"}.get(state, "badge-paused")
    return f'<span class="badge {cls}">{state.upper()}</span>'


def _build_html(statuses, engine, host: str, port: int) -> str:
    if not statuses:
        body = '<p style="color:#555">No active torrents.</p>'
    else:
        parts = []
        for s in statuses:
            files = engine.get_files(s.id)
            pct = round(s.progress * 100, 1)
            file_items = []
            for f in files:
                stream_url = f"http://{host}:{port}/stream/{s.id}/{f['index']}"
                transcode_url = f"http://{host}:{port}/transcode/{s.id}/{f['index']}"
                size_mb = f['size'] / 1_048_576
                note = ""
                suffix = Path(f['name']).suffix.lower()
                if suffix in {".mkv", ".mp4", ".avi", ".mov", ".webm", ".flv"}:
                    note = f'&nbsp;<a href="{transcode_url}" class="transcode-note">[transcode→fMP4]</a>'
                file_items.append(
                    f'<li><a href="{stream_url}">{f["name"]}</a>'
                    f'<span style="color:#555"> ({size_mb:.1f} MB)</span>{note}</li>'
                )
            files_html = "<ul class='files'>" + "".join(file_items) + "</ul>" if file_items else ""
            parts.append(f"""
<div class="torrent">
  <div class="torrent-name">{s.name}{_state_badge(s.state)}</div>
  <div class="meta">{pct}% &nbsp;|&nbsp; {s.num_peers} peers &nbsp;|&nbsp; ↓ {s.download_rate//1024} KB/s &nbsp;|&nbsp; ↑ {s.upload_rate//1024} KB/s</div>
  <div class="progress-bar"><div class="progress-fill" style="width:{pct}%"></div></div>
  {files_html}
</div>""")
        body = "".join(parts)

    return _HTML_TEMPLATE.format(content=body)


class StreamServer:
    def __init__(self, engine, host: str = "127.0.0.1", port: int = 7889):
        self._engine = engine
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_connection, self._host, self._port
        )

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ------------------------------------------------------------------ #
    # Connection handler                                                   #
    # ------------------------------------------------------------------ #

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        try:
            method, path, headers = await self._parse_request(reader)
            await self._route(method, path, headers, writer)
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _parse_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, str]]:
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = await reader.read(4096)
            if not chunk:
                break
            raw += chunk

        lines = raw.split(b"\r\n")
        request_line = lines[0].decode()
        method, path, *_ = request_line.split()
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if b": " in line:
                k, v = line.split(b": ", 1)
                headers[k.decode().lower()] = v.decode().strip()
        return method, path, headers

    async def _route(
        self, method: str, path: str, headers: dict, writer: asyncio.StreamWriter
    ):
        if method != "GET":
            await self._respond(writer, 405, {}, b"Method Not Allowed")
            return

        if path in ("/", ""):
            await self._serve_html(writer)
            return

        if path == "/api":
            await self._serve_list(writer)
            return

        parts = path.strip("/").split("/")

        if len(parts) == 3 and parts[0] == "stream":
            info_hash, file_idx_str = parts[1], parts[2]
            try:
                file_idx = int(file_idx_str)
            except ValueError:
                await self._respond(writer, 400, {}, b"Bad file index")
                return
            await self._serve_stream(info_hash, file_idx, headers.get("range"), writer)
            return

        if len(parts) == 3 and parts[0] == "transcode":
            info_hash, file_idx_str = parts[1], parts[2]
            try:
                file_idx = int(file_idx_str)
            except ValueError:
                await self._respond(writer, 400, {}, b"Bad file index")
                return
            await self._serve_transcode(info_hash, file_idx, writer)
            return

        await self._respond(writer, 404, {}, b"Not Found")

    # ------------------------------------------------------------------ #
    # GET /  — browser HTML dashboard                                     #
    # ------------------------------------------------------------------ #

    async def _serve_html(self, writer: asyncio.StreamWriter):
        statuses = self._engine.get_status()
        html = _build_html(statuses, self._engine, self._host, self._port)
        body = html.encode()
        await self._respond(writer, 200, {"Content-Type": "text/html; charset=utf-8"}, body)

    # ------------------------------------------------------------------ #
    # GET /api  — torrent listing (JSON)                                  #
    # ------------------------------------------------------------------ #

    async def _serve_list(self, writer: asyncio.StreamWriter):
        statuses = self._engine.get_status()
        result = []
        for s in statuses:
            files = self._engine.get_files(s.id)
            result.append({
                "info_hash": s.id,
                "name": s.name,
                "state": s.state,
                "progress": round(s.progress * 100, 1),
                "files": [
                    {
                        "index": f["index"],
                        "name": f["name"],
                        "size": f["size"],
                        "url": f"http://{self._host}:{self._port}/stream/{s.id}/{f['index']}",
                        "transcode_url": f"http://{self._host}:{self._port}/transcode/{s.id}/{f['index']}",
                    }
                    for f in files
                ],
            })
        body = json.dumps(result, indent=2).encode()
        await self._respond(writer, 200, {"Content-Type": "application/json"}, body)

    # ------------------------------------------------------------------ #
    # GET /stream/<hash>/<idx>  — file streaming with Range support       #
    # ------------------------------------------------------------------ #

    async def _serve_stream(
        self,
        info_hash: str,
        file_index: int,
        range_header: str | None,
        writer: asyncio.StreamWriter,
    ):
        info = self._engine.get_file_info(info_hash, file_index)
        if info is None:
            await self._respond(writer, 404, {}, b"Torrent or file not found")
            return

        file_size = info["size"]
        disk_path = info["path"]
        ct = _content_type(disk_path)

        # Parse range
        if range_header:
            try:
                start, end = self._parse_range(range_header, file_size)
            except ValueError:
                await self._respond(writer, 416, {"Content-Range": f"bytes */{file_size}"}, b"")
                return
            status = 206
        else:
            start, end = 0, file_size - 1
            status = 200

        length = end - start + 1

        # Find which pieces cover the start of the requested range
        first_piece = self._engine.byte_to_piece(info_hash, file_index, start)
        if first_piece is None:
            await self._respond(
                writer, 503,
                {"Retry-After": "5"},
                b"Metadata not yet available",
            )
            return

        # Signal libtorrent we need this piece urgently
        self._engine.hint_piece_urgency(info_hash, first_piece)

        # Wait for first piece
        available = await self._wait_for_piece(info_hash, first_piece)
        if not available:
            await self._respond(writer, 503, {"Retry-After": "5"}, b"Piece not available (timeout)")
            return

        # Find how many contiguous pieces are available from first_piece
        last_piece = self._engine.byte_to_piece(info_hash, file_index, end)
        if last_piece is None:
            last_piece = first_piece

        available_end = end
        for p in range(first_piece, last_piece + 1):
            if not self._engine.have_piece(info_hash, p):
                piece_len = self._engine.piece_length(info_hash)
                available_end = min(end, p * piece_len - 1)
                break

        actual_length = available_end - start + 1

        extra_headers = {
            "Content-Type": ct,
            "Accept-Ranges": "bytes",
            "Content-Length": str(actual_length),
            "Content-Range": f"bytes {start}-{available_end}/{file_size}",
        }
        if available_end < end:
            extra_headers["Connection"] = "close"

        await self._send_headers(writer, status, extra_headers)

        try:
            with open(disk_path, "rb") as f:
                f.seek(start)
                remaining = actual_length
                while remaining > 0:
                    data = f.read(min(_CHUNK, remaining))
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
                    remaining -= len(data)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # GET /transcode/<hash>/<idx>  — ffmpeg → fragmented MP4 pipe        #
    # ------------------------------------------------------------------ #

    async def _serve_transcode(
        self,
        info_hash: str,
        file_index: int,
        writer: asyncio.StreamWriter,
    ):
        if not shutil.which("ffmpeg"):
            await self._respond(writer, 503, {}, b"ffmpeg not found — install it to use transcode")
            return

        info = self._engine.get_file_info(info_hash, file_index)
        if info is None:
            await self._respond(writer, 404, {}, b"Torrent or file not found")
            return

        disk_path = info["path"]

        headers = {
            "Content-Type": "video/mp4",
            "Transfer-Encoding": "chunked",
            "Cache-Control": "no-cache",
            "Connection": "close",
        }
        await self._send_headers(writer, 200, headers)

        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-i", disk_path,
            "-c:v", "copy",       # no re-encode if possible
            "-c:a", "aac",
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "pipe:1",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            while True:
                chunk = await proc.stdout.read(_CHUNK)
                if not chunk:
                    break
                # chunked-encoding frame
                writer.write(f"{len(chunk):x}\r\n".encode())
                writer.write(chunk)
                writer.write(b"\r\n")
                await writer.drain()
            writer.write(b"0\r\n\r\n")
            await writer.drain()
            await proc.wait()
        except (OSError, BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _parse_range(self, header: str, file_size: int) -> tuple[int, int]:
        header = header.strip()
        if not header.startswith("bytes="):
            raise ValueError
        spec = header[6:]
        if "-" not in spec:
            raise ValueError
        s, e = spec.split("-", 1)
        start = int(s) if s else 0
        end = int(e) if e else file_size - 1
        if start > end or start >= file_size:
            raise ValueError
        end = min(end, file_size - 1)
        return start, end

    async def _wait_for_piece(self, info_hash: str, piece_idx: int) -> bool:
        elapsed = 0.0
        while elapsed < _PIECE_WAIT_TIMEOUT:
            if self._engine.have_piece(info_hash, piece_idx):
                return True
            await asyncio.sleep(_PIECE_WAIT_INTERVAL)
            elapsed += _PIECE_WAIT_INTERVAL
        return False

    async def _respond(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        headers: dict,
        body: bytes,
    ):
        headers["Content-Length"] = str(len(body))
        await self._send_headers(writer, status, headers)
        writer.write(body)
        await writer.drain()

    async def _send_headers(
        self, writer: asyncio.StreamWriter, status: int, headers: dict
    ):
        reason = {
            200: "OK", 206: "Partial Content", 400: "Bad Request",
            404: "Not Found", 405: "Method Not Allowed",
            416: "Range Not Satisfiable", 503: "Service Unavailable",
        }.get(status, "")
        lines = [f"HTTP/1.1 {status} {reason}"]
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
        lines.append("\r\n")
        writer.write("\r\n".join(lines).encode())
        await writer.drain()
