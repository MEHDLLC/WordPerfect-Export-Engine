"""The address book the corpus already contains.

Every carrier and clinic the firm writes to is sitting in the converted
letters, spelled out in the block each letter was addressed by. Mining that
gives a contact list nobody has to type: pick "Summit Mutual" for a matter and
the carrier's name, intake address and city/state/ZIP land in the matter's
fields; pick a clinic and it becomes a provider on the file, ready to be
addressed.

Two rules keep the list honest:

* Only durable details are stored — the fields marked `contact` in the
  dictionary. A claim number or a patient account number is a fact about a
  matter, and copying one into a contact card would put a previous client's
  number on the next client's letter.
* Address details are taken only from a letter that addresses exactly one
  entity of that role. A demand letter listing four clinics in its schedule
  contributes their names, never their addresses, because nothing in that
  table says which address belongs to which clinic.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass

from . import fields as F
from .db import now


@dataclass(frozen=True)
class Role:
    name: str
    prefixes: tuple[str, ...]
    name_key: str
    person_key: str      # the field holding a person at this entity


ROLES: dict[str, Role] = {
    "insurer": Role("insurer", ("insurer.", "adjuster."), "insurer.name", "adjuster.name"),
    "provider": Role("provider", ("provider.",), "provider.name", "provider.attn"),
    "firm": Role("firm", ("firm.",), "firm.name", "firm.attorney"),
}


class NotFound(KeyError):
    """No contact matched."""


class Ambiguous(KeyError):
    """Several contacts matched; the caller must narrow it down."""

    def __init__(self, matches):
        self.matches = matches
        super().__init__(", ".join(m["name"] for m in matches))


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").casefold()).strip()


def value_keys(role: Role) -> list[str]:
    """Durable fields to store for this role, minus the people field."""
    return [f.key for f in F.contact_fields(role.prefixes) if f.key != role.person_key]


# ---------------------------------------------------------------------------
# Building from a scanned corpus
# ---------------------------------------------------------------------------

def build(con, echo=print) -> dict:
    """Derive contacts from the scan. Manually edited contacts are left alone."""
    observations: dict[tuple[str, str], dict] = {}
    for doc_id, hits in _by_document(con):
        for role in ROLES.values():
            for entity in _entities_in(hits, role):
                _merge(observations, role, entity, doc_id)

    con.execute("DELETE FROM contacts WHERE source='corpus'")
    counts = Counter()
    for (role_name, slug), entity in sorted(observations.items()):
        display = entity["names"].most_common(1)[0][0]
        # Corpus contacts were just deleted, so anything still holding this
        # slug was edited by hand and stays as it is.
        existing = con.execute(
            "SELECT id FROM contacts WHERE role=? AND slug=?", (role_name, slug)
        ).fetchone()
        if existing is not None:
            counts["kept-manual"] += 1
            continue
        cur = con.execute(
            "INSERT INTO contacts(role, slug, name, doc_count, source, updated_at) "
            "VALUES (?,?,?,?,'corpus',?)",
            (role_name, slug, display, len(entity["docs"]), now()),
        )
        contact_id = cur.lastrowid
        counts[role_name] += 1
        for field_key, tally in entity["values"].items():
            ranked = tally.most_common()
            value, support = ranked[0]
            con.execute(
                "INSERT INTO contact_values(contact_id, field_key, value, doc_count, variants) "
                "VALUES (?,?,?,?,?)",
                (contact_id, field_key, value, support,
                 json.dumps(ranked[1:]) if len(ranked) > 1 else None),
            )
        for person, support in entity["people"].most_common():
            con.execute(
                "INSERT INTO contact_people(contact_id, field_key, name, doc_count) "
                "VALUES (?,?,?,?)",
                (contact_id, ROLES[role_name].person_key, person, support),
            )
    con.commit()
    if echo:
        for role_name in ROLES:
            if counts[role_name]:
                echo(f"  {counts[role_name]} {role_name} contact(s)")
        if counts["kept-manual"]:
            echo(f"  {counts['kept-manual']} manually edited contact(s) left untouched")
    return dict(counts)


def _by_document(con):
    rows = con.execute(
        "SELECT doc_id, field_key, value, detector FROM hits "
        "WHERE field_key IS NOT NULL ORDER BY doc_id"
    ).fetchall()
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(row["doc_id"], []).append(row)
    return grouped.items()


def _entities_in(hits, role: Role) -> list[dict]:
    """What one document says about entities of this role.

    A letter addressed to a single carrier yields that carrier with its full
    block. A letter naming several providers in a table yields their names
    only — the table says who was treated, not where to write.
    """
    mine = [h for h in hits if h["field_key"].startswith(role.prefixes)]
    if not mine:
        return []
    addressed = [h for h in mine if not h["detector"].startswith("table")]
    tabled = [h for h in mine if h["detector"].startswith("table")]

    names = {h["value"] for h in addressed if h["field_key"] == role.name_key}
    keys = set(value_keys(role))
    entities = []
    if len(names) == 1:
        entities.append({
            "name": next(iter(names)),
            "values": {h["field_key"]: h["value"] for h in addressed
                       if h["field_key"] in keys},
            "people": {h["value"] for h in addressed
                       if h["field_key"] == role.person_key},
        })
    else:
        entities += [{"name": name, "values": {}, "people": set()} for name in names]
    seen = {slugify(e["name"]) for e in entities}
    for hit in tabled:
        if hit["field_key"] == role.name_key and slugify(hit["value"]) not in seen:
            seen.add(slugify(hit["value"]))
            entities.append({"name": hit["value"], "values": {}, "people": set()})
    return entities


def _merge(observations, role: Role, entity: dict, doc_id) -> None:
    slug = slugify(entity["name"])
    if not slug:
        return
    record = observations.setdefault(
        (role.name, slug),
        {"names": Counter(), "docs": set(), "values": {}, "people": Counter()},
    )
    record["names"][entity["name"]] += 1
    record["docs"].add(doc_id)
    for field_key, value in entity["values"].items():
        record["values"].setdefault(field_key, Counter())[value] += 1
    for person in entity["people"]:
        record["people"][person] += 1


# ---------------------------------------------------------------------------
# Looking contacts up and using them
# ---------------------------------------------------------------------------

def listing(con, role: str | None = None, search: str | None = None) -> list[dict]:
    sql = "SELECT * FROM contacts"
    clauses, params = [], []
    if role:
        clauses.append("role = ?")
        params.append(role)
    if search:
        clauses.append("slug LIKE ?")
        params.append(f"%{slugify(search)}%")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY role, name"
    return [dict(r) for r in con.execute(sql, params)]


def find(con, query: str, role: str | None = None) -> dict:
    """Resolve a name the way a person would type it: partial, any case."""
    exact = [c for c in listing(con, role) if c["slug"] == slugify(query)]
    matches = exact or listing(con, role, query)
    if not matches:
        raise NotFound(query)
    if len(matches) > 1:
        raise Ambiguous(matches)
    return matches[0]


def values_for(con, contact_id: int) -> dict:
    return {
        r["field_key"]: r["value"]
        for r in con.execute(
            "SELECT field_key, value FROM contact_values WHERE contact_id=?", (contact_id,)
        )
        if r["value"]
    }


def variants_for(con, contact_id: int) -> dict:
    out = {}
    for row in con.execute(
        "SELECT field_key, variants FROM contact_values WHERE contact_id=?", (contact_id,)
    ):
        if row["variants"]:
            out[row["field_key"]] = json.loads(row["variants"])
    return out


def people_for(con, contact_id: int) -> list[dict]:
    return [
        dict(r) for r in con.execute(
            "SELECT field_key, name, doc_count FROM contact_people WHERE contact_id=? "
            "ORDER BY doc_count DESC, name",
            (contact_id,),
        )
    ]


def address_block(con, contact: dict, person: str | None = None) -> dict:
    """Everything needed to address a letter to this entity.

    person picks one of the people on file — an adjuster at the carrier, a
    records custodian at the clinic — defaulting to the one seen most often.
    """
    role = ROLES[contact["role"]]
    values = values_for(con, contact["id"])
    values[role.name_key] = contact["name"]
    if person:
        values[role.person_key] = person
    else:
        known = people_for(con, contact["id"])
        if known:
            values[role.person_key] = known[0]["name"]
    return values


def upsert(con, role: str, name: str, values: dict, people=()) -> int:
    """Add or edit a contact by hand. Marks it manual so a rebuild keeps it."""
    if role not in ROLES:
        raise KeyError(f"unknown contact role: {role}")
    spec = ROLES[role]
    con.execute(
        "INSERT INTO contacts(role, slug, name, doc_count, source, updated_at) "
        "VALUES (?,?,?,0,'manual',?) ON CONFLICT(role, slug) DO UPDATE SET "
        "name=excluded.name, source='manual', updated_at=excluded.updated_at",
        (role, slugify(name), name, now()),
    )
    contact_id = con.execute(
        "SELECT id FROM contacts WHERE role=? AND slug=?", (role, slugify(name))
    ).fetchone()[0]
    allowed = set(value_keys(spec))
    for field_key, value in values.items():
        if field_key not in allowed:
            continue
        con.execute(
            "INSERT INTO contact_values(contact_id, field_key, value, doc_count) "
            "VALUES (?,?,?,0) ON CONFLICT(contact_id, field_key) DO UPDATE SET "
            "value=excluded.value",
            (contact_id, field_key, value),
        )
    for person in people:
        con.execute(
            "INSERT INTO contact_people(contact_id, field_key, name, doc_count) "
            "VALUES (?,?,?,0) ON CONFLICT(contact_id, field_key, name) DO NOTHING",
            (contact_id, spec.person_key, person),
        )
    con.commit()
    return contact_id


def rejected_keys(role: str, values: dict) -> list[str]:
    """Keys upsert would ignore — matter facts, or fields of another role."""
    allowed = set(value_keys(ROLES[role]))
    return sorted(k for k in values if k not in allowed)
