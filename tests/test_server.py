"""Tests for ignition.server — auth, WebSocket handshake, metrics, search routing."""
import asyncio
import base64
import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest
from ignition.server import StreamServer, _ws_accept_key, _ws_encode_text, _build_torrent_cards


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _mock_engine(statuses=None):
    engine = MagicMock()
    engine.get_status.return_value = statuses or []
    engine.get_files.return_value = []
    engine.get_file_priorities.return_value = []
    return engine


def _server(username="", password=""):
    return StreamServer(_mock_engine(), username=username, password=password)


# ------------------------------------------------------------------ #
# Basic auth                                                           #
# ------------------------------------------------------------------ #

def test_auth_disabled_allows_all():
    s = _server()
    assert s._check_auth({}) is True
    assert s._check_auth({"authorization": "garbage"}) is True


def test_auth_correct_credentials():
    s = _server("admin", "secret")
    creds = base64.b64encode(b"admin:secret").decode()
    assert s._check_auth({"authorization": f"Basic {creds}"}) is True


def test_auth_wrong_password():
    s = _server("admin", "secret")
    creds = base64.b64encode(b"admin:wrong").decode()
    assert s._check_auth({"authorization": f"Basic {creds}"}) is False


def test_auth_missing_header():
    s = _server("admin", "secret")
    assert s._check_auth({}) is False


def test_auth_malformed_header():
    s = _server("admin", "secret")
    assert s._check_auth({"authorization": "Bearer token123"}) is False


# ------------------------------------------------------------------ #
# WebSocket key derivation                                             #
# ------------------------------------------------------------------ #

def test_ws_accept_key_rfc_example():
    # RFC 6455 section 1.3 test vector
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    expected = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    assert _ws_accept_key(key) == expected


def test_ws_encode_text_small():
    frame = _ws_encode_text("hi")
    assert frame[0] == 0x81        # FIN + text opcode
    assert frame[1] == 2           # length = 2
    assert frame[2:] == b"hi"


def test_ws_encode_text_medium():
    payload = "x" * 200
    frame = _ws_encode_text(payload)
    assert frame[0] == 0x81
    assert frame[1] == 126         # 16-bit length follows
    import struct
    length = struct.unpack(">H", frame[2:4])[0]
    assert length == 200


# ------------------------------------------------------------------ #
# HTML / metrics generation                                            #
# ------------------------------------------------------------------ #

def test_build_torrent_cards_empty():
    html = _build_torrent_cards([], _mock_engine(), "127.0.0.1", 7889)
    assert "No active torrents" in html


def test_build_torrent_cards_shows_name():
    from ignition.engine import TorrentStatus
    s = TorrentStatus(
        id="abc", name="my.torrent", progress=0.5,
        download_rate=1024, upload_rate=0, num_peers=3,
        num_seeds=1, total_size=1000, downloaded=500,
        state="downloading", eta_seconds=60,
    )
    html = _build_torrent_cards([s], _mock_engine([s]), "127.0.0.1", 7889)
    assert "my.torrent" in html
    assert "50.0%" in html


# ------------------------------------------------------------------ #
# Metrics format                                                       #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_metrics_response_format():
    from ignition.engine import TorrentStatus
    s = TorrentStatus(
        id="deadbeef", name="test", progress=1.0,
        download_rate=0, upload_rate=500,
        num_peers=5, num_seeds=5,
        total_size=2000, downloaded=2000,
        state="seeding", eta_seconds=None,
    )
    engine = _mock_engine([s])
    server = StreamServer(engine)

    output = []

    class FakeWriter:
        def write(self, data): output.append(data)
        async def drain(self): pass

    await server._serve_metrics(FakeWriter())
    body = b"".join(output).decode()
    assert "plunder_download_rate_bytes" in body
    assert "plunder_upload_rate_bytes" in body
    assert 'name="test"' in body
    assert "# TYPE" in body
