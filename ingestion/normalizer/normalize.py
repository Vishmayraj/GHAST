"""Normalize raw AIS Stream envelopes into GHAST's internal record shapes.

Pure functions: no I/O, no network. Given one decoded AIS Stream JSON
envelope (https://aisstream.io/documentation), return a normalized dict
ready for storage, or None if the envelope isn't one Stage 1 tracks.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# AIS Stream supports 25 message types; Stage 1 only needs the ones that
# drive trajectory-anomaly detection and vessel identity. Extend this set
# deliberately - each new type needs its own extractor below.
POSITION_MESSAGE_TYPES = {
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
}
STATIC_MESSAGE_TYPES = {"ShipStaticData"}
SUPPORTED_MESSAGE_TYPES = POSITION_MESSAGE_TYPES | STATIC_MESSAGE_TYPES

# AIS Stream's MetaData.time_utc looks like:
#   "2023-05-10 11:46:52.509865357 +0000 UTC"
_TIME_UTC_RE = re.compile(
    r"^(?P<datetime>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?P<frac>\.\d+)? \+0000 UTC$"
)


def parse_time_utc(value: str) -> datetime:
    """Parse AIS Stream's MetaData.time_utc string into an aware UTC datetime."""
    match = _TIME_UTC_RE.match(value.strip())
    if not match:
        raise ValueError(f"Unrecognized time_utc format: {value!r}")
    base = match.group("datetime")
    frac = match.group("frac") or ""
    # datetime.strptime only accepts up to 6 fractional digits (microseconds);
    # AIS Stream sometimes sends nanosecond precision, so truncate rather than fail.
    micros = (frac[1:] + "000000")[:6] if frac else "000000"
    dt = datetime.strptime(f"{base}.{micros}", "%Y-%m-%d %H:%M:%S.%f")
    return dt.replace(tzinfo=timezone.utc)


def _get(d: dict, *keys: str, default: Any = None) -> Any:
    """Look up the first present key. AIS Stream examples show inconsistent
    casing across message types and doc revisions (MMSI vs mmsi, Latitude
    vs latitude) - this tries each spelling we've actually seen."""
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return default


def normalize_envelope(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one AIS Stream envelope.

    Returns a dict with "kind" of "position" or "static", or None if the
    envelope is something Stage 1 doesn't track yet (SubscriptionConfirmation,
    a message type outside SUPPORTED_MESSAGE_TYPES, or missing an MMSI).
    """
    message_type = envelope.get("MessageType")
    if message_type not in SUPPORTED_MESSAGE_TYPES:
        return None

    meta = envelope.get("MetaData") or {}
    mmsi = _get(meta, "MMSI", "mmsi")
    if mmsi is None:
        return None

    time_utc_raw = _get(meta, "time_utc", "TimeUtc")
    received_at = parse_time_utc(time_utc_raw) if time_utc_raw else datetime.now(timezone.utc)

    ship_name = _get(meta, "ShipName", "shipname")
    if isinstance(ship_name, str):
        ship_name = ship_name.strip() or None

    body = (envelope.get("Message") or {}).get(message_type) or {}

    if message_type in STATIC_MESSAGE_TYPES:
        destination = body.get("Destination")
        if isinstance(destination, str):
            destination = destination.strip() or None
        return {
            "kind": "static",
            "mmsi": int(mmsi),
            "received_at": received_at,
            "ship_name": ship_name,
            "call_sign": body.get("CallSign") or None,
            "imo_number": body.get("ImoNumber"),
            "ship_type": body.get("Type"),
            "destination": destination,
            "max_draught": body.get("MaximumStaticDraught"),
        }

    # message_type is one of POSITION_MESSAGE_TYPES
    latitude = _get(meta, "Latitude", "latitude", default=body.get("Latitude"))
    longitude = _get(meta, "Longitude", "longitude", default=body.get("Longitude"))
    if latitude is None or longitude is None:
        return None

    return {
        "kind": "position",
        "mmsi": int(mmsi),
        "received_at": received_at,
        "ship_name": ship_name,
        "message_type": message_type,
        "latitude": float(latitude),
        "longitude": float(longitude),
        "sog_knots": body.get("Sog"),
        "cog_deg": body.get("Cog"),
        "true_heading_deg": body.get("TrueHeading"),
        "rate_of_turn": body.get("RateOfTurn"),
        "navigational_status": body.get("NavigationalStatus"),
        "raim": body.get("Raim"),
        "position_accuracy": body.get("PositionAccuracy"),
    }
