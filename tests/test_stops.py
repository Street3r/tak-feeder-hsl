# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for GTFS stop names (ranged and full download)."""

import http.server
import io
import threading
import unittest
import zipfile

from tak_feeder_hsl import stops

STOPS = "﻿stop_id,stop_code,stop_name,stop_lat,stop_lon\n1020,H0001,Pasila,60.19,24.93\n"


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("agency.txt", "x" * 50000)
        zf.writestr("stops.txt", STOPS)
        zf.writestr("trips.txt", "y" * 50000)
    return buf.getvalue()


class Handler(http.server.BaseHTTPRequestHandler):
    body = b""
    ranges = True
    requests: list = []

    def log_message(self, *args):  # silence
        pass

    def do_HEAD(self):
        self.send_response(200)
        if self.ranges:
            self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()

    def do_GET(self):
        rng = self.headers.get("Range")
        Handler.requests.append(rng)
        data = self.body
        if rng and self.ranges:
            start, end = (int(x) for x in rng.split("=")[1].split("-"))
            data = data[start:end + 1]
            self.send_response(206)
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class StopsTest(unittest.TestCase):
    def serve(self, ranges: bool) -> str:
        Handler.body, Handler.ranges, Handler.requests = make_zip(), ranges, []
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/hsl.zip"

    def test_ranged(self) -> None:
        url = self.serve(ranges=True)
        self.assertEqual(stops.load_stops(url, {}), {"1020": "Pasila"})
        self.assertTrue(all(Handler.requests))  # only range requests

    def test_full_download_fallback(self) -> None:
        url = self.serve(ranges=False)
        self.assertEqual(stops.load_stops(url, {}), {"1020": "Pasila"})


if __name__ == "__main__":
    unittest.main()
