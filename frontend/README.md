# frontend/

The user-facing surfaces.

- `site/`: the public site and the analyst console sample. Vanilla HTML, CSS and JS, no build step. See `site/README.md`.
- `dashboard/`: the real analyst dashboard (plan 02). It stays vanilla JS and reuses the console page and its modules from `site/`, pointed at the API with `source: "live"`. Not started.
