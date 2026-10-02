# data/jamming_zones/

Curated known jamming and spoofing zones, from public maritime advisories and published GNSS interference reports.

Stage 1: manually curated. Automated ingestion from public advisories is a Stage 2 item (MIP section 8.3) and is not built.

**No zone file is committed yet.** The loader exists and is tested, the data is not curated. Until someone loads a file, `jamming_zones` is empty and the `jamming` hypothesis can only come from the fleet-context agent's area result, never from a zone match.

## File format

A GeoJSON `FeatureCollection` (coordinates are `[longitude, latitude]`). Each feature is a `Polygon` or `MultiPolygon` with these properties:

| Property | Required | Meaning |
|---|---|---|
| `name` | yes | zone name; with `source_url` it is the key, so reloading updates instead of duplicating |
| `source_name` | yes | who published it (an advisory, a report, an agency) |
| `source_url` | yes | `http(s)` link to that publication. A feature without one is refused |
| `source_date` | yes | `YYYY-MM-DD`, date of the publication |
| `confidence` | yes | 0 to 1, how firmly the source places the interference in this area |
| `first_seen` | no | `YYYY-MM-DD` or `null`; the zone only matches flags on or after it |
| `last_seen` | no | `YYYY-MM-DD` or `null`; `null` means still current |
| `notes` | no | free text |

Rules for curating:

- Every feature comes from a public source with a URL. Nothing invented, nothing approximated from memory.
- If a source describes an area in words only, draw the polygon conservatively (smaller than the description) and say so in `notes`.
- One feature per source and area. Do not merge sources into one polygon.

## Loading

```
python scripts/load_jamming_zones.py data/jamming_zones/zones.geojson --dry-run
python scripts/load_jamming_zones.py data/jamming_zones/zones.geojson --dsn postgresql://ghast:ghast@localhost:5432/ghast
```

`--dry-run` validates and prints the zones without connecting. A real run upserts by `(name, source_url)` with `source = 'manual'` and prints inserted, updated and skipped counts (rows whose `source` is not `manual` are never overwritten). It runs in one transaction, so a failure loads nothing. Run `backend/models/schema.sql` first so the provenance columns exist (`ingestion/storage.py` applies it at start).
