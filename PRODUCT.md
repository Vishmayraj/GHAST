# Product

<!-- impeccable:product-schema 1 -->

Written from the owner's brief and the existing site, not from an interview. Anything marked (assumed) is unconfirmed.

## Platform

web

## Stack

Static HTML, CSS and vanilla JS. No framework, no build step. Served by nginx from `frontend/site/` (see `frontend/Dockerfile`). Owner decision: optimization is not a concern, and staying vanilla is what keeps the site simple to ship.

## Users

Maritime risk and security analysts who review flagged vessel-position anomalies and decide what each one is (assumed from existing site copy: insurers, fleet security teams). A second audience right now is whoever watches the project presentation.

## Product Purpose

GHAST (GNSS & AIS Hazardous Spoofing Tracker) watches vessel position reports, flags the ones that do not fit how that vessel should be moving, and hands an analyst an incident: a hypothesis, the evidence behind it, and a confidence. Success is an analyst who can tell targeted spoofing from area jamming, see why the system thinks so, and record a verdict.

## Positioning

It judges behavior, not a list of known bad zones. Every incident carries its evidence and the log of what the investigating agent did. Analyst verdicts feed a real precision number, so the product can say how often it is right instead of claiming it.

## Operating Context

An analyst works a queue of incidents newest first, opens one, reads the evidence and report, and closes it with a verdict. Hypotheses the agent can produce: jamming, targeted_spoof, equipment_fault, freeze_replay, benign. Verdicts an analyst can give: confirmed_spoof, jamming, equipment_fault, benign, unclear.

## Capabilities and Constraints

- The backend is under active work and changes daily. The site shows the product's end purpose, not today's backend state.
- No measured accuracy exists yet. The site must not state an F1, precision, recall, accuracy or benchmark figure anywhere.
- Anything on the site that looks like product output is sample data and is labeled as such.
- Use the real vocabulary above for hypotheses and verdicts.

## Brand Commitments

- Name: GHAST.
- Must look premium, clean and spacious, and must not look like AI-generated marketing. Copy must read as written by a person: plain and specific, no marketing cliches.
- The hero video (`frontend/site/assets/ghast-hero.*`) stays.

## Open Decisions

- Base map tile source for the real dashboard (plan 02).
- Whether the marketing mock stays labeled sample data or later reads from the live API.
- How the author and institution should be credited on the project page.
