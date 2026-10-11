"""
app.py
------
The Flask API: the "waiter" between the dashboard and the database.
It also serves the dashboard files, so one command runs everything:

    python backend/app.py      ->  open http://127.0.0.1:5000

Every data endpoint accepts the same optional filters in the URL:
    start_date, end_date   (YYYY-MM-DD, inclusive)
    borough_id, zone_id    (pickup location)
    dow                    (0 = Monday ... 6 = Sunday)
    hour_min, hour_max     (0-23)
    min_distance, max_distance   (miles)
    min_fare, max_fare           ($)
    payment_type_id        (1 = card, 2 = cash, ...)
    airport_only           (1 = only trips to/from JFK, LGA, EWR)

Example: /api/summary?borough_id=4&hour_min=7&hour_max=9
"""

import json
import mimetypes
import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory

from algorithms import count_by, quickselect_median, top_k
from db import (ROUTE_STATS_FILTERS, ZONE_STATS_FILTERS, BadRequest, can_use, close_db,
                get_db, int_arg, parse_filters, query, query_one, where_clause)

# Some Windows machines map .js to "text/plain" in the registry, which makes
# browsers refuse to run the dashboard's JavaScript modules. Set it explicitly.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
SUMMARY_JSON = ROOT / "data" / "processed" / "cleaning_summary.json"

app = Flask(__name__, static_folder=None)
app.teardown_appcontext(close_db)

# The same aggregate, written for each of the two tables it can come from.
# Speed is total distance / total time (not an average of per-trip speeds),
# so a 1-minute crawl and a 40-minute highway run are weighted fairly.
AGG_FROM_TRIPS = """
    COUNT(*) AS n, SUM(trip_distance) AS dist, SUM(trip_duration_min) AS dur,
    SUM(fare_amount) AS fare, SUM(total_amount) AS total,
    SUM(tip_pct) AS tip_sum, COUNT(tip_pct) AS tip_n
"""
AGG_FROM_SUMMARY = """
    SUM(trip_count) AS n, SUM(sum_distance) AS dist, SUM(sum_duration_min) AS dur,
    SUM(sum_fare) AS fare, SUM(sum_total) AS total,
    SUM(sum_tip_pct) AS tip_sum, SUM(tip_pct_trips) AS tip_n
"""

# Sortable trip columns -> the index that already stores the rows in that order
# (see database/indexes.sql).
SORT_INDEXES = {
    "pickup_datetime": "idx_trips_pickup_datetime",
    "trip_distance": "idx_trips_distance",
    "fare_amount": "idx_trips_fare",
    "trip_duration_min": "idx_trips_duration",
    "avg_speed_mph": "idx_trips_speed",
    "tip_pct": "idx_trips_tip_pct",
}
SORTABLE_TRIP_COLUMNS = set(SORT_INDEXES)
NULLABLE_SORTS = {"tip_pct"}      # NULL for cash rides, where tips are never recorded
BROAD_FILTER_ROWS = 50_000
UNKNOWN_ZONES = {264, 265}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def metrics(row):
    """Turn raw sums from SQL into the numbers the dashboard shows."""
    n = row.get("n") or 0
    if n == 0:
        return {"trips": 0, "avg_distance_mi": None, "avg_duration_min": None,
                "avg_speed_mph": None, "avg_fare": None, "fare_per_mile": None,
                "avg_total": None, "avg_tip_pct": None}
    dist, dur = row["dist"] or 0, row["dur"] or 0
    return {
        "trips": n,
        "avg_distance_mi": round(dist / n, 2),
        "avg_duration_min": round(dur / n, 2),
        "avg_speed_mph": round(dist / (dur / 60), 2) if dur else None,
        "avg_fare": round(row["fare"] / n, 2),
        "fare_per_mile": round(row["fare"] / dist, 2) if dist else None,
        "avg_total": round(row["total"] / n, 2),
        "avg_tip_pct": round(row["tip_sum"] / row["tip_n"], 2) if row["tip_n"] else None,
    }


def source_for(filters):
    """
    Choose where to read from. If every active filter can be answered by the
    pre-aggregated zone_date_hour_stats table (~100k rows), use it; otherwise
    fall back to the full trips table (7.5M rows). Returns
    (table, aggregate SQL, WHERE sql, params).
    """
    if can_use(filters, ZONE_STATS_FILTERS):
        where, params = where_clause(filters, date_col="pickup_date")
        return "zone_date_hour_stats", AGG_FROM_SUMMARY, where, params
    where, params = where_clause(filters)
    return "trips", AGG_FROM_TRIPS, where, params


_date_range_cache = None


def dataset_date_range():
    global _date_range_cache
    if _date_range_cache is None:
        row = query_one("SELECT (SELECT MIN(pickup_date) FROM zone_date_hour_stats) AS first, "
                        "(SELECT MAX(pickup_date) FROM zone_date_hour_stats) AS last")
        _date_range_cache = (date.fromisoformat(row["first"]), date.fromisoformat(row["last"]))
    return _date_range_cache


def weekday_counts(filters):
    """How many Mondays, Tuesdays... fall inside the selected date range."""
    first, last = dataset_date_range()
    start = max(filters["start_date"] or first, first)
    end = min(filters["end_date"] or last, last)
    counts = [0] * 7
    d = start
    while d <= end:
        counts[d.weekday()] += 1
        d += timedelta(days=1)
    return counts


def sample_fraction():
    """1.0 for the full dataset; less when load_database.py was run with --sample."""
    try:
        row = query_one("SELECT value FROM dataset_info WHERE key = 'trip_sample_fraction'")
    except sqlite3.OperationalError:   # database built before dataset_info existed
        return 1.0
    return float(row["value"]) if row else 1.0


@app.before_request
def _start_timer():
    g.t0 = time.perf_counter()


@app.after_request
def _add_timing(response):
    if hasattr(g, "t0"):
        response.headers["X-Query-Time-ms"] = f"{(time.perf_counter() - g.t0) * 1000:.0f}"
    return response


@app.errorhandler(BadRequest)
def _bad_request(err):
    return jsonify({"error": str(err)}), 400


@app.errorhandler(RuntimeError)
def _server_error(err):
    return jsonify({"error": str(err)}), 500


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    n = query_one("SELECT SUM(trip_count) AS n FROM zone_date_hour_stats")["n"]
    return jsonify({"status": "ok", "trips_in_database": n})


@app.get("/api/meta")
def meta():
    """Everything the dashboard needs to build its filter menus."""
    first, last = dataset_date_range()
    return jsonify({
        "date_range": {"first": first.isoformat(), "last": last.isoformat()},
        "sample_fraction": sample_fraction(),
        "boroughs": query("SELECT borough_id, name FROM boroughs ORDER BY name"),
        "zones": query("""SELECT z.zone_id, z.zone_name, b.name AS borough, z.borough_id, z.is_airport
                          FROM zones z JOIN boroughs b USING (borough_id) ORDER BY z.zone_name"""),
        "payment_types": query("SELECT payment_type_id, description FROM payment_types"),
    })


_geojson_cache = None


@app.get("/api/zones/geojson")
def zones_geojson():
    """Zone shapes for the map (built once, then cached in memory)."""
    global _geojson_cache
    if _geojson_cache is None:
        rows = query("""SELECT z.zone_id, z.zone_name, b.name AS borough, z.geometry
                        FROM zones z JOIN boroughs b USING (borough_id)
                        WHERE z.geometry IS NOT NULL""")
        _geojson_cache = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"zone_id": r["zone_id"], "zone": r["zone_name"], "borough": r["borough"]},
                "geometry": json.loads(r["geometry"]),
            } for r in rows],
        }
    return jsonify(_geojson_cache)


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------
@app.get("/api/summary")
def summary():
    """Headline numbers (KPI cards) for the current filters."""
    f = parse_filters(request.args)
    table, agg, where, params = source_for(f)
    row = query_one(f"SELECT {agg} FROM {table} {where}", params)
    return jsonify({**metrics(row), "source": table})


@app.get("/api/by-hour")
def by_hour():
    """Trips and speed for each hour of the day (0-23)."""
    f = parse_filters(request.args)
    table, agg, where, params = source_for(f)
    rows = query(f"SELECT pickup_hour AS hour, {agg} FROM {table} {where} "
                 f"GROUP BY pickup_hour ORDER BY pickup_hour", params)
    return jsonify([{"hour": r["hour"], **metrics(r)} for r in rows])


@app.get("/api/heatmap")
def heatmap():
    """Weekday x hour grid: when is the city busiest, and slowest?"""
    f = parse_filters(request.args)
    table, agg, where, params = source_for(f)
    rows = query(f"SELECT pickup_dow AS dow, pickup_hour AS hour, {agg} FROM {table} {where} "
                 f"GROUP BY pickup_dow, pickup_hour", params)
    # January 2019 has five Tuesdays but four Mondays. Dividing by how many of
    # each weekday are in the range gives "trips on an average Tuesday", so
    # weekdays can be compared fairly.
    days = weekday_counts(f)
    out = []
    for r in rows:
        m = metrics(r)
        m["trips_per_day"] = round(m["trips"] / days[r["dow"]], 1) if days[r["dow"]] else None
        out.append({"dow": r["dow"], "day": DAY_NAMES[r["dow"]], "hour": r["hour"], **m})
    return jsonify(out)


@app.get("/api/by-day")
def by_day():
    """Daily totals across the month (time series)."""
    f = parse_filters(request.args)
    table, agg, where, params = source_for(f)
    day_expr = "pickup_date" if table != "trips" else "substr(pickup_datetime, 1, 10)"
    rows = query(f"SELECT {day_expr} AS day, {agg} FROM {table} {where} "
                 f"GROUP BY day ORDER BY day", params)
    return jsonify([{"date": r["day"], "dow": date.fromisoformat(r["day"]).weekday(), **metrics(r)}
                    for r in rows])


@app.get("/api/zone-stats")
def zone_stats():
    """One row per pickup zone: colours the map."""
    f = {**parse_filters(request.args), "zone_id": None}   # the map shows every zone
    table, agg, where, params = source_for(f)
    rows = query(f"SELECT pickup_zone_id AS zone_id, {agg} FROM {table} {where} "
                 f"GROUP BY pickup_zone_id", params)
    return jsonify([{"zone_id": r["zone_id"], **metrics(r)} for r in rows])


@app.get("/api/top-routes")
def top_routes():
    """
    The K most travelled pickup -> dropoff zone pairs.
    SQL adds up each pair; the RANKING uses our hand-written min-heap top_k
    (O(n log k)) instead of ORDER BY / sorted().
    """
    f = parse_filters(request.args)
    k = int_arg(request.args, "k", 1, 50, default=10)
    if can_use(f, ROUTE_STATS_FILTERS):
        where, params = where_clause(f, date_col="pickup_date")
        table, cols = "route_date_stats", ("SUM(trip_count)", "SUM(sum_distance)",
                                           "SUM(sum_duration_min)", "SUM(sum_fare)")
    else:
        where, params = where_clause(f)
        table, cols = "trips", ("COUNT(*)", "SUM(trip_distance)",
                                "SUM(trip_duration_min)", "SUM(fare_amount)")
    pairs = query(f"""SELECT pickup_zone_id AS pu, dropoff_zone_id AS dz, {cols[0]} AS n,
                             {cols[1]} AS dist, {cols[2]} AS dur, {cols[3]} AS fare
                      FROM {table} {where} GROUP BY pickup_zone_id, dropoff_zone_id""", params)
    # Zones 264/265 ("Unknown", "Outside of NYC") are not real places: the
    # largest "route" in the raw data is Unknown -> Unknown. Skip them unless asked.
    if request.args.get("include_unknown") not in ("1", "true"):
        pairs = [r for r in pairs if r["pu"] not in UNKNOWN_ZONES and r["dz"] not in UNKNOWN_ZONES]
    best = top_k(pairs, k, key=lambda r: (r["n"], -r["pu"], -r["dz"]))

    names = {r["zone_id"]: r["zone_name"] for r in query("SELECT zone_id, zone_name FROM zones")}
    return jsonify({
        "distinct_routes_ranked": len(pairs),
        "source": table,
        "routes": [{
            "rank": i + 1,
            "pickup_zone_id": r["pu"], "pickup_zone": names[r["pu"]],
            "dropoff_zone_id": r["dz"], "dropoff_zone": names[r["dz"]],
            "trips": r["n"],
            "avg_distance_mi": round(r["dist"] / r["n"], 2),
            "avg_duration_min": round(r["dur"] / r["n"], 1),
            "avg_speed_mph": round(r["dist"] / (r["dur"] / 60), 1) if r["dur"] else None,
            "avg_fare": round(r["fare"] / r["n"], 2),
        } for i, r in enumerate(best)],
    })


@app.get("/api/zones/<int:zone_id>")
def zone_detail(zone_id):
    """
    Detail view for one pickup zone. Uses the hand-written algorithms:
      count_by + top_k  -> top 5 destinations
      quickselect       -> median fare (robust to outliers, unlike the mean)
    """
    f = {**parse_filters(request.args), "zone_id": zone_id, "borough_id": None}
    zone = query_one("""SELECT z.zone_id, z.zone_name, b.name AS borough, z.service_zone, z.is_airport
                        FROM zones z JOIN boroughs b USING (borough_id) WHERE z.zone_id = ?""", (zone_id,))
    if not zone:
        return jsonify({"error": f"zone {zone_id} not found"}), 404

    where, params = where_clause(f)
    rows = get_db().execute(
        f"""SELECT dropoff_zone_id, fare_amount, trip_distance, trip_duration_min,
                   total_amount, tip_pct
            FROM trips {where}""", params).fetchall()

    # One pass over the rows builds everything the detail panel needs.
    dropoffs, fares = [], []
    totals = {"n": 0, "dist": 0.0, "dur": 0.0, "fare": 0.0, "total": 0.0, "tip_sum": 0.0, "tip_n": 0}
    for dz, fare, dist, dur, total, tip_pct in rows:
        dropoffs.append(dz)
        fares.append(fare)
        totals["n"] += 1
        totals["dist"] += dist
        totals["dur"] += dur
        totals["fare"] += fare
        totals["total"] += total
        if tip_pct is not None:
            totals["tip_sum"] += tip_pct
            totals["tip_n"] += 1

    destinations = top_k(list(count_by(dropoffs).items()), 5, key=lambda kv: (kv[1], -kv[0]))
    names = {r["zone_id"]: r["zone_name"] for r in query("SELECT zone_id, zone_name FROM zones")}

    return jsonify({
        "zone": zone,
        **metrics(totals),
        "median_fare": quickselect_median(fares),
        "top_destinations": [{"zone_id": z, "zone": names[z], "trips": n,
                              "share_pct": round(n / len(dropoffs) * 100, 1)}
                             for z, n in destinations],
    })


# ---------------------------------------------------------------------------
# Trip list (sortable, filterable, paginated) for the detail table
# ---------------------------------------------------------------------------
@app.get("/api/trips")
def trips():
    f = parse_filters(request.args)
    sort = request.args.get("sort", "pickup_datetime")
    if sort not in SORTABLE_TRIP_COLUMNS:
        raise BadRequest(f"'sort' must be one of {', '.join(sorted(SORTABLE_TRIP_COLUMNS))}")
    order = "DESC" if request.args.get("order", "asc").lower() == "desc" else "ASC"
    page = int_arg(request.args, "page", 1, 1_000_000, default=1)
    per_page = int_arg(request.args, "per_page", 1, 100, default=25)

    where, params = where_clause(f, prefix="t.")
    if sort in NULLABLE_SORTS:
        # Sorting by tip % lists the rides that have one (card rides). Excluding
        # NULLs, rather than ORDER BY ... NULLS LAST, lets the index do the sorting.
        where += (" AND " if where else "WHERE ") + f"t.{sort} IS NOT NULL"

    # Query planning. SQLite has two ways to answer "first 25 rides matching X,
    # sorted by Y": walk X's index and sort every match, or walk Y's index in
    # order and keep the first 25 that match X. The first is fast when X is
    # selective (one small zone); the second when X matches many rides
    # (all of Manhattan): sorting 6.8M matches took ~30 s. SQLite cannot tell
    # the cases apart, so count matches, stopping at BROAD_FILTER_ROWS, and
    # force Y's index when the filter is broad.
    hint = ""
    broad = query_one(f"SELECT COUNT(*) AS n FROM (SELECT 1 FROM trips t {where} LIMIT ?)",
                      params + [BROAD_FILTER_ROWS])["n"] >= BROAD_FILTER_ROWS
    if broad:
        hint = f"INDEXED BY {SORT_INDEXES[sort]}"

    order_by = f"ORDER BY t.{sort} {order}, t.trip_id"
    # Pick the page of trips first, then look up names for just those rows.
    rows = query(f"""
        SELECT t.trip_id, t.pickup_datetime, t.dropoff_datetime,
               pz.zone_name AS pickup_zone, dz.zone_name AS dropoff_zone,
               t.trip_distance, t.trip_duration_min, t.avg_speed_mph,
               t.fare_amount, t.tip_amount, t.tip_pct, t.total_amount,
               p.description AS payment_type, t.passenger_count
        FROM (SELECT * FROM trips t {hint} {where} {order_by} LIMIT ? OFFSET ?) t
        JOIN zones pz ON pz.zone_id = t.pickup_zone_id
        JOIN zones dz ON dz.zone_id = t.dropoff_zone_id
        JOIN payment_types p ON p.payment_type_id = t.payment_type_id
        {order_by}""", params + [per_page, (page - 1) * per_page])
    return jsonify({"page": page, "per_page": per_page, "sort": sort, "order": order.lower(),
                    "trips": rows})


# ---------------------------------------------------------------------------
# Data quality (the transparency log)
# ---------------------------------------------------------------------------
@app.get("/api/data-quality")
def data_quality():
    reasons = query("""SELECT r.reason_code, r.description, COUNT(e.excluded_id) AS records
                       FROM exclusion_reasons r
                       LEFT JOIN excluded_records e USING (reason_code)
                       GROUP BY r.reason_code ORDER BY records DESC""")
    summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8")) if SUMMARY_JSON.exists() else {}
    return jsonify({
        "raw_rows": summary.get("raw_rows"),
        "clean_rows": summary.get("clean_rows"),
        "excluded_rows": summary.get("excluded_rows"),
        "excluded_pct": summary.get("excluded_pct"),
        "exclusion_reasons": reasons,
        "repaired_or_flagged": summary.get("repair_and_flag_reasons", {}),
    })


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/<path:filename>")
def frontend_files(filename):
    return send_from_directory(FRONTEND_DIR, filename)


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")   # set HOST=0.0.0.0 when deploying
    port = int(os.environ.get("PORT", 5000))
    print(f"Dashboard running at http://{host}:{port}  (press Ctrl+C to stop)")
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG") == "1")