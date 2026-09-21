"""Lightweight SQLite storage for prediction snapshots and rider observations.

Predictions are not actual arrival measurements - they're kept in a separate
table from rider-reported "bus arrived" observations so the two are never
confused when this data is eventually analyzed for on-time performance.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observed_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    route TEXT NOT NULL,
    stop_key TEXT NOT NULL,
    direction TEXT NOT NULL,
    trip_id TEXT,
    scheduled_time TEXT,
    predicted_time TEXT,
    minutes_out INTEGER,
    delay_seconds INTEGER,
    prediction_type TEXT,
    data_source TEXT
);

CREATE TABLE IF NOT EXISTS user_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    route TEXT NOT NULL,
    stop_key TEXT NOT NULL,
    direction TEXT,
    note TEXT NOT NULL
);
"""

_db_path = "data/history.db"


def init_db(db_path="data/history.db"):
    """Create the database file and tables if they don't already exist."""
    global _db_path
    _db_path = db_path
    parent = Path(db_path).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _connect():
    conn = sqlite3.connect(_db_path, timeout=5)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_snapshot(route, stop_key, direction, arrival, recorded_at, data_source=None):
    """Store one observed prediction (live or scheduled-only) for later analysis."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO observed_predictions (
                recorded_at, route, stop_key, direction, trip_id,
                scheduled_time, predicted_time, minutes_out, delay_seconds,
                prediction_type, data_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recorded_at,
                route,
                stop_key,
                direction,
                arrival.get("trip_id"),
                arrival.get("scheduled_time"),
                arrival.get("time") or arrival.get("arrival_time"),
                arrival.get("minutes"),
                arrival.get("delay_seconds"),
                arrival.get("prediction_type"),
                data_source,
            ),
        )


def record_observation(route, stop_key, direction, note, recorded_at):
    """Store a rider-reported observation, e.g. an actual bus arrival."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_observations (recorded_at, route, stop_key, direction, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (recorded_at, route, stop_key, direction, note),
        )


def recent_observations(limit=50):
    """Return the most recent rider-reported observations."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM user_observations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
