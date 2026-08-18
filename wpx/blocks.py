"""Read a letter's layout, not just its labels.

Half the data in a letter is positional rather than labelled: the letterhead
at the top is the firm, the block above the "Re:" line is whoever the letter
is addressed to, and the name after "Dear" is a person in that block. A
label-only scanner misses all of it, which is why a scanned letter would
otherwise keep the adjuster's name and the provider's address hard-coded.

This module identifies those blocks and assigns their lines to canonical
fields. It is deliberately conservative: anything it cannot classify is left
alone for the review report rather than guessed at.
"""

from __future__ import annotations

import re

from . import fields as F
from .detect import CSZ_RE, DATE_RE, PHONE_RE, SALUTATION_RE, Hit

STREET_RE = re.compile(
    r"^\s*(?:\d+\s+\S|P\.?\s?O\.?\s+Box|Post Office Box)", re.IGNORECASE
)
RE_LINE_RE = re.compile(r"^\s*RE\s*:", re.IGNORECASE)
GENERIC_SALUTATION_RE = re.compile(r"^\s*To Whom It May Concern", re.IGNORECASE)

INSURER_WORDS = {
    "insurance", "casualty", "mutual", "indemnity", "assurance", "underwriters",
    "claims", "surety", "reinsurance",
}
PROVIDER_WORDS = {
    "medical", "clinic", "hospital", "chiropractic", "health", "orthopedic",
    "orthopaedic", "imaging", "radiology", "therapy", "surgery", "rehabilitation",
    "physicians", "wellness", "spine", "urgent",
}
ORG_WORDS = {
    "llc", "l.l.c.", "inc", "inc.", "company", "co.", "group", "corp",
    "corporation", "pllc", "p.c.", "pc", "associates", "center", "centre",
    "partners", "services", "law", "office", "offices", "firm",
}
_ALL_ORG_WORDS = INSURER_WORDS | PROVIDER_WORDS | ORG_WORDS


def _words(text: str) -> set[str]:
    return {w.strip(".,").casefold() for w in text.split()}


def looks_like_org(text: str) -> bool:
    return bool(_words(text) & _ALL_ORG_WORDS)


def looks_like_person(text: str) -> bool:
    tokens = text.split()
    return (
        1 < len(tokens) <= 4
        and not looks_like_org(text)
        and all(t[:1].isupper() or t[:1] in "&." for t in tokens)
    )


def _is_blank(text: str) -> bool:
    return not text.strip()


def find_date_line(paragraphs):
    """The paragraph that is nothing but a date — the date of the letter."""
    for para in paragraphs[:12]:
        if DATE_RE.fullmatch(para.text.strip()):
            return para
    return None


def find_letterhead(paragraphs) -> range:
    """The firm's own block: everything above the letter's date line."""
    date_line = find_date_line(paragraphs)
    return range(0, date_line.index) if date_line else range(0, 0)


def read_letter_date(paragraphs) -> list[Hit]:
    para = find_date_line(paragraphs)
    if para is None:
        return []
    return [_hit(para, "matter.letter_date", 0.8, "letter_date")]


def find_addressee(paragraphs) -> range:
    """The block naming the recipient: the run of lines above 'Re:'.

    Falls back to the block above the salutation for letters with no Re: line.
    """
    anchor = None
    for para in paragraphs:
        if RE_LINE_RE.match(para.text):
            anchor = para.index
            break
    if anchor is None:
        for para in paragraphs:
            if SALUTATION_RE.match(para.text) or GENERIC_SALUTATION_RE.match(para.text):
                anchor = para.index
                break
    if anchor is None:
        return range(0, 0)

    end = anchor
    while end > 0 and _is_blank(paragraphs[end - 1].text):
        end -= 1
    start = end
    while start > 0 and not _is_blank(paragraphs[start - 1].text):
        start -= 1
    letterhead = find_letterhead(paragraphs)
    if start < letterhead.stop:
        return range(0, 0)  # no distinct addressee block; don't guess
    return range(start, end)


def _hit(para, key: str, confidence: float, detector: str, span=None) -> Hit:
    """A hit covering the paragraph's whole text, or the given (start, end)."""
    if span is not None:
        start, end = span
        return Hit(
            pidx=para.index,
            part=para.part,
            start=start,
            end=end,
            value=para.text[start:end],
            detector=detector,
            confidence=confidence,
            field_key=key,
        )
    stripped = para.text.strip()
    offset = para.text.index(stripped) if stripped else 0
    return Hit(
        pidx=para.index,
        part=para.part,
        start=offset,
        end=offset + len(stripped),
        value=stripped,
        detector=detector,
        confidence=confidence,
        field_key=key,
    )


def read_letterhead(paragraphs) -> list[Hit]:
    """Assign the letterhead lines to firm.* fields."""
    hits: list[Hit] = []
    span = find_letterhead(paragraphs)
    seen_name = False
    for i in span:
        para = paragraphs[i]
        text = para.text.strip()
        if not text:
            continue
        if PHONE_RE.search(text):
            # One letterhead line usually carries both numbers:
            # "Phone: (907) 555-0142   Fax: (907) 555-0143". Take each number
            # as its own span, keyed by the word that introduces it.
            for match in PHONE_RE.finditer(para.text):
                lead = para.text[:match.start()].lower()
                key = "firm.fax" if lead.rfind("fax") > lead.rfind("phone") else "firm.phone"
                hits.append(_hit(para, key, 0.7, "letterhead", span=match.span()))
            continue
        if CSZ_RE.search(text):
            key = "firm.city_state_zip"
        elif STREET_RE.match(text):
            key = "firm.address"
        elif "@" in text:
            key = "firm.email"
        elif re.search(r"attorney|esq\.?\b|counselor", text, re.IGNORECASE):
            # Narrow "R. Cole Halvorsen, Attorney at Law" down to the name.
            name = re.split(r"\s*,\s*(?=attorney|esq)", text, maxsplit=1,
                            flags=re.IGNORECASE)[0]
            offset = para.text.index(name)
            hits.append(_hit(para, "firm.attorney", 0.7, "letterhead",
                             span=(offset, offset + len(name))))
            continue
        elif not seen_name:
            key, seen_name = "firm.name", True
        else:
            continue
        hits.append(_hit(para, key, 0.7, "letterhead"))
    return hits


def read_addressee(paragraphs) -> list[Hit]:
    """Assign the addressee block to insurer.* or provider.* fields.

    Which of the two is decided by the block's own vocabulary — a line
    mentioning Insurance/Casualty/Mutual makes it a carrier letter, one
    mentioning Clinic/Hospital/Medical makes it a records request.
    """
    span = find_addressee(paragraphs)
    lines = [paragraphs[i] for i in span if paragraphs[i].text.strip()]
    if not lines:
        return []

    words = set()
    for para in lines:
        words |= _words(para.text)
    if words & PROVIDER_WORDS:
        prefix = "provider"
    elif words & INSURER_WORDS:
        prefix = "insurer"
    else:
        return []  # unclassifiable recipient: leave it for review

    person_key = "adjuster.name" if prefix == "insurer" else "provider.attn"
    hits: list[Hit] = []
    named_org = False
    for para in lines:
        text = para.text.strip()
        if CSZ_RE.search(text):
            key = f"{prefix}.city_state_zip"
        elif STREET_RE.match(text):
            key = f"{prefix}.address"
        elif re.match(r"^\s*(fax|phone|tel|telephone)\b", text, re.IGNORECASE):
            fax = re.match(r"^\s*fax\b", text, re.IGNORECASE)
            key = f"{prefix}.{'fax' if fax else 'phone'}"
            match = PHONE_RE.search(para.text)
            if key in F.BY_KEY and match:
                hits.append(_hit(para, key, 0.75, "addressee", span=match.span()))
            continue
        elif ":" in text:
            continue  # "Attn:" and similar labels are handled by the label pass
        elif looks_like_org(text) and not named_org:
            key, named_org = f"{prefix}.name", True
        elif looks_like_person(text):
            key = person_key
        else:
            continue
        if key in F.BY_KEY:
            hits.append(_hit(para, key, 0.75, "addressee"))
    return hits


def read_blocks(paragraphs) -> list[Hit]:
    return read_letterhead(paragraphs) + read_letter_date(paragraphs) + read_addressee(paragraphs)
