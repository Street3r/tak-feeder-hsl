# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright Stefan Gofferje (tak-feeder-hsl, trakbridge-plugin-hsl)
# Copyright (C) 2026 Street3r (changes, see README)
"""HSL High-Frequency Positioning (HFP) MQTT -> CoT."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import unquote

from . import __version__
from .common import (Health, as_float, cot_event, env, env_bool, env_float, env_list,
                     remarks_text, start_mqtt)
from .stops import GTFS_URL, StopNames

LOG = logging.getLogger("tak_feeder_hsl")

HFP_TOPIC_BASE = "/hfp/v2/journey/ongoing/vp"

# HFP transport modes (topic level 6) -> CoT type. ATAK has no civilian rail
# symbol, so trams, metros and trains use the generic multi-passenger vehicle,
# as in trakbridge-plugin-hsl 0.1.1.
RAIL_TYPE = "a-f-G-E-V-C-M"
BUS_TYPE = "a-f-G-E-V-C-M-H"
MODE_TYPES = {
    "tram": RAIL_TYPE,
    "metro": RAIL_TYPE,
    "train": RAIL_TYPE,
    "ferry": "a-f-S-X-C",
    "bus": BUS_TYPE,
    "ubus": BUS_TYPE,
    "robot": BUS_TYPE,
}
DEFAULT_TYPE = BUS_TYPE
# Every HFP transport mode, used when some are excluded (HSL_EXCLUDE_MODES).
ALL_MODES = tuple(MODE_TYPES)

TRIP_LOCK_SECONDS = 30  # anti-jitter: one car per trip for this long


@dataclass
class Settings:
    """Feeder settings from the environment (see .env.example)."""

    broker: str = "mqtt.hsl.fi"
    port: int = 1883
    route_filter: str = ""
    exclude_modes: tuple[str, ...] = ()
    update_interval: float = 5.0
    refresh_interval: float = 20.0
    stale_seconds: float = 60.0
    max_age: float = 120.0
    source_line: str = "Source: HSL open data, CC BY 4.0"
    stop_names: bool = True
    gtfs_url: str = GTFS_URL

    @classmethod
    def from_env(cls) -> "Settings":
        """Read settings."""
        d = cls()
        return cls(
            broker=env("HSL_MQTT_BROKER", d.broker),
            port=int(env_float("HSL_MQTT_PORT", d.port)),
            route_filter=env("HSL_ROUTE_FILTER", ""),
            exclude_modes=tuple(m.lower() for m in env_list("HSL_EXCLUDE_MODES")),
            update_interval=env_float("UPDATE_INTERVAL", d.update_interval),
            refresh_interval=env_float("REFRESH_INTERVAL", d.refresh_interval),
            stale_seconds=env_float("STALE_SECONDS", d.stale_seconds),
            max_age=env_float("MAX_AGE", d.max_age),
            source_line=env("SOURCE_LINE", d.source_line),
            stop_names=env_bool("HSL_STOP_NAMES", d.stop_names),
            gtfs_url=env("HSL_GTFS_URL", d.gtfs_url),
        )


def parse_topic(topic: str) -> Optional[tuple[str, str]]:
    """(mode, route_id) from /hfp/v2/journey/ongoing/vp/<mode>/<oper>/<veh>/<route>/..."""
    parts = topic.split("/")
    if len(parts) < 10:
        return None
    return parts[6], parts[9]


def topic_journey(topic: str) -> tuple[str, str]:
    """(headsign, next stop id) from .../<route>/<dir>/<headsign>/<start>/<next_stop>/...
    The next stop is "EOL" at the end of the line."""
    parts = topic.split("/")
    headsign = unquote(parts[11]) if len(parts) > 11 else ""
    next_stop = parts[13] if len(parts) > 13 else ""
    return headsign, "" if next_stop == "EOL" else next_stop


def filters(route_filter: str) -> list[str]:
    """Route filter patterns without the HSL: prefix."""
    return [f.strip().replace("HSL:", "") for f in route_filter.split(",") if f.strip()]


def matches_filter(route_id: str, desi: str, patterns: list[str]) -> bool:
    """Route id or passenger route number matches a pattern (wildcards ok)."""
    if not patterns:
        return True
    rid = route_id.replace("HSL:", "")
    return any(fnmatch.fnmatch(rid, p) or fnmatch.fnmatch(desi, p) for p in patterns)


def subscriptions(route_filter: str, exclude_modes: tuple[str, ...] = ()) -> list[str]:
    """Targeted topics for exact routes, the whole feed for wildcards or none.
    With excluded modes, one subscription per remaining mode, so the broker
    does not send the excluded vehicles at all."""
    patterns = filters(route_filter)
    excluded = {m.lower() for m in exclude_modes}
    modes = ["+"] if not excluded else [m for m in ALL_MODES if m not in excluded]
    if not patterns or any(ch in p for p in patterns for ch in "*?["):
        return [f"{HFP_TOPIC_BASE}/{m}/#" if m != "+" else f"{HFP_TOPIC_BASE}/#" for m in modes]
    return [f"{HFP_TOPIC_BASE}/{m}/+/+/{p}/#" for m in modes for p in patterns]


@dataclass
class Trip:
    """Latest state of one trip (one marker)."""

    uid: str
    mode: str
    route: str
    lat: float
    lon: float
    speed: float
    course: float
    start: str
    occupancy: Optional[float]
    car: str
    headsign: str = ""
    next_stop: str = ""
    delay: Optional[float] = None  # seconds behind schedule
    cars: set[str] = field(default_factory=set)
    seen: float = 0.0  # monotonic time of the last accepted position
    sent: float = 0.0  # monotonic time of the last send
    changed: bool = True


class HslSource:
    """Keeps the latest position of every trip and produces CoT from it."""

    def __init__(self, settings: Settings, health: Optional[Health] = None) -> None:
        self.s = settings
        self.health = health
        self.patterns = filters(settings.route_filter)
        self.trips: dict[str, Trip] = {}
        self.lock = threading.Lock()
        self.messages = 0
        self.client: Any = None
        self.stops = StopNames()

    # ------------------------------------------------------------- input
    def handle(self, topic: str, payload: bytes, now: Optional[float] = None) -> None:
        """Process one HFP message."""
        parsed = parse_topic(topic)
        if not parsed:
            return
        mode, route_id = parsed
        if mode in self.s.exclude_modes:
            return
        vp = json.loads(payload.decode("utf-8")).get("VP")
        if not isinstance(vp, dict):
            return
        desi = str(vp.get("desi") or "")
        if not matches_filter(route_id, desi, self.patterns):
            return
        lat, lon = as_float(vp.get("lat")), as_float(vp.get("long"))
        if lat is None or lon is None:
            return

        now = time.monotonic() if now is None else now
        trip_key = f"{mode}-{route_id}-{vp.get('oday')}-{vp.get('start')}-{vp.get('dir')}"
        car = f"{vp.get('oper')}_{vp.get('veh')}"
        with self.lock:
            self.messages += 1
            trip = self.trips.get(trip_key)
            if trip is not None:
                trip.cars.add(car)
                # Anti-jitter: cars of one train report slightly different
                # positions; follow one car while it keeps reporting.
                if car != trip.car and now - trip.seen < TRIP_LOCK_SECONDS:
                    return
            else:
                trip = Trip(
                    uid=f"HSL-{trip_key}", mode=mode, route="", lat=lat, lon=lon,
                    speed=0.0, course=0.0, start="", occupancy=None, car=car, cars={car},
                )
                self.trips[trip_key] = trip
            moved = (trip.lat, trip.lon) != (lat, lon) or trip.car != car
            trip.route = desi or route_id.replace("HSL:", "")
            trip.lat, trip.lon = lat, lon
            trip.speed = as_float(vp.get("spd"), 0.0) or 0.0  # m/s, null -> 0
            trip.course = as_float(vp.get("hdg"), 0.0) or 0.0
            trip.start = str(vp.get("start") or "")
            trip.occupancy = as_float(vp.get("occu"))
            trip.headsign, trip.next_stop = topic_journey(topic)
            dl = as_float(vp.get("dl"))  # HFP: negative = behind schedule
            trip.delay = None if dl is None else -dl
            trip.car = car
            trip.seen = now
            trip.changed = trip.changed or moved

    # ------------------------------------------------------------ output
    def build(self, trip: Trip) -> bytes:
        """CoT for one trip."""
        stop = self.stops.get(trip.next_stop) if trip.next_stop else None
        delay = None
        if trip.delay is not None:
            minutes = round(trip.delay / 60)
            delay = f"Delay: {minutes:+d} min" if minutes else "On time"
        lines = [
            f"Route: {trip.route}",
            f"To: {trip.headsign}" if trip.headsign else None,
            f"Next: {stop}" if stop else None,
            delay,
            f"Mode: {trip.mode}",
            f"Vehicles: {', '.join(sorted(trip.cars))}",
            f"Trip start: {trip.start}" if trip.start else None,
            f"Occupancy: {trip.occupancy:g} %" if trip.occupancy is not None else None,
        ]
        return cot_event(
            uid=trip.uid,
            cot_type=MODE_TYPES.get(trip.mode, DEFAULT_TYPE),
            lat=trip.lat,
            lon=trip.lon,
            callsign=f"HSL {trip.route}",
            remarks=remarks_text(lines, self.s.source_line, ("HSL", trip.mode)),
            stale=self.s.stale_seconds,
            course=trip.course,
            speed=trip.speed,
        )

    def collect(self, now: Optional[float] = None) -> list[bytes]:
        """Changed trips, plus unchanged ones every refresh interval.
        Trips silent for max_age seconds are dropped and stale out in ATAK."""
        now = time.monotonic() if now is None else now
        events = []
        with self.lock:
            for key in [k for k, t in self.trips.items() if now - t.seen > self.s.max_age]:
                del self.trips[key]
            for trip in self.trips.values():
                if trip.changed or now - trip.sent >= self.s.refresh_interval:
                    events.append(self.build(trip))
                    trip.sent = now
                    trip.changed = False
            messages, self.messages = self.messages, 0
        if self.health is not None:
            if messages:
                self.health.ok()
            self.health.check_silence()
        return events

    async def events(self) -> list[bytes]:
        """Source protocol: events for this round."""
        return self.collect()

    async def _stop_name_loop(self) -> None:
        headers = {"User-Agent": f"tak-feeder-hsl/{__version__}"}
        while True:
            try:
                await asyncio.to_thread(self.stops.refresh, self.s.gtfs_url, headers)
                await asyncio.sleep(24 * 3600)
            except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
                LOG.warning("GTFS stop names failed: %s, retrying in 1 h", exc)
                await asyncio.sleep(3600)

    async def start(self) -> None:
        """Connect to the HSL broker and load stop names in the background."""
        topics = subscriptions(self.s.route_filter, self.s.exclude_modes)
        LOG.info("HSL: %s:%s, topics %s", self.s.broker, self.s.port, ", ".join(topics))
        self.client = start_mqtt(
            "HSL", f"mqtt://{self.s.broker}:{self.s.port}", topics, self.handle,
            client_id=f"tak-feeder-hsl-{__version__}-{uuid.uuid4().hex[:8]}",
        )
        if self.s.stop_names:
            await self._stop_name_loop()
