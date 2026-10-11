"""
trips_io.py
-----------
Shared input handling for profile_data.py and clean_data.py, so both scripts
read the raw trips the same way.

TLC publishes the trip data as .parquet (the format named in the brief); the
course also distributed it as .csv. Both are accepted. The input path can be
given on the command line, so a sample file can be used without editing code:

    python pipeline/clean_data.py --input data/raw/yellow_tripdata_sample.csv
    python pipeline/clean_data.py --input data/raw/yellow_tripdata_2019-01.parquet
"""

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DEFAULT_NAMES = ("yellow_tripdata_2019-01.parquet", "yellow_tripdata_2019-01.csv")
DATETIME_COLS = ["tpep_pickup_datetime", "tpep_dropoff_datetime"]


def default_input():
    """The parquet file if it is there, otherwise the CSV."""
    for name in DEFAULT_NAMES:
        if (RAW_DIR / name).exists():
            return RAW_DIR / name
    return RAW_DIR / DEFAULT_NAMES[-1]


def input_path(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--input", type=Path, default=None,
                        help="raw yellow taxi file, .parquet or .csv "
                             "(default: data/raw/yellow_tripdata_2019-01.parquet or .csv)")
    path = parser.parse_args().input or default_input()
    if not path.exists():
        parser.error(f"input file not found: {path}. See README section 2.")
    return path


def read_trips(path):
    """Load raw trips with parsed timestamps, whatever the file format."""
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
        # Newer TLC parquet files carry an airport_fee column that is always
        # empty for 2019 and is not part of the 2019 data dictionary.
        df = df.drop(columns=[c for c in ("airport_fee", "Airport_fee") if c in df.columns])
        for col in DATETIME_COLS:
            df[col] = pd.to_datetime(df[col])
        df["store_and_fwd_flag"] = df["store_and_fwd_flag"].astype("string")
        return df
    return pd.read_csv(path, parse_dates=DATETIME_COLS,
                       dtype={"store_and_fwd_flag": "string"}, low_memory=False)
