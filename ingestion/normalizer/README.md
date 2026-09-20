# ingestion/normalizer/

Decodes AIVDM/NMEA sentences (via `pyais`) and normalizes them into the schema TimescaleDB expects, then writes clean records into storage.
