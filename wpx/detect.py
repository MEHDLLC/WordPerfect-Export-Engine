"""Find, inside a converted letter, the spans of text that are really data.

Four passes, in order of how much they can be trusted:

  labelled    "Claim No.: 12-3456-789"  -> the label names the field outright.
  salutation  "Dear Ms. Jones:"         -> a person, field decided by context.
  propagated  the client's name found in the Re: block, then matched everywhere
              else it appears in the same letter — this is what makes a whole
              document templatize instead of just its header block.
  pattern     a date, a dollar figure, a phone number with nothing naming it.
              These are candidates for a human to confirm, not automatic edits.

Nothing here edits a document; passes return Hit records that templatize.py
acts on and the review report lists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from . import fields as F
from .values import SSN_RE, canonical, split_name

MONEY_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?\b|\$\s?\d+(?:\.\d{2})?\b")
DATE_RE = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
    r"|\b(?:January|February|March|April|May|June|July|August|September|October"
    r"|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+"
    r"\d{1,2},?\s+\d{4}\b",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"\(?\b\d{3}\)?[-. ]\s?\d{3}[-. ]\d{4}\b")
CSZ_RE = re.compile(r"\b[A-Z][A-Za-z.\-' ]{2,30},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")
SALUTATION_RE = re.compile(r"^\s*Dear\s+(?P<name>[^,:]{2,60}?)\s*[:,]", re.IGNORECASE)

# "Label:" anchors. The label is deliberately short and punctuation-poor so
# ordinary prose containing a colon rarely qualifies, and an unknown label is
# dropped anyway when it fails to resolve to a canonical field.
LABEL_RE = re.compile(r"(?P<label>[A-Za-z][A-Za-z0-9 .'&/#\-]{0,40}?)\s*:[ \t]*")

# Trailing junk between a value and the label that follows it on the same line.
_VALUE_TRIM = " \t /;,|-–—"

# Surnames alone are propagated only when they are distinctive enough to be a
# name rather than an ordinary word that happens to end a person's name.
_COMMON_WORDS = {
    "inc", "llc", "company", "insurance", "hospital", "clinic", "center",
    "medical", "regional", "family", "group", "law", "office", "offices",
    "associates", "health", "care", "services", "farm", "state", "mutual",
}

MAX_VALUE_LEN = 90


@dataclass
class Hit:
    """One span of one paragraph that carries a value."""

    pidx: int              # paragraph position within the document
    part: str
    start: int
    end: int
    value: str
    detector: str
    confidence: float
    field_key: str | None = None
    label: str = ""

    def as_row(self) -> dict:
        return asdict(self)

    def overlaps(self, other: "Hit") -> bool:
        return self.pidx == other.pidx and self.start < other.end and other.start < self.end


def normalize_value(value: str) -> str:
    """One key per real-world value — see wpx/values.canonical."""
    return canonical(value)


def _clean(value: str) -> str:
    return value.strip(_VALUE_TRIM)


def find_labelled(paragraphs) -> list[Hit]:
    """Pass 1 — 'Date of Loss: 01/26/2026' and friends, one line at a time."""
    hits: list[Hit] = []
    for para in paragraphs:
        text = para.text
        line_start = 0
        for line in text.split("\n"):
            anchors = list(LABEL_RE.finditer(line))
            for i, anchor in enumerate(anchors):
                key = F.lookup_label(anchor.group("label"))
                if key is None:
                    continue
                value_start = anchor.end()
                value_end = anchors[i + 1].start() if i + 1 < len(anchors) else len(line)
                raw = line[value_start:value_end]
                stripped = _clean(raw)
                if not stripped or len(stripped) > MAX_VALUE_LEN:
                    continue
                offset = value_start + (len(raw) - len(raw.lstrip(_VALUE_TRIM)))
                hits.append(
                    Hit(
                        pidx=para.index,
                        part=para.part,
                        start=line_start + offset,
                        end=line_start + offset + len(stripped),
                        value=stripped,
                        detector="labelled",
                        confidence=0.95,
                        field_key=key,
                        label=anchor.group("label").strip(),
                    )
                )
            line_start += len(line) + 1  # the split consumed one "\n"
    return hits


def find_salutations(paragraphs) -> list[Hit]:
    """Pass 2 — the addressee of the letter, whoever that turns out to be."""
    hits: list[Hit] = []
    for para in paragraphs:
        match = SALUTATION_RE.match(para.text)
        if not match:
            continue
        hits.append(
            Hit(
                pidx=para.index,
                part=para.part,
                start=match.start("name"),
                end=match.end("name"),
                value=match.group("name").strip(),
                detector="salutation",
                confidence=0.55,
                field_key=None,
                label="Dear",
            )
        )
    return hits


def _name_variants(value: str, key: str) -> list[tuple[str, str, float]]:
    """Other spellings of a name worth searching the body for.

    A letter introduces "Dana R. Whitfield" once and then says "Ms. Whitfield"
    or just "Dana" for the rest of the page. Each variant is returned with the
    field it should map to — a bare first name belongs in client.first_name,
    not client.name, so the generated letter reads correctly.
    """
    out: list[tuple[str, str, float]] = [(value, key, 0.85)]
    prefix = key.rsplit(".", 1)[0]
    parts = split_name(value)
    if not parts or prefix not in F.person_prefixes():
        return out
    # Only the surname itself is claimed, never the honorific in front of it:
    # "Ms. Whitfield" becomes "Ms. {{client.last_name}}" and still reads as the
    # firm wrote it, where {{client.name}} would expand to the full name.
    for part, field_suffix, confidence in (
        ("last_name", "last_name", 0.65),
        ("first_name", "first_name", 0.6),
    ):
        token = parts.get(part, "")
        target = f"{prefix}.{field_suffix}"
        if (
            target in F.BY_KEY
            and len(token) >= 3
            and token.casefold() not in _COMMON_WORDS
            and token.casefold() != value.casefold()
        ):
            out.append((token, target, confidence))
    return out


def propagate(paragraphs, known: list[Hit]) -> list[Hit]:
    """Pass 3 — carry every resolved value through the rest of the letter.

    A demand letter names the client in the Re: block once and then twenty more
    times in the body. Only this pass makes those twenty replaceable.
    """
    searches: dict[str, tuple[str, float]] = {}
    for hit in known:
        if not hit.field_key or hit.detector == "propagated":
            continue
        spelled = (
            _name_variants(hit.value, hit.field_key)
            if F.BY_KEY[hit.field_key].kind == F.NAME
            else [(hit.value, hit.field_key, 0.85)]
        )
        for text, key, confidence in spelled:
            existing = searches.get(text.casefold())
            if existing is None or confidence > existing[1]:
                searches[text.casefold()] = (key, confidence)

    hits: list[Hit] = []
    for text, (key, confidence) in searches.items():
        if len(text) < 3:
            continue
        pattern = re.compile(
            r"(?<![A-Za-z0-9])" + r"\s+".join(re.escape(t) for t in text.split()) + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        for para in paragraphs:
            for match in pattern.finditer(para.text):
                hits.append(
                    Hit(
                        pidx=para.index,
                        part=para.part,
                        start=match.start(),
                        end=match.end(),
                        value=match.group(0),
                        detector="propagated",
                        confidence=confidence,
                        field_key=key,
                    )
                )
    return hits


# name, pattern, the field it proves (None = candidate for review only)
_PATTERNS = (
    # An SSN-shaped string is an SSN. Naming it outright matters: an
    # unrecognized one would be copied verbatim into a template and then sent
    # out on every future client's letter.
    ("ssn", SSN_RE, "client.ssn", 0.9),
    ("money", MONEY_RE, None, 0.3),
    ("date", DATE_RE, None, 0.3),
    ("phone", PHONE_RE, None, 0.3),
    ("address", CSZ_RE, None, 0.3),
)


def find_patterns(paragraphs) -> list[Hit]:
    """Pass 4 — typed values, mostly with nothing naming them. Review fodder."""
    hits: list[Hit] = []
    for para in paragraphs:
        for name, pattern, field_key, confidence in _PATTERNS:
            for match in pattern.finditer(para.text):
                hits.append(
                    Hit(
                        pidx=para.index,
                        part=para.part,
                        start=match.start(),
                        end=match.end(),
                        value=match.group(0).strip(),
                        detector=f"pattern:{name}",
                        confidence=confidence,
                        field_key=field_key,
                    )
                )
    return hits


def resolve_overlaps(hits: list[Hit]) -> list[Hit]:
    """Keep the best hit per span: named beats unnamed, then longer, then surer."""
    def rank(hit: Hit):
        return (hit.field_key is not None, hit.confidence, hit.end - hit.start)

    kept: list[Hit] = []
    for hit in sorted(hits, key=rank, reverse=True):
        if any(hit.overlaps(other) for other in kept):
            continue
        kept.append(hit)
    return sorted(kept, key=lambda h: (h.pidx, h.start))
