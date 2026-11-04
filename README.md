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
