-- =====================================================================
-- NYC Urban Mobility database schema (SQLite)
--
-- Design: a STAR SCHEMA, normalized to 3NF.
--   * One FACT table  (trips)  : one row per cleaned taxi ride.
--   * DIMENSION tables          : the "lookup" facts every ride points to
--                                 (zones, boroughs, vendors, rate codes,
--                                 payment types). Stored once, referenced
--                                 by ID, so a name is never repeated
--                                 7.5 million times.
--   * Data-quality tables       : excluded_records + exclusion_reasons keep
--                                 the transparency log inside the database.
--   * Two SUMMARY tables        : zone_date_hour_stats + route_date_stats,
--                                 pre-aggregated so the dashboard is instant
--                                 (deliberate trade-off, see report).
-- =====================================================================

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS dataset_info;
DROP TABLE IF EXISTS zone_date_hour_stats;
DROP TABLE IF EXISTS route_date_stats;
DROP TABLE IF EXISTS excluded_records;
DROP TABLE IF EXISTS exclusion_reasons;
DROP TABLE IF EXISTS trips;
DROP TABLE IF EXISTS zones;
DROP TABLE IF EXISTS boroughs;
DROP TABLE IF EXISTS vendors;
DROP TABLE IF EXISTS rate_codes;
DROP TABLE IF EXISTS payment_types;

-- ---------------------------------------------------------------------
-- Dimension tables
-- ---------------------------------------------------------------------
CREATE TABLE boroughs (
    borough_id   INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL UNIQUE
);

CREATE TABLE zones (
    zone_id       INTEGER PRIMARY KEY,               -- = TLC LocationID (1..265)
    zone_name     TEXT    NOT NULL,
    borough_id    INTEGER NOT NULL REFERENCES boroughs(borough_id),
    service_zone  TEXT,                              -- Yellow Zone / Boro Zone / Airports / EWR
    is_airport    INTEGER NOT NULL DEFAULT 0 CHECK (is_airport IN (0, 1)),
    geometry      TEXT                               -- GeoJSON polygon; NULL for 57,104,105,264,265
);

CREATE TABLE vendors (
    vendor_id    INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL
);

CREATE TABLE rate_codes (
    rate_code_id INTEGER PRIMARY KEY,
    description  TEXT    NOT NULL
);

CREATE TABLE payment_types (
    payment_type_id INTEGER PRIMARY KEY,
    description     TEXT    NOT NULL
);

-- ---------------------------------------------------------------------
-- Fact table: one row per cleaned ride
-- ---------------------------------------------------------------------
CREATE TABLE trips (
    trip_id                INTEGER PRIMARY KEY,
    source_row             INTEGER NOT NULL UNIQUE,  -- line in the raw CSV (traceability)
    vendor_id              INTEGER NOT NULL REFERENCES vendors(vendor_id),
    pickup_datetime        TEXT    NOT NULL,          -- 'YYYY-MM-DD HH:MM:SS', NYC local time
    dropoff_datetime       TEXT    NOT NULL,
    passenger_count        INTEGER CHECK (passenger_count BETWEEN 1 AND 9),  -- NULL = unknown
    trip_distance          REAL    NOT NULL CHECK (trip_distance > 0 AND trip_distance <= 100),
    rate_code_id           INTEGER REFERENCES rate_codes(rate_code_id),      -- NULL = invalid code
    store_and_fwd_flag     INTEGER NOT NULL CHECK (store_and_fwd_flag IN (0, 1)),
    pickup_zone_id         INTEGER NOT NULL REFERENCES zones(zone_id),
    dropoff_zone_id        INTEGER NOT NULL REFERENCES zones(zone_id),
    payment_type_id        INTEGER NOT NULL REFERENCES payment_types(payment_type_id),

    fare_amount            REAL    NOT NULL CHECK (fare_amount > 0 AND fare_amount <= 500),
    extra                  REAL    NOT NULL CHECK (extra >= 0),
    mta_tax                REAL    NOT NULL CHECK (mta_tax >= 0),
    tip_amount             REAL    NOT NULL CHECK (tip_amount >= 0),
    tolls_amount           REAL    NOT NULL CHECK (tolls_amount >= 0),
    improvement_surcharge  REAL    NOT NULL CHECK (improvement_surcharge >= 0),
    congestion_surcharge   REAL    NOT NULL CHECK (congestion_surcharge >= 0),
    total_amount           REAL    NOT NULL CHECK (total_amount > 0),

    -- Derived features (computed in pipeline/clean_data.py)
    trip_duration_min      REAL    NOT NULL CHECK (trip_duration_min > 0 AND trip_duration_min <= 360),
    avg_speed_mph          REAL    NOT NULL CHECK (avg_speed_mph BETWEEN 0 AND 80),
    fare_per_mile          REAL,                      -- NULL when distance < 0.25 mi
    tip_pct                REAL,                      -- NULL unless card payment and fare >= $2.50
    pickup_hour            INTEGER NOT NULL CHECK (pickup_hour BETWEEN 0 AND 23),
    pickup_dow             INTEGER NOT NULL CHECK (pickup_dow BETWEEN 0 AND 6),   -- 0 = Monday
    is_weekend             INTEGER NOT NULL CHECK (is_weekend IN (0, 1)),
    time_of_day            TEXT    NOT NULL CHECK (time_of_day IN
                               ('night', 'morning_rush', 'midday', 'evening_rush', 'evening')),
    is_airport_trip        INTEGER NOT NULL CHECK (is_airport_trip IN (0, 1)),

    CHECK (dropoff_datetime > pickup_datetime)
);

-- ---------------------------------------------------------------------
-- Data-quality tables (the transparency log)
-- ---------------------------------------------------------------------
CREATE TABLE exclusion_reasons (
    reason_code  TEXT PRIMARY KEY,
    description  TEXT NOT NULL
);

CREATE TABLE excluded_records (
    excluded_id      INTEGER PRIMARY KEY,
    source_row       INTEGER NOT NULL UNIQUE,
    reason_code      TEXT    NOT NULL REFERENCES exclusion_reasons(reason_code),
    pickup_datetime  TEXT,
    dropoff_datetime TEXT,
    trip_distance    REAL,
    pickup_zone_id   INTEGER,
    dropoff_zone_id  INTEGER,
    fare_amount      REAL,
    total_amount     REAL
);

-- ---------------------------------------------------------------------
-- Summary tables: pre-aggregated totals so the dashboard never has to
-- scan 7.5M rows for its common questions. Rebuilt by the loader from
-- the trips table; never edited by hand. They store SUMS and COUNTS (not
-- averages) so any combination of rows can be re-combined correctly.
-- ---------------------------------------------------------------------

-- One row per date x pickup zone x hour (~100k rows).
-- Answers: KPIs, hourly/daily charts, heatmap, map, for any date range,
-- borough, zone, weekday or hour filter.
CREATE TABLE zone_date_hour_stats (
    pickup_date      TEXT    NOT NULL,    -- 'YYYY-MM-DD'
    pickup_zone_id   INTEGER NOT NULL REFERENCES zones(zone_id),
    pickup_hour      INTEGER NOT NULL,
    pickup_dow       INTEGER NOT NULL,    -- stored to filter by weekday without date maths
    trip_count       INTEGER NOT NULL,
    sum_distance     REAL    NOT NULL,
    sum_duration_min REAL    NOT NULL,
    sum_fare         REAL    NOT NULL,
    sum_total        REAL    NOT NULL,
    sum_tip_pct      REAL    NOT NULL,    -- over card trips that have a tip_pct
    tip_pct_trips    INTEGER NOT NULL,
    PRIMARY KEY (pickup_date, pickup_zone_id, pickup_hour)
);

-- One row per date x pickup zone x dropoff zone.
-- Answers: "most travelled routes" for any date range / borough / zone.
CREATE TABLE route_date_stats (
    pickup_date      TEXT    NOT NULL,
    pickup_zone_id   INTEGER NOT NULL REFERENCES zones(zone_id),
    dropoff_zone_id  INTEGER NOT NULL REFERENCES zones(zone_id),
    pickup_dow       INTEGER NOT NULL,
    trip_count       INTEGER NOT NULL,
    sum_distance     REAL    NOT NULL,
    sum_duration_min REAL    NOT NULL,
    sum_fare         REAL    NOT NULL,
    PRIMARY KEY (pickup_date, pickup_zone_id, dropoff_zone_id)
);

-- ---------------------------------------------------------------------
-- How this database was built (e.g. the trip sample fraction), so the
-- API and dashboard can tell users when they are looking at a sample.
-- ---------------------------------------------------------------------
CREATE TABLE dataset_info (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
