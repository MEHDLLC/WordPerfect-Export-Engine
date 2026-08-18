"""Stage 5 — the facts of one matter, entered once.

A matter is a bag of canonical field values: the client's name, the carrier,
the claim number, the date of loss. Every template draws from the same bag, so
a claim number typed here is right on the demand letter, the med-pay request
and all six records requests at the same time — and correcting it corrects
every future letter.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from . import fields as F
from .db import now


def ensure_matter(con, ref: str) -> int:
    con.execute(
        "INSERT INTO matters(ref, created_at) VALUES (?,?) ON CONFLICT(ref) DO NOTHING",
        (ref, now()),
    )
    con.commit()
    return con.execute("SELECT id FROM matters WHERE ref=?", (ref,)).fetchone()[0]


def list_matters(con) -> list[dict]:
    rows = con.execute(
        "SELECT m.ref, m.created_at, COUNT(v.field_key) AS filled "
        "FROM matters m LEFT JOIN matter_values v ON v.matter_id = m.id "
        "GROUP BY m.id ORDER BY m.created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def set_values(con, ref: str, values: dict, scope: str = "") -> tuple[int, list[str]]:
    """Store values for a matter. Unknown field keys are reported, not stored."""
    matter_id = ensure_matter(con, ref)
    unknown = sorted(k for k in values if k not in F.BY_KEY)
    known = {k: v for k, v in values.items() if k in F.BY_KEY}
    con.executemany(
        "INSERT INTO matter_values(matter_id, scope, field_key, value, updated_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(matter_id, scope, field_key) DO UPDATE SET "
        "value=excluded.value, updated_at=excluded.updated_at",
        [(matter_id, scope, k, v, now()) for k, v in known.items()],
    )
    con.commit()
    return len(known), unknown


def matter_id(con, ref: str) -> int:
    row = con.execute("SELECT id FROM matters WHERE ref=?", (ref,)).fetchone()
    if row is None:
        raise KeyError(f"no such matter: {ref}")
    return row["id"]


def values(con, ref: str, scope: str = "") -> dict:
    """The matter's own facts, overlaid with one party's facts when scoped."""
    mid = matter_id(con, ref)
    rows = con.execute(
        "SELECT scope, field_key, value FROM matter_values WHERE matter_id=? "
        "AND scope IN ('', ?) ORDER BY scope",
        (mid, scope),
    )
    out: dict = {}
    for row in rows:  # '' sorts first, so a party value wins over the matter's
        out[row["field_key"]] = row["value"]
    return out


def scopes(con, ref: str, prefix: str = "") -> list[str]:
    """Party scopes recorded for a matter, e.g. ['provider:1', 'provider:2']."""
    rows = con.execute(
        "SELECT DISTINCT scope FROM matter_values WHERE matter_id=? AND scope <> '' "
        "ORDER BY scope",
        (matter_id(con, ref),),
    )
    found = [r["scope"] for r in rows]
    return [s for s in found if s.startswith(prefix)] if prefix else found


def next_scope(con, ref: str, role: str) -> str:
    """The next free party slot, e.g. 'provider:3'. Creates the matter if new."""
    ensure_matter(con, ref)
    used = scopes(con, ref, f"{role}:")
    numbers = [int(s.split(":", 1)[1]) for s in used if s.split(":", 1)[1].isdigit()]
    return f"{role}:{max(numbers, default=0) + 1}"


def firm_defaults(con) -> dict:
    """Firm-wide values, stored once under the reserved '_firm' matter.

    Letterhead is boilerplate inside a template, but a template that was
    parameterized with --include-constants still needs these, and so does any
    firm that later changes its address.
    """
    try:
        return values(con, "_firm")
    except KeyError:
        return {}


def template_fields(con, names=None) -> dict[str, list[str]]:
    """template name -> the field keys it uses, from the templatize step."""
    rows = con.execute("SELECT name, fields_json FROM templates ORDER BY name").fetchall()
    out = {r["name"]: json.loads(r["fields_json"] or "[]") for r in rows}
    if names:
        wanted = set(names)
        out = {k: v for k, v in out.items() if k in wanted}
    return out


def missing_for(con, ref: str, names=None, scope: str = "", supplied=()) -> dict[str, list[str]]:
    """Which fields each template still needs before it can be generated.

    supplied names fields the generator fills in by itself (today's date, the
    firm's own details), so they are not reported as gaps.
    """
    from .values import derive

    have = {k for k, v in derive(values(con, ref, scope)).items() if str(v).strip()}
    have |= {k for k, v in firm_defaults(con).items() if str(v).strip()}
    have |= set(supplied)
    return {
        name: sorted(k for k in keys if k not in have)
        for name, keys in template_fields(con, names).items()
    }


def intake_blank(keys=None) -> dict:
    """A blank intake sheet: every canonical field, grouped, ready to fill in."""
    wanted = set(keys) if keys else None
    sheet: dict = {}
    for group, group_fields in F.groups().items():
        section = {
            f.key: "" for f in group_fields
            if not f.derived and (wanted is None or f.key in wanted)
        }
        if section:
            sheet[group] = section
    return sheet


def flatten(sheet: dict) -> dict:
    """Accept either a flat {key: value} map or the grouped intake layout."""
    flat: dict = {}
    for key, value in sheet.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def load_file(path) -> dict:
    """Read matter values from .json or a two-column .csv (key,value)."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        return flatten(json.loads(path.read_text(encoding="utf-8")))
    if path.suffix.lower() in (".csv", ".tsv"):
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh, delimiter=delimiter))
        return {r[0].strip(): r[1].strip() for r in rows if len(r) >= 2 and r[0].strip()}
    raise ValueError(f"unsupported intake file type: {path.suffix}")


def party_scope_for(con, ref: str, role: str, name: str) -> str:
    """The scope holding this party, or the next free one if it is new.

    Addressing the same clinic twice updates that provider on the file rather
    than adding a second copy of it.
    """
    from .contacts import slugify

    ensure_matter(con, ref)   # addressing a brand-new matter creates it
    wanted = slugify(name)
    for scope in scopes(con, ref, f"{role}:"):
        row = con.execute(
            "SELECT value FROM matter_values WHERE matter_id=? AND scope=? AND field_key=?",
            (matter_id(con, ref), scope, f"{role}.name"),
        ).fetchone()
        if row and slugify(row["value"]) == wanted:
            return scope
    return next_scope(con, ref, role)


def scoped_keys(con, ref: str, scope: str) -> set[str]:
    """The field keys stored against one party (not inherited from the matter)."""
    rows = con.execute(
        "SELECT field_key FROM matter_values WHERE matter_id=? AND scope=?",
        (matter_id(con, ref), scope),
    )
    return {r["field_key"] for r in rows}
