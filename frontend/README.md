# frontend/

The user-facing surfaces.

- `site/`: the public site and the analyst console. Vanilla HTML, CSS and JS, no build step. See `site/README.md`.
- `nginx/`: the nginx config template used by `Dockerfile`. It serves `site/` and proxies `/api/` to the backend, adding the API key.
- `dashboard/`: placeholder only. The analyst dashboard is `site/console.html` and its modules, reading the API with `source: "live"` (the Docker image sets that). A separate dashboard directory was never needed.
