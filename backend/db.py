"""
db.py
-----
Database access for the API: opening connections and turning the dashboard's
filter settings (from the URL) into a safe SQL WHERE clause.

SECURITY: user input is NEVER pasted into SQL text. Every value goes in as a
"?" parameter, and column names for sorting come from a fixed whitelist. This
blocks SQL injection.
"""

import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from flask import g

ROOT = Path(__file__).resolve().parent.parent
# A relative MOBILITY_DB is taken from the project root, not the current
# directory, so it works however the server is launched (e.g. gunicorn --chdir).
_db_env = Path(os.environ.get("MOBILITY_DB", "data/mobility.db"))
DB_PATH = (_db_env if _db_env.is_absolute() else ROOT / _db_env).resolve()


class BadRequest(ValueError):
    """Raised for invalid query parameters; the API turns it into HTTP 400."""


def get_db():
    """One read-only connection per request, closed automatically afterwards."""
    if "db" not in g:
        if not DB_PATH.exists():
            raise RuntimeError(f"Database not found at {DB_PATH}. Run the pipeline first (see README).")
        # as_uri() turns the path into file:///C:/Users/hp%20pc/... so spaces and
        # Windows backslashes are handled; mode=ro opens the database read-only.
        g.db = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, params=()):
    return [dict(row) for row in get_db().execute(sql, params).fetchall()]


def query_one(sql, params=()):
    row = get_db().execute(sql, params).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def int_arg(args, name, lo, hi, default):
    """A whole-number URL parameter in [lo, hi]; bad input raises BadRequest (HTTP 400)."""
    return _int(args, name, lo, hi, default)


def _int(args, name, lo, hi, default=None):
    raw = args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadRequest(f"'{name}' must be a whole number")
    if not lo <= value <= hi:
        raise BadRequest(f"'{name}' must be between {lo} and {hi}")
    return value


def _float(args, name, lo, hi):
    raw = args.get(name)
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except ValueError:
        raise BadRequest(f"'{name}' must be a number")
    if not lo <= value <= hi:
        raise BadRequest(f"'{name}' must be between {lo} and {hi}")
    return value


def _date(args, name):
    raw = args.get(name)
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise BadRequest(f"'{name}' must be a date like 2019-01-15")


def parse_filters(args):
    """Read the supported filters from the query string and validate them."""
    f = {
        "start_date": _date(args, "start_date"),
        "end_date": _date(args, "end_date"),
        "borough_id": _int(args, "borough_id", 1, 100),
        "zone_id": _int(args, "zone_id", 1, 265),
        "dow": _int(args, "dow", 0, 6),
        "hour_min": _int(args, "hour_min", 0, 23),
        "hour_max": _int(args, "hour_max", 0, 23),
        "min_distance": _float(args, "min_distance", 0, 100),
        "max_distance": _float(args, "max_distance", 0, 100),
        "min_fare": _float(args, "min_fare", 0, 500),
        "max_fare": _float(args, "max_fare", 0, 500),
        "payment_type_id": _int(args, "payment_type_id", 1, 6),
        "airport_only": args.get("airport_only") in ("1", "true"),
    }
    if f["start_date"] and f["end_date"] and f["start_date"] > f["end_date"]:
        raise BadRequest("'start_date' is after 'end_date'")
    return f


# Which filters each pre-aggregated summary table can answer. Any other filter
# (distance, fare, payment type, airport) needs the full trips table.
ZONE_STATS_FILTERS = {"start_date", "end_date", "borough_id", "zone_id", "dow", "hour_min", "hour_max"}
ROUTE_STATS_FILTERS = {"start_date", "end_date", "borough_id", "zone_id", "dow"}


def active_filters(f):
    return {k for k, v in f.items() if v not in (None, False)}


def can_use(f, allowed):
    """True when every active filter is one the summary table can answer."""
    return active_filters(f) <= allowed


def where_clause(f, prefix="", date_col="pickup_datetime"):
    """
    Build 'WHERE ...' and its parameters. `prefix` is a table alias like 't.'.
    Works on the trips table and on the summary tables, which share the
    pickup_zone_id / pickup_dow / pickup_hour column names; for those, pass
    date_col="pickup_date". Dates are 'YYYY-MM-DD' text, so comparing
    strings compares dates correctly.
    """
    p = prefix
    parts, params = [], []
    if f["start_date"]:
        parts.append(f"{p}{date_col} >= ?")
        params.append(f["start_date"].isoformat())
    if f["end_date"]:
        parts.append(f"{p}{date_col} < ?")   # end date is inclusive
        params.append((f["end_date"] + timedelta(days=1)).isoformat())
    if f["zone_id"] is not None:
        parts.append(f"{p}pickup_zone_id = ?")
        params.append(f["zone_id"])
    elif f["borough_id"] is not None:
        parts.append(f"{p}pickup_zone_id IN (SELECT zone_id FROM zones WHERE borough_id = ?)")
        params.append(f["borough_id"])
    if f["dow"] is not None:
        parts.append(f"{p}pickup_dow = ?")
        params.append(f["dow"])
    if f["hour_min"] is not None:
        parts.append(f"{p}pickup_hour >= ?")
        params.append(f["hour_min"])
    if f["hour_max"] is not None:
        parts.append(f"{p}pickup_hour <= ?")
        params.append(f["hour_max"])
    if f["min_distance"] is not None:
        parts.append(f"{p}trip_distance >= ?")
        params.append(f["min_distance"])
    if f["max_distance"] is not None:
        parts.append(f"{p}trip_distance <= ?")
        params.append(f["max_distance"])
    if f["min_fare"] is not None:
        parts.append(f"{p}fare_amount >= ?")
        params.append(f["min_fare"])
    if f["max_fare"] is not None:
        parts.append(f"{p}fare_amount <= ?")
        params.append(f["max_fare"])
    if f["payment_type_id"] is not None:
        parts.append(f"{p}payment_type_id = ?")
        params.append(f["payment_type_id"])
    if f["airport_only"]:
        parts.append(f"{p}is_airport_trip = 1")
    sql = ("WHERE " + " AND ".join(parts)) if parts else ""
    return sql, params