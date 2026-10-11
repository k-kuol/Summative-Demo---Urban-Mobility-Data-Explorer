# NYC Urban Mobility Data Explorer — Team Guide

This file is the single source of truth for the project.
Every step every person needs to take is written here in order.
Start from the top of your section and work down. Do not skip steps.

---

## Who owns what

| Member | Role | Files |
|--------|------|-------|
| **Abay Tessema** | Data Engineering | `pipeline/profile_data.py`, `pipeline/clean_data.py`, `pipeline/convert_zones.py`, `pipeline/make_sample.py`, `database/load_database.py` |
| **Alier Akuang** | Database & API | `database/schema.sql`, `database/indexes.sql`, `backend/db.py`, `backend/app.py` (all endpoints except `/api/top-routes` and `/api/zones/<id>`), `README.md`, `requirements.txt` |
| **Kuol Akech** | Frontend | `frontend/index.html`, `frontend/css/style.css`, `frontend/js/api.js`, `frontend/js/main.js` |
| **Chol Mach Kuol** | Algorithms & Visualisation | `backend/algorithms.py`, `frontend/js/charts.js`, `frontend/js/map.js`, `/api/top-routes` and `/api/zones/<id>` in `backend/app.py` |

Never edit a file you do not own. If you need a change in someone else's file, message them.

---

## Step 0 — Every person does this first (before anything else)

### 0.1 Install Python 3.10 or higher
Check your version:
```bash
python3 --version
```
If it says 3.9 or lower, download Python from https://python.org.

### 0.2 Install Git
```bash
git --version
```
If not installed, download from https://git-scm.com.

### 0.3 Clone the repo
```bash
git clone https://github.com/apiel-max/MyTaxiApp.git
cd MyTaxiApp
```

### 0.4 Create a virtual environment and install dependencies
```bash
python3 -m venv venv

# On Mac/Linux:
source venv/bin/activate

# On Windows:
venv\Scripts\activate

pip install -r requirements.txt
```

You should see flask, pandas, and numpy install. If `requirements.txt` is missing, ask Alier.

### 0.5 Create your personal branch
```bash
# Abay:
git checkout -b abay/pipeline

# Alier:
git checkout -b alier/database

# Kuol:
git checkout -b kuol/frontend

# Chol:
git checkout -b chol/algorithms
```

### 0.6 How to commit and push your work
Every time you finish a piece of work:
```bash
git add <filename>
git commit -m "short description of exactly what you did"
git push origin <your-branch-name>
```

Then go to GitHub, open a Pull Request from your branch to `main`, and tag one teammate to review.

---

---

# ABAY TESSEMA — Data Engineering

Your job produces everything the rest of the team depends on.
Nothing else in the project runs until you finish Tasks 1–5.

## What you will produce
```
data/raw/                          ← you put the raw files here manually
data/processed/trips_clean.csv     ← clean rides ready for the database
data/processed/excluded_records.csv
data/processed/suspicious_records.csv
data/processed/cleaning_summary.json
data/processed/taxi_zones.geojson
data/mobility.db                   ← the SQLite database
docs/data_profile.txt              ← commit this
```

---

## Task 1 — Get the raw data files

You need three things from the NYC TLC website (https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page):

1. `yellow_tripdata_2019-01.csv` — January 2019 yellow taxi trips
   (the TLC site gives a `.parquet` file — open it in Python and save as CSV,
   or ask the team if someone already has the CSV)
2. `taxi_zone_lookup.csv` — the zone ID to borough/name mapping
3. `taxi_zones/` folder — the shapefile (download the ZIP, unzip it)

Place them like this:
```
data/raw/
  yellow_tripdata_2019-01.csv
  taxi_zone_lookup.csv
  taxi_zones/
    taxi_zones.shp
    taxi_zones.dbf
    taxi_zones.prj
    taxi_zones.shx
```

If you downloaded a `.parquet` file instead of CSV, convert it:
```python
import pandas as pd
df = pd.read_parquet("yellow_tripdata_2019-01.parquet")
df.to_csv("data/raw/yellow_tripdata_2019-01.csv", index=False)
```

Once you have the files, upload them to Google Drive and share the link
with the team so everyone can run the pipeline on their own machine.

---

## Task 2 — Make a small sample for fast testing

The full CSV is ~700MB and takes minutes to process. Make a 5% sample first
so you can test quickly:

```bash
python pipeline/make_sample.py data/raw/yellow_tripdata_2019-01.csv 0.05
```

Expected output:
```
  read 500,000 rows...
  read 1,000,000 rows...
  ...
Done. Read 7,667,792 rides, kept 383,389 (5%).
Sample saved to: data/raw/yellow_tripdata_sample.csv  (21.3 MB)
```

To use the sample instead of the full file, open `pipeline/clean_data.py`
and change line 33:
```python
# Change this:
RAW_CSV = ROOT / "data" / "raw" / "yellow_tripdata_2019-01.csv"

# To this for testing:
RAW_CSV = ROOT / "data" / "raw" / "yellow_tripdata_sample.csv"
```
Change it back to the full file before the final submission run.

---

## Task 3 — Profile the raw data

This reads the raw CSV and prints a quality report. Run it before cleaning
so you know what problems exist and have numbers for your report.

```bash
python pipeline/profile_data.py
```

This takes about 2 minutes on the full file. Expected output:
```
Loading yellow_tripdata_2019-01.csv ...
Rows: 7,667,792   Columns: 18

=== MISSING VALUES ===
  congestion_surcharge        7,667,792  (100.00%)
  passenger_count                 ...

=== DUPLICATES ===
  exact duplicate rows              0  ( 0.00%)

=== TIMESTAMPS ===
  pickup range: 2008-12-31 ...  ->  2019-02-28 ...
  pickup outside Jan 2019         4,123  ( 0.05%)
  ...

=== DISTANCE (miles) ===
  zero distance              14,302  ( 0.19%)
  ...

Saved to docs/data_profile.txt
```

The output is saved to `docs/data_profile.txt`. Commit it:
```bash
git add docs/data_profile.txt
git commit -m "pipeline: add raw data profile — 7.6M rows, key outliers documented"
git push origin abay/pipeline
```

---

## Task 4 — Clean the data

```bash
python pipeline/clean_data.py
```

This takes about 45 seconds on the sample, 5–8 minutes on the full file.
Expected output:
```
Loading yellow_tripdata_2019-01.csv ...
  7,667,792 rides loaded
Applying exclusion rules ...
  pickup_outside_jan_2019            4,123
  dropoff_not_after_pickup              87
  duration_over_6h                   1,204
  zero_distance                     14,302
  distance_over_100mi                  231
  fare_zero_or_negative             12,847
  fare_over_500                         43
  negative_charge_component          1,109
  speed_over_80mph                   3,201
  TOTAL EXCLUDED                    37,147  (0.48%)
Repairing and normalizing kept rides ...
  congestion_surcharge_filled    7,630,645
  passenger_count_0_set_unknown    108,432
  ...
Adding derived features ...
Saving outputs ...

Done in 312s: 7,630,645 clean rides (37,147 excluded, 214,832 repaired/flagged).
```

Check the outputs exist:
```bash
ls -lh data/processed/
```
You should see:
```
trips_clean.csv          (~1.8 GB)
excluded_records.csv     (~8 MB)
suspicious_records.csv   (~45 MB)
cleaning_summary.json    (~1 KB)
```

If you see `FileNotFoundError: yellow_tripdata_2019-01.csv` — the raw file
is missing from `data/raw/`. Go back to Task 1.

If you see `ValueError: X rides reference zones missing from the lookup` —
your `taxi_zone_lookup.csv` is incomplete. Re-download it.

Commit the summary (not the large CSVs — they are in `.gitignore`):
```bash
git add data/processed/cleaning_summary.json
git commit -m "pipeline: cleaning complete — 37k rides excluded, 9 rules applied"
git push origin abay/pipeline
```

---

## Task 5 — Convert the zone shapefile to GeoJSON

```bash
python pipeline/convert_zones.py
```

Expected output:
```
  note: LocationID 56 had 2 separate shapes -> merged
  note: LocationID 103 had 3 separate shapes -> merged
Wrote 262 zones (265 raw shapes) to data/processed/taxi_zones.geojson (1.8 MB)
```

If you see `FileNotFoundError: taxi_zones.shp` — the shapefile folder is
missing from `data/raw/taxi_zones/`. Go back to Task 1.

If you see fewer than 260 zones — the `.dbf` or `.shp` file is corrupted.
Re-download the shapefile ZIP and unzip again.

---

## Task 6 — Build the database

Run this after Tasks 4 and 5 are both complete.

For testing (fast, loads 10% of trips, ~30 seconds):
```bash
python database/load_database.py --sample 0.1
```

For the final submission (full dataset, ~3–5 minutes):
```bash
python database/load_database.py
```

Expected output:
```
Creating tables ...
Loading dimension tables ...
  7 boroughs, 265 zones (262 with map shapes), 3 vendors, 6 rate codes, 6 payment types
Loading trips ...
     250,000 trips inserted
     500,000 trips inserted
     ...
   7,630,645 trips inserted
Loading exclusion log ...
  37,147 excluded records
Building summary tables ...
  zone_date_hour_stats: 98,432 rows
  route_date_stats: 487,201 rows
Building indexes ...
Verifying ...
  foreign keys OK, row counts OK, summary tables add up

Done in 187s -> data/mobility.db (2.71 GB)
```

If you see `Foreign key violations` — re-run Tasks 4 and 5 first, then
delete `data/mobility.db` and run this again.

---

## Task 7 — Verify the server starts

```bash
python backend/app.py
```

Open a second terminal and run:
```bash
curl http://127.0.0.1:5000/api/health
```

Expected:
```json
{"status": "ok", "trips_in_database": 7630645}
```

If you see `Database not found` — check that `data/mobility.db` exists.
If you see `Connection refused` — the server is not running. Check the first terminal for errors.

---

## Task 8 — Commit your final outputs and open a PR

```bash
git add data/processed/cleaning_summary.json
git add docs/data_profile.txt
git commit -m "pipeline: all outputs ready — db built, 7.6M trips, 262 zones"
git push origin abay/pipeline
```

Go to GitHub → open a Pull Request from `abay/pipeline` to `main`.
Tag Alier to review (he needs the database to test his endpoints).

---

## Your report — Section 1 (400–500 words)

Write this after running the profiler so you have real numbers.
Answer these four questions in your own words:

1. **What is the dataset?**
   NYC TLC yellow taxi, January 2019. How many rows, what columns, what each column means.

2. **What data problems did you find?**
   Use the exact numbers from `docs/data_profile.txt`.
   Example: "14,302 rides had zero trip distance. 87 rides had a dropoff timestamp
   before the pickup. 4,123 rides had a pickup date outside January 2019."

3. **Why did you choose each cleaning threshold?**
   Example: "We set the maximum duration to 6 hours because the distribution
   showed a clear spike at 24 hours — those are meters left running overnight,
   not real trips. The 99.9th percentile of real trips was under 2 hours."

4. **One thing that surprised you.**
   Example: "`congestion_surcharge` was NULL for every single ride in January 2019.
   We discovered the surcharge only started in February 2019, so NULL means
   'not yet charged', not 'missing data'. We filled it with 0.0."

---

---

# ALIER AKUANG — Database & API

Your job is to own the schema, the DB connection layer, and all API endpoints
except the two that use Chol's algorithms. You also own the README and
requirements.txt, and you set up the GitHub repo.

## What you need before you start
- Abay must have finished Task 6 (the database must exist at `data/mobility.db`)
- If Abay is not done yet, ask him for the `--sample 0.1` version of the database
  so you can test your endpoints while he finishes the full run

---

## Task 1 — Set up the GitHub repository

1. Go to https://github.com and create a new repository called `MyTaxiApp`
2. Set it to **Private** (or Public if the team agrees)
3. Go to Settings → Collaborators → Add people
   Add: Abay, Kuol, Chol by their GitHub usernames
4. Go to Settings → Branches → Add branch protection rule
   - Branch name pattern: `main`
   - Check "Require a pull request before merging"
   - Check "Require at least 1 approval"
   - Save

Push the initial code:
```bash
cd MyTaxiApp
git remote set-url origin https://github.com/<your-username>/MyTaxiApp.git
git push -u origin main
```

---

## Task 2 — Read schema.sql top to bottom

Open `database/schema.sql` and read every line. You need to be able to explain
every table in the viva without looking at notes. The structure is:

**Dimension tables** (small lookup tables, rarely change):
- `boroughs` — 7 rows, one per NYC borough
- `zones` — 265 rows, one per taxi zone. Stores the GeoJSON polygon shape.
- `vendors`, `rate_codes`, `payment_types` — small code tables

**Fact table** — `trips` — one row per cleaned ride, 29 columns.
References all dimension tables by integer ID. A borough name is stored once
in `boroughs`, not repeated 7.5 million times in `trips`.

**Transparency tables**:
- `exclusion_reasons` — the 9 cleaning rules with human-readable descriptions
- `excluded_records` — every removed ride with its reason code

**Summary tables** (pre-aggregated, rebuilt by `load_database.py`):
- `zone_date_hour_stats` — one row per date × zone × hour (~100k rows).
  Most dashboard queries hit this instead of scanning 7.5M trips.
- `route_date_stats` — one row per date × pickup zone × dropoff zone.

These summary tables store **SUMS and COUNTS, never averages**. This is
important — if you store averages you cannot combine rows correctly later.

---

## Task 3 — Start the server and test every endpoint you own

```bash
python backend/app.py
```

You should see:
```
Dashboard running at http://127.0.0.1:5000  (press Ctrl+C to stop)
```

Open a second terminal. Test each endpoint:

```bash
# 1. Health check
curl -s http://127.0.0.1:5000/api/health | python3 -m json.tool
# Expected: {"status": "ok", "trips_in_database": 7630645}

# 2. Meta — boroughs, zones, payment types, date range
curl -s http://127.0.0.1:5000/api/meta | python3 -m json.tool
# Expected: {"boroughs": [...], "zones": [...], "payment_types": [...], "date_range": {...}}

# 3. Summary — headline KPI numbers
curl -s "http://127.0.0.1:5000/api/summary" | python3 -m json.tool
# Expected: {"trips": 7630645, "avg_fare": 13.xx, "avg_speed_mph": 12.xx, ...}

# 4. Summary with filters — Manhattan, morning rush
curl -s "http://127.0.0.1:5000/api/summary?borough_id=1&hour_min=7&hour_max=9" | python3 -m json.tool

# 5. By hour — 24 rows, one per hour of day
curl -s "http://127.0.0.1:5000/api/by-hour" | python3 -m json.tool

# 6. Heatmap — 7 days × 24 hours grid
curl -s "http://127.0.0.1:5000/api/heatmap" | python3 -m json.tool

# 7. By day — one row per calendar date in January
curl -s "http://127.0.0.1:5000/api/by-day" | python3 -m json.tool

# 8. Zone stats — one row per pickup zone (used to colour the map)
curl -s "http://127.0.0.1:5000/api/zone-stats" | python3 -m json.tool

# 9. Trips — paginated list, sorted by fare descending
curl -s "http://127.0.0.1:5000/api/trips?sort=fare_amount&order=desc&per_page=5" | python3 -m json.tool

# 10. Data quality — exclusion log
curl -s "http://127.0.0.1:5000/api/data-quality" | python3 -m json.tool
```

Every endpoint must return HTTP 200 with valid JSON.
- If you get HTTP 500 with `"Database not found"` — `data/mobility.db` is missing. Ask Abay.
- If you get HTTP 400 — you passed an invalid filter value. Check the parameter name.
- If you get `Connection refused` — the server is not running. Check the first terminal.

---

## Task 4 — Draw the architecture diagram

Go to https://app.diagrams.net (free, no account needed).
Draw boxes and arrows showing:

```
[data/raw/  (CSV + shapefile)]
        |
        v
[pipeline/clean_data.py]    [pipeline/convert_zones.py]
        |                           |
        v                           v
[data/processed/trips_clean.csv]  [taxi_zones.geojson]
        |
        v
[database/load_database.py]
        |
        v
[data/mobility.db  (SQLite — star schema)]
        |
        v
[backend/app.py  (Flask REST API — 12 endpoints)]
        |
        v
[browser: frontend/index.html  (HTML + CSS + JS)]
```

Label each arrow with what flows through it (e.g. "cleaned CSV rows",
"SQL queries", "JSON responses").

Export as PNG. Save to `docs/architecture.png`. Commit it:
```bash
git add docs/architecture.png
git commit -m "docs: add system architecture diagram"
git push origin alier/database
```

---

## Task 5 — Update README.md with the video and scrum board links

Once Kuol has uploaded the video, open `README.md` and replace:
```
**Video walkthrough:** <!-- ADD YOUR VIDEO LINK HERE before submitting -->
**Scrum board:** <!-- ADD YOUR SCRUM BOARD LINK HERE -->
```
with the real links. Commit:
```bash
git add README.md
git commit -m "README: add video link and scrum board link"
git push origin alier/database
```

---

## Task 6 — Open a PR and merge

```bash
git push origin alier/database
```
Go to GitHub → open a Pull Request from `alier/database` to `main`.
Tag Chol to review (he needs your endpoints working to test his two).

---

## Your report — Section 2 (400–500 words)

Answer these questions in your own words:

1. **Include `docs/architecture.png`** and describe what each layer does in 1–2 sentences.

2. **Why SQLite and not PostgreSQL?**
   Single file, no server process to install or manage, easy to share with
   teammates, fast enough for a read-only dashboard. The database is rebuilt
   from scratch by a script, so there is no risk of data loss.

3. **Why a star schema?**
   Storing "Manhattan" once in `boroughs` instead of repeating it 4 million
   times in `trips` saves ~200MB and makes GROUP BY borough queries fast.
   Foreign keys enforce that every trip references a real zone.

4. **Why two summary tables?**
   Scanning 7.5M rows for every dashboard request would take seconds.
   The `zone_date_hour_stats` table has ~100k rows and answers most queries
   in milliseconds. The trade-off is ~50MB of extra storage.

5. **Why are indexes applied after bulk insert?**
   Inserting 7.5M rows with indexes active is ~5x slower because every
   insert must update every index. Applying them once at the end is much faster.

---

---

# KUOL AKECH — Frontend

Your job is to own the HTML structure, all CSS, the API fetch layer (`api.js`),
and the dashboard orchestrator (`main.js`).
Chol owns `charts.js` and `map.js` — never edit those files.

## What you need before you start
- The server must be running: `python backend/app.py`
- You can do layout and style work without the server, but you need it to see real data

---

## Task 1 — Open the dashboard and understand every section

Start the server:
```bash
python backend/app.py
```

Open `http://127.0.0.1:5000` in your browser. You should see:

1. **Header** — title and 5 KPI numbers: rides, average fare, average speed,
   fare per mile, card tip percentage
2. **Filter bar** — date range, borough, zone, day of week, hour range,
   and a "More filters" button for distance, fare, payment type, airport
3. **Zone map** — New York City coloured by pickup count. Click a zone to
   see its top destinations and median fare in the side panel.
4. **Speed & fare/mile charts** — two line charts side by side, one per hour
5. **Heatmap** — weekday × hour grid showing when the city is busiest
6. **Daily bar chart** — one bar per day in January
7. **Top routes table** — the 10 busiest pickup → dropoff pairs
8. **Trip explorer** — paginated table of individual rides, sortable by column
9. **Data quality section** — horizontal bars showing what was excluded and why

Open DevTools (F12) → Network tab. Change the borough filter to "Manhattan"
and watch the API calls fire. Every section makes its own independent request.

---

## Task 2 — Understand how filters flow through the page

Open `frontend/js/main.js`. Find `readFilters()` near the top:

```javascript
function readFilters() {
  const data = new FormData(form);
  const f = {};
  for (const [k, v] of data.entries()) f[k] = typeof v === "string" ? v.trim() : v;
  f.airport_only = data.has("airport_only");
  if (f.hour_min && f.hour_max && Number(f.hour_min) > Number(f.hour_max)) {
    [f.hour_min, f.hour_max] = [f.hour_max, f.hour_min];
  }
  return f;
}
```

This reads every `<input>` and `<select>` in the `<form id="filters">` by
their `name` attribute. If you add a new filter to `index.html` with a `name`,
it gets picked up here automatically — no JS change needed.

Find `refresh()` near the bottom of `main.js`:
```javascript
function refresh() {
  const f = readFilters();
  loadMeter(f);
  loadMap(f);
  loadZoneDetail(f);
  loadHourly(f);
  loadRhythm(f);
  loadRoutes(f);
  loadTrips(f);
}
```

Every time a filter changes, `refresh()` is called and all sections reload
in parallel. Each section is independent — a slow query in one section does
not block the others.

---

## Task 3 — Understand how numbers are formatted

Open `frontend/js/api.js`. Find the `fmt` object:

```javascript
export const fmt = {
  int:     (v) => (v == null ? "–" : intFmt.format(Math.round(v))),
  money:   (v) => (v == null ? "–" : `$${v.toFixed(2)}`),
  mph:     (v) => (v == null ? "–" : `${v.toFixed(1)} mph`),
  miles:   (v) => (v == null ? "–" : `${v.toFixed(2)} mi`),
  minutes: (v) => (v == null ? "–" : `${v.toFixed(1)} min`),
  pct:     (v) => (v == null ? "–" : `${v.toFixed(1)}%`),
  hour:    (h) => (h === 0 ? "12 am" : h < 12 ? `${h} am` : h === 12 ? "12 pm" : `${h - 12} pm`),
};
```

Always use `fmt.*` when displaying numbers. Never format inline.
Also always use `esc()` before putting any API string into `innerHTML`:

```javascript
// Correct — prevents XSS if a zone name contains < or >
tbody.innerHTML = `<td>${esc(row.zone_name)}</td>`;

// Wrong — security risk
tbody.innerHTML = `<td>${row.zone_name}</td>`;
```

---

## Task 4 — Take the 6 required screenshots

With the server running and the dashboard open at `http://127.0.0.1:5000`,
take these screenshots. Save them to `docs/screenshots/` with exactly these names:

**1. `dashboard_overview.png`**
Load the page with no filters. Wait for all sections to finish loading.
Take a full-page screenshot (on Mac: Cmd+Shift+3, or use the browser's
full-page screenshot tool in DevTools → three-dot menu → Capture full size screenshot).

**2. `map_speed.png`**
In the "Shade zones by" dropdown above the map, select "Average speed".
Wait for the map to recolour. Screenshot just the map section.

**3. `heatmap.png`**
Scroll to the "Thursday and Friday evenings are the city's peak" section.
Make sure the "Rides" toggle is selected (not "Speed").
Screenshot the heatmap grid.

**4. `top_routes.png`**
Scroll to the "The busiest routes are short hops on the Upper East Side" section.
Screenshot the full table showing all 10 routes.

**5. `zone_detail.png`**
Click on a zone in the map — try clicking on the Upper East Side area
(the dense cluster on the right side of Manhattan).
Wait for the side panel to load showing top destinations and median fare.
Screenshot the map + side panel together.

**6. `data_quality.png`**
Scroll to the bottom "What we removed, and why" section.
Screenshot both the "Rides excluded" and "Rides kept but repaired" bar charts.

```bash
git add docs/screenshots/
git commit -m "docs: add 6 dashboard screenshots for report"
git push origin kuol/frontend
```

---

## Task 5 — Record the 5-minute video

Use Loom (free at loom.com), OBS, or QuickTime screen recording.
Record your screen with your voice. Follow this structure exactly:

**0:00–0:30 — You introduce the project**
Say: "This is the NYC Urban Mobility Data Explorer. It lets you explore
every yellow taxi ride in New York City in January 2019 — over 7.5 million
trips — filtered by borough, time of day, fare range, and more.
The whole dashboard updates instantly when you change a filter."

**0:30–2:00 — Live walkthrough**
- Change the borough filter to "Manhattan". Point out the map updating.
- Change the hour filter to 7am–9am. Show the speed chart dropping.
- Click on a zone on the map (e.g. Upper East Side). Show the side panel
  with top destinations and median fare.
- Reset all filters. Show the heatmap and point out the Thursday/Friday
  evening peak.

**2:00–3:30 — Alier explains the architecture**
Alier shares his screen and shows `docs/architecture.png`.
He explains each layer in 2–3 sentences: what the pipeline does,
why SQLite, what the star schema is, what the summary tables are for.

**3:30–4:30 — Chol explains the algorithms**
Chol shares his screen and opens `backend/algorithms.py`.
He explains what the MinHeap does, why it is faster than sorting,
and shows the self-test passing.

**4:30–5:00 — Each person says one sentence**
Go around: "I built [X] which does [Y]."

Upload to YouTube (unlisted is fine) or Google Drive.
Copy the link and give it to Alier to put in the README.

---

## Task 6 — Open a PR

```bash
git push origin kuol/frontend
```
Go to GitHub → open a Pull Request from `kuol/frontend` to `main`.
Tag Abay to review.

---

## Your report contribution

You do not write a full section but you provide the screenshots that go into
Section 4 (Chol's insights section). Make sure all 6 screenshots are in
`docs/screenshots/` and committed before the deadline.

---

---

# CHOL MACH KUOL — Algorithms & Visualisation

Your job is to own the three hand-written algorithms, the two SVG chart files,
the SVG zone map, and the two API endpoints that call your algorithms.

## What you need before you start
- Abay must have finished Task 6 (database must exist)
- Run `python backend/algorithms.py` — if it prints errors, fix them before anything else

---

## Task 1 — Run the algorithm self-tests

```bash
python backend/algorithms.py
```

Expected output:
```
All algorithm self-tests passed.
```

If you see an `AssertionError`, it will tell you which trial number failed
and what the expected vs actual values were. Read the error, find the bug
in `algorithms.py`, fix it, and run again until it passes.

Do not move to Task 2 until this passes cleanly.

---

## Task 2 — Test your two endpoints

Start the server:
```bash
python backend/app.py
```

Open a second terminal and test:

```bash
# Top 5 busiest routes across all of January
curl -s "http://127.0.0.1:5000/api/top-routes?k=5" | python3 -m json.tool
```

Expected response shape:
```json
{
  "distinct_routes_ranked": 49823,
  "source": "route_date_stats",
  "routes": [
    {
      "rank": 1,
      "pickup_zone": "Upper East Side North",
      "dropoff_zone": "Upper East Side North",
      "trips": 14821,
      "avg_distance_mi": 0.82,
      "avg_fare": 7.43
    },
    ...
  ]
}
```

Check that `distinct_routes_ranked` is around 50,000 — that is how many
zone pairs your `top_k` ranked to find the best 5.

```bash
# Zone detail for Midtown Center (zone 161)
curl -s "http://127.0.0.1:5000/api/zones/161" | python3 -m json.tool
```

Expected response shape:
```json
{
  "zone": {"zone_name": "Midtown Center", "borough": "Manhattan"},
  "trips": 284921,
  "median_fare": 9.5,
  "top_destinations": [
    {"zone": "Midtown East", "trips": 18432, "share_pct": 6.5},
    ...
  ]
}
```

Check that `median_fare` is a number (not null) and `top_destinations` has 5 entries.

```bash
# Zone detail for JFK Airport (zone 132)
curl -s "http://127.0.0.1:5000/api/zones/132" | python3 -m json.tool
# median_fare should be close to 52.0 — the JFK flat fare
```

```bash
# Top routes filtered to Monday morning rush
curl -s "http://127.0.0.1:5000/api/top-routes?k=10&dow=0&hour_min=7&hour_max=9" | python3 -m json.tool
```

---

## Task 3 — Open the dashboard and check the charts and map

```bash
open http://127.0.0.1:5000
```

Check each of these manually:

**Map:**
- Zones are coloured (not all grey)
- Hovering over a zone shows a tooltip with the zone name and pickup count
- Clicking a zone loads the side panel with top destinations and median fare
- The zoom in/out/fit buttons work
- Dragging pans the map

**Speed & fare/mile charts:**
- Both line charts render with data points
- Hovering over one chart highlights the same hour on the other chart
- The tooltip shows speed, fare/mile, and ride count for that hour

**Heatmap:**
- 7 rows (Mon–Sun) × 24 columns (0–23)
- Cells are coloured — darker = more rides
- Hovering shows a tooltip with rides per day, speed, and fare

**Daily bar chart:**
- 31 bars (one per day in January)
- Weekends and holidays are a different colour (orange)
- New Year's Day (Jan 1) and MLK Day (Jan 21) are annotated

**Top routes table:**
- 10 rows visible
- Rank bars show relative trip counts

If any chart is blank, open DevTools (F12) → Console tab and look for
red error messages. The most common cause is the server not running or
the database missing.

---

## Task 4 — Write your algorithm notes file and commit it

Run this to create the file:
```bash
touch docs/algorithm_notes.md
```

Open `backend/algorithms.py` in VS Code. Read each function, then open
`docs/algorithm_notes.md` and type out the content below — read each line
against the actual code as you go so you understand what you are writing.
Do not copy-paste blindly.

The content to write:

---

```
# Algorithm Notes — Chol Mach Kuol

## 1. MinHeap + top_k

### What problem does top_k solve, and why is a heap better than sorting?

We get around 50,000 zone pairs from the database, each with a trip count.
We only need the top 10. Sorting all 50,000 would be O(n log n) —
about 50,000 × 15.6 operations. A min-heap of size 10 is O(n log k) —
about 50,000 × 3.3 operations. That is roughly 5x fewer comparisons
for the same result.

### What happens when a new item arrives and the heap is already full?

The heap always holds the 10 strongest items seen so far. The root
(index 0) is always the weakest of those 10 — that is what "min" means.
When a new item arrives, we compare it against the root using
key(item) > key(heap.peek()). If the new item is stronger, replace_top()
overwrites index 0 with it, then _sift_down bubbles it down until the
heap property is restored. If the new item is weaker than the current
weakest top-10, we skip it entirely.

### Time and space complexity

Time: O(n log k) — n is the total number of zone pairs (~50,000),
k is how many we keep (e.g. 10).
Space: O(k) — the heap never grows beyond k items.

### Pseudocode

function top_k(items, k, key):
    create empty min-heap
    for each item in items:
        if heap has fewer than k items:
            push item onto heap
        else if key(item) > key(heap root):
            replace heap root with item
            sift root down to restore heap property
    pop all items from heap into result array, filling back-to-front
    return result  (largest first)

---

## 2. count_by

### What does count_by return? Concrete example.

It returns a dict mapping each unique value to how many times it appeared.

Example — dropoff zones for zone 161: [48, 162, 48]

    count_by([48, 162, 48])  →  {48: 2, 162: 1}

Zone 48 appeared twice, zone 162 once.

### Why a plain dict instead of collections.Counter?

The assignment prohibits built-in grouping tools. Counter is a subclass
of dict that does exactly this automatically. Writing it manually with
"if v in counts: counts[v] += 1 else: counts[v] = 1" shows we understand
what a hash map is doing — hashing the key, finding the bucket,
incrementing the value.

### Time and space complexity

Time: O(n) — one pass, each dict lookup and insert is O(1) average.
Space: O(d) where d is the number of distinct values
(e.g. up to 262 distinct dropoff zones).

### Pseudocode

function count_by(values):
    counts = empty dict
    for each value v in values:
        if v is already a key in counts:
            counts[v] = counts[v] + 1
        else:
            counts[v] = 1
    return counts

---

## 3. quickselect_median

### Difference between two-way and three-way partition

Two-way partition splits the array into two regions: items less than
the pivot, and items greater than or equal to the pivot.
Three-way (Dutch national flag) splits into three: strictly less than,
equal to, and strictly greater than. The equal region is the key addition.

### Why does the JFK flat fare cause a problem for two-way quickselect?

JFK airport trips have a fixed $52.00 fare. In a zone like JFK,
thousands of fares are exactly $52.00. If $52.00 is chosen as the pivot
in a two-way partition, every equal item still gets placed on one side.
The next recursion picks another pivot from a range still full of $52.00
values, and the same thing happens again. In the worst case this repeats
O(n) times, making the whole algorithm O(n²).

### How does three-way partition fix it?

After one pass, all $52.00 values are collected into the middle block
a[lt..gt]. The code then checks: if the target index k falls inside that
block, return $52.00 immediately. If k is outside the block, recurse only
into the smaller region that contains no $52.00 values at all.
The entire spike is eliminated in a single pass.

### Time and space complexity

Average case: O(n) — each round roughly halves the search range,
giving n + n/2 + n/4 + ... = 2n work.
Worst case: O(n²) — if the pivot is always the smallest or largest
element every round. This is very unlikely because the code uses
_median_of_three to pick the pivot.
Space: O(n) — line "a = list(values)" makes a full copy so the
caller's list is not modified.

### Pseudocode for the three-way partition step

pivot = median of a[lo], a[mid], a[hi]
lt = lo, i = lo, gt = hi

while i <= gt:
    if a[i] < pivot:
        swap a[i] and a[lt]
        lt += 1, i += 1
    else if a[i] > pivot:
        swap a[i] and a[gt]
        gt -= 1    (do NOT increment i — the swapped item is unexamined)
    else:
        i += 1     (a[i] == pivot, already in the right place)

result: a[lo..lt-1] < pivot,  a[lt..gt] == pivot,  a[gt+1..hi] > pivot
```

---

Once you have typed it all out, verify and commit:
```bash
ls docs/algorithm_notes.md
git add docs/algorithm_notes.md
git commit -m "docs: algorithm notes — complexity, pseudocode, JFK edge case"
git push origin chol/algorithms
```

This file is your report Section 3. You have already written it.

---

## Task 5 — Commit your work and open a PR

```bash
git add backend/algorithms.py frontend/js/charts.js frontend/js/map.js
git commit -m "algorithms: all self-tests pass — MinHeap, top_k, count_by, quickselect"
git push origin chol/algorithms
```

Go to GitHub → open a Pull Request from `chol/algorithms` to `main`.
Tag Kuol to review (he needs your charts and map to test the full dashboard).

---

## Your report — Section 3 (400–500 words)

Your `docs/algorithm_notes.md` from Task 4 is your Section 3.
Copy it into the report PDF as-is. It already has everything:
- What each algorithm does and why it was chosen
- Pseudocode for all three
- Complexity for all three
- The JFK edge case explained

You do not need to write anything new.

---

## Your report — Section 4 (400–500 words)

Run the dashboard with real data and find three things that surprised you.
For each one write:
- The API call or filter combination you used to find it
- Use one of Kuol's screenshots to illustrate it
- 2–3 sentences on what it means for how New York moves

**Starting points:**
1. Set hour filter to 5am, note the speed. Then set it to 8am. How much
   does speed drop? What does that do to fare per mile? What does that cost
   a commuter who takes a taxi every morning?
2. Look at the top routes table with no filters. Why is Upper East Side →
   Upper East Side the single busiest route? What does that say about how
   people use taxis for short local trips?
3. Check the "Airport trips only" checkbox. How does the average tip
   percentage compare to the city-wide average? Why might airport passengers
   tip differently from regular riders?

---

# SHARED SUBMISSION CHECKLIST

Go through this together the day before the deadline.
Every item must be checked before submitting.

## Code
- [ ] `python backend/algorithms.py` prints "All algorithm self-tests passed" (Chol)
- [ ] `python backend/app.py` starts without errors (Alier)
- [ ] All 12 endpoints return HTTP 200 with valid JSON — test with the curl commands above (Alier + Chol)
- [ ] Dashboard loads at `http://127.0.0.1:5000` and all 6 sections render (Kuol)
- [ ] Changing filters updates every chart and the KPI numbers (Kuol)
- [ ] Clicking a zone on the map loads the side panel (Chol)

## Files that must exist in the repo
- [ ] `data/processed/cleaning_summary.json` (Abay)
- [ ] `docs/data_profile.txt` (Abay)
- [ ] `docs/architecture.png` (Alier)
- [ ] `docs/screenshots/dashboard_overview.png` (Kuol)
- [ ] `docs/screenshots/map_speed.png` (Kuol)
- [ ] `docs/screenshots/heatmap.png` (Kuol)
- [ ] `docs/screenshots/top_routes.png` (Kuol)
- [ ] `docs/screenshots/zone_detail.png` (Kuol)
- [ ] `docs/screenshots/data_quality.png` (Kuol)
- [ ] `README.md` has a real video link and a real scrum board link (Alier)
- [ ] `codevibe-reports/<your-name>.report.json` — one per team member (everyone)

## Report PDF — team lead assembles this
- [ ] Section 1 — Dataset & cleaning (Abay, 400–500 words)
- [ ] Section 2 — Architecture & design decisions with `architecture.png` (Alier, 400–500 words)
- [ ] Section 3 — Algorithms with pseudocode and complexity (Chol, 400–500 words)
- [ ] Section 4 — Three insights with screenshots (Chol, 400–500 words)
- [ ] Section 5 — One reflection paragraph per person (everyone)
- [ ] 2–3 pages total, exported as PDF

## Submission — team lead does this
- [ ] Team participation sheet filled in, all members added with comment-only permission
- [ ] GitHub commit history shows meaningful commits to code files from all 4 members
- [ ] Repo zipped
- [ ] Submitted on Canvas with: GitHub link, video link, PDF report, zip file
