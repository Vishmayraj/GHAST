# ingestion/

Pulls real AIS data continuously from public sources and turns it into clean, normalized records in TimescaleDB. This is Weeks 1-4 of `ImplementationPlans/Sem5IP.md`.

- `collector/` — the live AIS feed consumer.
- `normalizer/` — AIVDM/NMEA decoding and schema normalization.
