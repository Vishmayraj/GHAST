# 03 Jamming zones and fleet-wide clustering

Depends on: 01 (agent fixes) for the hypothesis wiring. The zone loader half can start any time.

## Why

The HLD's central distinction is "many vessels affected at once (area jamming)" versus "one vessel (targeted spoofing)". Today the `jamming_zones` table is empty, so the `jamming` hypothesis cannot fire, and `ml/models/clustering/` is a README. The agent cannot make the one distinction the product is about.

## Part A: zones, curated from real public sources

1. Define the file format in `data/jamming_zones/README.md`: GeoJSON `FeatureCollection`, each feature a polygon or multipolygon with properties `name`, `source_name`, `source_url`, `source_date`, `confidence` (0 to 1), `first_seen`, `last_seen` (nullable), `notes`.
2. Curate the first file from public advisories and reports only (maritime security notices, published GNSS interference reports). Every feature must carry its source URL. No invented or approximated zones. If a source gives only a description, draw the polygon conservatively and say so in `notes`.
3. `scripts/load_jamming_zones.py`: validates the GeoJSON, upserts into `jamming_zones` keyed by `(name, source_url)`, `source = 'manual'`, idempotent, prints inserted/updated counts. Add a `--dry-run`. Tests with a temp file and a fake connection.
4. `docs/data-pipeline.md` and `docs/agent.md`: update the `jamming_zones` status once data exists.

## Part B: fleet-wide DBSCAN

1. `ml/models/clustering/fleet_cluster.py`, pure function first: given flagged or anomalous reports (`mmsi`, `time`, `lat`, `lon`) within a time bucket, run DBSCAN with the haversine metric and return clusters. Parameters (`eps_km`, `min_vessels`, `bucket_minutes`) are named constants marked uncalibrated. Count distinct vessels per cluster, not reports.
2. Add `scikit-learn` to `scoring/requirements.txt` if the scorer runs it (it is already in `ml/requirements.txt`).
3. Agent tool `fleet_context` in `agent/tools/`: for a flagged report, look at other vessels' recent detector flags near it in the same bucket. Result `{cluster_vessels, radius_km, isolated: bool}`. It reads the same data the scorer produced; do not recompute the model.
4. Wire into `form_hypothesis`: a cluster of at least `min_vessels` vessels raises `jamming` (below the zone-match confidence, since a zone match is curated evidence); an isolated flag supports `targeted_spoof`. Record the rule in the `docs/agent.md` table.
5. Tune on real data without labels: run the clusterer over the live history and over April historical, and report clusters per day and vessels per cluster. Pick parameters by looking at those outputs plus a spot check on a map; write the reasoning in `docs/research_notes/`. There is no accuracy figure and none should be claimed.

## Part C: feedback loop (HLD section 4.2)

When an analyst confirms a `jamming` verdict (plan 01), offer to create an `automated` zone row from the cluster hull, with `confidence` low and `active = false` until reviewed. Do not auto-activate.

## Done when

- The loader runs (dry-run at least) on the curated file with source URLs on every feature.
- `fleet_context` and `form_hypothesis` have fake-based tests, including isolated versus clustered cases built from hand-made reports.
- `ml/models/clustering/README.md` describes the code, and the landing-site claim about a DBSCAN check is now true or still labeled as mock.

## Not run without the database

Tuning needs live and historical rows.
