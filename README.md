# Mississippi election night 2026: counties vs our model

A standalone page (separate from polling-agg's election-night data desk and from the Florida page): a 3D map of
Mississippi's counties with each county's counted vote against what the 2026 margin model expected there, for
the Senate race (Hyde-Smith vs Colom).

- `index.html` fetches NYT's live Mississippi results in the browser every 60 s (no backend).
- `ms_bench.js` (from `build.py`): each county's turnout weight (2024 Senate vote: Mississippi had no statewide
  race in 2022), its lean (2024 President + 2020 Senate, Hyde-Smith vs Espy, averaged on log-odds), and the
  expected share if the state lands on the model's margin (one uniform log-odds shift), the method of
  polling-agg's county benchmarks.
- `ms_geo.js` (from `build.py`): Mississippi county shapes from us-atlas (Census cartographic boundaries).

Rebuild the inputs: `py build.py` (reads the polling-agg repo's NYT archive and forecast).

## Projection and markets (live job)

Kalshi refuses cross-site browser requests, so prices come from a GitHub Actions job: `night.py` (workflow
`Mississippi live`, started by hand: `gh workflow run night.yml`, one job runs up to 340 minutes) every 30 s
reads NYT's count, runs `county_project.py` (polling-agg's projector v1, copied unchanged) from the margin
model's start (Colom -5.54, sd 6.1, as the desk), reads the Kalshi (SENATEMS-26-D/R) and Polymarket books, and
writes `live.json` to the orphan branch `live`. The page shows our win chance, the projected final margin with
its 80% band, the count-only margin, each venue's bid/ask (the cheapest buy of each side: YES on it or NO on the
other), taker fees, edge after fee, the desk's FLIP rule, and two charts over the night.

## Late count

The job runs through Dec 14 (a pass every 60 s; each job dispatches the next, and a cron restarts it if one dies). `live.json`
keeps a history row whenever the count, projection or a price moved. The raw NYT file for every change is recorded by
polling-agg's cloud job in the release `election-night-2026`.
