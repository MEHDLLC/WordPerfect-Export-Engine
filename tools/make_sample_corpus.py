#!/usr/bin/env python3
"""Generate a synthetic corpus of converted letters for testing and demos.

The firm's real letters contain client names, claim numbers and medical
information, none of which belongs in a git repository. These stand in for
them: same shapes (letterhead, Re: block, salutation, body, signature), same
label vocabulary, entirely invented people, carriers and providers.

    python3 tools/make_sample_corpus.py samples/Converted
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wpx.minidocx import Table, build  # noqa: E402
from wpx.values import LONG, format_date, parse_date  # noqa: E402


def long_date(short: str) -> str:
    """'01/26/2026' -> 'January 26, 2026' — real letters mix both spellings."""
    return format_date(parse_date(short), LONG)

FIRM = [
    "NORTHSTAR INJURY LAW, LLC",
    "R. Cole Halvorsen, Attorney at Law",
    "412 W. Denali Avenue, Suite 300",
    "Anchorage, AK 99501",
    "Phone: (907) 555-0142   Fax: (907) 555-0143",
    "",
]

SIGNATURE = [
    "",
    "Sincerely,",
    "",
    "",
    "R. Cole Halvorsen",
    "NORTHSTAR INJURY LAW, LLC",
]

# Two matters, so a corpus scan can tell a per-matter value from firm
# boilerplate that never changes.
MATTER_A = dict(
    client="Dana R. Whitfield",
    client_addr="1187 Chugach Loop",
    client_csz="Wasilla, AK 99654",
    dob="04/17/1988",
    dol="01/26/2026",
    file_no="2026-0114",
    carrier="Summit Mutual Insurance Company",
    claim_no="AK-4471-88203",
    policy_no="SM 7741 220 B",
    adjuster="Marcy Lindholm",
    insured="Trent Baugher",
    carrier_addr="P.O. Box 21188",
    carrier_csz="Bloomington, IL 61710",
)
MATTER_B = dict(
    client="Owen T. Marchetti",
    client_addr="3320 Turnagain Street",
    client_csz="Anchorage, AK 99517",
    dob="11/02/1976",
    dol="03/06/2026",
    file_no="2026-0209",
    carrier="Cascade Casualty Group",
    claim_no="CC-9930-41772",
    policy_no="CC 4410 771 A",
    adjuster="Devon Prater",
    insured="Alicia Reyes",
    carrier_addr="900 Fourth Avenue, Suite 1400",
    carrier_csz="Seattle, WA 98164",
)


def re_block(m, extra=()):
    return [
        f"Re:  Our Client:  {m['client']}",
        f"     Your Insured:  {m['insured']}",
        f"     Claim No.:  {m['claim_no']}",
        f"     Policy No.:  {m['policy_no']}",
        f"     Date of Loss:  {m['dol']}",
        f"     Our File No.:  {m['file_no']}",
        *extra,
        "",
    ]


def carrier_block(m, letter_date):
    return [
        letter_date,
        "",
        m["adjuster"],
        m["carrier"],
        m["carrier_addr"],
        m["carrier_csz"],
        "",
    ]


def lor(m, letter_date):
    return [
        *FIRM,
        *carrier_block(m, letter_date),
        *re_block(m),
        f"Dear {m['adjuster']}:",
        "",
        # Split mid-name across runs, the way WordPerfect's exporter does.
        ["Please be advised that this office represents ", m["client"].split()[0] + " ",
         " ".join(m["client"].split()[1:]), " for injuries sustained in the above-referenced"],
        f"collision of {m['dol']}. All further communication regarding this claim should be",
        "directed to this office rather than to our client.",
        "",
        f"Please confirm coverage and policy limits under policy {m['policy_no']} and forward a",
        f"copy of the declarations page. {m['client'].split()[0]} has not given, and will not give, a recorded",
        "statement without counsel present.",
        "",
        f"Our client was treated the same week as the collision of {long_date(m['dol'])},",
        f"and Mr. {m['client'].split()[-1]} will forward the records as they arrive.",
        *SIGNATURE,
    ]


def demand(m, letter_date, medicals, wages, amount, deadline, bills):
    first = m["client"].split()[0]
    return [
        *FIRM,
        *carrier_block(m, letter_date),
        *re_block(m),
        f"Dear {m['adjuster']}:",
        "",
        f"This letter constitutes a settlement demand on behalf of {m['client']} arising",
        f"from the collision of {m['dol']}, in which your insured, {m['insured']}, failed to yield",
        "and struck our client's vehicle from behind.",
        "",
        "MEDICAL SPECIALS",
        # A schedule of bills: one row per provider, which is what
        # {{#each provider}} exists for once this becomes a template.
        Table([["Provider", "Dates of Service", "Amount Billed"], *bills]),
        "",
        "SPECIAL DAMAGES",
        f"Total Medicals:  {medicals}",
        f"Lost Wages:  {wages}",
        "",
        f"Demand:  {amount}",
        f"Response Deadline:  {deadline}",
        "",
        [f"We look forward to your response. {first} ", "is prepared to resolve this matter without",
         " litigation if the above demand is met."],
        *SIGNATURE,
    ]


def med_pay(m, letter_date):
    return [
        *FIRM,
        *carrier_block(m, letter_date),
        *re_block(m),
        f"Dear {m['adjuster']}:",
        "",
        f"Please open a medical payments claim for {m['client']} under policy {m['policy_no']}.",
        f"Date of Birth:  {m['dob']}",
        "Enclosed are the billing statements submitted to date. Please remit payment",
        "directly to the providers listed and copy this office on each payment.",
        *SIGNATURE,
    ]


def records_request(m, provider, letter_date, account_no):
    return [
        *FIRM,
        [letter_date],
        "",
        provider["name"],
        f"Attn:  {provider['attn']}",
        provider["addr"],
        provider["csz"],
        f"Fax:  {provider['fax']}",
        "",
        f"Re:  Patient:  {m['client']}",
        f"     Date of Birth:  {m['dob']}",
        f"     Account No.:  {account_no}",
        f"     Dates of Service:  {m['dol']} to present",
        "",
        "To Whom It May Concern:",
        "",
        f"This office represents {m['client']} in connection with injuries sustained on",
        f"{m['dol']}. Enclosed is a signed authorization. Please provide complete medical",
        "records and itemized billing for the dates of service listed above.",
        "",
        "Please direct any questions regarding this request to our office.",
        *SIGNATURE,
    ]


VALLEY = dict(name="Valley Regional Medical Center", attn="Custodian of Records",
              addr="2500 Palmer-Wasilla Highway", csz="Palmer, AK 99645", fax="(907) 555-0188")
LARKSPUR = dict(name="Larkspur Chiropractic Clinic", attn="Medical Records",
                addr="781 Bogard Road, Suite B", csz="Wasilla, AK 99654", fax="(907) 555-0170")

DOCS = {
    "L LOR Summit Mutual.docx": lambda: lor(MATTER_A, "February 3, 2026"),
    "L Demand Summit Mutual.docx": lambda: demand(
        MATTER_A, "June 12, 2026", "$18,412.60", "$4,850.00", "$95,000.00", "July 15, 2026",
        [["Valley Regional Medical Center", "01/26/2026 - 03/14/2026", "$12,480.00"],
         ["Larkspur Chiropractic Clinic", "02/02/2026 - 05/20/2026", "$5,932.60"]]),
    "L Summit Mutual med pay.docx": lambda: med_pay(MATTER_A, "February 10, 2026"),
    "ML Valley Regional.docx": lambda: records_request(
        MATTER_A, VALLEY, "February 10, 2026", "VR-882014"),
    "ML Larkspur Chiropractic.docx": lambda: records_request(
        MATTER_A, LARKSPUR, "February 10, 2026", "LC-40127"),
    "L LOR Cascade Casualty.docx": lambda: lor(MATTER_B, "March 18, 2026"),
    "L Demand Cascade Casualty.docx": lambda: demand(
        MATTER_B, "August 4, 2026", "$26,905.14", "$11,200.00", "$140,000.00",
        "September 5, 2026",
        [["Valley Regional Medical Center", "03/06/2026 - 04/30/2026", "$19,704.14"],
         ["Summit Physical Therapy", "04/02/2026 - 07/18/2026", "$7,201.00"]]),
    "ML Valley Regional 2.docx": lambda: records_request(
        MATTER_B, VALLEY, "March 20, 2026", "VR-903771"),
}


def main(outdir="samples/Converted", echo=print):
    out = Path(outdir)
    for name, make in DOCS.items():
        build(out / name, make())
        if echo:
            echo(f"  wrote {out / name}")
    if echo:
        echo(f"{len(DOCS)} sample letters in {out}")
    return out


if __name__ == "__main__":
    main(*sys.argv[1:])
