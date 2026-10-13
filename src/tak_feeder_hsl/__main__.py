# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright Stefan Gofferje (tak-feeder-hsl)
# Copyright (C) 2026 Street3r (changes, see README)
"""Run the feeder: python -m tak_feeder_hsl"""

from __future__ import annotations

import asyncio
import logging

from . import __version__
from .common import Health, TakSettings, env_float, run_feeder, setup_logging
from .feeder import HslSource, Settings

LOG = logging.getLogger("tak_feeder_hsl")


async def main() -> None:
    """Read settings and run."""
    tak = TakSettings.from_env()
    setup_logging(tak.debug)
    settings = Settings.from_env()
    LOG.info("tak-feeder-hsl %s -> %s", __version__, tak.cot_url)
    health = Health(max_silence=env_float("HEALTH_MAX_SILENCE", 600.0))
    await run_feeder(tak, HslSource(settings, health), settings.update_interval)


def run() -> None:
    """Console script entry point."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
