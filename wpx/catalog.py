"""Stage 3 — decide which values are the firm's, and which are the matter's.

The scan says "this letter contains 'NORTHSTAR INJURY LAW, LLC' and
'AK-4471-88203'". Only looking across the whole corpus tells you that the
first is letterhead that must survive templatizing untouched, while the second
changes with every file and has to become a placeholder.

The rule is deliberately dull and inspectable, and the dictionary outranks the
evidence:

  * a named field marked firm boilerplate is constant;
  * any other named field is a per-matter variable, however often it recurs —
    a firm that scans one client's folder would otherwise see that client's
    claim number in 100% of the documents and conclude it was letterhead;
  * a value no detector could name is called boilerplate only if it turns up
    in nearly every document of a corpus big enough for that to mean something.
    That last case is what keeps a paralegal's name or a standing fax notice
    out of the review queue without anyone having to declare it.
"""

from __future__ import annotations

from collections import Counter

from . import fields as F
from .db import now

CONSTANT = "constant"
VARIABLE = "variable"
UNKNOWN = "unknown"

# A value must show up in at least this share of a corpus this large before
# repetition alone is taken as evidence of boilerplate.
CONSTANT_SHARE = 0.8
MIN_CORPUS_FOR_SHARE = 3


def build_catalog(con, constant_share: float = CONSTANT_SHARE) -> dict:
    """Roll every hit up by value and classify it. Returns a count summary."""
    total_docs = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    rows = con.execute(
        "SELECT norm_value, value, field_key, doc_id FROM hits WHERE norm_value <> ''"
    ).fetchall()

    by_value: dict[str, dict] = {}
    for row in rows:
        entry = by_value.setdefault(
            row["norm_value"],
            {"sample": row["value"], "docs": set(), "hits": 0, "keys": Counter()},
        )
        entry["docs"].add(row["doc_id"])
        entry["hits"] += 1
        if row["field_key"]:
            entry["keys"][row["field_key"]] += 1

    summary = Counter()
    payload = []
    for norm_value, entry in by_value.items():
        key = entry["keys"].most_common(1)[0][0] if entry["keys"] else None
        doc_count = len(entry["docs"])
        kind = classify(key, doc_count, total_docs, constant_share)
        summary[kind] += 1
        payload.append(
            (norm_value, entry["sample"], key, doc_count, entry["hits"], kind, now())
        )

    con.execute("DELETE FROM catalog")
    con.executemany(
        "INSERT INTO catalog(norm_value, sample, field_key, doc_count, hit_count, "
        "kind, decided_at) VALUES (?,?,?,?,?,?,?)",
        payload,
    )
    con.commit()
    summary["values"] = len(payload)
    summary["docs"] = total_docs
    return dict(summary)


def classify(field_key, doc_count: int, total_docs: int, share: float = CONSTANT_SHARE) -> str:
    if field_key is not None:
        field = F.BY_KEY.get(field_key)
        if field is not None and not field.always_variable:
            return CONSTANT
        return VARIABLE
    if total_docs >= MIN_CORPUS_FOR_SHARE and doc_count >= max(2, total_docs * share):
        return CONSTANT  # unnamed, but in every letter: boilerplate
    return UNKNOWN


def kinds(con) -> dict[str, str]:
    """norm_value -> kind, for the templatizer to consult."""
    return {r["norm_value"]: r["kind"] for r in con.execute("SELECT norm_value, kind FROM catalog")}


def value_keys(con) -> dict[str, str]:
    """norm_value -> field key, for values the corpus as a whole has named.

    A date labelled "Date of Loss:" in the demand letter is the same date that
    appears bare in the records request. Carrying that across documents is
    what stops the second letter from keeping it hard-coded.
    """
    return {
        r["norm_value"]: r["field_key"]
        for r in con.execute(
            "SELECT norm_value, field_key FROM catalog WHERE field_key IS NOT NULL"
        )
    }


def field_summary(con) -> list[dict]:
    """Per canonical field: how many documents use it, and how many values."""
    rows = con.execute(
        "SELECT field_key, COUNT(DISTINCT doc_id) AS docs, "
        "COUNT(DISTINCT norm_value) AS values_seen, COUNT(*) AS hits "
        "FROM hits WHERE field_key IS NOT NULL GROUP BY field_key "
        "ORDER BY docs DESC, hits DESC"
    ).fetchall()
    out = []
    for row in rows:
        field = F.BY_KEY.get(row["field_key"])
        out.append(
            {
                "field_key": row["field_key"],
                "label": field.label if field else row["field_key"],
                "group": field.group if field else "?",
                "docs": row["docs"],
                "values": row["values_seen"],
                "hits": row["hits"],
            }
        )
    return out


def flagged_documents(con) -> list[dict]:
    """Documents carrying markup the text passes step over.

    Tracked changes, field codes and content controls all put visible text
    somewhere this tool does not edit, so a template built from such a
    document has to be opened and checked by a person.
    """
    rows = con.execute(
        "SELECT name, flags FROM docs WHERE COALESCE(flags, '') <> '' ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def unmapped(con, limit: int = 50) -> list[dict]:
    """Values no detector could name — the human review queue.

    Every entry here is a value that would stay hard-coded in a template, so
    this list is the honest measure of how much of the corpus is really
    automated. Two kinds of value are left out because they are not gaps: those
    the roll-up judged to be boilerplate, and those another document in the
    corpus already named — the templatizer resolves both without help.
    """
    rows = con.execute(
        "SELECT h.norm_value, MIN(h.value) AS sample, h.detector, "
        "COUNT(DISTINCT h.doc_id) AS docs, COUNT(*) AS hits, "
        "GROUP_CONCAT(DISTINCT d.name) AS in_docs "
        "FROM hits h JOIN docs d ON d.id = h.doc_id "
        "LEFT JOIN catalog c ON c.norm_value = h.norm_value "
        "WHERE h.field_key IS NULL AND c.field_key IS NULL "
        "AND COALESCE(c.kind, ?) <> ? "
        "GROUP BY h.norm_value, h.detector "
        "ORDER BY docs DESC, hits DESC LIMIT ?",
        (UNKNOWN, CONSTANT, limit),
    ).fetchall()
    return [dict(r) for r in rows]
