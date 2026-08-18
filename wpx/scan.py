"""Stage 2 — read every converted letter and record what it contains.

Running every detector in one place keeps their interaction explicit: block
structure and labels establish what is known, propagation spreads those values
through the body, and the pattern pass only ever fills gaps neither of the
others claimed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import blocks, detect
from .db import now
from .docxml import DocxFile

DOC_SUFFIXES = (".docx",)


def detect_document(paragraphs, overrides: dict | None = None) -> list[detect.Hit]:
    """Every detector, in trust order, de-overlapped.

    overrides maps a normalized value to a field key a human confirmed (or to
    None for "this is prose, never templatize it"). They are applied before
    propagation, so mapping a value once in the review step spreads it through
    every letter that mentions it.
    """
    found = (
        detect.find_labelled(paragraphs)
        + blocks.read_blocks(paragraphs)
        + detect.find_salutations(paragraphs)
        + detect.find_patterns(paragraphs)
    )
    if overrides:
        found = apply_overrides(found, overrides)
    known = [h for h in found if h.field_key]
    return detect.resolve_overlaps(found + detect.propagate(paragraphs, known))


def apply_overrides(hits, overrides: dict) -> list[detect.Hit]:
    for hit in hits:
        norm = detect.normalize_value(hit.value)
        if norm not in overrides:
            continue
        hit.field_key = overrides[norm]
        hit.confidence = 0.95 if hit.field_key else 0.0
        hit.detector = f"{hit.detector}+override"
    return [h for h in hits if not (h.detector.endswith("+override") and h.field_key is None)]


def apply_catalog(hits, value_keys: dict, confidence: float = 0.7):
    """Name still-unnamed values using what the rest of the corpus called them."""
    for hit in hits:
        if hit.field_key:
            continue
        key = value_keys.get(detect.normalize_value(hit.value))
        if key:
            hit.field_key = key
            hit.confidence = max(hit.confidence, confidence)
            hit.detector = f"{hit.detector}+corpus"
    return hits


def load_overrides(con) -> dict:
    return {
        r["norm_value"]: r["field_key"]
        for r in con.execute("SELECT norm_value, field_key FROM value_overrides")
    }


def scan_file(path, overrides: dict | None = None) -> tuple[DocxFile, list[detect.Hit]]:
    doc = DocxFile(path)
    return doc, detect_document(doc.paragraphs(), overrides)


def sha256_file(path, bufsize: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(bufsize):
            digest.update(chunk)
    return digest.hexdigest()


def iter_docs(root) -> list[Path]:
    root = Path(root)
    if root.is_file():
        return [root]
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in DOC_SUFFIXES and not p.name.startswith("~$")
    )


def scan_corpus(con, root, echo=print) -> dict:
    """Scan a folder of converted .docx files into the docs/hits tables."""
    totals = {"docs": 0, "hits": 0, "named": 0, "failed": 0}
    overrides = load_overrides(con)
    for path in iter_docs(root):
        try:
            doc, hits = scan_file(path, overrides)
        except Exception as exc:  # a corrupt conversion must not stop the batch
            totals["failed"] += 1
            if echo:
                echo(f"  !! {path.name}: {exc!r}")
            continue
        para_count = len(doc.paragraphs())
        flags = ",".join(sorted(doc.features()))
        cur = con.execute(
            "INSERT INTO docs(path, name, sha256, para_count, flags, scanned_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, "
            "para_count=excluded.para_count, flags=excluded.flags, "
            "scanned_at=excluded.scanned_at RETURNING id",
            (str(path.resolve()), path.name, sha256_file(path), para_count, flags, now()),
        )
        doc_id = cur.fetchone()[0]
        con.execute("DELETE FROM hits WHERE doc_id=?", (doc_id,))
        con.executemany(
            "INSERT INTO hits(doc_id, part, pidx, span_start, span_end, value, "
            "norm_value, field_key, detector, confidence, label) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (doc_id, h.part, h.pidx, h.start, h.end, h.value,
                 detect.normalize_value(h.value), h.field_key, h.detector,
                 h.confidence, h.label)
                for h in hits
            ],
        )
        named = sum(1 for h in hits if h.field_key)
        totals["docs"] += 1
        totals["hits"] += len(hits)
        totals["named"] += named
        if echo:
            note = f"  [{flags}]" if flags else ""
            echo(f"  {path.name}: {len(hits)} values, {named} mapped to fields{note}")
    con.commit()
    return totals
