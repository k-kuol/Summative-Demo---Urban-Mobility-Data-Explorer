# Video walkthrough plan (5 minutes)

A shot list and talking points, not a script to read word for word. Say it
in your own words; the timings add up to 5:00.

| Time | Show on screen | Cover |
|------|----------------|-------|
| 0:00–0:30 | Dashboard landing page | The question: how does New York move by yellow cab, and what does congestion cost riders? The data: 7.67M rides from January 2019, plus the zone lookup and zone shapes. |
| 0:30–1:30 | README architecture diagram | Walk the diagram left to right: raw files, the 4 pipeline scripts, the SQLite star schema, the Flask API, the browser. Why SQLite (one file, no server, enough for read-only analytics). Why summary tables (100k rows instead of 7.6M for most filters) and the trade-off (they only cover date/zone/hour filters; others fall back to `trips`). |
| 1:30–2:15 | `clean_data.py` rules, then the "What we removed" panel | 10 exclusion rules, first-match-wins so every removed ride has one reason. Examples: a $623,259 fare, pickups dated 2001 and 2088, 48,691 zero-mile rides. The repair log: passenger count 0, rate code 99, vendor 4. The surprise: cash rides record 0% tips, so tip % is card-only. |
| 2:15–3:00 | `algorithms.py` | The top-routes problem: ~40k zone pairs, need 10. Min-heap of size k: O(n log k) vs O(n log n) for sorting. Quickselect for median fare, with 3-way partitioning because thousands of fares are identical ($52 JFK flat fare). Run `python backend/algorithms.py` to show the self-tests pass. |
| 3:00–4:30 | The dashboard, live | Filters (borough, hour, fare). Click a zone on the map to open its detail panel (top destinations come from count_by + top_k, median fare from quickselect). Speed-by-hour chart: insight 1. Weekday × hour heatmap: insight 2. Top routes table: insight 3. Sort the trip table by fare. |
| 4:30–5:00 | README / deployed URL | How to run it (one command per step), the deployed demo, and one thing you would build next. |

## Before recording

- Run the full database locally so the numbers on screen match the report.
- Close other tabs and notifications; zoom the browser to 110% so text is readable.
- Record in one take per section; cut them together afterwards.
