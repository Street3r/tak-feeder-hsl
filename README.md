# tak-feeder-hsl

> [!WARNING]
> **Work in progress. Not recommended for use.** This fork is being improved
> and has not been tested against a live TAK server yet. Things may change or
> break without notice.

TAK feeder for HSL (Helsinki Regional Transport) real-time vehicle positions.
It subscribes to HSL's High-Frequency Positioning (HFP) MQTT feed and sends
trams, metros, commuter trains, ferries and buses to a TAK server as CoT.

This is a fork of [sgofferj/tak-feeder-hsl](https://github.com/sgofferj/tak-feeder-hsl)
by Stefan Gofferje, who now maintains the same functionality as
[trakbridge-plugin-hsl](https://github.com/sgofferj/trakbridge-plugin-hsl).
The fork keeps a standalone container and brings in the plugin's fixes.

## What it sends

| Field | Value |
|---|---|
| UID | `HSL-<mode>-<route>-<oday>-<start>-<dir>` (unchanged from upstream) |
| Callsign | `HSL <route>`, e.g. `HSL 15`, `HSL M1`, `HSL I` |
| Type | trams, metros, trains `a-f-G-E-V-C-M`; buses `a-f-G-E-V-C-M-H`; ferries `a-f-S-X-C` |
| Speed / course | m/s and degrees from HFP (`null` is sent as 0) |
| Remarks | route, destination, next stop, delay, mode, vehicles, trip start, occupancy, source, `#HSL #<mode>` |

- **Anti-jitter:** the cars of one train report slightly different positions,
  so a trip follows one car while it keeps reporting.
- **Next stop:** HFP gives only the stop id; names come from HSL's GTFS
  package, refreshed daily. Only `stops.txt` is read, with HTTP range requests
  when the server allows them. HFP has no arrival estimate, so the remarks
  show the delay instead of a time:

  ```
  Route: I
  To: Lentoasema
  Next: Pasila
  Delay: +2 min
  ```
- **Send rate:** positions are buffered. Every `UPDATE_INTERVAL` seconds the
  vehicles that moved are sent; the rest every `REFRESH_INTERVAL` seconds.

## Configuration

Environment variables (see [.env.example](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `COT_URL` | `udp://239.2.3.1:6969` | TAK server, e.g. `ssl://takserver:8089` |
| `PYTAK_TLS_CLIENT_CERT` / `PYTAK_TLS_CLIENT_KEY` | | Client certificate and unencrypted key (PEM). Old names `CLIENT_CERT` / `CLIENT_KEY` still work |
| `PYTAK_TLS_DONT_VERIFY` | `0` | `1` = do not verify the server certificate |
| `PYTAK_TLS_CLIENT_CAFILE` | | CA file for verifying the server |
| `HSL_ROUTE_FILTER` | (all) | Route ids or passenger numbers, wildcards allowed: `15,M1,M2,L*` |
| `HSL_MQTT_BROKER` / `HSL_MQTT_PORT` | `mqtt.hsl.fi` / `1883` | HFP broker |
| `HSL_STOP_NAMES` | `1` | `0` = no GTFS download, no next stop names |
| `HSL_GTFS_URL` | `https://infopalvelut.storage.hsldev.com/gtfs/hsl.zip` | GTFS package |
| `UPDATE_INTERVAL` | `5` | Seconds between send rounds |
| `REFRESH_INTERVAL` | `20` | Re-send unmoved vehicles this often |
| `STALE_SECONDS` | `60` | Marker lifetime without new data |
| `MAX_AGE` | `120` | Stop sending a vehicle silent this long |
| `SOURCE_LINE` | `Source: HSL open data, CC BY 4.0` | Attribution line in the remarks |
| `HEALTH_MAX_SILENCE` | `600` | Unhealthy when no HSL data for this long (0 = off) |
| `DEBUG` | `0` | Debug logging |

## Running

The image is built for amd64 and arm64: `ghcr.io/street3r/tak-feeder-hsl:latest`.

```yaml
services:
  feeder-hsl:
    image: ghcr.io/street3r/tak-feeder-hsl:latest
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./certs:/data:ro
```

The container runs as uid 10001. The certificate and key must be readable by
that user (`chmod 644`), or add `user: "0:0"` to the service. A Docker health
check reports the container unhealthy when HSL data stops arriving.

## Data

HSL open data, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
(© Helsingin seudun liikenne HSL). The source line is included in every
marker's remarks.

## Credits and changes

Original work © Stefan Gofferje:
[tak-feeder-hsl](https://github.com/sgofferj/tak-feeder-hsl) and
[trakbridge-plugin-hsl](https://github.com/sgofferj/trakbridge-plugin-hsl),
used with his permission.

Changes in this fork (Street3r):

- 2026-10-13
  - HFP mode names `metro` and `train` are recognised (they showed as buses);
    CoT types as in trakbridge-plugin-hsl 0.1.1.
  - `null` speed and heading are sent as 0 instead of `None`.
  - Positions are buffered and sent per `UPDATE_INTERVAL` instead of every
    MQTT message; vehicles silent for `MAX_AGE` are dropped.
  - The feeder no longer exits when the MQTT broker is unreachable at start;
    pytak reconnects to TAK after outages without losing the buffer.
  - Remarks: destination, next stop (GTFS stop names), delay, mode,
    occupancy in %, data source, `#HSL #<mode>` tags.
  - Standard variable names (`PYTAK_TLS_CLIENT_*`) with the old ones as
    aliases; health check; unprivileged container; tests and CI.

## License

GNU General Public License v3.0 or later, see [LICENSE.md](LICENSE.md).
