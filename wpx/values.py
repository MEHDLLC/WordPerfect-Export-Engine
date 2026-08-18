"""Understanding values well enough to reuse them.

Two problems this solves, both of which otherwise break "enter it once":

Dates. The same date is written "01/26/2026" in the Re: block and
"January 26, 2026" in the body. Treated as raw text those are two unrelated
values, so one intake entry cannot fill both. Here a date is parsed to a real
date, catalogued by that, and re-formatted at generation time in whichever
spelling the original letter used.

Names. A letter says "Dana R. Whitfield" once and "Ms. Whitfield" or "Dana"
thereafter. Substituting the full name into every one of those spots changes
how the letter reads, so first and last names are their own fields — derived
from the full name automatically, never asked for twice at intake.

No third-party date library: the firm's Windows box gets standard library only.
"""

from __future__ import annotations

import re
from datetime import date

SHORT, LONG, ISO = "short", "long", "iso"
DATE_STYLES = (SHORT, LONG, ISO)

_DATE_FORMATS = (
    "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%Y-%m-%d",
    "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
)
_MONTH_NAME_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October"
    r"|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)",
    re.IGNORECASE,
)
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Name tails that are not surnames.
_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v", "esq", "esq.", "md", "m.d.", "phd"}
_HONORIFICS = {"mr", "mr.", "ms", "ms.", "mrs", "mrs.", "dr", "dr.", "miss", "atty", "atty."}

MONEY_RE = re.compile(r"^\$\s?([\d,]+(?:\.\d{1,2})?)$")

# A social security number, in the spellings that appear in medical
# authorizations and PIP applications.
SSN_RE = re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b|\b(?<!\d)\d{9}(?!\d)\b")


def mask(text: str) -> str:
    """Blank out anything that must not appear in a report or a log line.

    Reports get read over shoulders, mailed around and pasted into tickets. A
    social security number that the scanner correctly identified should not
    then leak through the tool's own output.
    """
    return SSN_RE.sub("***-**-####", text or "")


def parse_date(text: str):
    """A date, or None if the text is not one. Tolerates 'Sept.' and stray dots."""
    from datetime import datetime

    cleaned = re.sub(r"\s+", " ", (text or "").strip().rstrip("."))
    cleaned = re.sub(r"(?<=[A-Za-z])\.\s", " ", cleaned)      # "Feb. 3, 2026"
    cleaned = re.sub(r"\bSept\b", "Sep", cleaned, flags=re.IGNORECASE)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def format_date(value: date, style: str = SHORT) -> str:
    if style == LONG:
        return f"{value:%B} {value.day}, {value.year}"
    if style == ISO:
        return value.isoformat()
    return f"{value.month:02d}/{value.day:02d}/{value.year}"


def date_style(text: str) -> str:
    """Which spelling a document used, so a template can preserve it."""
    if _ISO_RE.match(text.strip()):
        return ISO
    return LONG if _MONTH_NAME_RE.search(text) else SHORT


def render_date(text: str, style: str) -> str | None:
    """Re-spell a stored date in the requested style; None if it is not a date."""
    parsed = parse_date(text)
    return format_date(parsed, style) if parsed else None


def canonical(text: str) -> str:
    """One key per real-world value, whatever spelling the document used.

    'January 26, 2026' and '01/26/2026' both become 'date:2026-01-26', so the
    catalog sees one value that two letters spell differently.
    """
    collapsed = re.sub(r"\s+", " ", (text or "")).strip(" \t.,;:")
    parsed = parse_date(collapsed)
    if parsed:
        return f"date:{parsed.isoformat()}"
    money = MONEY_RE.match(collapsed)
    if money:
        amount = money.group(1).replace(",", "")
        return f"money:{float(amount):.2f}"
    return collapsed.casefold()


def split_name(full: str) -> dict:
    """'Dana R. Whitfield, Jr.' -> first Dana, last Whitfield.

    Honorifics and suffixes are stripped rather than mistaken for names, and a
    single-token name is treated as a last name only if nothing else fits.
    """
    tokens = [t for t in re.split(r"[\s,]+", (full or "").strip()) if t]
    tokens = [t for t in tokens if t.casefold() not in _HONORIFICS]
    while tokens and tokens[-1].casefold() in _SUFFIXES:
        tokens.pop()
    if not tokens:
        return {}
    if len(tokens) == 1:
        return {"first_name": tokens[0], "last_name": tokens[0]}
    return {"first_name": tokens[0], "last_name": tokens[-1]}


def derive(values: dict) -> dict:
    """Fill the parts of a name that follow from the whole one.

    Storing client.name is enough; client.first_name and client.last_name
    appear without anyone typing them, and an explicitly stored value always
    wins (some clients go by a name their full name does not contain).
    """
    from . import fields as F

    people = F.person_prefixes()
    out = dict(values)
    for key, full in list(values.items()):
        if not key.endswith(".name") or not str(full).strip():
            continue
        prefix = key[: -len(".name")]
        if prefix not in people:
            continue  # an organization: "Valley Regional Medical Center" has no surname
        for part, derived_value in split_name(str(full)).items():
            target = f"{prefix}.{part}"
            if not str(out.get(target, "")).strip():
                out[target] = derived_value
    return out
