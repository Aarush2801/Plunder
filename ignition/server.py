"""
HTTP streaming server for Plunder.
Exposes in-progress torrent downloads as streamable HTTP endpoints.

Endpoints:
  GET /                          — list torrents as JSON
  GET /stream/<info_hash>/<idx>  — stream file with Range support (VLC-compatible)
"""
from __future__ import annotations

import asyncio
import json
import os
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


def _content_type(filepath: str) -> str:
    return _MIME.get(Path(filepath).suffix.lower(), "application/octet-stream")


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

        if path == "/" or path == "":
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
            await self._serve_stream(
                info_hash, file_idx, headers.get("range"), writer
            )
            return

        await self._respond(writer, 404, {}, b"Not Found")

    # ------------------------------------------------------------------ #
    # GET /  — torrent listing                                            #
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
                # Trim response to what's available
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

        # Stream bytes from disk
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
        reason = {200: "OK", 206: "Partial Content", 400: "Bad Request",
                  404: "Not Found", 405: "Method Not Allowed",
                  416: "Range Not Satisfiable", 503: "Service Unavailable"}.get(status, "")
        lines = [f"HTTP/1.1 {status} {reason}"]
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
        lines.append("\r\n")
        writer.write("\r\n".join(lines).encode())
        await writer.drain()
