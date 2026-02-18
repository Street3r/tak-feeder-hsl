# tak-feeder-hsl

TAK Feeder for HSL (Helsinki Regional Transport) real-time vehicle data via MQTT.
(C) 2026 Stefan Gofferje

This feeder subscribes to the HSL High-Frequency Positioning (HFP) MQTT broker and generates TAK CoT events for various transport modes (tram, subway, rail, ferry, bus).

## Features

- Real-time tracking of HSL vehicles.
- "Anti-jitter" logic to prevent icon jumping when multiple cars in a train report slightly different positions.
- Train composition display: all cars in a train are listed in the CoT infobox.

## Configuration

Configurable via environment variables:

- `COT_URL`: TAK server URL (e.g., `ssl://takserver:8089` or `udp://239.2.3.1:6969`).
- `CLIENT_CERT`: Path to user certificate for mTLS.
- `CLIENT_KEY`: Path to user key for mTLS.
- `PYTAK_TLS_DONT_VERIFY`: Set to `1` to skip TLS verification.
- `HSL_ROUTE_FILTER`: Comma-separated list of route IDs or names to filter (supports wildcards, e.g., `15,M1,M2,L*`).
- `UPDATE_INTERVAL`: Minimum interval between updates for the same trip (default: `10`).
- `HSL_MQTT_BROKER`: HSL MQTT broker address (default: `mqtt.hsl.fi`).
- `HSL_MQTT_PORT`: HSL MQTT port (default: `1883`).
- `DEBUG`: Set to `1` for debug logging.

## Usage

### Docker

```bash
docker run -d \
  --name tak-feeder-hsl \
  -e COT_URL=udp://239.2.3.1:6969 \
  -e HSL_ROUTE_FILTER=15,M1,M2 \
  ghcr.io/sgofferj/tak-feeder-hsl:latest
```

### Docker Compose

```yaml
version: '3'
services:
  tak-feeder-hsl:
    image: ghcr.io/sgofferj/tak-feeder-hsl:latest
    restart: always
    environment:
      - COT_URL=udp://239.2.3.1:6969
      - HSL_ROUTE_FILTER=15,M1,M2
      - UPDATE_INTERVAL=10
```

## License

This project is licensed under the terms of the GNU General Public License v3.0. See [LICENSE.md](LICENSE.md) for details.
