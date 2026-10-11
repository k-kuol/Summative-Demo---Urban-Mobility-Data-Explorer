"""
clean_data.py
-------------
Step 2 of the pipeline: CLEAN, NORMALIZE and ENRICH the raw trip data.

What it does, in order:
  1. Loads the raw yellow taxi CSV and the zone lookup table.
  2. Checks every ride against a list of validity rules. A ride that breaks a
     rule is REMOVED and written to the exclusion log with the reason.
  3. Repairs small problems in otherwise good rides (blank congestion fee,
     "0 passengers", invalid rate code) and records those in a suspicious log.
  4. Normalizes formats (timestamps, money to 2 decimals, Y/N flags to 1/0).
  5. Adds derived features (speed, fare per mile, tip %, time-of-day...).
  6. Saves the clean trips plus both logs and a summary.

Usage (from the project root):
    python pipeline/clean_data.py                       # data/raw/yellow_tripdata_2019-01.parquet or .csv
    python pipeline/clean_data.py --input path/to/file  # e.g. a sample made by make_sample.py

Outputs (in data/processed/):
    trips_clean.csv          the clean, enriched rides
    excluded_records.csv     every removed ride and why
    suspicious_records.csv   every repaired ride and what was repaired
    cleaning_summary.json    counts per rule (used in the report)
"""

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from trips_io import input_path, read_trips

ROOT = Path(__file__).resolve().parent.parent
LOOKUP_CSV = ROOT / "data" / "raw" / "taxi_zone_lookup.csv"
OUT_DIR = ROOT / "data" / "processed"

# ---------------------------------------------------------------------------
# Thresholds. Each one is justified in the report; change them here only.
# ---------------------------------------------------------------------------
MONTH_START = pd.Timestamp("2019-01-01")
MONTH_END = pd.Timestamp("2019-02-01")
MAX_DURATION_MIN = 6 * 60      # longer = meter left running (spike at ~24 h)
MAX_DISTANCE_MI = 100          # NYC to the furthest TLC destinations is well under this
MAX_FARE = 500                 # a JFK flat fare was $52; $500 is generous
MAX_SPEED_MPH = 80             # faster than any legal NYC road speed
AIRPORT_ZONES = {1, 132, 138}  # Newark (EWR), JFK, LaGuardia
UNKNOWN_ZONES = {264, 265}     # "Unknown" and "Outside of NYC"
MIN_DISTANCE_FOR_RATIO = 0.25  # miles; below this fare_per_mile is meaningless
NYC_BASE_FARE = 2.50           # 2019 initial meter charge; below this tip_pct is meaningless

VALID_RATECODES = {1, 2, 3, 4, 5, 6}
VALID_PAYMENT_TYPES = {1, 2, 3, 4, 5, 6}

MONEY_COLS = ["fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount",
              "improvement_surcharge", "congestion_surcharge", "total_amount"]


def load_raw(path):
    print(f"Loading {path.name} ...")
    df = read_trips(path)
    # Keep the original line position so any removed ride can be traced back
    # to the raw file (row 1 = first ride after the header).
    df.insert(0, "source_row", np.arange(1, len(df) + 1))
    print(f"  {len(df):,} rides loaded")
    return df


def apply_exclusion_rules(df):
    """
    Each rule is a (name, boolean mask) pair. A ride is excluded by the FIRST
    rule it breaks, so every excluded ride has exactly one reason and the
    counts in the summary add up to the total removed.
    """
    duration_min = (df["tpep_dropoff_datetime"] - df["tpep_pickup_datetime"]).dt.total_seconds() / 60
    hours = duration_min / 60
    speed = df["trip_distance"] / hours.where(hours > 0)

    negative_component = (df[["extra", "mta_tax", "tip_amount", "tolls_amount",
                              "improvement_surcharge", "total_amount"]] < 0).any(axis=1)

    # A duplicate is a ride whose every raw field matches an earlier ride.
    # source_row is unique by construction, so it must be left out of the
    # comparison. keep="first" keeps the original and drops only the copies.
    duplicate = df.drop(columns="source_row").duplicated(keep="first")

    rules = [
        ("duplicate_record", duplicate),
        ("pickup_outside_jan_2019",
         (df["tpep_pickup_datetime"] < MONTH_START) | (df["tpep_pickup_datetime"] >= MONTH_END)),
        ("dropoff_not_after_pickup", duration_min <= 0),
        (f"duration_over_{MAX_DURATION_MIN // 60}h", duration_min > MAX_DURATION_MIN),
        ("zero_distance", df["trip_distance"] <= 0),
        (f"distance_over_{MAX_DISTANCE_MI}mi", df["trip_distance"] > MAX_DISTANCE_MI),
        ("fare_zero_or_negative", df["fare_amount"] <= 0),
        (f"fare_over_{MAX_FARE}", df["fare_amount"] > MAX_FARE),
        ("negative_charge_component", negative_component),
        (f"speed_over_{MAX_SPEED_MPH}mph", speed > MAX_SPEED_MPH),
    ]

    reason = pd.Series(pd.NA, index=df.index, dtype="string")
    for name, mask in rules:
        reason = reason.mask(reason.isna() & mask.fillna(False), name)

    excluded = df[reason.notna()].copy()
    excluded.insert(1, "exclusion_reason", reason[reason.notna()])
    kept = df[reason.isna()].copy()

    counts = {name: int((reason == name).sum()) for name, _ in rules}
    return kept, excluded, counts


def repair_and_normalize(df, lookup):
    """Fix small issues in rides we are keeping, and standardize formats."""
    notes = pd.Series("", index=df.index, dtype="string")

    def note(mask, text):
        nonlocal notes
        notes = notes.mask(mask, notes.where(notes == "", notes + ";") + text)

    # The congestion surcharge only began in February 2019, so blank means "not charged".
    df["congestion_surcharge"] = df["congestion_surcharge"].fillna(0.0)

    # "0 passengers" is a data-entry habit (99.7% from vendor 1), not an empty cab.
    zero_pax = df["passenger_count"] == 0
    note(zero_pax, "passenger_count_0_set_unknown")
    df["passenger_count"] = df["passenger_count"].astype("Int64").mask(zero_pax)

    # Rate code 99 is not in the TLC data dictionary.
    bad_rate = ~df["RatecodeID"].isin(VALID_RATECODES)
    note(bad_rate, "invalid_ratecode_set_unknown")
    df["RatecodeID"] = df["RatecodeID"].astype("Int64").mask(bad_rate)

    # Vendor 4 is not in the 2019 dictionary but its rides look normal: keep, flag.
    note(~df["VendorID"].isin([1, 2]), "undocumented_vendor_id")

    # Rides whose total does not equal the sum of its parts: keep, flag.
    parts = df[MONEY_COLS[:-1]].sum(axis=1)
    note((parts - df["total_amount"]).abs() > 0.01, "total_not_equal_sum_of_parts")

    # Valid zone IDs that carry no real location.
    note(df["PULocationID"].isin(UNKNOWN_ZONES), "pickup_zone_unknown")
    note(df["DOLocationID"].isin(UNKNOWN_ZONES), "dropoff_zone_unknown")

    # Every zone ID must exist in the dimension table (enforced again by the DB).
    valid_zone_ids = set(lookup["LocationID"])
    bad_zone = ~df["PULocationID"].isin(valid_zone_ids) | ~df["DOLocationID"].isin(valid_zone_ids)
    if bad_zone.any():
        raise ValueError(f"{int(bad_zone.sum())} rides reference zones missing from the lookup")

    # Normalization: money to cents precision, Y/N to 1/0, consistent names.
    for col in MONEY_COLS:
        df[col] = df[col].round(2)
    df["trip_distance"] = df["trip_distance"].round(2)
    df["store_and_fwd_flag"] = (df["store_and_fwd_flag"] == "Y").astype("int8")
    df = df.rename(columns={
        "VendorID": "vendor_id",
        "tpep_pickup_datetime": "pickup_datetime",
        "tpep_dropoff_datetime": "dropoff_datetime",
        "RatecodeID": "rate_code_id",
        "PULocationID": "pickup_zone_id",
        "DOLocationID": "dropoff_zone_id",
        "payment_type": "payment_type_id",
    })

    suspicious = df.loc[notes != "", ["source_row", "pickup_datetime", "vendor_id",
                                      "pickup_zone_id", "dropoff_zone_id",
                                      "fare_amount", "total_amount"]].copy()
    suspicious.insert(1, "issues", notes[notes != ""])
    issue_counts = (notes[notes != ""].str.split(";").explode().value_counts().to_dict())
    return df, suspicious, {k: int(v) for k, v in issue_counts.items()}


def add_features(df):
    """
    Derived features (justification in the report):
      trip_duration_min  how long the ride took; the base for speed.
      avg_speed_mph      distance / time. A direct proxy for traffic congestion:
                         where and when the city moves slowest.
      fare_per_mile      price efficiency. Short, slow trips cost more per mile;
                         shows what congestion and minimum fares cost riders.
      tip_pct            tip as % of fare. ONLY for card payments, because cash
                         tips are never recorded (0% of cash rides show a tip).
      pickup_hour, pickup_dow, is_weekend, time_of_day
                         time buckets for rush-hour / nightlife patterns.
      is_airport_trip    pickup or dropoff at JFK, LaGuardia or Newark.
    """
    duration_min = (df["dropoff_datetime"] - df["pickup_datetime"]).dt.total_seconds() / 60
    df["trip_duration_min"] = duration_min.round(2)
    df["avg_speed_mph"] = (df["trip_distance"] / (duration_min / 60)).round(2)
    # Ratios explode when the denominator is tiny (a $2.50 fare over 0.01 mi is
    # "$250 per mile"), so they are left empty (NULL) unless the denominator is
    # meaningful: at least a quarter mile, and at least the $2.50 base fare.
    long_enough = df["trip_distance"] >= MIN_DISTANCE_FOR_RATIO
    df["fare_per_mile"] = (df["fare_amount"] / df["trip_distance"]).round(2).where(long_enough)

    is_card = df["payment_type_id"] == 1
    real_fare = df["fare_amount"] >= NYC_BASE_FARE
    df["tip_pct"] = (df["tip_amount"] / df["fare_amount"] * 100).round(2).where(is_card & real_fare)

    df["pickup_hour"] = df["pickup_datetime"].dt.hour.astype("int8")
    df["pickup_dow"] = df["pickup_datetime"].dt.dayofweek.astype("int8")   # 0 = Monday
    df["is_weekend"] = (df["pickup_dow"] >= 5).astype("int8")
    df["time_of_day"] = pd.cut(
        df["pickup_hour"], bins=[-1, 5, 9, 15, 19, 23],
        labels=["night", "morning_rush", "midday", "evening_rush", "evening"],
    ).astype("string")

    df["is_airport_trip"] = (df["pickup_zone_id"].isin(AIRPORT_ZONES)
                             | df["dropoff_zone_id"].isin(AIRPORT_ZONES)).astype("int8")
    return df


def main():
    raw_path = input_path("Clean, normalize and enrich the raw yellow taxi trips.")
    start = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    raw = load_raw(raw_path)
    lookup = pd.read_csv(LOOKUP_CSV)
    total = len(raw)

    print("Applying exclusion rules ...")
    kept, excluded, exclusion_counts = apply_exclusion_rules(raw)
    del raw
    for name, count in exclusion_counts.items():
        print(f"  {name:<30}{count:>9,}")
    print(f"  {'TOTAL EXCLUDED':<30}{len(excluded):>9,}  ({len(excluded) / total:.2%})")

    print("Repairing and normalizing kept rides ...")
    kept, suspicious, issue_counts = repair_and_normalize(kept, lookup)
    for name, count in issue_counts.items():
        print(f"  {name:<30}{count:>9,}")

    print("Adding derived features ...")
    kept = add_features(kept)

    print("Saving outputs ...")
    ts_format = "%Y-%m-%d %H:%M:%S"
    kept.to_csv(OUT_DIR / "trips_clean.csv", index=False, date_format=ts_format)
    excluded.to_csv(OUT_DIR / "excluded_records.csv", index=False, date_format=ts_format)
    suspicious.to_csv(OUT_DIR / "suspicious_records.csv", index=False, date_format=ts_format)

    summary = {
        "source_file": raw_path.name,
        "raw_rows": total,
        "clean_rows": len(kept),
        "excluded_rows": len(excluded),
        "excluded_pct": round(len(excluded) / total * 100, 3),
        "exclusion_reasons": exclusion_counts,
        "repaired_or_flagged_rows": len(suspicious),
        "repair_and_flag_reasons": issue_counts,
        "thresholds": {
            "max_duration_min": MAX_DURATION_MIN, "max_distance_mi": MAX_DISTANCE_MI,
            "max_fare": MAX_FARE, "max_speed_mph": MAX_SPEED_MPH,
        },
    }
    (OUT_DIR / "cleaning_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nDone in {time.time() - start:.0f}s: {len(kept):,} clean rides "
          f"({len(excluded):,} excluded, {len(suspicious):,} repaired/flagged).")


if __name__ == "__main__":
    main()