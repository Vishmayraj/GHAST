# GHAST site design system

The rules the site is built on. If you change the look, change this file too.
Product context (who it is for, what it may claim) is in `PRODUCT.md` at the repo root.

## The idea

A chart room: white paper, dark ink, hairline rules, and one signal color for the thing that needs attention. Quiet, exact and unhurried. Premium here means restraint, space and motion that explains something. It does not mean effects.

Three modes, one per kind of page:

| Mode | Pages | What matters |
|---|---|---|
| Persuade and read | home, how-it-works, project | clear argument, reading comfort |
| Story | flow | one idea at a time, motion carries it |
| Operate | console, trust | scanning, consistency, no surprises |

## Color

Defined in `styles/tokens.css`. Never hard-code a color in a page, use the token.

| Token | Value | Use |
|---|---|---|
| `--paper` | `#ffffff` | page background |
| `--paper-deep` | `#f2f4f6` | hover and selected rows, quiet panels |
| `--ink` | `#0a1a2b` | text, strong rules, hero background |
| `--ink-2` | `#3a4a5c` | secondary text |
| `--ink-3` | `#5a6674` | meta text, dashed prediction lines |
| `--rule` | ink at 14% | hairlines |
| `--sea` | `#1f4e79` | the reported track, data lines only |
| `--sea-tint` | `#eef2f6` | map and chart water |
| `--signal` | `#d8431f` | flagged or unreviewed. Nothing else |

Signal red marks the flagged point, the unreviewed dot in the queue, and the flagged hypothesis word. If it shows up on decoration, remove it. No gradients, glows or shadows. Depth comes from rules and from `--paper-deep`.

The owner has said twice that they do not want cream or beige. Keep the page white.

The Flow page rewrites `--paper`, `--ink`, `--ink-2`, `--ink-3`, `--rule` and `--signal` on `:root` from JavaScript (see Flow below), so everything that uses the tokens, including the header, follows.

## Type

Fonts load from Google Fonts in each page's `<head>` (see the font note in the handover README).

| Role | Family | Notes |
|---|---|---|
| Headlines | Newsreader 400 | tracking -0.02em, line-height 1.05, `text-wrap: balance` |
| Body and UI | Geist 400, 500 | body 19px at 1.65, measure 62ch |
| Data | Geist Mono | MMSI, times, thresholds, table numbers, tabular numerals. Never for decoration |
| Accent | Pinyon Script | the `.script` class. A single short word per headline, set 1.35em. Never for body text, never small |

Sizes are tokens: `--fs-meta` 15px, `--fs-small` 17px, `--fs-body` 19px, `--fs-h3` 26px, `--fs-h2` up to 52px, `--fs-h1` up to 96px. Small text was made bigger on purpose after feedback. Do not go below `--fs-meta` for anything a person has to read.

Alignment: left by default. About half the sections flip to right-aligned with `.split--flip` (heading on the right, text right-aligned). Do not center body text. Centered is only for the two centered points on the Flow page.

## Space and layout

- 12 column grid, content width 1200px, 24px gutters (`.wrap`, `.split`).
- A `.split` puts the heading in columns 1 to 6 and the text in 7 to 12. `.split--flip` mirrors it.
- Section padding is `--pad-section`, about 56px on a laptop to 104px on a large screen. Owner feedback: less whitespace than the first version. Do not add more.
- Sections are separated by a single hairline, never by a background change.
- Corners: 2px on buttons and inputs, 0 everywhere else. No cards as page structure. Lists are rows with hairlines.
- Below 860px everything stacks to one column. The header becomes a Menu button below 960px.

## Components

| Component | Where | Notes |
|---|---|---|
| Header | every page | wordmark left, text links right. Transparent over the home hero, white after it (`data-theme`) |
| Wordmark | header, favicon | a short track, a dashed gap, an open circle (predicted) and a filled circle (reported) |
| Button `.btn` | console | solid ink, white text, 2px radius, lifts 2px on hover |
| Text link `.link` | everywhere | 1px underline at 6px offset |
| Tag `.tag` | sample data, shadow | small outlined label |
| Routes `.routes` | home | big serif rows separated by rules, the title slides on hover |
| Table `.tbl` | console, trust | ink rule under the header, hairlines between rows |
| Log `.log` | how-it-works | a timeline of rows that slide in |
| Empty state `.empty` | trust, console | rule above and below, plain sentence |
| `kbd` | console | key hints |

## Motion

Everything respects `prefers-reduced-motion`: content shows immediately and nothing moves.

Reveal system (`scripts/ui.js` and `styles/base.css`):

| Attribute or class | Effect |
|---|---|
| `data-reveal` | fades and rises into view. `data-reveal="left"`, `"right"`, `"scale"` change the direction |
| `data-stagger` on a parent | its children reveal one after another, 110ms apart |
| `data-split` on a heading | each word rises out of a mask. Script words are cloned word by word |
| `data-draw` on a container | starts the `.draw` strokes and `.pop` dots inside it |
| `.draw` | an SVG stroke that draws itself. Set `--len` to the path length |
| `.pop` | an SVG shape that scales and fades in. `--d` delays it |
| `.ping` | a repeating pulse ring, used only on the flagged point |
| `--d` | any element can set `--d: 300ms` to delay its own transition |

Rules: one easing for entrances (`--ease`, a strong ease-out), one for drawing (`--ease-io`). Animate only `opacity` and `transform` (and SVG stroke offsets). Never animate layout properties.

Page glide (`scripts/smooth-scroll.js`): wheel scrolling keeps moving after the wheel stops and slowly loses speed. The strength is `EASE` at the top of the file. Lower is a longer glide (now 0.048). It is off for touch devices and reduced motion, and it leaves keyboard, scrollbar drag and anchor jumps alone. Elements with `data-native-scroll`, textareas and selects keep normal wheel behavior.

Home hero (`scripts/scroll-hero.js`, `styles/hero.css`): scroll sets a target position, and the video time and the title lines ease toward it every frame so the footage glides. The hero is 300vh tall (260vh on phones). The video is encoded with frequent keyframes so seeking is cheap. Do not re-encode it without keeping that (see `frontend/site/README.md`).

## The Flow page

`flow.html`, `styles/flow.css`, `scripts/flow.js`. One vertical line runs top to bottom. Eight points sit on it, each with a node, a text block and a small drawing.

- Layout: points alternate left (`pt--L`), right (`pt--R`) and centered (`pt--C`). A centered point has a non-fading cover behind it so the line does not run through the text, and its node sits above the text.
- The line fills with scroll (`--fill` on `.flow`).
- Each point fades in and out around the middle of the screen. Opacity is a function of its distance from the viewport center, and the point also drifts a few pixels. A point is `is-active` above 0.6 opacity. That class starts the drawings inside it.
- Color: white for points 1 to 3, black from point 4. The shift is driven by scroll and happens in the gap between point 3 and point 4, where both are faded out, so no text is ever on screen at mid-gray. This replaced a continuous white to black gradient that was unreadable in the middle. To move the switch, change `SWITCH` in `flow.js`.
- To add a point, copy a `.pt` block, pick a side class, and keep the order of numbers. Test it at 390px wide.

## Voice

Plain, specific and short. Written by a person who knows the product.

- No em-dashes, curly quotes or other non-keyboard characters. ASCII only.
- No "X, not Y" headlines, nautical puns, eyebrow labels above every heading, or sentences that explain the page.
- Say what the product does for an analyst, not which algorithm is deployed.
- Talk to the reader. "Try to fool it." "Good evening. 5 incidents are waiting for a verdict."

## Honesty rules

These come from `PRODUCT.md` and the backend plans. They are not up for debate in a design pass.

1. No accuracy, F1, precision, recall or benchmark number anywhere. None has been measured on real data yet.
2. Anything that looks like product output is sample data and is tagged "Sample data" or "Illustration". Tag it once, on the module.
3. Use the real vocabulary. Hypotheses: `jamming`, `targeted_spoof`, `equipment_fault`, `freeze_replay`, `benign`. Verdicts: `confirmed_spoof`, `jamming`, `equipment_fault`, `benign`, `unclear`. Statuses: `reported`, `escalated`, `resolved`.
4. Do not describe a component as running if it is only planned.
5. The team is "a small team". Do not credit one person.

## Accessibility

- Text contrast is at least 4.5:1 on white. `--ink-3` is the lightest text color allowed.
- Every control is keyboard reachable with a visible 2px focus ring. The playground dot moves with the arrow keys. The console takes `j` and `k` to move through the queue and `1` to `5` to pick a verdict.
- Decorative SVGs are `aria-hidden`. Informative ones have a `role="img"` and a label.
- The Menu closes on Escape.
- The mid-flip on the Flow page is the one place where contrast dips for a moment. Nothing readable is on screen then.

## Things not to bring back

Frosted or blurred header, glow dots, pulsing eyebrow badges, gradient washes, serif italics inside headlines, three identical cards in a row, fake browser chrome, pill badges, a quote interlude, claims about unbuilt features, cream backgrounds.
