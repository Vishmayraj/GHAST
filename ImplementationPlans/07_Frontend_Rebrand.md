# 07 Frontend rebrand: a premium site and a console the backend can plug into

Depends on: nothing. Do not read the backend code to do this. The backend is in flux and will mislead you. Everything you need about the product is in `PRODUCT.md`, this file, and the six backend plans (01 to 06).

Skills to use: `impeccable` (run `impeccable context` once, then the new-work flow; write `DESIGN.md` from the direction below before editing UI, and read its craft-floor before the first edit) and the `taste-skill` set (`minimalist-ui` and `high-end-visual-design` for restraint and spacing; ignore anything in them that conflicts with this file, for example pill buttons and double-bezel cards).

## Goal

Reshape `frontend/site/` into something we can present tomorrow. It must look like a finished premium product, it must show what GHAST is for, and it must have a fixed place for every piece of backend work in plans 01 to 06, so finished backend work slots in without redesign.

Constraints, all from the owner:

- Same stack: vanilla HTML, CSS and JS. No framework, no build step, no npm. Keep `frontend/Dockerfile` working unchanged (it copies `frontend/site/`).
- Keep the hero video and its scroll-scrub behavior (`scroll-hero.js`, the encoding notes in `site/README.md`). Do not re-encode it.
- Show the end purpose, not the current backend state. Nothing on the site should depend on what runs today.
- The look must not read as AI output. Copy included.

## Honesty rules (short, non-negotiable)

1. No accuracy, F1, precision, recall or benchmark number anywhere. None exists yet (plan 01).
2. Product-looking output (incidents, tracks, charts, stats) is sample data and says so, once, in a small consistent tag on the module. No apologetic footer on every page.
3. Use the real vocabulary. Hypotheses: `jamming`, `targeted_spoof`, `equipment_fault`, `freeze_replay`, `benign`. Review verdicts: `confirmed_spoof`, `jamming`, `equipment_fault`, `benign`, `unclear`. Statuses: `reported`, `escalated`, `resolved`. Do not invent statuses like "auto-dismissed".
4. Do not describe a component as running if it is a plan. Write copy in terms of what the product does for an analyst, not which algorithm is deployed. Drop the named "DBSCAN", "Bi-LSTM", "MLflow", "IEEE DataPort" and "benchmarked against published baselines" lines.

## What reads as AI today (remove all of it)

Visual:

- The fixed translucent navy bar with backdrop blur, the glowing dot mark, and the mono pill "View on GitHub".
- The hero eyebrow with a pulsing dot and mono uppercase ("Anomaly flagged"), and the "keep scrolling" cue.
- The blue gradient wash with a grid over it under the hero.
- An eyebrow line above nearly every heading ("What just happened", "Next-state prediction", "Fleet-wide check").
- Serif italic on one word inside a headline ("wake", "flag", "adrift", "course"). This is the single biggest tell. Headlines become one typeface, one style.
- Rows of three identical cards as page structure.
- Fake browser chrome with three dots on the dashboard preview.
- Pill badges, pill confidence bar, rounded-full everything.
- The Heraclitus quote interlude.

Copy:

- Em-dashes throughout. Use none. Use periods and commas.
- "X, not Y" headlines ("A wake, not a signature", "Read the wake, not a watchlist").
- Nautical puns ("What flows with us", "A course plotted like a real one", "wake").
- "It isn't X, it's Y" sentences, triplets of abstractions, and any sentence that explains the page while on the page ("The site around it is honest about that").
- Buyer-pitch language ("the false-positive risk an insurer buyer will actually judge this on").
- Claims about things that do not exist yet (see honesty rule 4).

## Direction

The world: a chart room. Paper, ink, fine rules, and one signal color for the thing that matters. Quiet, exact, and unhurried, like a good hydrographic chart or a printed intelligence report. Premium here means restraint and space, not effects.

Mode per surface: home and project pages are Persuade and Read. The console is Operate. Trust is Read.

### Color

Replace the current all-blue token set. Light by default (it will be shown on a projector, and analysts read long text). The hero stays dark because the video is.

| Token | Value | Use |
|---|---|---|
| `--paper` | `#F5F2EB` | page background |
| `--paper-deep` | `#ECE8DE` | selected rows, quiet panels |
| `--ink` | `#0A2036` | text, rules at full strength, hero background (same navy as the video) |
| `--ink-2` | `#3A4A5C` | body secondary |
| `--ink-3` | `#5A6674` | meta text (keep 4.5:1 on paper) |
| `--rule` | `rgba(10,32,54,0.14)` | hairlines |
| `--sea` | `#1F4E79` | data lines only (the reported track) |
| `--sea-tint` | `#E3EAEF` | map water |
| `--signal` | `#D8431F` | flagged or unreviewed, nothing else |

Signal color appears only where something needs attention: the flagged point on a chart, the unreviewed marker in the list, the flagged hypothesis word. If it appears on decoration, remove it. No gradients, no glows, no shadows. Depth comes from paper versus paper-deep and from rules.

### Type

Two families plus mono for data only. Load from Google Fonts with real fallbacks (the build sandbox cannot fetch fonts, so self-hosting is a follow-up; leave a note in `site/README.md`).

- Headlines: `Newsreader`, roman only, weight 400, optical size on. Tracking -0.02em, line-height 1.05. No italics anywhere.
- Body and UI: `Geist`, weight 400 and 500. Body 1.0625rem at 1.65 line-height, measure 62ch.
- Data (MMSI, timestamps, coordinates, thresholds, numbers in tables): `Geist Mono` at 0.8125rem, with `font-variant-numeric: tabular-nums`. Mono is for data only, never for labels or decoration.
- Drop Instrument Sans and Instrument Serif entirely.

Scale, about 1.33 ratio: 13, 15, 17, 22, 30, 44, 64, 96 px. Display headline max 6rem. Section headlines `clamp(2rem, 1.5rem + 2.2vw, 3.25rem)`. Use `text-wrap: balance` on headlines and `text-wrap: pretty` on body.

### Space and layout

- Vertical section padding `clamp(6rem, 10vw, 11rem)`. This is most of the premium feel. Be generous and do not fill the gaps.
- Content on a 12-column grid, max width 1200px, gutters 24px. Headline in columns 1 to 6, body in columns 7 to 12. Asymmetric and left-aligned. Do not center stacks of text.
- Sections separated by a single hairline, not by background color changes.
- Corners: 2px on inputs and buttons, 0 elsewhere. No cards as page structure. Lists are rows with hairlines between them.
- One primary button style: solid ink, paper text, 2px radius, no shadow. Secondary is a text link with a 1px underline at 6px offset.

### Motion

- One authored moment: the hero scrub. Leave it.
- Everything else gets one reveal: opacity and 10px translate, 500ms, `cubic-bezier(0.16, 1, 0.3, 1)`, on content blocks, no stagger. Reuse `reveal.js` (IntersectionObserver). Respect `prefers-reduced-motion`.
- Console state changes are instant or 120ms color only.

### Browser surfaces

Theme `::selection` (ink on paper-deep), `:focus-visible` (2px ink outline, 3px offset), the scrollbar (thin, `--rule` thumb), caret color, and link underline offset. Tabular numerals in every table.

## Header

Replace `nav.css` and the nav markup on every page.

- Height 72px. Full width, same 1200px content column as the page.
- Left: wordmark. An authored inline SVG mark (24px): a short horizontal track that steps off the line, ending in an open circle (predicted) and a filled circle (reported). The gap between them is the product. Next to it the word GHAST in Geist 500, letter-spacing 0.12em. No glow, no dot.
- Right: four text links (How it works, Console, Trust, Project), 0.9375rem, 2.5rem gap. Current page gets a 1px underline at 6px offset. One last plain text link "Source" to the repo. No pill, no button.
- Over the hero: transparent, paper-colored text. After the hero ends: paper background with a hairline under it. Toggle with one IntersectionObserver on a sentinel at the end of the hero and a `data-theme` attribute. No blur, no scroll listener.
- Non-home pages: sticky, paper background, hairline.
- Mobile (under 720px): wordmark and a text button "Menu" that opens a plain full-width panel of the links, large serif, no animation beyond a 200ms fade. Close on link click and Escape.

## Pages

Rename files and update all links, `Dockerfile` is unaffected.

| Old | New | Notes |
|---|---|---|
| `index.html` | `index.html` | hero, statement, three routes |
| `approach.html` | `how-it-works.html` | four steps |
| `platform.html` | `console.html` | the analyst console, the showcase |
| new | `trust.html` | plan 01 surface |
| `about.html` | `project.html` | short, plain |

### Home

Hero: keep video and scrub. Remove the eyebrow and the pulse. Title GHAST. Under it, the spelled-out name in plain Geist: "GNSS and AIS Hazardous Spoofing Tracker". Tagline: "Watches vessel position reports, flags the ones that do not add up, and shows an analyst why." Scroll cue becomes the single word "Scroll" in meta style, or remove it. Scrim becomes a flat fade to ink, not a gradient stack. The cut from video to paper is a straight edge, not a wash.

Below the hero:

1. Statement. Headline: "A ship reports where it is. GHAST checks whether that is possible." Body (plain, about 70 words): every vessel broadcasts position, speed and course; when that broadcast is jammed or faked the numbers stop fitting together, such as a turn too sharp for the hull, a jump with no time to make it, or a position frozen while the speed says otherwise; GHAST learns how each vessel normally moves and flags the reports that break from it.
2. Three routes as rows, not cards. Large serif link on the left, one plain sentence on the right, hairline between. How it works (how a report becomes a flag and then a decision). Console (open a sample incident and work it). Trust (how often it is right, and how that gets measured).
3. One closing line and the Source link. No footer essay.

### How it works

Headline: "From one odd position to a decision." Four steps in a vertical editorial sequence. Numerals are allowed here because the order carries meaning. Each step has a serif title, two or three plain sentences, and where useful a drawing.

1. Predict. For each vessel the system predicts the next position from its recent track. A report that lands far from that is a flag. Keep the predicted-versus-reported SVG (`charts.js`) and restyle it: predicted is a dashed `--ink-3` line, reported is solid `--sea`, the flagged point is `--signal`.
2. Place it. One vessel off while its neighbours are fine points at spoofing aimed at that ship. Many vessels off in the same area point at jamming. Known jamming zones add to the picture. Keep the two fleet diagrams, restyled, captioned "One vessel" and "Many vessels".
3. Investigate. An agent pulls the track, the zone list and the vessel's history, states a hypothesis and how sure it is, and logs every step it took. Show the log as a plain timeline of rules and text, no cards.
4. Decide. An analyst confirms or rejects. Those verdicts are how GHAST finds out how often it is right. Link to Trust.

### Console (the showcase, plan 02 and 01 D surface)

Operate mode. This page is the visual spec the real dashboard in `frontend/dashboard/` will copy, and later it can become the dashboard itself. Build it as a working sample, not a screenshot.

Layout at desktop: a thin status strip on top, then two panes. Left pane (about 360px): the incident queue. Right pane: the open incident, scrolling on its own.

- Status strip: three quiet readings separated by rules: Database, Latest position, Latest incident, each with an age ("4 s ago"). Data source for plan 06 health. Right end holds the tag "Sample data".
- Queue: filter row (status: all, reported, escalated, resolved; hypothesis select), then rows. A row holds vessel name, hypothesis in words, time, and confidence as a plain mono number. An unreviewed incident has a 6px `--signal` square before the name. Selected row uses `--paper-deep`. No colored side stripes.
- Open incident, top to bottom, all on paper, separated by hairlines:
  1. Header: vessel name, MMSI and flagged time in mono, hypothesis as a serif sentence ("Targeted spoof, 81 percent sure"), status.
  2. Track: the map module (below).
  3. Evidence: a plain list of findings.
  4. Detector votes: a table. Columns: detector, result, value. Rows for the prediction error detector, the freeze check, and the Laya pattern classifier. The Laya row carries a small "shadow" tag (plan 04 rolls it out in shadow mode first).
  5. Fleet context: one sentence and the small clustered versus isolated diagram (plan 03 `fleet_context`).
  6. Agent log: timeline of tool calls with one line each.
  7. Report: the stored markdown report, rendered as prose.
  8. Review: radio list of the five verdicts, a notes field, and a button "Record verdict". In sample mode it updates the row in memory and shows "Recorded in this session only." Hidden for incidents already resolved, replaced by the verdict and who and when.
- Map module: an inline SVG chart on `--sea-tint` with a fine graticule, an abstract coastline, the reported track in `--sea`, predicted positions as open circles, the flagged point in `--signal`, and an optional jamming-zone polygon layer (hatched, `--ink-3`, empty when the data source returns no zones). Wrap it in `[data-module="map"]` and give it one render function so MapLibre can replace it later without touching the rest. Do not use an external tile service in this pass.
- Empty, loading and error states are designed, not afterthoughts: "No incidents match these filters.", "Loading incidents.", "The service did not answer. Retrying."
- Mobile: queue and detail become two screens with a back text link.

### Trust (plan 01 surface)

Read mode, short and confident. Headline: "How often is it right?" Opening paragraph in plain words: nobody can say yet, and this page shows how it gets measured.

Sections, each a headline and a few sentences plus one module:

1. The alert budget. A reviewer can only read so many flags. The operating threshold is chosen so that only a set number of reports per 1,000 get flagged. Module: a small table of flag rate options (5, 1, 0.5, 0.1 percent) with the threshold for each, marked sample, and an empty slot for "chosen".
2. The review loop. Every incident ends with a human verdict. Module: the five verdicts in a row with one line each.
3. The numbers, when there are enough. Module `[data-module="review-stats"]`: table of precision per hypothesis and per detector combination, with counts. Below 30 reviewed incidents it shows "Too few reviewed incidents to trust a rate. 0 of 30." Design this empty state well, it is what will actually be on screen.
4. What we do not claim. A short plain list of what is not measured yet. Honest and calm, no apology.

### Project

Short. Plain paragraph on what GHAST is, that it is a three-semester build, what is done and what is not, and the Source link. Credit the author and institution as the owner decides (see `PRODUCT.md`). Remove the quote, the "what we do differently" cards and any comparison copy. Four or five paragraphs at most.

## The framework: a data layer and named module slots

This is what lets backend work land without redesign. All rendering code talks to one facade and never to mock files directly.

### Files

```
site/scripts/
  config.js            window.GHAST_CONFIG = { source: "sample", apiBase: "", apiKey: "" }
  data/api.js          window.GHAST.data, the only thing UI code calls
  data/sample.js       sample implementation of the same methods
  data/live.js         fetch implementation (written now, unused until the API exists)
  mock/*.js            sample payloads, one file per resource
  modules/*.js         one file per module, each renders into its [data-module]
  ui/header.js, ui/reveal.js, ui/menu.js
```

`source` is read from `config.js` and can be overridden by `?source=live` for testing. `api.js` picks `sample.js` or `live.js` and exposes the same methods either way.

### Facade methods (mirror the plan 02 endpoints)

| Method | Endpoint it will call |
|---|---|
| `health()` | `GET /health` |
| `incidents({status, hypothesis, mmsi, limit, before})` | `GET /incidents` |
| `incident(id)` | `GET /incidents/{id}` |
| `track(mmsi, {from, to, includeHistorical})` | `GET /vessels/{mmsi}/track` |
| `vesselIncidents(mmsi)` | `GET /vessels/{mmsi}/incidents` |
| `zones()` | `GET /zones` (GeoJSON) |
| `review(id, {verdict, notes})` | `POST /incidents/{id}/review` |
| `reviewStats()` | a read endpoint plan 02 needs to add for plan 01 D3; sample only for now |
| `thresholds()` | sample only for now, from plan 01 Part B report output |

Every method returns a promise that resolves to data or rejects with an error object. Modules handle loading, empty and error. Live mode sends `X-API-Key` from config.

### Sample payload shapes

Shape them like plan 02 so the swap is a config change. Use these field names. The wiring pass will reconcile any differences against the real API, so keep every name in one place (the sample files).

Incident list item: `id`, `mmsi`, `vessel_name` (sample only), `flagged_at`, `hypothesis`, `confidence`, `status`, `review_verdict`, `reviewed_by`, `reviewed_at`.

Incident detail adds: `window_start`, `window_end`, `votes` (array of `{detector, fired, value, threshold, label, confidence, mode}`), `evidence` (array of `{text}`), `fleet_context` (`{cluster_vessels, radius_km, isolated}`), `tool_call_log` (array of `{tool, summary, at}`), `report_text` (markdown), `track` (array of `{time, lat, lon, predicted_lat, predicted_lon, flagged}`).

Write six to eight sample incidents that cover every hypothesis, every status, one reviewed and one escalated, one jamming cluster, one freeze replay, one benign. Vessel names are plainly fictional. Timestamps are relative to "now" so the page never looks stale.

### Module slots

Every backend plan has a fixed home. Each slot is an element with `data-module="<name>"` that renders real content if the facade returns data, and a designed empty state otherwise. Build the slot and the empty state now, even where the sample has data.

| Slot (`data-module`) | Page | Backend plan | Fills when |
|---|---|---|---|
| `status-strip` | console | 06 healthchecks, 02 `/health` | API exists |
| `incident-queue` | console | 02 `/incidents` | API exists |
| `incident-detail` | console | 02 `/incidents/{id}`, 01 C evidence | API exists |
| `detector-votes` | console | 01 C4, 04 Laya vote (shadow tag) | API exists |
| `fleet-context` | console | 03 B `fleet_context` | clustering done |
| `map` | console | 02 track, 03 A zones | API plus zones loaded |
| `review-form` | console | 01 D, 02 review POST | schema plus endpoint |
| `review-stats` | trust | 01 D3 `review_stats` | 30 or more reviews |
| `alert-budget` | trust | 01 A and B | real threshold chosen |
| `model-versions` | trust | 05 | a v2 exists, hidden until then |
| `artifact-provenance` | trust | 06 step 1 | artifacts fetched, hidden until then |

Hidden slots render nothing, not an empty box.

## Build order and commits

Commit at each milestone with a one-line conventional message, author `Vishmayraj <zalavishmayraj@gmail.com>`, then produce patches for `git am`.

1. `feat(site): new tokens, type, base styles and header` (tokens, base, nav, wordmark, menu, header.js; every page wired to the new header and fonts; old styles removed)
2. `feat(site): home page and hero rewrite`
3. `feat(site): how it works page`
4. `feat(site): data facade, sample payloads and module slots`
5. `feat(site): analyst console page`
6. `feat(site): trust and project pages`
7. `docs: update site README and frontend docs for the new structure` (update the structure tree in `frontend/site/README.md`, the site section of `docs/backend-and-frontend.md`, and `frontend/README.md`; do not rewrite the backend half)

Between milestones, do not run verification loops. Build, inspect once (desktop and a 390px mobile width together), fix in one batch, stop.

## Done when

- No em-dash, no italic word in a headline, no eyebrow, no three-up card row, no gradient, no shadow, no backdrop blur anywhere in `frontend/site/`.
- Home, how it works, console, trust and project all share one header and one type system.
- The console works end to end on sample data: filter the queue, open an incident, read every section, record a verdict.
- Every slot in the table exists in markup and has a designed empty state. Switching `config.js` to `source: "live"` with no API running shows the error state, not a broken page.
- Keyboard: every control reachable, visible focus, the menu closes on Escape.
- Contrast passes on paper for every text color. Body measure is 62ch.
- `impeccable detect --json` over the changed files run once at the end, findings fixed.
- No pip or npm installs were needed. Verify by reading the code and by opening pages through `python3 -m http.server` if a browser tool exists. State which parts were only read, not seen.

## Not in this pass

MapLibre and real tiles, the real API wiring, self-hosted fonts, the React dashboard idea from plan 02 (see plan 08: the dashboard stays vanilla), and any live data.
