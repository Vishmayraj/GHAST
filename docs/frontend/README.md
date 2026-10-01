# Frontend handover

Everything a new person needs to take over `frontend/site/`. The visual rules are in `DESIGN.md` next to this file. What the product is and may claim is in `PRODUCT.md` at the repo root.

## What it is

A static site: six HTML pages, plain CSS, plain JS. No framework, no build step, no npm. It is served by nginx (`frontend/Dockerfile`, compose service `frontend`, port `${FRONTEND_PORT:-3000}`). The owner chose this on purpose.

| Page | File | Job |
|---|---|---|
| Home | `index.html` | scroll-scrubbed hero, statement, "Try to fool it" playground, links to the rest |
| The Flow | `flow.html` | one report from ship to decision, a line that fills as you scroll, white to black |
| How it works | `how-it-works.html` | four steps with drawn charts |
| Console | `console.html` | a working analyst console on sample data. It is the spec for the real dashboard |
| Trust | `trust.html` | alert budget, review loop, review statistics |
| Project | `project.html` | who and what, where it stands |

## Run it

```
cd frontend/site
python3 -m http.server 8080
```

Open `http://localhost:8080`. Nothing to install. Add `?source=live` to a page to try the live data source (it will show error states until the API exists).

## Files

```
frontend/site/
  *.html                  the six pages
  assets/                 hero video (mp4, webm), og-image.jpg, favicon.svg
  robots.txt
  styles/
    tokens.css            colors, type scale, spacing tokens
    base.css              reset, layout, buttons, reveal and draw animations, script and kbd
    nav.css               header, mobile menu, footer
    hero.css              home hero
    pages.css             routes, steps, tables, logs, playground
    console.css           console layout
    flow.css              Flow page
  scripts/
    config.js             source: "sample" or "live"; apiBase; apiKey
    data/api.js           window.GHAST.data, the only thing UI code calls
    data/sample.js        sample data (fictional vessels, relative times)
    data/live.js          fetch calls for the planned API
    console.js            console modules and keyboard shortcuts
    trust.js              trust modules
    flow.js               Flow page behavior
    playground.js         "Try to fool it"
    scroll-hero.js        home hero scrub
    smooth-scroll.js      inertial wheel scrolling
    ui.js                 header theme, mobile menu, split headlines, reveals, staggers
```

## Things that are repeated by hand

The pages are plain HTML, so some blocks are copied into every page. Change them in all six files together.

- The whole `<head>`: fonts link, favicon, description, Open Graph and Twitter tags, theme color, the list of stylesheets.
- The `<header>` (wordmark, nav links) and the `<footer>`.
- The script tags at the bottom: `smooth-scroll.js` then `ui.js` come last on every page.

When you add a page: copy an existing small page (`project.html`), change the title, description and `og:` tags, add it to the nav in every page, and set `aria-current="page"` on its own link.

A quick check that nothing drifted: `grep -L "assets/favicon.svg" frontend/site/*.html` should print nothing, and the nav should have the same links in every file.

## Data layer and module slots

UI code never reads sample files directly. It calls `GHAST.data.*`:

| Method | Live endpoint (plan 02) |
|---|---|
| `health()` | `GET /health` |
| `incidents({status, hypothesis, mmsi, limit, before})` | `GET /incidents` |
| `incident(id)` | `GET /incidents/{id}` |
| `track(mmsi, {from, to, includeHistorical})` | `GET /vessels/{mmsi}/track` |
| `vesselIncidents(mmsi)` | `GET /vessels/{mmsi}/incidents` |
| `zones()` | `GET /zones` |
| `review(id, {verdict, notes})` | `POST /incidents/{id}/review` |
| `reviewStats()` | no endpoint yet. Plan 02 must add one for plan 01 part D3 |
| `thresholds()` | no endpoint yet. Comes from plan 01 part B |

Field names in `data/sample.js` match plan 02. When the real API differs, change `sample.js` and `live.js`, not the modules.

Each backend feature has a fixed `data-module` slot:

| Slot | Page | Backend plan |
|---|---|---|
| `status-strip` | console | 06 healthchecks, 02 `/health` |
| `incident-queue` | console | 02 `/incidents` |
| `incident-detail` | console | 02 `/incidents/{id}`, 01 C |
| `detector-votes` | console | 01 C4, 04 (the Laya row has a "shadow" tag until plan 04 enables it) |
| `fleet-context` | console | 03 B |
| `map` | console | 02 track, 03 A zones |
| `review-form` | console | 01 D, 02 review POST |
| `alert-budget` | trust | 01 A and B |
| `review-stats` | trust | 01 D3, needs 30 or more reviews |
| `playground` | home | none, it is an illustration |

Slots for plan 05 (`model-versions`) and plan 06 (`artifact-provenance`) do not exist yet. Add them to `trust.html` when those plans land.

## Going live checklist

1. Plan 02 API is running. Set `apiBase` in `scripts/config.js` and `source: "live"`.
2. Allow the site's origin in the API's CORS settings, or serve both behind one nginx.
3. The `apiKey` in `config.js` is visible to anyone who opens the page. It is a stopgap for plan 02's simple key. Real auth is Stage 3.
4. Reconcile field names (see above). Check `incident.votes`, `fleet_context`, `tool_call_log` and `track`.
5. Replace the SVG map in `console.js` (`renderMap`) with MapLibre GL, loaded from a script tag. The base-map tile source is an open owner decision. Keep the `[data-module="map"]` wrapper and the single render function.
6. Move the console into `frontend/dashboard/` (plan 02), or make `console.html` the dashboard. The decision was: the dashboard stays vanilla and reuses these modules, no second design.
7. Remove the "Sample data" tags only on modules that are really reading the API. They show automatically when `source` is `sample`.

## Open items

- Fonts load from Google Fonts. Self-host them for a deployment that must work offline, and drop the two `preconnect` links.
- Set a canonical URL and `og:url` once the public domain is known. The Open Graph image is a frame from the hero video (`assets/og-image.jpg`, 1200x630). Regenerate it with ffmpeg if the video changes.
- Image paths in the Open Graph tags are relative. Some crawlers want absolute URLs. Make them absolute at deploy time.
- No automated tests. Check by opening every page at desktop and 390px wide, with the glide on and with reduced motion on.
- The pages under 860px have had less testing than desktop.
- Content for the thin pages (project, trust) is still short. The owner has more to add.

## Decisions already made

Do not reopen these without asking the owner.

- Vanilla HTML, CSS and JS. No build step.
- Plain white background. No cream.
- No accuracy numbers on the site.
- "A small team", not one person.
- The hero video and its scroll scrub stay.
- The Flow page goes white to black with the shift between points 3 and 4. A continuous gradient was tried and rejected because the middle was unreadable.
- Wheel scrolling glides. The owner wanted it longer, so keep `EASE` low.
