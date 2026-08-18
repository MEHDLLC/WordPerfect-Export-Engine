"""Stage 4 — turn a converted letter into a reusable template.

The output is the same .docx, with the same letterhead, tabs, tables and
margins WordPerfect produced, except that the values which change from matter
to matter now read {{client.name}}, {{insurer.claim_no}} and so on. Nothing is
rebuilt and no styling is recreated, so a template still looks exactly like the
letter the firm has been sending for twenty years.

Every value the templatizer declines to touch is written to a sidecar report
next to the template. That list is the point of the exercise as much as the
template is: it says precisely what a human still has to look at.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field as _dc_field
from pathlib import Path

from . import fields as F
from .catalog import CONSTANT
from .db import now
from .detect import normalize_value
from .docxml import DocxFile, ReplacementError
from .scan import detect_document
from .values import date_style, mask, parse_date

DEFAULT_MIN_CONFIDENCE = 0.6


@dataclass
class TemplateResult:
    name: str
    source: str
    path: str
    replaced: int = 0
    fields_used: Counter = _dc_field(default_factory=Counter)
    left_in_place: list = _dc_field(default_factory=list)

    def report(self) -> dict:
        return {
            "template": self.name,
            "source": self.source,
            "created_at": now(),
            "replaced": self.replaced,
            "fields": [
                {
                    "key": key,
                    "label": F.BY_KEY[key].label if key in F.BY_KEY else key,
                    "count": count,
                }
                for key, count in sorted(self.fields_used.items())
            ],
            "left_in_place": self.left_in_place,
        }


def _placeholder_for(hit) -> str:
    """The placeholder to write for a hit, carrying a date's spelling with it.

    The Re: block writes 01/26/2026 and the body writes January 26, 2026. Both
    come from one stored date; the style suffix is how each keeps its own
    spelling.
    """
    field = F.BY_KEY[hit.field_key]
    if field.kind == F.DATE and parse_date(hit.value):
        return F.placeholder(hit.field_key, date_style(hit.value))
    return F.placeholder(hit.field_key)


def _skip_reason(hit, kinds: dict, min_confidence: float, include_constants: bool):
    """Why this value stays as literal text, or None if it becomes a placeholder."""
    if hit.field_key is None:
        return "unmapped"
    if hit.field_key not in F.BY_KEY:
        return "unknown-field"
    if hit.confidence < min_confidence:
        return "low-confidence"
    if not include_constants and kinds.get(normalize_value(hit.value)) == CONSTANT:
        return "firm-boilerplate"
    return None


def templatize(
    src,
    dest,
    kinds: dict | None = None,
    overrides: dict | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    include_constants: bool = False,
    corpus_keys: dict | None = None,
) -> TemplateResult:
    """Write a template of src at dest and return what was and wasn't replaced."""
    src, dest = Path(src), Path(dest)
    kinds = kinds or {}
    doc = DocxFile(src)
    paragraphs = doc.paragraphs()
    hits = detect_document(paragraphs, overrides)
    if corpus_keys:
        from .scan import apply_catalog

        apply_catalog(hits, corpus_keys)

    result = TemplateResult(name=dest.name, source=str(src.resolve()), path=str(dest))
    skipped = Counter()
    skipped_samples: dict[tuple[str, str], str] = {}
    by_paragraph: dict[int, list] = {}

    for hit in hits:
        reason = _skip_reason(hit, kinds, min_confidence, include_constants)
        if reason:
            key = (reason, normalize_value(hit.value))
            skipped[key] += 1
            skipped_samples.setdefault(key, hit.value)
            continue
        by_paragraph.setdefault(hit.pidx, []).append(hit)

    for pidx, para_hits in by_paragraph.items():
        edits = [(h.start, h.end, _placeholder_for(h)) for h in para_hits]
        try:
            paragraphs[pidx].apply(edits)
        except ReplacementError as exc:
            for hit in para_hits:
                key = ("split-across-markup", normalize_value(hit.value))
                skipped[key] += 1
                skipped_samples.setdefault(key, f"{hit.value}  ({exc})")
            continue
        for hit in para_hits:
            result.fields_used[hit.field_key] += 1
            result.replaced += 1

    result.left_in_place = [
        {"reason": reason, "value": mask(skipped_samples[(reason, norm)]), "count": count}
        for (reason, norm), count in sorted(skipped.items(), key=lambda kv: -kv[1])
    ]
    doc.save(dest)
    dest.with_suffix(".fields.json").write_text(
        json.dumps(result.report(), indent=2), encoding="utf-8"
    )
    return result


def templatize_corpus(
    con,
    src_root,
    out_root,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    include_constants: bool = False,
    echo=print,
) -> list[TemplateResult]:
    """Templatize a whole folder, recording each template in the database."""
    from .catalog import kinds as catalog_kinds, value_keys
    from .scan import iter_docs, load_overrides

    kinds = catalog_kinds(con)
    overrides = load_overrides(con)
    corpus_keys = value_keys(con)
    out_root = Path(out_root)
    results = []
    for path in iter_docs(src_root):
        dest = out_root / path.name
        result = templatize(path, dest, kinds, overrides, min_confidence,
                            include_constants, corpus_keys)
        con.execute(
            "INSERT INTO templates(name, path, source_doc, fields_json, replaced, created_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET path=excluded.path, "
            "source_doc=excluded.source_doc, fields_json=excluded.fields_json, "
            "replaced=excluded.replaced, created_at=excluded.created_at",
            (dest.name, str(dest.resolve()), str(path.resolve()),
             json.dumps(sorted(result.fields_used)), result.replaced, now()),
        )
        results.append(result)
        if echo:
            unresolved = sum(
                item["count"] for item in result.left_in_place
                if item["reason"] in ("unmapped", "low-confidence")
            )
            echo(f"  {dest.name}: {result.replaced} placeholders, "
                 f"{len(result.fields_used)} fields, {unresolved} left for review")
    con.commit()
    return results
