# How the conversion pipeline works

Notes for whoever maintains this next — what each stage assumes, where it is
deliberately conservative, and what is still missing.

## Stage 1 — WordPerfect to Word

`wpd_convert.py` drives WordPerfect 2021 through its PerfectScript COM
interface. The hard-won details are commented in the file itself; the two that
cost the most time were:

* PerfectScript exposes no type information, so pywin32 cannot tell a method
  from a property. Merely *accessing* `app.FileOpen` invoked it with no
  arguments, which is where the `65441` errors came from. `_FlagAsMethod` fixes
  it, and dispatch must stay late-bound (`dynamic.Dispatch`) — the makepy
  wrapper's precomputed dispatch IDs do not match the real interface.
* The `.docx` export enum on WP 2021 is `Word2007_FileSave_ExportType` = 70.
  `list-constants` prints what a given install actually exposes.

The job queue is resumable and every attempt is logged, because a batch of
thousands of files will not run clean the first time.

## Stage 2 — detection

Four passes run over each letter, in descending order of trust. Later passes
never overwrite an earlier pass's claim on a span; `resolve_overlaps` keeps the
best hit per span.

1. **Labelled** — `Claim No.: AK-4471-88203`. The label is matched against the
   alias list in `wpx/fields.py`, so twenty years of inconsistent labelling
   (`DOL`, `D.O.L.`, `Date of Accident`) lands on one field.
2. **Blocks** — the letterhead above the date line is the firm; the block above
   the `Re:` line is the addressee. The block's own vocabulary decides whether
   it is a carrier (Insurance, Casualty, Mutual) or a provider (Clinic,
   Hospital, Medical), which in turn decides whether the person named in it is
   the adjuster or the records custodian.
3. **Propagation** — every value named by passes 1 and 2 is then searched for
   through the whole letter, including `Ms. Whitfield` and a bare first name.
   This is what makes a body paragraph templatize rather than just a header
   block, and it works across runs, so WordPerfect splitting a name into three
   pieces mid-export does not defeat it.
4. **Patterns** — dates, money, phone numbers and city/state/ZIP lines with
   nothing naming them. These are candidates for review, never automatic edits.

Two things widen coverage without guessing:

* **Corpus resolution.** A value labelled `Date of Loss:` in the demand letter
  is recognized when it appears bare in the records request, because the
  catalog remembers what the corpus as a whole called it.
* **Overrides.** `wpx map "<value>" <field.key>` records a human decision once;
  every later scan applies it, and `--ignore` marks a value as ordinary prose.

## Stage 3 — boilerplate or variable

The dictionary outranks the evidence:

* a field marked firm boilerplate (`always_variable=False`) is constant;
* any other named field is a per-matter variable however often it recurs — a
  firm that scans one client's folder would otherwise see that client's claim
  number in every document and conclude it was letterhead;
* an *unnamed* value that appears in nearly every document of a corpus of three
  or more is treated as boilerplate, which keeps standing notices out of the
  review queue.

## Stage 5 — templatizing

Templates are the original `.docx` files with text swapped in place. Nothing is
rebuilt: the zip is reopened, only the parts carrying visible text are edited,
and every other entry is copied byte for byte. Styles, tables, headers, footers
and section properties therefore survive exactly as WordPerfect wrote them.

An edit that would span a tab or a line break is refused rather than guessed,
and lands in the sidecar report instead. Each template gets a `.fields.json`
beside it listing both the placeholders written and every value left alone,
with the reason (`firm-boilerplate`, `unmapped`, `low-confidence`,
`split-across-markup`). That report is the honest measure of coverage.

## Stages 7 and 8 — one intake, many letters

A matter is a bag of canonical values. Facts that repeat — a matter has one
client but four clinics — are stored under a party scope (`provider:1`,
`provider:2`) that overlays the matter's own values at generation time. A
template mentioning any `provider.*` field is therefore rendered once per
provider, so `generate` produces the whole packet in one command.

Missing values are marked `[MISSING: Claim number]` in the output by default,
not left blank: a gap that is visible in review is safer than one that is not.
`--on-missing` can make it blank, leave the placeholder, or fail outright.

## Values that are the same fact spelled differently

Two shapes would otherwise defeat "enter it once", so both are handled in
`wpx/values.py`:

**Dates.** `01/26/2026` in the Re: block and `January 26, 2026` in the body are
one date. Values are catalogued by the parsed date (`date:2026-01-26`), so the
two spellings collapse to one value; the templatizer records how each
occurrence was written as a style suffix (`{{matter.date_of_loss:long}}`), and
the renderer re-spells the single stored date accordingly. The firm types the
date once, in whatever format it likes, and every letter keeps its own house
style. Money is canonicalized the same way, so `$18,412.60` and `$ 18412.6` do
not look like different figures.

**Names.** A letter introduces "Dana R. Whitfield" and then says "Ms.
Whitfield" or "Dana". Replacing all three with `{{client.name}}` would make the
generated letter read "Mr. Dana R. Whitfield will forward the records".
Instead, only the surname span is claimed — "Mr. `{{client.last_name}}`" keeps
the honorific the firm wrote — and first/last names are *derived* fields: they
never appear on the intake sheet and are computed from the full name at
generation time, honorifics and suffixes stripped. An explicitly stored value
always wins, for clients who go by something their legal name does not contain.
Derivation is gated on the field dictionary, so an organization
("Valley Regional Medical Center") is never given a surname.

## Handling client data

The matter tables hold names, dates of birth, claim numbers and medical
providers, so:

* the database is created mode `0600` (POSIX; on Windows the file share's ACLs
  govern, and `chmod` is best-effort);
* every command warns when the database sits inside a git working tree, because
  a database created next to the source tree does eventually get committed;
* social security numbers are recognized by shape even when nothing labels
  them. This is a correctness issue as much as a privacy one: an unrecognized
  SSN would be copied into a template and then sent out on every later client's
  letter;
* SSNs are masked in the review report and in each template's sidecar, since
  those get mailed around and pasted into tickets.

This is a floor, not a security model. There is no encryption at rest, no
per-user access control and no audit of reads. Keep the database inside
whatever the firm already uses for client files, and out of any repository.

## Documents that need a person

`scan` records the markup it steps over — tracked changes, field codes, content
controls, text boxes — and `review` lists the documents carrying it. Text
inside a revision mark or a field code is not editable by the text passes, so a
template built from such a document has to be opened and checked. Reporting
them is the point: silently skipping them would make coverage look better than
it is.

## Still open

**Repeating table rows.** A demand letter's schedule of medical bills has one
row per provider. Party scopes already generate one *letter* per provider, but
not one *row*. The cheap version: support `{{#each provider}}` in a table
row's first cell, clone that row per party scope at render time, and delete the
template row — the XML layer already holds the elements, so this is element
cloning plus a scoped substitution pass, roughly 120 lines and a day with
tests. Recognizing which existing table *is* a schedule should stay manual: a
person marks the row once per template, rather than a detector guessing.

**Word-native fields.** Placeholders are `{{field}}` text, which the generator
fills but Word does not understand. Emitting `w:sdt` content controls tagged
with the field key (and saving as `.dotx`) would let a paralegal open a
template in Word and fill it there, with the field names showing in the
document. The XML is writable with the current layer, but content controls are
easy to emit in a form Word quietly rejects, so this needs verification on the
Windows box with real Word before it ships — budget a day, most of it testing.

**Smaller items.** Money spelled out in words ("Twenty Thousand Dollars
($20,000.00)") is not linked to its numeral. Name parsing does not split
middle names or preserve suffixes as their own field. Neither has bitten the
sample corpus yet; both are a few lines in `wpx/values.py` when they do.
