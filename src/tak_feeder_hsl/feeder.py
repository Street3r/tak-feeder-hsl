"""
HSL High-Frequency Positioning (HFP) MQTT Worker module.
"""

import asyncio
import json
import uuid
import time
import fnmatch
from typing import Dict, Any, Optional, Tuple

import pytak
import paho.mqtt.client as mqtt

from . import cot_utils


class HSLWorker(pytak.QueueWorker):  # type: ignore[misc]
    """
    HSL MQTT HFP Worker.
    Subscribes to HSL MQTT broker and generates CoT events.
    """

    def __init__(self, queue: asyncio.Queue[bytes], config: Dict[str, Any]) -> None:
        super().__init__(queue, config)
        self.broker = config.get("HSL_MQTT_BROKER", "mqtt.hsl.fi")
        self.port = int(config.get("HSL_MQTT_PORT", "1883"))
        self.route_filter = config.get("HSL_ROUTE_FILTER", "")
        self.client: Optional[mqtt.Client] = None
        self.loop = asyncio.get_event_loop()

        # trip_locks stores {trip_key: (car_id, last_seen_timestamp)}
        self.trip_locks: Dict[str, Tuple[str, float]] = {}
        # trip_cars stores {trip_key: {car_id1, car_id2, ...}}
        self.trip_cars: Dict[str, set[str]] = {}

        # Transport mode mapping to CoT types
        self.mode_map = {
            "tram": "a-f-G-E-V-L-T",
            "subway": "a-f-G-E-V-L-R",
            "rail": "a-f-G-E-V-L",
            "ferry": "a-f-S-X-C",
            "bus": "a-f-G-E-V-C-M",
        }

    async def handle_data(self, data: bytes) -> None:
        """Unused by this worker."""

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: Any,
        _flags: Any,
        rc: int,
        _properties: Any = None,
    ) -> None:
        """MQTT on_connect callback."""
        if rc == 0:
            self._logger.info(
                "Connected to HSL MQTT Broker at %s:%d", self.broker, self.port
            )

            # If any filter has a wildcard, we must subscribe to the global topic
            # because we can't do server-side wildcard matching on route_id level
            # easily while maintaining efficiency.
            if "*" in self.route_filter or not self.route_filter:
                topic = "/hfp/v2/journey/ongoing/vp/#"
                client.subscribe(topic)
                self._logger.info(
                    "Subscribed to global topic (wildcard filter active): %s", topic
                )
            else:
                # Targeted subscriptions for exact matches
                for r_id in self.route_filter.split(","):
                    clean_rid = r_id.strip().replace("HSL:", "")
                    topic = f"/hfp/v2/journey/ongoing/vp/+/+/+/{clean_rid}/#"
                    client.subscribe(topic)
                    self._logger.info("Subscribed to targeted topic: %s", topic)
        else:
            self._logger.error("Failed to connect to MQTT broker, return code %d", rc)

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: Any,
        rc: int,
        _properties: Any = None,
    ) -> None:
        """MQTT on_disconnect callback."""
        self._logger.warning("Disconnected from MQTT broker with code: %d", rc)

    def _matches_filter(self, r_id: str, desi: str) -> bool:
        """Check if a route matches the configured filter (supports wildcards)."""
        if not self.route_filter:
            return True

        # Clean IDs
        clean_rid = r_id.replace("HSL:", "")
        clean_desi = desi.replace("HSL:", "")

        filters = [f.strip().replace("HSL:", "") for f in self.route_filter.split(",")]

        for pattern in filters:
            if fnmatch.fnmatch(clean_rid, pattern) or fnmatch.fnmatch(
                clean_desi, pattern
            ):
                return True
        return False

    def _on_message(
        self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage
    ) -> None:
        """MQTT on_message callback."""
        try:
            # Topic: /hfp/v2/journey/ongoing/vp/<mode>/<oper>/<veh>/<route>/...
            parts = msg.topic.split("/")
            if len(parts) < 10:
                return

            mode = parts[6]
            r_id = parts[9]

            payload_data = json.loads(msg.payload.decode("utf-8"))
            vp = payload_data.get("VP")
            if not vp:
                return

            # Passenger-visible route number (e.g. "15")
            desi = vp.get("desi", "")

            # Apply wildcard filter
            if not self._matches_filter(r_id, desi):
                return

            lat = vp.get("lat")
            lon = vp.get("long")
            if lat is None or lon is None:
                return

            # Stabilization: Lock trip to a single car ID
            trip_key = (
                f"{mode}-{r_id}-{vp.get('oday')}-{vp.get('start')}-{vp.get('dir')}"
            )
            car_id = f"{vp.get('oper')}_{vp.get('veh')}"
            now = time.time()

            # Track all cars seen for this trip
            if trip_key not in self.trip_cars:
                self.trip_cars[trip_key] = set()
            self.trip_cars[trip_key].add(car_id)

            if trip_key in self.trip_locks:
                locked_car, last_seen = self.trip_locks[trip_key]
                if car_id != locked_car and now - last_seen < 30:
                    return

            self.trip_locks[trip_key] = (car_id, now)

            cot_type = self.mode_map.get(mode, "a-f-G-E-V-C-M")
            v_id = f"{vp.get('oper')}_{vp.get('veh')}"

            cot_event = cot_utils.generate_hsl_cot(
                vehicle_id=trip_key,
                route_id=desi or r_id,
                lat=lat,
                lon=lon,
                route_type=-1,
                cot_type_override=cot_type,
                speed=vp.get("spd", 0.0),
                bearing=vp.get("hdg", 0.0),
                trip_id=vp.get("start"),
                occupancy=vp.get("occu", "Unknown"),
                car_id=car_id,
                all_cars=sorted(list(self.trip_cars[trip_key])),
            )

            if cot_event:
                self.loop.call_soon_threadsafe(self.queue.put_nowait, cot_event)
                self._logger.debug(
                    "Enqueued CoT for HSL vehicle %s (Route %s)", v_id, desi or r_id
                )

        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            self._logger.error("Error processing MQTT message: %s", exc)

    async def run(self, number_of_iterations: Optional[int] = None) -> None:
        """Main loop."""
        self._logger.info("Starting HSL MQTT Worker")

        client_id = f"tak-feeder-hsl-{uuid.uuid4()}"
        cb_api = mqtt.CallbackAPIVersion.VERSION2  # type: ignore[attr-defined]
        self.client = mqtt.Client(
            callback_api_version=cb_api,
            client_id=client_id,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect  # type: ignore[assignment]
        self.client.on_message = self._on_message

        self.client.connect(self.broker, self.port, 60)
        self.client.loop_start()

        iterations = 0
        try:
            while number_of_iterations is None or iterations < number_of_iterations:
                if number_of_iterations is not None:
                    iterations += 1
                await asyncio.sleep(1)

                if iterations % 60 == 0:
                    now = time.time()
                    self.trip_locks = {
                        k: v for k, v in self.trip_locks.items() if now - v[1] < 300
                    }
                    self.trip_cars = {
                        k: v for k, v in self.trip_cars.items() if k in self.trip_locks
                    }
        finally:
            if self.client:
                self.client.loop_stop()
                self.client.disconnect()


class HSLReceiver(pytak.QueueWorker):  # type: ignore[misc]
    """Drains RX queue."""

    async def handle_data(self, data: bytes) -> None:
        """Log received data."""

    async def run(self, number_of_iterations: Optional[int] = None) -> None:
        """Main RX loop."""
        iterations = 0
        while number_of_iterations is None or iterations < number_of_iterations:
            if number_of_iterations is not None:
                iterations += 1
            await self.queue.get()
