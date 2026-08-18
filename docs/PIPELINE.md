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

## Known limits / next steps

* **Tracked changes and fields.** Text inside `w:instrText` and `w:delText` is
  skipped. Documents converted with revision marks intact should be reviewed.
* **Tables.** Paragraphs inside tables are scanned and templatized normally,
  but no detector understands a table as a *structure* (e.g. a billing table
  whose rows should repeat per provider).
* **Name parsing** is deliberately shallow: full name, first name, and a
  `Mr./Ms. Surname` form. There is no title/middle/suffix split.
* **Dates are not normalized.** `01/26/2026` and `January 26, 2026` are
  different values to the catalog. Storing a canonical date per field and
  formatting on render would let one intake value fill both spellings.
* **No Word UI.** Placeholders are `{{field}}` text, not content controls. A
  future stage could emit real Word content controls or MERGEFIELDs so the
  templates are fillable inside Word itself.
* **Access control.** The matter database holds client names, dates of birth
  and claim data. It is a plain SQLite file with no encryption or audit of
  reads; keep it inside whatever the firm already uses for client files.
