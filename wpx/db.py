"""SQLite schema for the analysis, template and matter stages.

Deliberately the same database file wpd_convert.py already writes: one file is
the whole audit trail, from "this .wpd was converted at 03:14" through "this
value in that letter became {{insurer.claim_no}}" to "this matter's claim
number is X". Every table is created on demand, so pointing the new commands
at an existing firmconvert.db is safe.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id         INTEGER PRIMARY KEY,
    path       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    sha256     TEXT,
    para_count INTEGER,
    flags      TEXT,          -- markup needing a human: tracked changes, field codes
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

-- The address book, mined from the corpus: every carrier and clinic the firm
-- has written to, with the block it was addressed by. Durable entity details
-- only (see Field.contact) — never a claim number or an account number, which
-- belong to a matter.
CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY,
    role       TEXT NOT NULL,          -- insurer | provider | firm
    slug       TEXT NOT NULL,
    name       TEXT NOT NULL,
    doc_count  INTEGER NOT NULL DEFAULT 0,
    source     TEXT NOT NULL DEFAULT 'corpus',   -- corpus | manual
    updated_at TEXT,
    UNIQUE (role, slug)
);

CREATE TABLE IF NOT EXISTS contact_values (
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    field_key  TEXT NOT NULL,
    value      TEXT,
    doc_count  INTEGER NOT NULL DEFAULT 0,
    variants   TEXT,          -- JSON [[value, count], ...] a carrier with two intake addresses
    PRIMARY KEY (contact_id, field_key)
);

-- Adjusters at a carrier, records custodians at a clinic: one entity, many people.
CREATE TABLE IF NOT EXISTS contact_people (
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    field_key  TEXT NOT NULL,          -- adjuster.name | provider.attn
    name       TEXT NOT NULL,
    doc_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (contact_id, field_key, name)
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


# Columns added after the first release. Existing databases are upgraded in
# place rather than rebuilt, because the conversion audit trail lives in them.
MIGRATIONS = (
    ("docs", "flags", "TEXT"),
)


def _migrate(con: sqlite3.Connection) -> None:
    for table, column, decl in MIGRATIONS:
        existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    con.commit()


def protect(path) -> None:
    """Keep the database readable only by its owner.

    It holds client names, dates of birth, claim numbers and medical
    providers. This is a floor, not a security model — see docs/PIPELINE.md.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows ACLs are managed by the file share, not by chmod


def in_git_worktree(path) -> Path | None:
    """The repository this file sits inside, if any — client data must not be
    committed, and a database created next to the source tree easily is."""
    for parent in Path(path).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


# UPSERT ("ON CONFLICT ... DO UPDATE") arrived in SQLite 3.24, June 2018.
# Every Python from 3.8 onwards ships something newer, but an old embedded
# build would otherwise fail with a bare syntax error deep inside a scan.
MIN_SQLITE = (3, 24, 0)


def check_sqlite(version=None) -> None:
    version = version or sqlite3.sqlite_version_info
    if tuple(version[:3]) < MIN_SQLITE:
        have = ".".join(str(n) for n in version[:3])
        need = ".".join(str(n) for n in MIN_SQLITE)
        raise RuntimeError(
            f"this Python is built against SQLite {have}; wpx needs {need} or newer. "
            "Install a current Python (3.9+) from python.org and re-run."
        )


def open_db(path) -> sqlite3.Connection:
    check_sqlite()
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    con.executescript(SCHEMA)
    _migrate(con)
    protect(path)
    con.row_factory = sqlite3.Row
    return con
