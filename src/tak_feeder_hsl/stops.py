# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
"""Stop names from HSL's GTFS package (only stops.txt is read)."""

from __future__ import annotations

import csv
import io
import logging
import urllib.request
import zipfile
from typing import Optional

LOG = logging.getLogger("tak_feeder_hsl")

GTFS_URL = "https://infopalvelut.storage.hsldev.com/gtfs/hsl.zip"


class HttpRangeFile(io.RawIOBase):
    """Read-only, seekable view of a remote file using HTTP Range requests,
    so zipfile can read one member without downloading the whole package."""

    def __init__(self, url: str, headers: dict[str, str], timeout: float = 60.0) -> None:
        super().__init__()
        self.url, self.headers, self.timeout = url, headers, timeout
        self.pos = 0
        req = urllib.request.Request(url, method="HEAD", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            if resp.headers.get("Accept-Ranges", "").lower() != "bytes":
                raise OSError("server does not support range requests")
            self.size = int(resp.headers["Content-Length"])

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def read(self, size: int = -1) -> bytes:  # type: ignore[override]
        if self.pos >= self.size or size == 0:
            return b""
        end = self.size - 1 if size is None or size < 0 else min(self.size, self.pos + size) - 1
        hdrs = {**self.headers, "Range": f"bytes={self.pos}-{end}"}
        req = urllib.request.Request(self.url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            data: bytes = resp.read()
        self.pos += len(data)
        return data

    def readinto(self, buffer: bytearray) -> int:  # type: ignore[override]
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def parse_stops(text: str) -> dict[str, str]:
    """stop_id -> stop_name from stops.txt."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    return {row["stop_id"]: row["stop_name"] for row in reader
            if row.get("stop_id") and row.get("stop_name")}


def load_stops(url: str, headers: dict[str, str]) -> dict[str, str]:
    """Read stops.txt from the GTFS zip: ranged reads if possible, else download."""
    try:
        remote: io.RawIOBase = HttpRangeFile(url, headers)
        source: object = io.BufferedReader(remote, buffer_size=1 << 20)
    except OSError as exc:
        LOG.info("GTFS: %s, downloading the whole package", exc)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310
            source = io.BytesIO(resp.read())
    with zipfile.ZipFile(source) as archive:  # type: ignore[arg-type]
        return parse_stops(archive.read("stops.txt").decode("utf-8"))


class StopNames:
    """Stop id -> name, refreshed daily. Empty until the first load."""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}

    def get(self, stop_id: str) -> Optional[str]:
        """Name of the stop, if known."""
        return self.names.get(stop_id)

    def refresh(self, url: str, headers: dict[str, str]) -> None:
        """Blocking load; run in a thread."""
        names = load_stops(url, headers)
        if names:
            self.names = names
        LOG.info("GTFS: %d stop names", len(self.names))
