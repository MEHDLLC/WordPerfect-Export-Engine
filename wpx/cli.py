"""Command line for the post-conversion stages.

    python3 -m wpx scan       --in Converted
    python3 -m wpx catalog
    python3 -m wpx review
    python3 -m wpx map "01/26/2026" matter.date_of_loss
    python3 -m wpx templatize --in Converted --out Templates
    python3 -m wpx intake     --matter 2026-0114 --out intake.json
    python3 -m wpx matter set --matter 2026-0114 --from intake.json
    python3 -m wpx check      --matter 2026-0114
    python3 -m wpx generate   --matter 2026-0114 --templates Templates --out Letters

--db is accepted on every subcommand, so the order of arguments never matters.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, catalog, fields as F, forms, matters, render, scan, templatize
from .db import in_git_worktree, now, open_db
from .values import mask

DEFAULT_DB = "firmconvert.db"


def _con(args):
    """Open the database, warning if it is sitting inside a git checkout.

    The matter tables hold client names, dates of birth and claim data. A
    database created next to the source tree gets committed sooner or later.
    """
    repo = in_git_worktree(args.db)
    if repo is not None:
        print(f"warning: {args.db} is inside the git repository at {repo}.\n"
              "         Keep matter data out of version control — move it to the "
              "firm's client file share\n         or add it to .gitignore.",
              file=sys.stderr)
    return open_db(args.db)


# --- analysis ---------------------------------------------------------------

def cmd_scan(args):
    con = _con(args)
    totals = scan.scan_corpus(con, args.input)
    print(f"\nscanned {totals['docs']} document(s): {totals['hits']} values, "
          f"{totals['named']} mapped to fields, {totals['failed']} unreadable")
    if totals["docs"]:
        print("next: python3 -m wpx catalog")


def cmd_catalog(args):
    con = _con(args)
    summary = catalog.build_catalog(con, args.constant_share)
    print(f"{summary['values']} distinct values across {summary['docs']} document(s)")
    for kind in ("constant", "variable", "unknown"):
        print(f"  {kind:9s} {summary.get(kind, 0)}")
    print("\nconstant = firm boilerplate, left as-is in templates")
    print("variable = changes per matter, becomes a {{placeholder}}")
    print("unknown  = no field matched; see 'python3 -m wpx review'")


def cmd_review(args):
    con = _con(args)
    print("Fields found in the corpus")
    print(f"  {'field':26} {'docs':>5} {'values':>7} {'hits':>6}  label")
    for row in catalog.field_summary(con):
        print(f"  {row['field_key']:26} {row['docs']:5d} {row['values']:7d} "
              f"{row['hits']:6d}  {row['label']}")
    flagged = catalog.flagged_documents(con)
    if flagged:
        print(f"\nDocuments carrying markup this tool does not edit "
              f"({len(flagged)}) — open each template and check it:")
        for row in flagged:
            print(f"  {row['name']}: {row['flags']}")

    rows = catalog.unmapped(con, args.limit)
    print(f"\nValues no detector could name ({len(rows)} shown) — map or ignore each:")
    if not rows:
        print("  (none)")
    for row in rows:
        docs = row["in_docs"] or ""
        if len(docs) > 60:
            docs = docs[:57] + "..."
        print(f"  {mask(row['sample'])!r}  [{row['detector']}] in {row['docs']} doc(s): {docs}")
    if rows:
        print('\n  python3 -m wpx map "<value>" <field.key>   to name one')
        print('  python3 -m wpx map "<value>" --ignore       to leave it as literal text')


def cmd_map(args):
    con = _con(args)
    from .detect import normalize_value

    norm = normalize_value(args.value)
    if args.ignore:
        key = None
    else:
        if not args.field or args.field not in F.BY_KEY:
            sys.exit(f"unknown field: {args.field!r} (see 'python3 -m wpx fields')")
        key = args.field
    con.execute(
        "INSERT INTO value_overrides(norm_value, field_key, note, created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(norm_value) DO UPDATE SET field_key=excluded.field_key, "
        "note=excluded.note, created_at=excluded.created_at",
        (norm, key, args.note or "", now()),
    )
    con.commit()
    print(f"{args.value!r} -> {key or 'ignored'}")
    print("re-run 'python3 -m wpx scan' and 'catalog' to apply it across the corpus")


def cmd_fields(args):
    for group, group_fields in F.groups().items():
        print(f"\n[{group}]")
        for field in group_fields:
            flag = "" if field.always_variable else "   (firm boilerplate)"
            print(f"  {field.key:26} {field.label}{flag}")
            if field.aliases:
                print(f"  {'':26} labels: {', '.join(field.aliases)}")


# --- templates --------------------------------------------------------------

def cmd_templatize(args):
    con = _con(args)
    if not con.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]:
        sys.exit("no catalog yet — run 'python3 -m wpx scan' then 'catalog' first")
    results = templatize.templatize_corpus(
        con, args.input, args.output, args.min_confidence, args.include_constants
    )
    total = sum(r.replaced for r in results)
    review = sum(
        item["count"] for r in results for item in r.left_in_place
        if item["reason"] in ("unmapped", "low-confidence", "split-across-markup")
    )
    print(f"\n{len(results)} template(s) in {args.output}: {total} placeholders, "
          f"{review} value(s) left for review")
    print("each template has a .fields.json beside it listing both")


def cmd_forms(args):
    report = forms.dedupe(args.input, args.output)
    print(f"\n{report['templates']} template(s) are {report['distinct']} distinct form(s)")
    if args.output:
        print(f"deduplicated set written to {args.output} (with forms.json)")
    else:
        print("re-run with --out <dir> to write one template per form")


def cmd_intake(args):
    con = _con(args)
    keys = None
    if args.templates:
        used = matters.template_fields(con)
        keys = sorted({k for fields_used in used.values() for k in fields_used})
    sheet = matters.intake_blank(keys)
    if args.matter:
        try:
            existing = matters.values(con, args.matter)
        except KeyError:
            existing = {}
        for section in sheet.values():
            for key in section:
                section[key] = existing.get(key, "")
    text = json.dumps(sheet, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote intake sheet: {args.output}")
        print("fill it in, then: python3 -m wpx matter set --matter <ref> --from " + args.output)
    else:
        print(text)


def cmd_matter(args):
    con = _con(args)
    if args.action == "list":
        rows = matters.list_matters(con)
        if not rows:
            print("no matters yet")
        for row in rows:
            print(f"  {row['ref']:20} {row['filled']:3d} field(s)   {row['created_at']}")
        return
    if not args.matter:
        sys.exit("--matter is required")
    if args.action == "show":
        for scope in [""] + matters.scopes(con, args.matter):
            values = matters.values(con, args.matter, scope) if scope else \
                matters.values(con, args.matter)
            print(f"\n[{scope or 'matter'}]")
            for key in sorted(values):
                if scope and key not in matters.scoped_keys(con, args.matter, scope):
                    continue
                print(f"  {key:26} {values[key]}")
        return
    if args.action == "new":
        matters.ensure_matter(con, args.matter)
        print(f"created matter {args.matter}")
        return
    # set
    values = {}
    if args.from_file:
        values.update(matters.load_file(args.from_file))
    for pair in args.set or []:
        key, _, value = pair.partition("=")
        values[key.strip()] = value.strip()
    if not values:
        sys.exit("nothing to set: pass --from <file> or --set key=value")
    scope = args.scope or ""
    if args.add_party:
        scope = matters.next_scope(con, args.matter, args.add_party)
    stored, unknown = matters.set_values(con, args.matter, values, scope)
    blank = sum(1 for k, v in values.items() if k in F.BY_KEY and not str(v).strip())
    note = f" ({blank} still blank)" if blank else ""
    print(f"stored {stored} value(s) for {args.matter}"
          + (f" [{scope}]" if scope else "") + note)
    if unknown:
        print(f"ignored {len(unknown)} unknown field(s): {', '.join(unknown)}")
        print("see 'python3 -m wpx fields' for the canonical list")


def cmd_check(args):
    con = _con(args)
    supplied = render.default_values(con).keys()
    missing = matters.missing_for(con, args.matter, args.only, args.scope, supplied)
    if not missing:
        print("no templates registered — run 'python3 -m wpx templatize' first")
        return
    ready = [name for name, keys in missing.items() if not keys]
    print(f"{len(ready)}/{len(missing)} template(s) ready for {args.matter}")
    for name, keys in sorted(missing.items()):
        if keys:
            print(f"  {name}: needs {', '.join(keys)}")
    for name in sorted(ready):
        print(f"  {name}: ready")


def cmd_generate(args):
    con = _con(args)
    results = render.generate(
        con, args.matter, args.templates, args.output,
        only=args.only, on_missing=args.on_missing,
        name_pattern=args.name_pattern,
    )
    missing = sorted({k for r in results for k in r.missing})
    print(f"\ngenerated {len(results)} document(s) in {args.output}")
    if missing:
        print(f"missing values used in those documents: {', '.join(missing)}")
        print(f"  python3 -m wpx matter set --matter {args.matter} --set <field>=<value>")


# --- wiring -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default: {DEFAULT_DB})")

    parser = argparse.ArgumentParser(
        prog="python3 -m wpx",
        description="Analyze converted WordPerfect documents, build Word templates, "
                    "and generate a matter's letters from one set of facts.",
    )
    parser.add_argument("--version", action="version", version=f"wpx {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", parents=[common], help="read converted .docx files and detect values")
    p.add_argument("--in", dest="input", required=True, help="folder of converted .docx files")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("catalog", parents=[common], help="classify values as firm boilerplate or per-matter")
    p.add_argument("--constant-share", type=float, default=catalog.CONSTANT_SHARE,
                   help="share of documents a value must appear in to count as boilerplate")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("review", parents=[common], help="what was recognized, and what still needs a human")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("map", parents=[common], help="tell the scanner what an unrecognized value is")
    p.add_argument("value")
    p.add_argument("field", nargs="?")
    p.add_argument("--ignore", action="store_true", help="treat this value as ordinary prose")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_map)

    p = sub.add_parser("fields", parents=[common], help="list the canonical field dictionary")
    p.set_defaults(func=cmd_fields)

    p = sub.add_parser("templatize", parents=[common], help="write .docx templates with {{placeholders}}")
    p.add_argument("--in", dest="input", required=True)
    p.add_argument("--out", dest="output", required=True)
    p.add_argument("--min-confidence", type=float, default=templatize.DEFAULT_MIN_CONFIDENCE)
    p.add_argument("--include-constants", action="store_true",
                   help="also parameterize letterhead and other firm boilerplate")
    p.set_defaults(func=cmd_templatize)

    p = sub.add_parser("forms", parents=[common],
                       help="group templates that are really the same form")
    p.add_argument("--in", dest="input", required=True, help="folder of templates")
    p.add_argument("--out", dest="output", help="write one template per distinct form")
    p.set_defaults(func=cmd_forms)

    p = sub.add_parser("intake", parents=[common], help="print or write a blank intake sheet")
    p.add_argument("--matter", help="pre-fill with this matter's stored values")
    p.add_argument("--out", dest="output")
    p.add_argument("--templates", action="store_true",
                   help="limit to fields the registered templates actually use")
    p.set_defaults(func=cmd_intake)

    p = sub.add_parser("matter", parents=[common], help="create, fill and inspect a matter")
    p.add_argument("action", choices=["new", "set", "show", "list"])
    p.add_argument("--matter")
    p.add_argument("--from", dest="from_file", help="intake .json or key,value .csv")
    p.add_argument("--set", action="append", metavar="field.key=value")
    p.add_argument("--scope", default="", help="store against one party, e.g. provider:2")
    p.add_argument("--add-party", metavar="ROLE",
                   help="store as a new party of this role (currently: provider)")
    p.set_defaults(func=cmd_matter)

    p = sub.add_parser("check", parents=[common], help="which templates a matter can already produce")
    p.add_argument("--matter", required=True)
    p.add_argument("--only", nargs="*")
    p.add_argument("--scope", default="", help="check against one party, e.g. provider:1")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("generate", parents=[common], help="fill every template for one matter")
    p.add_argument("--matter", required=True)
    p.add_argument("--templates", required=True)
    p.add_argument("--out", dest="output", required=True)
    p.add_argument("--only", nargs="*", help="template names to render (default: all)")
    p.add_argument("--on-missing", choices=[render.MARK, render.BLANK, render.LEAVE, render.ERROR],
                   default=render.MARK, help="what to write where a value is missing")
    p.add_argument("--name-pattern", default="{ref} - {template}")
    p.set_defaults(func=cmd_generate)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except BrokenPipeError:  # piping into head/less is not an error
        sys.stdout = None


if __name__ == "__main__":
    main()
