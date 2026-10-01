# frontend/site/

Design rules and the handover guide are in `docs/frontend/`. This file is the file map.

The public GHAST site and the analyst console sample. Plain HTML, CSS and
JS, no build step, no framework. `assets/` lives inside `site/` so anything
that serves this directory (`frontend/Dockerfile`, a static host, a CDN)
gets the hero video with no extra copy step.

## Structure

```
site/
  index.html              home: hero, statement, playground, four routes
  flow.html               one line top to bottom; points fade in and out; page goes white to black
  how-it-works.html       predict, place it, investigate, decide
  console.html            analyst console on sample data (the dashboard spec)
  trust.html              alert budget, review loop, review statistics
  project.html            what GHAST is and where it stands
  assets/ghast-hero.mp4 / .webm, og-image.jpg, favicon.svg
  styles/
    tokens.css            white, ink, rules, one signal color; Newsreader, Geist, Geist Mono, Pinyon Script accents
    base.css              reset, layout, buttons, reveal, browser surfaces
    nav.css               header, mobile menu, footer
    hero.css              scroll-scrubbed hero (home only)
    pages.css             routes, steps, tables, shared patterns
    console.css           console layout
    flow.css              the Flow page
  scripts/
    config.js             source: "sample" or "live", apiBase, apiKey
    data/api.js           window.GHAST.data, the only thing UI code calls
    data/sample.js        sample implementation (fictional vessels, relative times)
    data/live.js          fetch implementation of the plan 02 endpoints
    console.js            console modules
    trust.js              trust modules
    scroll-hero.js        two-phase scroll-scrub (home only)
    ui.js                 header theme, mobile menu, split headlines, reveals, staggers
    smooth-scroll.js      inertial wheel scrolling (off for touch and reduced motion)
    playground.js         home page "Try to fool it" illustration
    flow.js               flow page: line fill, point fades, white to black shift
```

The pages are plain static HTML. Fonts load from Google Fonts with real
fallbacks. Self-hosting them is a follow-up.

## Data and module slots

UI code never reads sample files directly. It calls `GHAST.data.*`, which
resolves to the sample or live implementation from `config.js` (or
`?source=live`). Field names match plan 02, so going live is a config change.
If the API differs, change `data/sample.js` and `data/live.js`, not the modules.

Each backend feature has a fixed `data-module` slot:

| Slot | Page | Backend plan |
|---|---|---|
| `status-strip` | console | 06 healthchecks, 02 `/health` |
| `incident-queue` | console | 02 `/incidents` |
| `incident-detail` | console | 02 `/incidents/{id}`, 01 C |
| `detector-votes` | console | 01 C4, 04 (shadow tag) |
| `fleet-context` | console | 03 B |
| `map` | console | 02 track, 03 A zones |
| `review-form` | console | 01 D, 02 review POST |
| `alert-budget` | trust | 01 A and B |
| `review-stats` | trust | 01 D3 (needs a read endpoint in plan 02) |

`model-versions` (plan 05) and `artifact-provenance` (plan 06) have no slot yet.
Add one to `trust.html` when those exist.

## The hero video's encoding matters

`assets/ghast-hero.{mp4,webm}` is re-encoded with a keyframe every 4
frames (`-g 4 -keyint_min 4`), not whatever a default encode gives
you. `video.currentTime` seeks have to decode forward from the
nearest keyframe, so a long GOP (the original render had exactly 2
keyframes across the whole 10s clip) makes every scroll-driven seek
decode up to seconds of frames — the scrubbing lags behind the
scroll and only catches up once you stop. If this video ever gets
regenerated, re-encode it the same way before dropping it in:

```
ffmpeg -i <source> -an -c:v libx264 -preset slow -crf 22 \
  -g 4 -keyint_min 4 -sc_threshold 0 -pix_fmt yuv420p \
  -movflags +faststart ghast-hero.mp4

ffmpeg -i <source> -an -c:v libvpx-vp9 -crf 30 -b:v 0 \
  -g 4 -keyint_min 4 -pix_fmt yuv420p -row-mt 1 \
  ghast-hero.webm
```

(`-an` drops audio. The element is always muted, so it's dead
weight.)

## The hero — two phases over one scroll-scrubbed video

`.hero` is a tall wrapper (several viewport heights); `.hero__sticky`
inside it uses `position: sticky` to stay pinned to the viewport while
that scroll distance is consumed. `scroll-hero.js` only ever *reads*
scroll position on the normal document and writes derived values back
(`video.currentTime`, a `--reveal` custom property per title line) —
it never intercepts the wheel or touch scroll, so the page always
scrolls like a normal page.

The video's own choreography splits into two parts, and the site
mirrors that split (`PHASE_SPLIT` in `scroll-hero.js`, currently
0.55 — the fraction of the clip at which the anomaly is visually
flagged):

- **Phase A** (0 → flag moment): pure cold open, no UI at all.
- **Phase B** (flag moment → clip end): the video keeps scrubbing,
  pinned in the background, while the title card's lines scroll up
  into place over it — each line driven by the same scroll read, so
  it reads as continuous scrolling rather than a cut to a new screen.
  Add more lines by giving each a `data-hero-line="start,end"` range
  in `index.html`; the script needs no changes.

`prefers-reduced-motion: reduce` gets a materially different
experience, not just a shorter transition: the video plays once,
normally, unpinned, and the scroll-scrub logic doesn't run at all.

## Sample data

Anything that looks like product output comes from `scripts/data/sample.js`
and carries a "Sample data" tag. The vessels are fictional. Nothing on the
site states an accuracy figure.
