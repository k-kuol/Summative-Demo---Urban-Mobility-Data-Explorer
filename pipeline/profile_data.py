"""
profile_data.py
---------------
Step 1 of the pipeline: LOOK before we clean.

Reads the raw yellow taxi CSV and reports what is missing, duplicated or
physically impossible. The numbers it prints are the evidence behind every
cleaning rule in clean_data.py, and they go straight into the report.

Usage (from the project root):
    python pipeline/profile_data.py                       # default raw file
    python pipeline/profile_data.py --input path/to/file  # .parquet or .csv
Output:
    printed to the screen and saved to docs/data_profile.txt
"""

import sys
from pathlib import Path

import pandas as pd

from trips_io import input_path, read_trips

ROOT = Path(__file__).resolve().parent.parent
LOOKUP_CSV = ROOT / "data" / "raw" / "taxi_zone_lookup.csv"
OUT_TXT = ROOT / "docs" / "data_profile.txt"

MONTH_START = pd.Timestamp("2019-01-01")
MONTH_END = pd.Timestamp("2019-02-01")

lines = []


def say(text=""):
    print(text)
    lines.append(str(text))


def pct(n, total):
    return f"{n:>10,}  ({n / total:6.2%})"


def main():
    raw_path = input_path("Report missing, duplicate and impossible values in the raw trips.")
    say(f"Loading {raw_path.name} ...")
    df = read_trips(raw_path)
    n = len(df)
    say(f"Rows: {n:,}   Columns: {df.shape[1]}")
    say()

    # ---------------------------------------------------------------- missing
    say("=== MISSING VALUES ===")
    for col in df.columns:
        miss = int(df[col].isna().sum())
        if miss:
            say(f"  {col:<24}{pct(miss, n)}")
    say()

    # ------------------------------------------------------------- duplicates
    say("=== DUPLICATES ===")
    dup = int(df.duplicated().sum())
    say(f"  exact duplicate rows      {pct(dup, n)}")
    say()

    # --------------------------------------------------------------- time
    say("=== TIMESTAMPS ===")
    pu, do = df["tpep_pickup_datetime"], df["tpep_dropoff_datetime"]
    dur_min = (do - pu).dt.total_seconds() / 60
    say(f"  pickup range: {pu.min()}  ->  {pu.max()}")
    say(f"  pickup outside Jan 2019   {pct(int(((pu < MONTH_START) | (pu >= MONTH_END)).sum()), n)}")
    say(f"  dropoff before pickup     {pct(int((dur_min < 0).sum()), n)}")
    say(f"  zero duration             {pct(int((dur_min == 0).sum()), n)}")
    say(f"  duration < 1 min          {pct(int(((dur_min > 0) & (dur_min < 1)).sum()), n)}")
    say(f"  duration > 3 hours        {pct(int((dur_min > 180).sum()), n)}")
    say(f"  duration > 24 hours       {pct(int((dur_min > 1440).sum()), n)}")
    say(f"  duration quantiles (min): " + ", ".join(
        f"p{q * 100:g}={dur_min.quantile(q):.1f}" for q in (0.01, 0.5, 0.99, 0.999)))
    say()

    # --------------------------------------------------------------- distance
    say("=== DISTANCE (miles) ===")
    d = df["trip_distance"]
    say(f"  zero distance             {pct(int((d == 0).sum()), n)}")
    say(f"  negative distance         {pct(int((d < 0).sum()), n)}")
    say(f"  > 100 miles               {pct(int((d > 100).sum()), n)}")
    say(f"  max distance              {d.max():,.1f}")
    say(f"  quantiles: " + ", ".join(f"p{q / 10:g}={d.quantile(q / 1000):.2f}" for q in (10, 500, 990, 999)))
    say()

    # ------------------------------------------------------------------ speed
    say("=== IMPLIED SPEED (mph) ===")
    ok = (dur_min > 0) & (d > 0)
    speed = d[ok] / (dur_min[ok] / 60)
    say(f"  speed > 60 mph            {pct(int((speed > 60).sum()), n)}")
    say(f"  speed > 100 mph           {pct(int((speed > 100).sum()), n)}")
    say(f"  median speed              {speed.median():.1f} mph")
    say()

    # ------------------------------------------------------------------ money
    say("=== MONEY ($) ===")
    for col in ["fare_amount", "tip_amount", "tolls_amount", "total_amount", "extra", "mta_tax"]:
        s = df[col]
        say(f"  {col:<20} negative {pct(int((s < 0).sum()), n)}   zero {int((s == 0).sum()):>9,}"
            f"   max {s.max():>10,.2f}")
    say(f"  fare > $500               {pct(int((df['fare_amount'] > 500).sum()), n)}")
    parts = df[["fare_amount", "extra", "mta_tax", "tip_amount", "tolls_amount",
                "improvement_surcharge"]].sum(axis=1) + df["congestion_surcharge"].fillna(0)
    mismatch = (parts - df["total_amount"]).abs() > 0.01
    say(f"  total != sum of parts     {pct(int(mismatch.sum()), n)}")
    say()

    # ------------------------------------------------------------ categories
    say("=== CATEGORICAL CODES ===")
    for col in ["VendorID", "passenger_count", "RatecodeID", "payment_type", "store_and_fwd_flag"]:
        counts = df[col].value_counts(dropna=False).sort_index()
        say(f"  {col}: " + ", ".join(f"{k}={v:,}" for k, v in counts.items()))
    say("  (valid per TLC dictionary: RatecodeID 1-6, payment_type 1-6, VendorID 1-2)")
    say()

    # ------------------------------------------------------------- locations
    say("=== LOCATIONS ===")
    lookup = pd.read_csv(LOOKUP_CSV)
    valid_ids = set(lookup["LocationID"])
    for col in ["PULocationID", "DOLocationID"]:
        s = df[col]
        say(f"  {col}: not in lookup {pct(int((~s.isin(valid_ids)).sum()), n)}   "
            f"unknown (264/265) {pct(int(s.isin([264, 265]).sum()), n)}")
    say(f"  same pickup & dropoff zone{pct(int((df['PULocationID'] == df['DOLocationID']).sum()), n)}")
    say()

    # ------------------------------------------------------------- tip quirk
    say("=== TIPS BY PAYMENT TYPE ===")
    say("  (1=credit card, 2=cash, 3=no charge, 4=dispute)")
    tips = df.groupby("payment_type")["tip_amount"].agg(["count", "mean", lambda s: (s > 0).mean()])
    tips.columns = ["trips", "avg_tip", "share_with_tip"]
    for pt, row in tips.iterrows():
        say(f"  type {pt}: trips={int(row.trips):>9,}  avg tip=${row.avg_tip:5.2f}  "
            f"tipped={row.share_with_tip:6.1%}")

    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSaved to {OUT_TXT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())