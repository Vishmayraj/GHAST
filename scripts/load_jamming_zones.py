"""Load curated jamming zones from a GeoJSON file into `jamming_zones`.

    python scripts/load_jamming_zones.py data/jamming_zones/zones.geojson --dry-run
    python scripts/load_jamming_zones.py data/jamming_zones/zones.geojson --dsn postgresql://...

The file format is in data/jamming_zones/README.md. Every feature must name its source and carry
a source URL: this loader refuses a feature without one, and it invents nothing. Rows are keyed
by (name, source_url) and written with source = 'manual', so running it twice changes nothing
the second time. `--dry-run` validates the file and prints what it holds without opening a
database connection.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GEOMETRY_TYPES = ("Polygon", "MultiPolygon")


class ZoneFileError(ValueError):
    """The file is not loadable. The message names the feature and the problem."""


@dataclass(frozen=True)
class Zone:
    name: str
    source_name: str
    source_url: str
    source_date: date
    confidence: float
    first_seen: date | None
    last_seen: date | None
    notes: str | None
    geometry: dict[str, Any]


UPSERT = """
INSERT INTO jamming_zones (name, zone, source, confidence, active, first_seen, last_seen, notes, source_name, source_url, source_date)
VALUES ($1, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON($2), 4326))::geography, 'manual', $3, TRUE, $4, $5, $6, $7, $8, $9)
ON CONFLICT (name, source_url) DO UPDATE
SET zone = EXCLUDED.zone, confidence = EXCLUDED.confidence, first_seen = EXCLUDED.first_seen,
    last_seen = EXCLUDED.last_seen, notes = EXCLUDED.notes, source_name = EXCLUDED.source_name,
    source_date = EXCLUDED.source_date, updated_at = now()
WHERE jamming_zones.source = 'manual'
RETURNING (xmax = 0) AS inserted
"""


def _text(props: dict[str, Any], key: str, where: str) -> str:
    value = props.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ZoneFileError(f"{where}: property '{key}' is required and must be non-empty text")
    return value.strip()


def _optional_date(props: dict[str, Any], key: str, where: str) -> date | None:
    value = props.get(key)
    if value is None:
        return None
    return _date(value, key, where)


def _date(value: Any, key: str, where: str) -> date:
    if not isinstance(value, str) or not DATE.match(value):
        raise ZoneFileError(f"{where}: '{key}' must be a YYYY-MM-DD date, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ZoneFileError(f"{where}: '{key}' is not a real date: {value!r}") from None


def _check_ring(ring: Any, where: str) -> None:
    if not isinstance(ring, list) or len(ring) < 4:
        raise ZoneFileError(f"{where}: a ring needs at least 4 positions (first equals last)")
    for position in ring:
        if not (isinstance(position, list) and len(position) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in position[:2])):
            raise ZoneFileError(f"{where}: positions must be [longitude, latitude] numbers")
        longitude, latitude = position[0], position[1]
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ZoneFileError(f"{where}: position {position[:2]} is outside longitude -180..180, latitude -90..90 (GeoJSON order is [longitude, latitude])")
    if ring[0][:2] != ring[-1][:2]:
        raise ZoneFileError(f"{where}: ring is not closed (first and last position differ)")


def _check_geometry(geometry: Any, where: str) -> dict[str, Any]:
    if not isinstance(geometry, dict) or geometry.get("type") not in GEOMETRY_TYPES:
        raise ZoneFileError(f"{where}: geometry must be a Polygon or MultiPolygon")
    polygons = [geometry.get("coordinates")] if geometry["type"] == "Polygon" else geometry.get("coordinates")
    if not isinstance(polygons, list) or not polygons:
        raise ZoneFileError(f"{where}: geometry has no coordinates")
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ZoneFileError(f"{where}: a polygon needs at least an outer ring")
        for ring in polygon:
            _check_ring(ring, where)
    return geometry


def parse_zones(document: Any) -> list[Zone]:
    """Validate a parsed GeoJSON document. Raises ZoneFileError on the first problem."""
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection" or not isinstance(document.get("features"), list):
        raise ZoneFileError("the file must be a GeoJSON FeatureCollection with a 'features' list")
    if not document["features"]:
        raise ZoneFileError("the file has no features")
    zones: list[Zone] = []
    seen: set[tuple[str, str]] = set()
    for index, feature in enumerate(document["features"], start=1):
        where = f"feature {index}"
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ZoneFileError(f"{where}: not a GeoJSON Feature")
        props = feature.get("properties")
        if not isinstance(props, dict):
            raise ZoneFileError(f"{where}: missing properties")
        name = _text(props, "name", where)
        where = f"feature {index} ({name})"
        source_url = _text(props, "source_url", where)
        if not re.match(r"^https?://\S+$", source_url):
            raise ZoneFileError(f"{where}: 'source_url' must be an http(s) URL, got {source_url!r}")
        confidence = props.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ZoneFileError(f"{where}: 'confidence' must be a number from 0 to 1")
        first_seen, last_seen = _optional_date(props, "first_seen", where), _optional_date(props, "last_seen", where)
        if first_seen and last_seen and last_seen < first_seen:
            raise ZoneFileError(f"{where}: 'last_seen' is before 'first_seen'")
        notes = props.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise ZoneFileError(f"{where}: 'notes' must be text or null")
        if (name, source_url) in seen:
            raise ZoneFileError(f"{where}: the same name and source_url appear twice in this file")
        seen.add((name, source_url))
        zones.append(Zone(
            name=name, source_name=_text(props, "source_name", where), source_url=source_url,
            source_date=_date(props.get("source_date"), "source_date", where), confidence=float(confidence),
            first_seen=first_seen, last_seen=last_seen, notes=notes,
            geometry=_check_geometry(feature.get("geometry"), where),
        ))
    return zones


def load_file(path: Path) -> list[Zone]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ZoneFileError(f"{path}: no such file") from None
    except ValueError as error:
        raise ZoneFileError(f"{path}: not valid JSON ({error})") from None
    return parse_zones(document)


async def upsert_zones(connection: Any, zones: Sequence[Zone]) -> tuple[int, int, int]:
    """Returns (inserted, updated, skipped). Skipped are rows a human or Stage 2 owns (source != manual)."""
    inserted = updated = skipped = 0
    for zone in zones:
        row = await connection.fetchrow(
            UPSERT, zone.name, json.dumps(zone.geometry), zone.confidence, zone.first_seen, zone.last_seen,
            zone.notes, zone.source_name, zone.source_url, zone.source_date,
        )
        if row is None:
            skipped += 1
        elif row["inserted"]:
            inserted += 1
        else:
            updated += 1
    return inserted, updated, skipped


def describe(zones: Sequence[Zone]) -> str:
    lines = [f"{len(zones)} zone{'s' if len(zones) != 1 else ''} valid"]
    for zone in zones:
        lines.append(f"  {zone.name}  confidence {zone.confidence:.2f}  {zone.geometry['type']}  {zone.source_name}, {zone.source_date}")
    return "\n".join(lines)


async def _run(args: argparse.Namespace, zones: list[Zone]) -> int:
    import asyncpg

    connection = await asyncpg.connect(args.dsn)
    try:
        async with connection.transaction():
            inserted, updated, skipped = await upsert_zones(connection, zones)
    finally:
        await connection.close()
    print(f"inserted {inserted}, updated {updated}, skipped {skipped} (not manual)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--dsn", default=os.environ.get("POSTGRES_DSN"), help="Defaults to $POSTGRES_DSN.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print; never connects to a database.")
    args = parser.parse_args(argv)
    try:
        zones = load_file(args.file)
    except ZoneFileError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(describe(zones))
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    if not args.dsn:
        parser.error("--dsn is required (or set POSTGRES_DSN), or use --dry-run")
    return asyncio.run(_run(args, zones))


if __name__ == "__main__":
    raise SystemExit(main())
