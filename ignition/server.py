"""
HTTP streaming server for Plunder.

Endpoints:
  GET /                                  — browser UI (HTML dashboard, WebSocket-powered)
  GET /ws                                — WebSocket: push live torrent JSON every second
  GET /api                               — torrent list as JSON
  GET /api/<hash>/files                  — file list with per-file download priority
  GET /api/<hash>/files/<idx>/priority/<0-7>  — set file download priority (0=skip)
  GET /search?q=<query>                  — search via apibay.org, returns magnet links
  GET /stream/<info_hash>/<idx>          — stream file with Range support (VLC-compatible)
  GET /transcode/<info_hash>/<idx>       — re-encode via ffmpeg → fragmented MP4
  GET /metrics                           — Prometheus exposition format
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import shutil
import struct
import subprocess
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
from urllib.request import urlopen, Request

_CHUNK = 256 * 1024
_PIECE_WAIT_INTERVAL = 0.5
_PIECE_WAIT_TIMEOUT = 120.0
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_SEARCH_API = "https://apibay.org/q.php?q={}"
_TRACKER_LIST = "&tr=udp://tracker.opentrackr.org:1337&tr=udp://tracker.openbittorrent.com:6969"

_MIME = {
    ".mkv": "video/x-matroska", ".mp4": "video/mp4", ".avi": "video/x-msvideo",
    ".mov": "video/quicktime", ".ts": "video/mp2t", ".flv": "video/x-flv",
    ".webm": "video/webm", ".mp3": "audio/mpeg", ".flac": "audio/flac",
    ".iso": "application/octet-stream",
}


def _content_type(filepath: str) -> str:
    return _MIME.get(Path(filepath).suffix.lower(), "application/octet-stream")


def _ws_accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _WS_MAGIC).encode()).digest()
    return base64.b64encode(digest).decode()


def _ws_encode_text(data: str) -> bytes:
    payload = data.encode()
    n = len(payload)
    if n < 126:
        header = bytes([0x81, n])
    elif n < 65536:
        header = bytes([0x81, 126]) + struct.pack(">H", n)
    else:
        header = bytes([0x81, 127]) + struct.pack(">Q", n)
    return header + payload


def _state_badge(state: str) -> str:
    cls = {"seeding": "badge-seeding", "downloading": "badge-downloading"}.get(state, "badge-paused")
    return f'<span class="badge {cls}">{state.upper()}</span>'


def _build_torrent_cards(statuses, engine, host: str, port: int) -> str:
    if not statuses:
        return '<p style="color:#555">No active torrents.</p>'
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
            if Path(f['name']).suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".webm", ".flv"}:
                note = f'&nbsp;<a href="{transcode_url}" class="transcode-note">[transcode]</a>'
            file_items.append(
                f'<li><a href="{stream_url}">{f["name"]}</a>'
                f'<span style="color:#555"> ({size_mb:.1f} MB)</span>{note}</li>'
            )
        files_html = "<ul class='files'>" + "".join(file_items) + "</ul>" if file_items else ""
        parts.append(f"""
<div class="torrent" data-hash="{s.id}">
  <div class="torrent-name">{s.name}{_state_badge(s.state)}</div>
  <div class="meta">{pct}%&nbsp;|&nbsp;{s.num_peers} peers&nbsp;|&nbsp;
    &#8595; {s.download_rate//1024} KB/s&nbsp;|&nbsp;&#8593; {s.upload_rate//1024} KB/s</div>
  <div class="progress-bar"><div class="progress-fill" style="width:{pct}%"></div></div>
  {files_html}
</div>""")
    return "".join(parts)


_HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PLUNDER</title>
<style>
  :root{{--green:#00ff41;--dark:#0d0d0d;--mid:#1a1a1a;--dim:#555}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--dark);color:var(--green);font-family:'Courier New',monospace;font-size:14px;padding:24px}}
  h1{{font-size:1.4em;letter-spacing:4px;margin-bottom:16px;border-bottom:1px solid var(--green);padding-bottom:8px;display:flex;align-items:center;gap:12px}}
  #status{{font-size:0.75em;letter-spacing:1px;color:var(--dim)}}
  .search-bar{{display:flex;gap:8px;margin-bottom:20px}}
  .search-bar input{{flex:1;background:#111;border:1px solid #333;color:var(--green);
    padding:6px 10px;font-family:inherit;font-size:13px;outline:none}}
  .search-bar input:focus{{border-color:var(--green)}}
  .search-bar button{{background:none;border:1px solid var(--green);color:var(--green);
    padding:6px 14px;font-family:inherit;cursor:pointer}}
  .search-bar button:hover{{background:var(--green);color:#000}}
  #search-results{{margin-bottom:20px}}
  .result{{padding:6px 0;border-top:1px solid #1a1a1a;display:flex;justify-content:space-between;align-items:center}}
  .result a{{color:var(--green);text-decoration:none;font-size:0.85em}}
  .result a:hover{{text-decoration:underline}}
  .result .seeds{{color:#555;font-size:0.8em;margin-left:12px;white-space:nowrap}}
  .torrent{{background:var(--mid);border:1px solid #222;border-radius:4px;padding:16px;margin-bottom:16px}}
  .torrent-name{{font-size:1.1em;font-weight:bold;margin-bottom:8px}}
  .meta{{color:var(--dim);font-size:0.85em;margin-bottom:10px}}
  .progress-bar{{background:#111;border:1px solid #333;height:8px;border-radius:2px;margin-bottom:10px}}
  .progress-fill{{background:var(--green);height:100%;border-radius:2px;transition:width .4s}}
  .files{{list-style:none}}
  .files li{{padding:4px 0;border-top:1px solid #222}}
  .files a{{color:var(--green);text-decoration:none}}
  .files a:hover{{text-decoration:underline}}
  .badge{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:.75em;border:1px solid;margin-left:8px;vertical-align:middle}}
  .badge-seeding{{color:#00ff41;border-color:#00ff41}}
  .badge-downloading{{color:#ffaa00;border-color:#ffaa00}}
  .badge-paused{{color:#555;border-color:#555}}
  .transcode-note{{color:#555;font-size:.8em;margin-left:8px}}
  footer{{margin-top:32px;color:var(--dim);font-size:.8em}}
</style>
</head>
<body>
<h1>&#x2B1B; PLUNDER <span id="status">&#9679; connecting...</span></h1>
<div class="search-bar">
  <input id="q" type="text" placeholder="search torrents..." />
  <button onclick="doSearch()">search</button>
</div>
<div id="search-results"></div>
<div id="torrents">{cards}</div>
<footer>PLUNDER &mdash; <a style="color:#555" href="/api">/api</a>
  &nbsp;&bull;&nbsp;<a style="color:#555" href="/metrics">/metrics</a></footer>
<script>
(function(){{
  const host = location.host;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  let ws, retryMs = 1000;

  function connect() {{
    ws = new WebSocket(proto + '://' + host + '/ws');
    ws.onopen = () => {{
      document.getElementById('status').textContent = '\u25CF live';
      document.getElementById('status').style.color = '#00ff41';
      retryMs = 1000;
    }};
    ws.onmessage = (e) => {{
      try {{
        const torrents = JSON.parse(e.data);
        let html = '';
        if (!torrents.length) {{
          html = '<p style="color:#555">No active torrents.</p>';
        }} else {{
          torrents.forEach(t => {{
            const pct = t.progress.toFixed(1);
            html += `<div class="torrent">
              <div class="torrent-name">${{t.name}}<span class="badge badge-${{t.state}}">${{t.state.toUpperCase()}}</span></div>
              <div class="meta">${{pct}}% | ${{t.num_peers}} peers | \u2193 ${{(t.download_rate/1024).toFixed(0)}} KB/s | \u2191 ${{(t.upload_rate/1024).toFixed(0)}} KB/s</div>
              <div class="progress-bar"><div class="progress-fill" style="width:${{pct}}%"></div></div>
            </div>`;
          }});
        }}
        document.getElementById('torrents').innerHTML = html;
      }} catch(err) {{}}
    }};
    ws.onclose = () => {{
      document.getElementById('status').textContent = '\u25CF reconnecting...';
      document.getElementById('status').style.color = '#555';
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, 15000);
    }};
  }}

  connect();

  window.doSearch = function() {{
    const q = document.getElementById('q').value.trim();
    if (!q) return;
    document.getElementById('search-results').innerHTML = '<p style="color:#555">searching...</p>';
    fetch('/search?q=' + encodeURIComponent(q))
      .then(r => r.json())
      .then(results => {{
        if (!results.length) {{
          document.getElementById('search-results').innerHTML = '<p style="color:#555">no results</p>';
          return;
        }}
        let html = '';
        results.slice(0, 20).forEach(r => {{
          html += `<div class="result">
            <a href="${{r.magnet}}">${{r.name}}</a>
            <span class="seeds">S:${{r.seeders}} L:${{r.leechers}} (${{(r.size/1048576).toFixed(0)}} MB)</span>
          </div>`;
        }});
        document.getElementById('search-results').innerHTML = html;
      }})
      .catch(() => {{
        document.getElementById('search-results').innerHTML = '<p style="color:#f55">search failed</p>';
      }});
  }};

  document.getElementById('q').addEventListener('keydown', e => {{
    if (e.key === 'Enter') doSearch();
  }});
}})();
</script>
</body>
</html>
"""


class StreamServer:
    def __init__(
        self,
        engine,
        host: str = "127.0.0.1",
        port: int = 7889,
        username: str = "",
        password: str = "",
    ):
        self._engine = engine
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self._username = username
        self._password = password

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
            method, path, query, headers = await self._parse_request(reader)
            await self._route(method, path, query, headers, writer)
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
    ) -> tuple[str, str, dict, dict[str, str]]:
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = await reader.read(4096)
            if not chunk:
                break
            raw += chunk

        lines = raw.split(b"\r\n")
        request_line = lines[0].decode()
        method, raw_path, *_ = request_line.split()
        parsed = urlparse(raw_path)
        path = parsed.path
        query = parse_qs(parsed.query)

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if b": " in line:
                k, v = line.split(b": ", 1)
                headers[k.decode().lower()] = v.decode().strip()
        return method, path, query, headers

    def _check_auth(self, headers: dict) -> bool:
        if not self._username:
            return True
        auth = headers.get("authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth[6:]).decode()
            user, _, pwd = decoded.partition(":")
            return user == self._username and pwd == self._password
        except Exception:
            return False

    async def _route(
        self, method: str, path: str, query: dict,
        headers: dict, writer: asyncio.StreamWriter
    ):
        if method != "GET":
            await self._respond(writer, 405, {}, b"Method Not Allowed")
            return

        if not self._check_auth(headers):
            await self._respond(
                writer, 401,
                {"WWW-Authenticate": 'Basic realm="PLUNDER"'},
                b"Unauthorized",
            )
            return

        # WebSocket upgrade
        if headers.get("upgrade", "").lower() == "websocket" and path == "/ws":
            await self._serve_websocket(headers, writer)
            return

        if path in ("/", ""):
            await self._serve_html(writer)
            return

        if path == "/api":
            await self._serve_list(writer)
            return

        if path == "/metrics":
            await self._serve_metrics(writer)
            return

        if path == "/search":
            q = query.get("q", [""])[0]
            await self._serve_search(q, writer)
            return

        parts = path.strip("/").split("/")

        # /api/<hash>/files
        if len(parts) == 3 and parts[0] == "api" and parts[2] == "files":
            await self._serve_file_list(parts[1], writer)
            return

        # /api/<hash>/files/<idx>/priority/<value>
        if len(parts) == 6 and parts[0] == "api" and parts[2] == "files" and parts[4] == "priority":
            try:
                idx = int(parts[3])
                priority = int(parts[5])
            except ValueError:
                await self._respond(writer, 400, {}, b"Bad params")
                return
            await self._set_file_priority(parts[1], idx, priority, writer)
            return

        if len(parts) == 3 and parts[0] == "stream":
            try:
                file_idx = int(parts[2])
            except ValueError:
                await self._respond(writer, 400, {}, b"Bad file index")
                return
            await self._serve_stream(parts[1], file_idx, headers.get("range"), writer)
            return

        if len(parts) == 3 and parts[0] == "transcode":
            try:
                file_idx = int(parts[2])
            except ValueError:
                await self._respond(writer, 400, {}, b"Bad file index")
                return
            await self._serve_transcode(parts[1], file_idx, writer)
            return

        await self._respond(writer, 404, {}, b"Not Found")

    # ------------------------------------------------------------------ #
    # GET /ws  — WebSocket live push                                      #
    # ------------------------------------------------------------------ #

    async def _serve_websocket(self, headers: dict, writer: asyncio.StreamWriter):
        key = headers.get("sec-websocket-key", "")
        if not key:
            await self._respond(writer, 400, {}, b"Missing Sec-WebSocket-Key")
            return

        accept = _ws_accept_key(key)
        handshake = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        writer.write(handshake.encode())
        await writer.drain()

        try:
            while True:
                statuses = self._engine.get_status()
                payload = json.dumps([
                    {
                        "id": s.id,
                        "name": s.name,
                        "state": s.state,
                        "progress": round(s.progress * 100, 1),
                        "download_rate": s.download_rate,
                        "upload_rate": s.upload_rate,
                        "num_peers": s.num_peers,
                        "eta": s.eta_seconds,
                    }
                    for s in statuses
                ])
                writer.write(_ws_encode_text(payload))
                await writer.drain()
                await asyncio.sleep(1.0)
        except (OSError, BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------------ #
    # GET /  — browser HTML dashboard                                     #
    # ------------------------------------------------------------------ #

    async def _serve_html(self, writer: asyncio.StreamWriter):
        statuses = self._engine.get_status()
        cards = _build_torrent_cards(statuses, self._engine, self._host, self._port)
        body = _HTML_PAGE.format(cards=cards).encode()
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
    # GET /api/<hash>/files  — file list with priorities                  #
    # ------------------------------------------------------------------ #

    async def _serve_file_list(self, info_hash: str, writer: asyncio.StreamWriter):
        files = self._engine.get_files(info_hash)
        if files is None:
            await self._respond(writer, 404, {}, b"Torrent not found")
            return
        priorities = self._engine.get_file_priorities(info_hash)
        result = [
            {
                "index": f["index"],
                "name": f["name"],
                "size": f["size"],
                "priority": priorities[f["index"]] if f["index"] < len(priorities) else 4,
            }
            for f in files
        ]
        body = json.dumps(result, indent=2).encode()
        await self._respond(writer, 200, {"Content-Type": "application/json"}, body)

    # ------------------------------------------------------------------ #
    # GET /api/<hash>/files/<idx>/priority/<value>  — set priority        #
    # ------------------------------------------------------------------ #

    async def _set_file_priority(
        self, info_hash: str, file_index: int, priority: int, writer: asyncio.StreamWriter
    ):
        if not 0 <= priority <= 7:
            await self._respond(writer, 400, {}, b"Priority must be 0-7")
            return
        self._engine.set_file_priority(info_hash, file_index, priority)
        body = json.dumps({"ok": True, "file_index": file_index, "priority": priority}).encode()
        await self._respond(writer, 200, {"Content-Type": "application/json"}, body)

    # ------------------------------------------------------------------ #
    # GET /search?q=  — search via apibay.org                            #
    # ------------------------------------------------------------------ #

    async def _serve_search(self, query: str, writer: asyncio.StreamWriter):
        if not query:
            await self._respond(writer, 400, {}, b"Missing q param")
            return
        url = _SEARCH_API.format(quote(query))
        try:
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: urlopen(Request(url, headers={"User-Agent": "PLUNDER/1.0"}), timeout=10).read()
            )
            items = json.loads(raw)
            # apibay returns [{"id":"0",...}] when no results
            if items and items[0].get("id") == "0":
                items = []
            for item in items:
                ih = item.get("info_hash", "")
                name = item.get("name", "")
                item["magnet"] = (
                    f"magnet:?xt=urn:btih:{ih}&dn={quote(name)}{_TRACKER_LIST}"
                )
            body = json.dumps(items, indent=2).encode()
            await self._respond(writer, 200, {"Content-Type": "application/json"}, body)
        except Exception as e:
            await self._respond(writer, 502, {}, f"Search failed: {e}".encode())

    # ------------------------------------------------------------------ #
    # GET /metrics  — Prometheus exposition format                        #
    # ------------------------------------------------------------------ #

    async def _serve_metrics(self, writer: asyncio.StreamWriter):
        statuses = self._engine.get_status()
        lines = []

        def gauge(name: str, help_text: str):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")

        gauge("plunder_download_rate_bytes", "Current download rate in bytes/sec")
        for s in statuses:
            lbl = f'info_hash="{s.id}",name="{s.name}"'
            lines.append(f"plunder_download_rate_bytes{{{lbl}}} {s.download_rate}")

        gauge("plunder_upload_rate_bytes", "Current upload rate in bytes/sec")
        for s in statuses:
            lbl = f'info_hash="{s.id}",name="{s.name}"'
            lines.append(f"plunder_upload_rate_bytes{{{lbl}}} {s.upload_rate}")

        gauge("plunder_progress_ratio", "Download progress 0.0-1.0")
        for s in statuses:
            lbl = f'info_hash="{s.id}",name="{s.name}"'
            lines.append(f"plunder_progress_ratio{{{lbl}}} {s.progress:.4f}")

        gauge("plunder_peers_total", "Number of connected peers")
        for s in statuses:
            lbl = f'info_hash="{s.id}",name="{s.name}"'
            lines.append(f"plunder_peers_total{{{lbl}}} {s.num_peers}")

        gauge("plunder_total_size_bytes", "Total torrent size in bytes")
        for s in statuses:
            lbl = f'info_hash="{s.id}",name="{s.name}"'
            lines.append(f"plunder_total_size_bytes{{{lbl}}} {s.total_size}")

        body = "\n".join(lines).encode() + b"\n"
        await self._respond(
            writer, 200,
            {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
            body,
        )

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

        first_piece = self._engine.byte_to_piece(info_hash, file_index, start)
        if first_piece is None:
            await self._respond(writer, 503, {"Retry-After": "5"}, b"Metadata not yet available")
            return

        self._engine.hint_piece_urgency(info_hash, first_piece)
        available = await self._wait_for_piece(info_hash, first_piece)
        if not available:
            await self._respond(writer, 503, {"Retry-After": "5"}, b"Piece not available (timeout)")
            return

        last_piece = self._engine.byte_to_piece(info_hash, file_index, end) or first_piece
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
    # GET /transcode/<hash>/<idx>  — ffmpeg fragmented MP4 pipe          #
    # ------------------------------------------------------------------ #

    async def _serve_transcode(
        self, info_hash: str, file_index: int, writer: asyncio.StreamWriter
    ):
        if not shutil.which("ffmpeg"):
            await self._respond(writer, 503, {}, b"ffmpeg not found - install it to use transcode")
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
            "ffmpeg", "-loglevel", "error", "-i", disk_path,
            "-c:v", "copy", "-c:a", "aac",
            "-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
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
        return start, min(end, file_size - 1)

    async def _wait_for_piece(self, info_hash: str, piece_idx: int) -> bool:
        elapsed = 0.0
        while elapsed < _PIECE_WAIT_TIMEOUT:
            if self._engine.have_piece(info_hash, piece_idx):
                return True
            await asyncio.sleep(_PIECE_WAIT_INTERVAL)
            elapsed += _PIECE_WAIT_INTERVAL
        return False

    async def _respond(self, writer, status, headers, body):
        headers["Content-Length"] = str(len(body))
        await self._send_headers(writer, status, headers)
        writer.write(body)
        await writer.drain()

    async def _send_headers(self, writer, status, headers):
        reason = {
            200: "OK", 206: "Partial Content", 400: "Bad Request", 401: "Unauthorized",
            404: "Not Found", 405: "Method Not Allowed", 416: "Range Not Satisfiable",
            502: "Bad Gateway", 503: "Service Unavailable",
        }.get(status, "")
        lines = [f"HTTP/1.1 {status} {reason}"]
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
        lines.append("\r\n")
        writer.write("\r\n".join(lines).encode())
        await writer.drain()
