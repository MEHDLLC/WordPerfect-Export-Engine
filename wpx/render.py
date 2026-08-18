"""Stage 6 — fill every template for a matter in one pass.

Rendering is the mirror image of templatizing: find {{field}} in the paragraph
text (even where Word split it across runs) and put the matter's value in its
place, leaving the rest of the file byte-identical.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as _dc_field
from datetime import date
from pathlib import Path

from . import fields as F, repeat
from .db import now
from .docxml import DocxFile
from .values import derive, render_date

MARK, BLANK, LEAVE, ERROR = "mark", "blank", "leave", "error"


class MissingValue(KeyError):
    """A template asked for a field the matter has no value for."""


@dataclass
class RenderResult:
    template: str
    path: str
    filled: int = 0
    repeated: int = 0          # rows or blocks written by an {{#each}} marker
    missing: list = _dc_field(default_factory=list)


def letter_date_today() -> str:
    today = date.today()
    return f"{today:%B} {today.day}, {today.year}"


def render(template_path, values: dict, dest, on_missing: str = MARK,
           parties: dict | None = None) -> RenderResult:
    """Write a filled copy of template_path at dest.

    Values are derived first, so storing client.name is enough to fill
    {{client.first_name}} and {{client.last_name}} as well. parties maps a
    role to that matter's parties ({"provider": [{...}, {...}]}); each
    {{#each role}} block is cloned once per party before the ordinary pass runs.
    """
    template_path, dest = Path(template_path), Path(dest)
    values = derive(values)
    doc = DocxFile(template_path)
    result = RenderResult(template=template_path.name, path=str(dest))
    missing: set[str] = set()

    def substituter(scope_values: dict):
        """A replacement function bound to one set of values.

        Repeated rows each get their own; everything else gets the matter's.
        """
        def substitute(match: re.Match) -> str | None:
            key, style = match.group(1), match.group(2)
            value = scope_values.get(key)
            if value not in (None, ""):
                result.filled += 1
                if style:
                    # A date the firm typed as 01/26/2026 prints as
                    # "January 26, 2026" where the letter spelled it out.
                    respelled = render_date(str(value), style)
                    if respelled:
                        return respelled
                return str(value)
            missing.add(key)
            if on_missing == ERROR:
                raise MissingValue(key)
            if on_missing == LEAVE:
                return None  # leave the placeholder in place
            if on_missing == BLANK:
                return ""
            label = F.BY_KEY[key].label if key in F.BY_KEY else key
            return f"[MISSING: {label}]"

        return substitute

    # Always run the expansion pass: it also strips the markers from a template
    # whose role has no parties recorded, so {{#each provider}} never reaches
    # the finished letter.
    prepared = {
        role: [derive({**values, **party}) for party in party_list]
        for role, party_list in (parties or {}).items()
    }
    result.repeated = repeat.expand(doc, prepared, substituter)

    for paragraph in doc.paragraphs():
        paragraph.replace_all(F.PLACEHOLDER_RE, substituter(values))

    result.missing = sorted(missing)
    doc.save(dest)
    return result


def default_values(con=None) -> dict:
    """Values every letter can assume without anyone typing them."""
    defaults = {"matter.letter_date": letter_date_today()}
    if con is not None:
        from .matters import firm_defaults

        defaults.update({k: v for k, v in firm_defaults(con).items() if v})
    return defaults


def placeholders(template_path) -> set[str]:
    """The field keys a template asks for, read from the template itself."""
    doc = DocxFile(template_path)
    return {m.group(1) for p in doc.paragraphs() for m in F.PLACEHOLDER_RE.finditer(p.text)}


def needed_fields(template_path) -> set[str]:
    """The keys someone actually has to supply: derived ones are free."""
    return {k for k in placeholders(template_path) if not _is_derived(k)}


def _is_derived(key: str) -> bool:
    field = F.BY_KEY.get(key)
    return bool(field and field.derived)


ROLES = repeat.ROLES


def repeat_roles(template_path) -> set[str]:
    """Roles this template repeats over inside the document."""
    return repeat.roles(DocxFile(template_path))


def scoping_keys(template_path) -> set[str]:
    """The placeholders that decide who a whole letter is written to."""
    doc = DocxFile(template_path)
    all_keys = {
        m.group(1) for p in doc.paragraphs() for m in F.PLACEHOLDER_RE.finditer(p.text)
    }
    return all_keys - repeat.row_level_keys(doc)


def _party_role(keys, repeated=()) -> str | None:
    """Which party, if any, the whole letter is written about.

    A records request mentions provider.* fields, so it is generated once per
    provider on the matter. A demand letter that repeats a table row per
    provider mentions them too — but there the parties are consumed inside the
    document, so it stays one letter.
    """
    for role in ROLES:
        if role in repeated:
            continue
        if any(key.startswith(f"{role}.") for key in keys):
            return role
    return None


def find_templates(template_root, only=None) -> list[Path]:
    templates = sorted(
        p for p in Path(template_root).glob("*.docx") if not p.name.startswith("~$")
    )
    if only:
        wanted = {o.lower() for o in only}
        templates = [
            p for p in templates
            if p.name.lower() in wanted or p.stem.lower() in wanted
        ]
    return templates


def generate(
    con,
    ref: str,
    template_root,
    outdir,
    only=None,
    on_missing: str = MARK,
    name_pattern: str = "{ref} - {template}",
    echo=print,
) -> list[RenderResult]:
    """Render every template (or the named subset) for one matter.

    Templates written about a repeating party are rendered once per party, so
    one command produces the whole packet: one demand letter, one med-pay
    request, and a records request for each provider on the file.
    """
    from .matters import scopes, values as matter_values

    base = default_values(con)
    base.update({k: v for k, v in matter_values(con, ref).items() if v})

    def party_values(role: str) -> list[dict]:
        return [
            {k: v for k, v in matter_values(con, ref, scope).items() if v}
            for scope in scopes(con, ref, f"{role}:")
        ]

    outdir = Path(outdir)
    results = []
    for template in find_templates(template_root, only):
        repeated = repeat_roles(template)
        parties = {role: party_values(role) for role in repeated}
        role = _party_role(scoping_keys(template), repeated)
        party_scopes = scopes(con, ref, f"{role}:") if role else []
        for scope in party_scopes or [""]:
            values = dict(base)
            if scope:
                values.update({k: v for k, v in matter_values(con, ref, scope).items() if v})
            label = values.get(f"{role}.name") if scope else None
            stem = name_pattern.format(ref=ref, template=template.stem)
            if label:
                stem = f"{stem} - {label}"
            elif scope:
                stem = f"{stem} - {scope.replace(':', ' ')}"
            dest = outdir / f"{safe_name(stem)}.docx"
            result = render(template, values, dest, on_missing, parties)
            results.append(result)
            if echo:
                note = (f"  ({len(result.missing)} missing: {', '.join(result.missing)})"
                        if result.missing else "")
                rows = f", {result.repeated} repeated row(s)" if result.repeated else ""
                echo(f"  {dest.name}: {result.filled} values{rows}{note}")

    manifest = {
        "matter": ref,
        "generated_at": now(),
        "templates": [
            {"template": r.template, "output": r.path, "filled": r.filled,
             "missing": r.missing}
            for r in results
        ],
    }
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{safe_name(ref)}.manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return results


def safe_name(name: str) -> str:
    """A file or folder name that Windows will accept."""
    return re.sub(r'[\\/:*?"<>|]+', "-", name).strip()
