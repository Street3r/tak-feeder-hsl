# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the HSL feeder."""

import json
import unittest
import xml.etree.ElementTree as ET

from tak_feeder_hsl.feeder import HslSource, Settings, subscriptions


def vp(**kw):
    base = {"desi": "I", "oper": 90, "veh": 1001, "lat": 60.2, "long": 24.9,
            "spd": 20.5, "hdg": 180, "oday": "2026-10-13", "start": "12:04",
            "dir": "1", "occu": 0}
    base.update(kw)
    return json.dumps({"VP": base}).encode()


TOPIC = "/hfp/v2/journey/ongoing/vp/{mode}/0090/01001/3001I/1/Lentoasema/12:04/1020/3/60;24/19/09/84"


class HslTest(unittest.TestCase):
    def setUp(self) -> None:
        self.src = HslSource(Settings(refresh_interval=20, max_age=120))

    def events(self, now):
        return [ET.fromstring(e) for e in self.src.collect(now)]

    def test_modes_map_to_cot_types(self) -> None:
        # HFP uses "metro" and "train"; the old feeder expected subway/rail.
        for mode, cot_type in (("train", "a-f-G-E-V-C-M"), ("metro", "a-f-G-E-V-C-M"),
                               ("tram", "a-f-G-E-V-C-M"), ("bus", "a-f-G-E-V-C-M-H"),
                               ("ferry", "a-f-S-X-C")):
            src = HslSource(Settings())
            src.handle(TOPIC.format(mode=mode), vp(), now=0)
            event = ET.fromstring(src.collect(0)[0])
            self.assertEqual(event.get("type"), cot_type, mode)

    def test_uid_unchanged_from_upstream(self) -> None:
        self.src.handle(TOPIC.format(mode="train"), vp(), now=0)
        event = self.events(0)[0]
        self.assertEqual(event.get("uid"), "HSL-train-3001I-2026-10-13-12:04-1")
        self.assertEqual(event.find("detail/contact").get("callsign"), "HSL I")

    def test_null_speed_and_heading(self) -> None:
        self.src.handle(TOPIC.format(mode="bus"), vp(spd=None, hdg=None), now=0)
        track = self.events(0)[0].find("detail/track")
        self.assertEqual(track.get("speed"), "0.00")
        self.assertEqual(track.get("course"), "0.0")

    def test_send_rate(self) -> None:
        for i in range(10):  # ten messages in one round -> one event
            self.src.handle(TOPIC.format(mode="bus"), vp(lat=60.2 + i / 1000), now=i / 10)
        self.assertEqual(len(self.events(1)), 1)
        self.assertEqual(len(self.events(2)), 0)  # unchanged, not yet refreshed
        self.assertEqual(len(self.events(22)), 1)  # refreshed
        self.src.handle(TOPIC.format(mode="bus"), vp(lat=60.3), now=23)
        self.assertEqual(len(self.events(24)), 1)  # moved

    def test_old_trips_dropped(self) -> None:
        self.src.handle(TOPIC.format(mode="bus"), vp(), now=0)
        self.events(0)
        self.assertEqual(self.events(200), [])
        self.assertEqual(self.src.trips, {})

    def test_anti_jitter(self) -> None:
        self.src.handle(TOPIC.format(mode="train"), vp(veh=1, lat=60.0), now=0)
        self.src.handle(TOPIC.format(mode="train"), vp(veh=2, lat=60.5), now=1)
        event = self.events(1)[0]
        self.assertEqual(event.find("point").get("lat"), "60.000000")
        self.assertIn("90_1, 90_2", event.find("detail/remarks").text)

    def test_remarks(self) -> None:
        self.src.handle(TOPIC.format(mode="bus"), vp(occu=40), now=0)
        text = self.events(0)[0].find("detail/remarks").text
        self.assertIn("Occupancy: 40 %", text)
        self.assertIn("Source: HSL open data, CC BY 4.0", text)
        self.assertTrue(text.endswith("#HSL #bus"))

    def test_journey_lines(self) -> None:
        self.src.stops.names = {"1020": "Pasila"}
        self.src.handle(TOPIC.format(mode="train"), vp(dl=-130), now=0)
        text = self.events(0)[0].find("detail/remarks").text
        self.assertIn("To: Lentoasema", text)
        self.assertIn("Next: Pasila", text)
        self.assertIn("Delay: +2 min", text)
        self.src.handle(TOPIC.format(mode="train"), vp(dl=10, lat=60.3), now=1)
        self.assertIn("On time", self.events(1)[0].find("detail/remarks").text)

    def test_filter(self) -> None:
        src = HslSource(Settings(route_filter="M*,15"))
        src.handle(TOPIC.format(mode="bus"), vp(desi="15"), now=0)
        src.handle(TOPIC.format(mode="bus").replace("3001I", "1550"), vp(desi="550"), now=0)
        self.assertEqual(len(src.collect(0)), 1)
        self.assertEqual(subscriptions("M*,15"), ["/hfp/v2/journey/ongoing/vp/#"])
        self.assertEqual(subscriptions("HSL:1015"),
                         ["/hfp/v2/journey/ongoing/vp/+/+/+/1015/#"])

    def test_exclude_modes(self) -> None:
        src = HslSource(Settings(exclude_modes=("train",)))
        src.handle(TOPIC.format(mode="train"), vp(), now=0)
        src.handle(TOPIC.format(mode="bus"), vp(), now=0)
        self.assertEqual(len(src.collect(0)), 1)
        base = "/hfp/v2/journey/ongoing/vp"
        subs = subscriptions("", ("TRAIN",))
        self.assertNotIn(f"{base}/train/#", subs)
        self.assertIn(f"{base}/bus/#", subs)
        self.assertIn(f"{base}/metro/#", subs)
        self.assertEqual(subscriptions("HSL:1015", ("train", "bus", "ubus", "robot", "ferry", "metro")),
                         [f"{base}/tram/+/+/1015/#"])

    def test_exclude_modes_from_env(self) -> None:
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"HSL_EXCLUDE_MODES": "Train, ferry"}):
            self.assertEqual(Settings.from_env().exclude_modes, ("train", "ferry"))
        with mock.patch.dict(os.environ, {"HSL_EXCLUDE_MODES": ""}):
            self.assertEqual(Settings.from_env().exclude_modes, ())


if __name__ == "__main__":
    unittest.main()
