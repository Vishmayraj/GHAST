# frontend/

The delivery layer's user-facing surfaces.

- `site/` — the public landing site: the project's cinematic scroll-driven
  introduction and explainer, including its own `assets/` (the hero video).
  Vanilla HTML/CSS/JS, no build step. See `site/README.md` for structure and
  how mock data is organized so the real backend can replace it cleanly.
- `dashboard/` — the analyst dashboard (Stage 1 scope, per the HLD/MIP):
  map view, incident list, per-vessel drill-down. Not yet started.
