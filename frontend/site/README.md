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
  index.html            # single page; sections load in document order
  styles/
    tokens.css           # palette, type scale, spacing — anchored on the
                          # hero video's #0096c7 ocean color
    base.css             # reset, layout primitives, scroll-reveal utility
    hero.css             # pinned/scroll-scrubbed cinematic hero
    sections.css          # explainer sections below the fold
  scripts/
    scroll-hero.js        # maps scroll progress -> video currentTime
    reveal.js              # IntersectionObserver-based scroll reveals
    mock-data.js            # illustrative vessel/incident data (see below)
    charts.js                # tiny inline-SVG chart/diagram helpers
```

## The hero

`.hero` is a tall wrapper (several viewport heights); `.hero__sticky`
inside it uses `position: sticky` to stay pinned to the viewport while
that scroll distance is consumed. `scroll-hero.js` only ever *reads*
scroll position on the normal document and writes it to
`video.currentTime` — it never intercepts the wheel or touch scroll,
so the page always scrolls like a normal page.

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
