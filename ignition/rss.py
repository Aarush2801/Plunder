"""
RSS feed auto-downloader.

Polls one or more RSS/Atom feeds on a configurable interval, matches item titles
against optional regex patterns, and auto-adds magnet links or .torrent URLs to
the engine.

Configure in ~/.ignition/config.toml:
    rss_feeds    = ["https://example.com/rss", ...]
    rss_interval = 300        # seconds (default 300)
    rss_patterns = ["Ubuntu", "Debian.*amd64"]  # regex; empty = accept all
"""
from __future__ import annotations

import asyncio
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING
from urllib.request import urlopen, Request

if TYPE_CHECKING:
    from ignition.engine import TorrentEngine

_ATOM_NS = "http://www.w3.org/2005/Atom"
_USER_AGENT = "PLUNDER/1.0 (RSS fetcher)"


class RSSWatcher:
    def __init__(
        self,
        engine: "TorrentEngine",
        feeds: list[str],
        interval: int = 300,
        patterns: list[str] | None = None,
    ):
        self._engine = engine
        self._feeds = feeds
        self._interval = max(30, interval)
        self._patterns = [re.compile(p, re.IGNORECASE) for p in (patterns or [])]
        self._seen: set[str] = set()
        self._task: asyncio.Task | None = None

    async def start(self):
        if self._feeds:
            self._task = asyncio.create_task(self._loop(), name="rss-watcher")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------ #

    async def _loop(self):
        while True:
            await self._poll_all()
            await asyncio.sleep(self._interval)

    async def _poll_all(self):
        loop = asyncio.get_event_loop()
        for url in self._feeds:
            try:
                await loop.run_in_executor(None, self._poll_feed, url)
            except Exception:
                pass

    def _poll_feed(self, url: str):
        req = Request(url, headers={"User-Agent": _USER_AGENT})
        with urlopen(req, timeout=20) as resp:
            data = resp.read()

        root = ET.fromstring(data)
        items = root.findall(".//item") or root.findall(f".//{{{_ATOM_NS}}}entry")

        for item in items:
            title = self._get_text(item, "title", f"{{{_ATOM_NS}}}title") or ""
            link = self._extract_link(item)
            if not link:
                continue
            if link in self._seen:
                continue
            if self._patterns and not any(p.search(title) for p in self._patterns):
                continue

            self._seen.add(link)
            try:
                self._add_link(link)
            except Exception:
                pass

    def _add_link(self, link: str):
        if link.startswith("magnet:"):
            self._engine.add_magnet(link)
        elif link.endswith(".torrent"):
            req = Request(link, headers={"User-Agent": _USER_AGENT})
            with urlopen(req, timeout=30) as r:
                data = r.read()
            fd, tmp = tempfile.mkstemp(suffix=".torrent")
            try:
                os.write(fd, data)
                os.close(fd)
                self._engine.add_torrent_file(tmp)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # ------------------------------------------------------------------ #

    def _get_text(self, el: ET.Element, *tags: str) -> str | None:
        for tag in tags:
            child = el.find(tag)
            if child is not None and child.text:
                return child.text.strip()
        return None

    def _extract_link(self, item: ET.Element) -> str | None:
        # <enclosure url="..." type="application/x-bittorrent"/> or magnet
        enc = item.find("enclosure")
        if enc is not None:
            url = enc.get("url", "")
            if url.startswith("magnet:") or url.endswith(".torrent"):
                return url

        # <link> plain text (RSS)
        link_el = item.find("link")
        if link_el is not None:
            text = (link_el.text or "").strip()
            if text.startswith("magnet:") or text.endswith(".torrent"):
                return text

        # <atom:link href="..."/>
        atom_link = item.find(f"{{{_ATOM_NS}}}link")
        if atom_link is not None:
            href = atom_link.get("href", "")
            if href.startswith("magnet:") or href.endswith(".torrent"):
                return href

        return None
