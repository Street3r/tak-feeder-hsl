"""
CoT utilities for HSL vehicles.
"""

import xml.etree.ElementTree as ET
from typing import Any
import pytak


def generate_hsl_cot(
    vehicle_id: str,
    route_id: str,
    lat: float,
    lon: float,
    route_type: int,
    **kwargs: Any,
) -> bytes:
    """
    Generate a Cursor on Target (CoT) XML message for an HSL vehicle.
    Maps GTFS route_type to specific CoT types.
    """
    # Use override if provided (useful for MQTT worker)
    cot_type = kwargs.get("cot_type_override")

    if not cot_type:
        # GTFS Route Types mapped to MIL-STD-2525 SIDC:
        if route_type == 0:
            cot_type = "a-f-G-E-V-L-T"
        elif route_type == 1:
            cot_type = "a-f-G-E-V-L-R"
        elif route_type == 2:
            cot_type = "a-f-G-E-V-L"
        elif route_type == 4:
            cot_type = "a-f-S-X-C"
        else:
            cot_type = "a-f-G-E-V-C-M"

    root = ET.Element("event")
    root.set("version", "2.0")
    root.set("type", cot_type)
    root.set("uid", f"HSL-{vehicle_id}")
    root.set("how", "m-g")
    root.set("time", pytak.cot_time())
    root.set("start", pytak.cot_time())
    root.set("stale", pytak.cot_time(30))

    point = ET.SubElement(root, "point")
    point.set("lat", str(lat))
    point.set("lon", str(lon))
    point.set("hae", "0")
    point.set("ce", "10")
    point.set("le", "10")

    detail = ET.SubElement(root, "detail")

    track = ET.SubElement(detail, "track")
    track.set("course", str(kwargs.get("bearing", 0.0)))
    track.set("speed", str(kwargs.get("speed", 0.0)))

    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", f"HSL {route_id}")

    remarks = ET.SubElement(detail, "remarks")
    all_cars = kwargs.get("all_cars", [])
    if all_cars:
        cars_str = ", ".join(all_cars)
    else:
        cars_str = kwargs.get("car_id", "Unknown")

    remarks.text = (
        f"Route: {route_id}\n"
        f"Cars: {cars_str}\n"
        f"Trip: {kwargs.get('trip_id', 'Unknown')}\n"
        f"Occupancy: {kwargs.get('occupancy', 'Unknown')}\n"
    )

    return ET.tostring(root)
