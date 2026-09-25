# frontend/site/

The public GHAST landing site. Plain HTML/CSS/JS, no build step, no
framework — the scroll-driven hero, reveal animations and mock data
views here don't need one, and adding React/webpack for a static
explainer page would be complexity without a corresponding need.
(The analyst dashboard in `frontend/dashboard/` is a different
problem — real-time map state, incident filtering, drill-down — and
is where the HLD's React + MapLibre/Deck.gl choice actually applies.)

## Structure

```
site/
  index.html            # home: hero + short teaser + links out
  about.html             # differentiation / "what we do differently"
  approach.html           # detection methodology + fleet differentiation
  platform.html             # investigation agent + dashboard preview
  styles/
    tokens.css           # 5-color palette (ocean/navy/red/white/black),
                          # Sora + Space Grotesk type scale
    base.css             # reset, layout primitives, page-header pattern
    nav.css               # shared fixed top nav (all pages)
    hero.css              # pinned/scroll-scrubbed cinematic hero (index only)
    sections.css            # explainer sections used on approach/platform/about
  scripts/
    scroll-hero.js        # two-phase scroll-scrub (index only, see below)
    reveal.js              # IntersectionObserver-based scroll reveals
    mock-data.js            # illustrative vessel/incident data (see below)
    charts.js                # tiny inline-SVG chart/diagram helper
    sections.js               # renders mock data into the DOM (approach/platform)
```

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

## Mock data

Anywhere the site shows something that looks like real platform
output (incident reports, the dashboard preview, deviation charts),
the values come from `scripts/mock-data.js` rather than being
hand-written into the markup, and are visibly labeled as illustrative.
The intent is that this file is exactly what a real API response
would replace — the DOM-building code doesn't know or care whether
the object came from a `<script>` file or `fetch()`.
