"""SQLite schema for the analysis, template and matter stages.

Deliberately the same database file wpd_convert.py already writes: one file is
the whole audit trail, from "this .wpd was converted at 03:14" through "this
value in that letter became {{insurer.claim_no}}" to "this matter's claim
number is X". Every table is created on demand, so pointing the new commands
at an existing firmconvert.db is safe.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id         INTEGER PRIMARY KEY,
    path       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    sha256     TEXT,
    para_count INTEGER,
    scanned_at TEXT
);

CREATE TABLE IF NOT EXISTS hits (
    id         INTEGER PRIMARY KEY,
    doc_id     INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    part       TEXT,
    pidx       INTEGER,
    span_start INTEGER,
    span_end   INTEGER,
    value      TEXT,
    norm_value TEXT,
    field_key  TEXT,
    detector   TEXT,
    confidence REAL,
    label      TEXT
);
CREATE INDEX IF NOT EXISTS idx_hits_doc ON hits(doc_id);
CREATE INDEX IF NOT EXISTS idx_hits_field ON hits(field_key);
CREATE INDEX IF NOT EXISTS idx_hits_value ON hits(norm_value);

-- One row per distinct value seen anywhere in the corpus. 'kind' is what the
-- roll-up decided: boilerplate that never changes, or a per-matter variable.
CREATE TABLE IF NOT EXISTS catalog (
    norm_value TEXT PRIMARY KEY,
    sample     TEXT,
    field_key  TEXT,
    doc_count  INTEGER,
    hit_count  INTEGER,
    kind       TEXT,          -- constant | variable | unknown
    decided_at TEXT
);

-- Human corrections to the detectors: "this value is the date of loss".
-- Applied on every later scan, so a value only has to be mapped once.
CREATE TABLE IF NOT EXISTS value_overrides (
    norm_value TEXT PRIMARY KEY,
    field_key  TEXT,          -- NULL means "never treat this as data"
    note       TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS matters (
    id         INTEGER PRIMARY KEY,
    ref        TEXT NOT NULL UNIQUE,
    created_at TEXT
);

-- scope is '' for facts that belong to the matter itself, and a party name
-- such as 'provider:1' for facts that repeat: a matter has one client and one
-- carrier, but four clinics, each needing its own records request.
CREATE TABLE IF NOT EXISTS matter_values (
    matter_id  INTEGER NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    scope      TEXT NOT NULL DEFAULT '',
    field_key  TEXT NOT NULL,
    value      TEXT,
    updated_at TEXT,
    PRIMARY KEY (matter_id, scope, field_key)
);

CREATE TABLE IF NOT EXISTS templates (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    path        TEXT,
    source_doc  TEXT,
    fields_json TEXT,
    replaced    INTEGER,
    created_at  TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_db(path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    return con
