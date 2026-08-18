"""The firm's canonical field dictionary.

This is the heart of "enter it once": every letter in the corpus refers to the
same handful of facts — who the client is, which carrier, which claim number,
which date of loss — under a dozen different labels. Each fact gets exactly one
key here, every label the firm has ever used for it is listed as an alias, and
templates only ever reference the key.

Adding a field is a one-line change; the scanner, the templatizer and the
intake form all read this table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as _dc_field

# Value shapes, used to sanity-check what a label captured and to format input.
TEXT, NAME, DATE, MONEY, PHONE, NUMBER, ADDRESS = (
    "text", "name", "date", "money", "phone", "number", "address",
)


@dataclass(frozen=True)
class Field:
    key: str
    label: str          # human label shown on the intake form
    group: str          # intake form section
    kind: str = TEXT
    aliases: tuple[str, ...] = ()   # labels seen in the documents themselves
    always_variable: bool = True    # False = usually firm boilerplate
    derived: bool = False           # computed from another field, never asked for
    note: str = ""

    def matches_label(self, label: str) -> bool:
        norm = normalize_label(label)
        return norm in {normalize_label(a) for a in (self.label, *self.aliases)}


def normalize_label(label: str) -> str:
    """'Our File No.' -> 'our file no'  — punctuation and spacing collapsed."""
    label = label.replace("#", " no ").replace("&", " and ")
    label = re.sub(r"[^a-z0-9]+", " ", label.lower())
    return re.sub(r"\s+", " ", label).strip()


FIELDS: tuple[Field, ...] = (
    # --- the firm itself: same on every letter, so not a per-matter variable --
    Field("firm.name", "Firm name", "firm", TEXT, always_variable=False),
    Field("firm.attorney", "Attorney", "firm", NAME,
          aliases=("attorney", "attorney at law"), always_variable=False),
    Field("firm.address", "Firm street address", "firm", ADDRESS, always_variable=False),
    Field("firm.city_state_zip", "Firm city, state ZIP", "firm", ADDRESS, always_variable=False),
    Field("firm.phone", "Firm phone", "firm", PHONE, always_variable=False),
    Field("firm.fax", "Firm fax", "firm", PHONE, always_variable=False),
    Field("firm.email", "Firm email", "firm", TEXT, always_variable=False),

    # --- the client -------------------------------------------------------
    Field("client.name", "Client name", "client", NAME,
          aliases=("our client", "client", "claimant", "patient", "patient name",
                   "our insured", "insured", "re")),
    # Derived from client.name, so a letter that says "Dana" or "Ms. Whitfield"
    # keeps saying that instead of expanding to the full name.
    Field("client.first_name", "Client first name", "client", NAME, derived=True),
    Field("client.last_name", "Client last name", "client", NAME, derived=True),
    Field("client.address", "Client street address", "client", ADDRESS),
    Field("client.city_state_zip", "Client city, state ZIP", "client", ADDRESS),
    Field("client.dob", "Client date of birth", "client", DATE,
          aliases=("date of birth", "dob", "d o b", "birth date")),
    Field("client.phone", "Client phone", "client", PHONE),
    Field("client.ssn", "Client SSN", "client", TEXT,
          aliases=("ssn", "social security no", "social security number", "ss no"),
          note="Store last four only unless the release requires the full number."),

    # --- the matter -------------------------------------------------------
    Field("matter.file_no", "Our file number", "matter", NUMBER,
          aliases=("our file", "our file no", "file no", "file number", "our ref",
                   "our reference")),
    Field("matter.date_of_loss", "Date of loss", "matter", DATE,
          aliases=("date of loss", "dol", "d o l", "date of accident", "doa",
                   "date of incident", "accident date", "loss date")),
    Field("matter.letter_date", "Letter date", "matter", DATE,
          aliases=("date",), note="Usually today's date at generation time."),
    Field("matter.dates_of_service", "Dates of service", "matter", TEXT,
          aliases=("date of service", "dates of service", "dos")),
    Field("matter.incident_location", "Where the loss occurred", "matter", TEXT,
          aliases=("location", "location of loss", "place of accident")),

    # --- the carrier and its people --------------------------------------
    Field("insurer.name", "Insurance carrier", "insurer", NAME,
          aliases=("insurance company", "carrier", "insurer", "insurance carrier",
                   "company")),
    Field("insurer.claim_no", "Claim number", "insurer", NUMBER,
          aliases=("claim no", "claim number", "claim", "your claim no",
                   "claim no s", "your claim number", "claim id")),
    Field("insurer.policy_no", "Policy number", "insurer", NUMBER,
          aliases=("policy no", "policy number", "policy")),
    Field("insurer.address", "Carrier street address", "insurer", ADDRESS),
    Field("insurer.city_state_zip", "Carrier city, state ZIP", "insurer", ADDRESS),
    Field("adjuster.name", "Adjuster", "insurer", NAME,
          aliases=("adjuster", "claims adjuster", "claim representative",
                   "claim rep", "claims representative", "adjustor")),
    Field("adjuster.first_name", "Adjuster first name", "insurer", NAME, derived=True),
    Field("adjuster.last_name", "Adjuster last name", "insurer", NAME, derived=True),
    Field("insured.name", "At-fault party / your insured", "insurer", NAME,
          aliases=("your insured", "at fault driver", "at fault party",
                   "tortfeasor", "defendant driver")),
    Field("insured.first_name", "At-fault party first name", "insurer", NAME, derived=True),
    Field("insured.last_name", "At-fault party last name", "insurer", NAME, derived=True),

    # --- medical providers (records and billing requests) -----------------
    Field("provider.name", "Provider name", "provider", NAME,
          aliases=("provider", "facility", "clinic", "hospital", "medical provider")),
    Field("provider.attn", "Provider attention line", "provider", TEXT,
          aliases=("attn", "attention", "records custodian", "medical records",
                   "custodian of records")),
    Field("provider.address", "Provider street address", "provider", ADDRESS),
    Field("provider.city_state_zip", "Provider city, state ZIP", "provider", ADDRESS),
    Field("provider.fax", "Provider fax", "provider", PHONE),
    Field("provider.account_no", "Patient account number", "provider", NUMBER,
          aliases=("account no", "acct no", "patient account no",
                   "patient account number", "mrn", "medical record no")),

    # --- money ------------------------------------------------------------
    Field("demand.amount", "Demand amount", "demand", MONEY,
          aliases=("demand", "demand amount", "settlement demand", "amount demanded")),
    Field("demand.medicals_total", "Total medical specials", "demand", MONEY,
          aliases=("total medicals", "medical specials", "total medical bills",
                   "medical bills", "specials")),
    Field("demand.lost_wages", "Lost wages", "demand", MONEY,
          aliases=("lost wages", "wage loss", "lost income")),
    Field("demand.response_deadline", "Response deadline", "demand", DATE,
          aliases=("response deadline", "reply by", "respond by")),
)

BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}

# alias -> key, built once. Longest aliases win so "date of loss" is not
# swallowed by the bare "date" alias on matter.letter_date.
_ALIAS_TO_KEY: dict[str, str] = {}
for _f in FIELDS:
    for _a in (_f.label, *_f.aliases):
        _ALIAS_TO_KEY.setdefault(normalize_label(_a), _f.key)


def lookup_label(label: str) -> str | None:
    """Map a label found in a document to a canonical field key."""
    return _ALIAS_TO_KEY.get(normalize_label(label))


def groups() -> dict[str, list[Field]]:
    out: dict[str, list[Field]] = {}
    for f in FIELDS:
        out.setdefault(f.group, []).append(f)
    return out


def placeholder(key: str, style: str = "") -> str:
    """{{client.name}}, or {{matter.date_of_loss:long}} for a date spelling."""
    return "{{" + key + (f":{style}" if style else "") + "}}"


# The optional ":style" suffix records how a date was written in the original
# letter, so a template can reproduce that spelling from one stored value.
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)(?::([a-z]+))?\s*\}\}")


def person_prefixes() -> set[str]:
    """Field prefixes that name a person, i.e. where a surname makes sense."""
    return {k[: -len(".last_name")] for k in BY_KEY if k.endswith(".last_name")}
