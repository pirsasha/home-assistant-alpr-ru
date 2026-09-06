from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import DB_PATH


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                open_gate INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                camera_entity TEXT NOT NULL DEFAULT '',
                trigger_entity TEXT NOT NULL DEFAULT '',
                plate TEXT NOT NULL DEFAULT '',
                confidence REAL,
                detector_confidence REAL,
                valid_format INTEGER,
                allowed INTEGER NOT NULL DEFAULT 0,
                gate_opened INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events(occurred_at DESC);
            """
        )


def normalize_plate(value: str) -> str:
    translit = str.maketrans({
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
        "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    })
    return "".join(ch for ch in value.upper().replace("Ё", "Е").translate(translit) if ch.isalnum())


def list_vehicles() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute("SELECT * FROM vehicles ORDER BY plate").fetchall()
    return [dict(row) for row in rows]


def upsert_vehicle(payload: dict[str, Any]) -> dict[str, Any]:
    plate = normalize_plate(str(payload.get("plate") or ""))
    if not plate:
        raise ValueError("Госномер обязателен")
    now = utc_now()
    with db() as connection:
        existing = connection.execute("SELECT * FROM vehicles WHERE plate = ?", (plate,)).fetchone()
        if existing:
            connection.execute(
                """UPDATE vehicles
                   SET name = ?, note = ?, enabled = ?, open_gate = ?, updated_at = ?
                   WHERE plate = ?""",
                (
                    str(payload.get("name") or "").strip(),
                    str(payload.get("note") or "").strip(),
                    int(bool(payload.get("enabled", True))),
                    int(bool(payload.get("open_gate", True))),
                    now,
                    plate,
                ),
            )
        else:
            connection.execute(
                """INSERT INTO vehicles
                   (plate, name, note, enabled, open_gate, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    plate,
                    str(payload.get("name") or "").strip(),
                    str(payload.get("note") or "").strip(),
                    int(bool(payload.get("enabled", True))),
                    int(bool(payload.get("open_gate", True))),
                    now,
                    now,
                ),
            )
        row = connection.execute("SELECT * FROM vehicles WHERE plate = ?", (plate,)).fetchone()
    return dict(row)


def delete_vehicle(vehicle_id: int) -> None:
    with db() as connection:
        connection.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))


def allowed_vehicle(plate: str) -> dict[str, Any] | None:
    normalized = normalize_plate(plate)
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM vehicles WHERE plate = ? AND enabled = 1",
            (normalized,),
        ).fetchone()
    return dict(row) if row else None


def add_event(payload: dict[str, Any]) -> int:
    with db() as connection:
        cursor = connection.execute(
            """INSERT INTO events
               (occurred_at, camera_entity, trigger_entity, plate, confidence,
                detector_confidence, valid_format, allowed, gate_opened, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.get("occurred_at") or utc_now(),
                str(payload.get("camera_entity") or ""),
                str(payload.get("trigger_entity") or ""),
                str(payload.get("plate") or ""),
                payload.get("confidence"),
                payload.get("detector_confidence"),
                None if payload.get("valid_format") is None else int(bool(payload.get("valid_format"))),
                int(bool(payload.get("allowed"))),
                int(bool(payload.get("gate_opened"))),
                str(payload.get("error") or ""),
            ),
        )
        return int(cursor.lastrowid)


def list_events(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(500, int(limit)))
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]
