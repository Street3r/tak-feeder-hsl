"""
Main entry point for the HSL TAK feeder.
"""

import asyncio
import os
import logging
from configparser import ConfigParser

import pytak
from dotenv import load_dotenv

from .feeder import HSLWorker, HSLReceiver


async def main() -> None:
    """
    Main function to initialize and run the HSL feeder.
    """
    load_dotenv()

    # Explicitly check for None and provide defaults
    cot_url = os.getenv("COT_URL") or "udp://239.2.3.1:6969"
    if cot_url.lower() == "none":
        cot_url = "udp://239.2.3.1:6969"

    cert = os.getenv("CLIENT_CERT")
    if cert and not os.path.isabs(cert):
        cert = os.path.abspath(cert)

    key = os.getenv("CLIENT_KEY")
    if key and not os.path.isabs(key):
        key = os.path.abspath(key)

    # Configuration from environment variables
    config_dict = {
        "COT_URL": cot_url,
        "PYTAK_TLS_CLIENT_CERT": cert or "",
        "PYTAK_TLS_CLIENT_KEY": key or "",
        "PYTAK_TLS_DONT_VERIFY": os.getenv("PYTAK_TLS_DONT_VERIFY", "0"),
        "HSL_ROUTE_FILTER": os.getenv("HSL_ROUTE_FILTER", ""),
        "UPDATE_INTERVAL": os.getenv("UPDATE_INTERVAL", "10"),
        "DEBUG": os.getenv("DEBUG", "0"),
        "HSL_MQTT_BROKER": os.getenv("HSL_MQTT_BROKER", "mqtt.hsl.fi"),
        "HSL_MQTT_PORT": os.getenv("HSL_MQTT_PORT", "1883"),
    }

    config_parser = ConfigParser()
    config_parser["mycottool"] = config_dict
    config = config_parser["mycottool"]

    if config.get("DEBUG") == "1":
        logging.getLogger().setLevel(logging.DEBUG)

    clitool = pytak.CLITool(config)
    await clitool.setup()

    clitool.add_tasks(
        set(
            [
                HSLWorker(clitool.tx_queue, config_dict),
                HSLReceiver(clitool.rx_queue, config_dict),
            ]
        )
    )

    await clitool.run()


if __name__ == "__main__":
    asyncio.run(main())
