# WordPerfect Export Engine

Tooling for a law firm moving off WordPerfect: convert the back catalogue to
Word, turn the converted letters into a small library of forms, and generate a
matter's whole packet from one set of facts typed once.

The conversion stage needs Windows and a licensed WordPerfect 2021. Everything
after it is pure Python standard library and runs anywhere.

## The pipeline

| Stage | Command | What it does |
|---|---|---|
| 1. Convert | `wpd_convert.py` | Drives WordPerfect 2021 over COM to save `.wpd` as `.docx`, with a resumable SQLite job queue and a watchdog for hung conversions. |
| 2. Scan | `python3 -m wpx scan` | Reads each converted letter and records every value that looks like data — labelled fields, letterhead, addressee blocks, names repeated through the body. |
| 3. Catalog | `python3 -m wpx catalog` | Decides which values are firm boilerplate and which change from matter to matter. |
| 4. Review | `python3 -m wpx review` / `map` | Lists what no detector could name, so a human can name it once for the whole corpus. |
| 5. Templatize | `python3 -m wpx templatize` | Rewrites each letter as a `.docx` template with `{{field}}` placeholders, leaving formatting untouched. `--collapse-schedules` turns a frozen table of bills into one row that repeats. |
| 6. Forms | `python3 -m wpx forms` | Collapses the templates that turn out to be the same letter saved under different names. |
| 6b. Contacts | `python3 -m wpx contacts build` | Mines every carrier, adjuster and clinic the firm has written to, with the block each letter was addressed by. |
| 7. Intake | `python3 -m wpx intake` / `matter set` | One sheet of canonical fields per matter — the "type it once" step. |
| 7b. Address | `python3 -m wpx address` | Names an entity and the matter is addressed: carrier block, adjuster, clinic address, fax. |
| 8. Generate | `python3 -m wpx generate` | Fills every form for that matter: one records request per medical provider, and one demand letter whose bill schedule has a row for each. |

## Quick start

Try the whole thing on a synthetic corpus (invented clients, carriers and
clinics — no real file data lives in this repository):

```bash
python3 tools/make_sample_corpus.py samples/Converted

python3 -m wpx scan       --db demo.db --in samples/Converted
python3 -m wpx catalog    --db demo.db
python3 -m wpx review     --db demo.db
python3 -m wpx templatize --db demo.db --in samples/Converted --out Templates
python3 -m wpx forms      --db demo.db --in Templates --out Forms

python3 -m wpx contacts build --db demo.db
python3 -m wpx contacts list  --db demo.db

python3 -m wpx intake     --db demo.db --templates --out intake.json
# fill intake.json in, then:
python3 -m wpx matter set --db demo.db --matter 2026-0311 --from intake.json

# who it is against, and who treated the client — from the contact list
python3 -m wpx address    --db demo.db --matter 2026-0311 --insurer "Summit Mutual"
python3 -m wpx address    --db demo.db --matter 2026-0311 --provider "Valley Regional"
python3 -m wpx address    --db demo.db --matter 2026-0311 --provider "Larkspur"

python3 -m wpx check      --db demo.db --matter 2026-0311
python3 -m wpx generate   --db demo.db --matter 2026-0311 --templates Forms --out Letters
```

On the real corpus, stage 1 comes first:

```bash
python wpd_convert.py init --db firmconvert.db
python wpd_convert.py add  --db firmconvert.db --src "C:\WPFiles" --recursive
python wpd_convert.py run  --db firmconvert.db --outdir "C:\Converted"
```

All stages share one SQLite database, so the audit trail runs from "this .wpd
was converted at 03:14" through "this value became `{{insurer.claim_no}}`" to
"this matter's claim number is X".

## What a template looks like

The letterhead, tabs and spacing are exactly what WordPerfect produced; only
the values changed:

```
NORTHSTAR INJURY LAW, LLC
R. Cole Halvorsen, Attorney at Law
412 W. Denali Avenue, Suite 300

{{matter.letter_date}}

{{adjuster.name}}
{{insurer.name}}

Re:  Our Client:  {{client.name}}
     Claim No.:  {{insurer.claim_no}}
     Date of Loss:  {{matter.date_of_loss}}

Dear {{adjuster.name}}:

Please be advised that this office represents {{client.name}} for injuries
sustained in the above-referenced collision of {{matter.date_of_loss}}.
{{client.first_name}} has not given, and will not give, a recorded statement.
```

## Fields

`python3 -m wpx fields` prints the canonical dictionary — the single list of
facts every form draws from (client, matter, carrier, adjuster, provider,
demand, firm). Adding a field is a one-line change in `wpx/fields.py`; the
scanner, the templatizer and the intake sheet all read the same table.

Two things are handled so that one entry really does cover every letter:

* **Dates are stored once, spelled per letter.** Type `3/6/2026`; the Re: block
  prints `03/06/2026` and the body prints `March 6, 2026`, because each
  placeholder carries the spelling the original letter used
  (`{{matter.date_of_loss:long}}`).
* **Name parts are derived, not typed.** Storing `client.name` fills
  `{{client.first_name}}` and `{{client.last_name}}`, so a letter that said
  "Mr. Whitfield" or "Dana" keeps saying that instead of expanding to the full
  name.

## The contact list

`contacts build` reads the scan and produces the address book the corpus
already contained:

```
  [insurer ] Summit Mutual Insurance Company            Bloomington, IL 61710
  [provider] Larkspur Chiropractic Clinic               Wasilla, AK 99654
  [provider] Summit Physical Therapy                       (name only: no address on file)
  [provider] Valley Regional Medical Center             Palmer, AK 99645
```

Then one command addresses a matter:

```
$ python3 -m wpx address --matter 2026-0620 --insurer "Summit Mutual"
2026-0620 is now addressed to Summit Mutual Insurance Company, attn Marcy Lindholm (4 field(s))
```

and every letter for that matter comes out with the carrier block, the
adjuster, the clinic's address and its fax already in place. Names match
partially and case-insensitively; an ambiguous one lists the candidates rather
than picking. Adjusters and records custodians are held as people *at* an
entity, so `--adjuster` picks one without a second contact card.

Two boundaries are enforced rather than trusted:

* **Contacts hold entity details only.** Claim numbers, policy numbers and
  patient account numbers are facts about a matter; the dictionary marks which
  fields are durable, and a manual edit that tries to store a claim number on a
  carrier is refused. Copying a contact can never carry a previous client's
  number onto the next client's letter.
* **Addresses come only from letters that addressed one entity.** A demand
  letter naming four clinics in its bill schedule contributes their names, not
  their addresses — nothing in that table says which address belongs to which
  clinic. Those contacts show up as "name only", ready for someone to fill in
  with `contacts set`.

`contacts build` is re-runnable; anything edited by hand is marked manual and
left alone. `contacts list --csv contacts.csv` exports the book.

## Rows that repeat

A demand letter's schedule of medical bills needs one row per provider, not one
letter per provider. Mark the row once:

```
{{#each provider}} | {{provider.name}} | {{provider.dates_of_service}} | {{provider.billed_amount}}
```

and every provider on the matter gets a row, with the cell formatting intact.
`templatize --collapse-schedules` writes that marker for you where a converted
letter left several identical rows behind. A template that repeats over
providers stays a single letter; one that mentions providers without a marker
is still generated once per provider. `{{/each}}` in a later row or paragraph
repeats a multi-row unit.

Client data lives in the same SQLite file: it is created mode `0600`, every
command warns if it sits inside a git checkout, and social security numbers are
recognized by shape (so they become placeholders rather than being baked into a
shared template) and masked in reports. See `docs/PIPELINE.md`.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

No third-party packages required.

## Layout

```
wpd_convert.py   stage 1: WordPerfect COM automation (Windows only)
wpx/             stages 2-8
  fields.py      the canonical field dictionary
  docxml.py      read/write .docx without disturbing formatting
  detect.py      value detection: labels, salutations, propagation, patterns
  blocks.py      letter structure: letterhead, addressee block
  scan.py        stage 2      catalog.py    stage 3
  contacts.py    the address book mined from the corpus
  repeat.py      {{#each}} blocks: one row per party
  values.py      dates and names: one stored value, many spellings
  templatize.py  stage 5      forms.py      stage 6
  matters.py     stage 7      render.py     stage 8
  db.py          shared SQLite schema
tools/           sample corpus generator
docs/PIPELINE.md how detection works, and what it deliberately does not do
```
