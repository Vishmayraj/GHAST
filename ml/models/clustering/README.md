# ml/models/clustering/

Fleet-wide DBSCAN, to separate area jamming (many vessels at once) from targeted spoofing (one vessel).

`fleet_cluster.py` is pure numpy (no scikit-learn, no torch, no I/O):

- `cluster_reports(reports, eps_km, min_vessels, bucket_minutes)`: reports are `mmsi`, `time`, `lat`, `lon`. Reports are bucketed by time, each vessel keeps its latest report per bucket, and DBSCAN with the haversine distance runs per bucket. Vessels are counted, never reports. Returns clusters with their vessels, centroid and radius.
- `cluster_containing(mmsi, reports)`: the cluster that holds one vessel, treating all reports as simultaneous.
- `dbscan(distances, eps, min_samples)`: the textbook algorithm on a distance matrix.

`EPS_KM = 25`, `MIN_VESSELS = 3` and `BUCKET_MINUTES = 30` are uncalibrated starting values. Tuning them needs live and historical rows and has not been done. No labels exist, so nothing here can claim an accuracy; the only honest outputs are counts such as clusters per day and vessels per cluster.

Where it is used: `agent/agents/fleet_context.py`. When an incident is stored, the fleet-context agent clusters this vessel's flag with other vessels' incidents from the same hour, and calls the situation an area only when the cluster holds at least 3 vessels. The result is stored in `evidence.fleet_context` and shown on the console. It does not change the incident's hypothesis (see `docs/agent.md`).

Tests: `cd ml && pytest models/clustering/tests`.
