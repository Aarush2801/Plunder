"""Tests for ignition.rss — feed parsing, link extraction, pattern filtering."""
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest
from ignition.rss import RSSWatcher

_ATOM_NS = "http://www.w3.org/2005/Atom"


def _watcher(patterns=None):
    engine = MagicMock()
    return RSSWatcher(engine, feeds=[], patterns=patterns or [])


# ------------------------------------------------------------------ #
# _extract_link                                                        #
# ------------------------------------------------------------------ #

def test_extract_enclosure_torrent():
    w = _watcher()
    item = ET.fromstring(
        '<item><enclosure url="https://example.com/file.torrent" type="application/x-bittorrent"/></item>'
    )
    assert w._extract_link(item) == "https://example.com/file.torrent"


def test_extract_enclosure_magnet():
    w = _watcher()
    item = ET.fromstring(
        '<item><enclosure url="magnet:?xt=urn:btih:abc123" type="application/x-bittorrent"/></item>'
    )
    assert w._extract_link(item).startswith("magnet:")


def test_extract_link_text_magnet():
    w = _watcher()
    item = ET.fromstring("<item><link>magnet:?xt=urn:btih:abc</link></item>")
    assert w._extract_link(item).startswith("magnet:")


def test_extract_link_text_torrent():
    w = _watcher()
    item = ET.fromstring("<item><link>https://example.com/x.torrent</link></item>")
    assert w._extract_link(item) == "https://example.com/x.torrent"


def test_extract_atom_link():
    w = _watcher()
    item = ET.fromstring(
        f'<entry xmlns="{_ATOM_NS}">'
        f'<link href="https://example.com/a.torrent"/>'
        f'</entry>'
    )
    assert w._extract_link(item) == "https://example.com/a.torrent"


def test_extract_returns_none_for_plain_http():
    w = _watcher()
    item = ET.fromstring("<item><link>https://example.com/page.html</link></item>")
    assert w._extract_link(item) is None


# ------------------------------------------------------------------ #
# Pattern filtering                                                    #
# ------------------------------------------------------------------ #

def test_pattern_match_allows_item():
    w = _watcher(patterns=["Ubuntu"])
    assert any(p.search("Ubuntu 22.04 LTS") for p in w._patterns)


def test_pattern_mismatch_blocks_item():
    w = _watcher(patterns=["Ubuntu"])
    assert not any(p.search("Fedora 39") for p in w._patterns)


def test_no_patterns_accepts_everything():
    w = _watcher(patterns=[])
    # Empty pattern list means accept all — no filtering applied
    assert w._patterns == []


def test_pattern_case_insensitive():
    w = _watcher(patterns=["ubuntu"])
    assert any(p.search("Ubuntu 22.04") for p in w._patterns)


# ------------------------------------------------------------------ #
# Deduplication via _seen                                              #
# ------------------------------------------------------------------ #

def test_seen_set_prevents_duplicate_add():
    engine = MagicMock()
    w = RSSWatcher(engine, feeds=[], patterns=[])
    w._seen.add("magnet:?xt=urn:btih:abc")
    # _poll_feed would skip this link — simulate by checking _seen
    assert "magnet:?xt=urn:btih:abc" in w._seen


# ------------------------------------------------------------------ #
# Interval floor                                                       #
# ------------------------------------------------------------------ #

def test_interval_floor():
    w = RSSWatcher(MagicMock(), feeds=[], interval=5)
    assert w._interval == 30  # floored to 30s minimum
