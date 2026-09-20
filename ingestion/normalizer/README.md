# ingestion/normalizer/

Turns a raw AIS Stream envelope into the flat dict shape `storage.py` writes to TimescaleDB. Pure functions - no I/O, fully unit tested in `ingestion/tests/test_normalize.py`.

Stage 1 handles two kinds of envelope, everything else is passed over (`normalize_envelope` returns `None`):
- **Position** (`PositionReport`, `StandardClassBPositionReport`, `ExtendedClassBPositionReport`) → the trajectory data the ML detection layer (`ml/`) and the agent's track-history tool need.
- **Static** (`ShipStaticData`) → vessel identity (name, call sign, IMO number, type, destination).

AIS Stream's JSON casing is inconsistent across message types (`MMSI` vs `mmsi`, `Latitude` vs `latitude`); the normalizer checks both rather than assuming one.
