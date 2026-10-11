"""
load_database.py
----------------
Step 3 of the pipeline: build the SQLite database from the cleaned data.

  1. Creates all tables from database/schema.sql
  2. Fills the dimension tables (boroughs, zones + map shapes, vendors, ...)
  3. Bulk-inserts the cleaned trips and the exclusion log
  4. Builds the summary tables, then the indexes (database/indexes.sql)
  5. Verifies integrity (foreign keys, row counts)

Usage (from the project root, after clean_data.py and convert_zones.py):
    python database/load_database.py               # all 7.5M trips (~2.7 GB, a few minutes)
    python database/load_database.py --sample 0.1  # random 10% (fast, for laptops / demos)
    python database/load_database.py --sample 0.01 --out deploy/mobility_demo.db   # deploy demo

Output:
    data/mobility.db
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "mobility.db"
SCHEMA_SQL = ROOT / "database" / "schema.sql"
INDEXES_SQL = ROOT / "database" / "indexes.sql"
LOOKUP_CSV = ROOT / "data" / "raw" / "taxi_zone_lookup.csv"
ZONES_GEOJSON = ROOT / "data" / "processed" / "taxi_zones.geojson"
TRIPS_CSV = ROOT / "data" / "processed" / "trips_clean.csv"
EXCLUDED_CSV = ROOT / "data" / "processed" / "excluded_records.csv"

CHUNK_ROWS = 250_000
AIRPORT_ZONES = {1, 132, 138}

# Code meanings from the TLC "Data Dictionary - Yellow Taxi Trip Records"
VENDORS = [
    (1, "Creative Mobile Technologies"),
    (2, "VeriFone Inc."),
    (4, "Undocumented vendor (not in 2019 data dictionary)"),
]
RATE_CODES = [
    (1, "Standard rate"), (2, "JFK"), (3, "Newark"),
    (4, "Nassau or Westchester"), (5, "Negotiated fare"), (6, "Group ride"),
]
PAYMENT_TYPES = [
    (1, "Credit card"), (2, "Cash"), (3, "No charge"),
    (4, "Dispute"), (5, "Unknown"), (6, "Voided trip"),
]
EXCLUSION_REASONS = {
    "duplicate_record": "Exact copy of an earlier ride (every raw field identical)",
    "pickup_outside_jan_2019": "Pickup timestamp outside the dataset month (meter clock error)",
    "dropoff_not_after_pickup": "Dropoff at or before pickup (zero or negative duration)",
    "duration_over_6h": "Trip longer than 6 hours (meter left running)",
    "zero_distance": "Zero miles travelled (cancelled ride or GPS failure)",
    "distance_over_100mi": "More than 100 miles (odometer/GPS error)",
    "fare_zero_or_negative": "Fare of $0 or less (refund, correction or void)",
    "fare_over_500": "Fare above $500 (data-entry error)",
    "negative_charge_component": "A negative tax, tip, toll or total",
    "speed_over_80mph": "Implied average speed above 80 mph (physically impossible)",
}

TRIP_COLUMNS = [
    "source_row", "vendor_id", "pickup_datetime", "dropoff_datetime", "passenger_count",
    "trip_distance", "rate_code_id", "store_and_fwd_flag", "pickup_zone_id",
    "dropoff_zone_id", "payment_type_id", "fare_amount", "extra", "mta_tax", "tip_amount",
    "tolls_amount", "improvement_surcharge", "congestion_surcharge", "total_amount",
    "trip_duration_min", "avg_speed_mph", "fare_per_mile", "tip_pct", "pickup_hour",
    "pickup_dow", "is_weekend", "time_of_day", "is_airport_trip",
]
EXCLUDED_COLUMNS = [
    "source_row", "reason_code", "pickup_datetime", "dropoff_datetime", "trip_distance",
    "pickup_zone_id", "dropoff_zone_id", "fare_amount", "total_amount",
]


def to_rows(df):
    """DataFrame -> list of tuples with proper Python None for missing values."""
    return list(df.astype(object).where(df.notna(), None).itertuples(index=False, name=None))


def load_dimensions(con):
    lookup = pd.read_csv(LOOKUP_CSV)
    # Tidy the two placeholder rows: 264 "Unknown/N/A" and 265 "N/A/Outside of NYC"
    lookup.loc[lookup["LocationID"] == 264, ["Borough", "Zone"]] = ["Unknown", "Unknown"]
    lookup.loc[lookup["LocationID"] == 265, ["Borough", "Zone"]] = ["Outside NYC", "Outside of NYC"]
    lookup["service_zone"] = lookup["service_zone"].replace("N/A", None)

    boroughs = sorted(lookup["Borough"].unique())
    borough_ids = {name: i + 1 for i, name in enumerate(boroughs)}
    con.executemany("INSERT INTO boroughs (borough_id, name) VALUES (?, ?)",
                    [(i, name) for name, i in borough_ids.items()])

    shapes = {}
    for feature in json.loads(ZONES_GEOJSON.read_text(encoding="utf-8"))["features"]:
        shapes[feature["properties"]["location_id"]] = json.dumps(
            feature["geometry"], separators=(",", ":"))

    con.executemany(
        "INSERT INTO zones (zone_id, zone_name, borough_id, service_zone, is_airport, geometry) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(int(r.LocationID), r.Zone, borough_ids[r.Borough], r.service_zone,
          int(r.LocationID in AIRPORT_ZONES), shapes.get(int(r.LocationID)))
         for r in lookup.itertuples()],
    )
    con.executemany("INSERT INTO vendors VALUES (?, ?)", VENDORS)
    con.executemany("INSERT INTO rate_codes VALUES (?, ?)", RATE_CODES)
    con.executemany("INSERT INTO payment_types VALUES (?, ?)", PAYMENT_TYPES)
    con.executemany("INSERT INTO exclusion_reasons VALUES (?, ?)", list(EXCLUSION_REASONS.items()))
    print(f"  {len(borough_ids)} boroughs, {len(lookup)} zones "
          f"({len(shapes)} with map shapes), {len(VENDORS)} vendors, "
          f"{len(RATE_CODES)} rate codes, {len(PAYMENT_TYPES)} payment types")


def load_trips(con, sample):
    placeholders = ", ".join("?" * len(TRIP_COLUMNS))
    sql = f"INSERT INTO trips ({', '.join(TRIP_COLUMNS)}) VALUES ({placeholders})"
    loaded = 0
    reader = pd.read_csv(TRIPS_CSV, usecols=TRIP_COLUMNS, chunksize=CHUNK_ROWS,
                         dtype={"passenger_count": "Int64", "rate_code_id": "Int64"})
    for i, chunk in enumerate(reader):
        if sample < 1:
            chunk = chunk.sample(frac=sample, random_state=42 + i)
        con.executemany(sql, to_rows(chunk[TRIP_COLUMNS]))
        loaded += len(chunk)
        print(f"  {loaded:>10,} trips inserted", end="\r")
    print()
    return loaded


def load_excluded(con):
    df = pd.read_csv(EXCLUDED_CSV)
    df = df.rename(columns={
        "exclusion_reason": "reason_code",
        "tpep_pickup_datetime": "pickup_datetime",
        "tpep_dropoff_datetime": "dropoff_datetime",
        "PULocationID": "pickup_zone_id",
        "DOLocationID": "dropoff_zone_id",
    })
    placeholders = ", ".join("?" * len(EXCLUDED_COLUMNS))
    con.executemany(
        f"INSERT INTO excluded_records ({', '.join(EXCLUDED_COLUMNS)}) VALUES ({placeholders})",
        to_rows(df[EXCLUDED_COLUMNS]))
    return len(df)


def build_summaries(con):
    con.execute("""
        INSERT INTO zone_date_hour_stats
        SELECT substr(pickup_datetime, 1, 10), pickup_zone_id, pickup_hour, pickup_dow,
               COUNT(*),
               SUM(trip_distance), SUM(trip_duration_min),
               SUM(fare_amount), SUM(total_amount),
               COALESCE(SUM(tip_pct), 0), COUNT(tip_pct)
        FROM trips
        GROUP BY 1, pickup_zone_id, pickup_hour
    """)
    con.execute("""
        INSERT INTO route_date_stats
        SELECT substr(pickup_datetime, 1, 10), pickup_zone_id, dropoff_zone_id, pickup_dow,
               COUNT(*), SUM(trip_distance), SUM(trip_duration_min), SUM(fare_amount)
        FROM trips
        GROUP BY 1, pickup_zone_id, dropoff_zone_id
    """)
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("zone_date_hour_stats", "route_date_stats")}


def verify(con, expected_trips):
    problems = con.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise RuntimeError(f"Foreign key violations: {problems[:5]}")
    n = con.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    if n != expected_trips:
        raise RuntimeError(f"Expected {expected_trips} trips, found {n}")
    for table in ("zone_date_hour_stats", "route_date_stats"):
        total = con.execute(f"SELECT SUM(trip_count) FROM {table}").fetchone()[0]
        if total != n:
            raise RuntimeError(f"{table} does not add up to the trips table")
    print("  foreign keys OK, row counts OK, summary tables add up")


def main():
    parser = argparse.ArgumentParser(description="Build data/mobility.db")
    parser.add_argument("--sample", type=float, default=1.0,
                        help="fraction of trips to load, e.g. 0.1 for 10%% (default: all)")
    parser.add_argument("--out", type=Path, default=DB_PATH,
                        help="where to write the database (default: data/mobility.db)")
    args = parser.parse_args()
    if not 0 < args.sample <= 1:
        parser.error("--sample must be between 0 and 1")

    for path in (TRIPS_CSV, EXCLUDED_CSV, ZONES_GEOJSON, LOOKUP_CSV):
        if not path.exists():
            raise SystemExit(f"Missing {path.relative_to(ROOT)}. Run the pipeline scripts first "
                             "(see README).")

    db_path = args.out if args.out.is_absolute() else ROOT / args.out
    db_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    # Bulk-load settings: safe here because the whole file is rebuilt from scratch.
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    con.execute("PRAGMA cache_size = -200000")  # ~200 MB

    print("Creating tables ...")
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    con.execute("PRAGMA foreign_keys = ON")

    print("Loading dimension tables ...")
    load_dimensions(con)
    con.execute("INSERT INTO dataset_info VALUES ('trip_sample_fraction', ?)", (str(args.sample),))
    con.commit()

    print(f"Loading trips{'' if args.sample == 1 else f' ({args.sample:.0%} sample)'} ...")
    n_trips = load_trips(con, args.sample)
    con.commit()

    print("Loading exclusion log ...")
    n_excluded = load_excluded(con)
    con.commit()
    print(f"  {n_excluded:,} excluded records")

    print("Building summary tables ...")
    for table, rows in build_summaries(con).items():
        print(f"  {table}: {rows:,} rows")
    con.commit()

    print("Building indexes ...")
    con.executescript(INDEXES_SQL.read_text(encoding="utf-8"))

    print("Verifying ...")
    verify(con, n_trips)
    if args.sample < 1:
        con.execute("VACUUM")   # compact the small demo/deploy copy before it is shared
    con.close()
    print(f"\nDone in {time.time() - start:.0f}s -> {db_path} "
          f"({db_path.stat().st_size / 1e9:.2f} GB)")


if __name__ == "__main__":
    main()