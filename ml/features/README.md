# ml/features/

Extracts model features from raw AIS tracks: speed over ground, course over ground, heading, rate of turn, and declared vessel class.

`pipeline.py` reads `vessel_position` through a server-side cursor
(`stream_feature_windows`) and emits windows vessel by vessel as rows arrive,
rather than loading a full date range into memory at once. `window_rows`
stays as a pure, in-memory helper for tests and small fixtures.
`load_training_windows`/`fetch_training_windows` keep their old
list-returning signatures for existing callers (the evaluation harness) but
now drain the same streaming cursor underneath.
