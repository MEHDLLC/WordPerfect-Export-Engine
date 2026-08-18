"""Blocks that repeat once per party — a demand letter's schedule of bills.

Party scopes already generate one *letter* per medical provider. A demand
letter needs the other shape: one letter, one table, one *row* per provider.

A person marks the repeating unit in the template:

    {{#each provider}} | {{provider.name}} | {{provider.dates_of_service}} | ...

Put the marker in a table row and that row repeats; put it in an ordinary
paragraph and that paragraph repeats. To repeat several rows or paragraphs as a
unit, close the block with {{/each}} in the last one.

Marking is deliberately manual. Recognizing which of a converted letter's
tables is a schedule and which is a signature block is exactly the judgement
call a detector gets wrong, and getting it wrong means silently deleting rows
from a demand letter. What the templatizer does instead is point out the tables
that look repeatable, in the sidecar report, and leave the decision to a human.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from . import fields as F
from .docxml import Paragraph, _w

# Party roles a matter can carry several of.
ROLES = ("provider",)

START_RE = re.compile(r"\{\{\s*#each\s+([A-Za-z0-9_]+)\s*\}\}")
END_RE = re.compile(r"\{\{\s*/each\s*\}\}")


@dataclass
class RepeatBlock:
    """One marked unit: the sibling elements to clone, and their container."""

    role: str
    parent: ET.Element
    elements: list


def _parent_map(root: ET.Element) -> dict:
    return {child: parent for parent in root.iter() for child in parent}


def _unit(paragraph: ET.Element, parents: dict) -> ET.Element:
    """The element that repeats: the enclosing table row, or the paragraph."""
    node = paragraph
    while node in parents:
        parent = parents[node]
        if node.tag == _w("tr"):
            return node
        node = parent
    return paragraph


def find_blocks(root: ET.Element) -> list[RepeatBlock]:
    """Every {{#each role}} block in one document part, in document order."""
    parents = _parent_map(root)
    paragraphs = list(root.iter(_w("p")))
    blocks: list[RepeatBlock] = []
    claimed: set = set()

    for i, para in enumerate(paragraphs):
        match = START_RE.search(Paragraph(para).text)
        if not match:
            continue
        start = _unit(para, parents)
        if start in claimed or start not in parents:
            continue
        parent = parents[start]
        siblings = list(parent)
        elements = [start]

        for later in paragraphs[i + 1:]:
            if not END_RE.search(Paragraph(later).text):
                continue
            end = _unit(later, parents)
            if parents.get(end) is parent and end in siblings:
                lo, hi = siblings.index(start), siblings.index(end)
                if hi >= lo:
                    elements = siblings[lo : hi + 1]
            break

        claimed.update(elements)
        blocks.append(RepeatBlock(match.group(1), parent, elements))
    return blocks


def roles(doc) -> set[str]:
    """The party roles a template repeats over."""
    return {block.role for root in doc.parts.values() for block in find_blocks(root)}


def strip_markers(element: ET.Element) -> None:
    for para in element.iter(_w("p")):
        wrapped = Paragraph(para)
        wrapped.replace_all(START_RE, lambda m: "")
        wrapped.replace_all(END_RE, lambda m: "")


def expand(doc, parties: dict, substitute_with) -> int:
    """Clone every marked block once per party of its role.

    substitute_with(values) returns the replacement function to run inside that
    copy, so each clone is filled from its own party's values.

    A role with no parties recorded still renders one copy, with the missing
    policy applied to its fields. An empty schedule row saying
    "[MISSING: Provider name]" is a question someone will answer; a row this
    tool deleted on its own is one nobody will ever see.
    """
    expanded = 0
    for root in doc.parts.values():
        for block in find_blocks(root):
            party_values = parties.get(block.role) or [None]
            index = list(block.parent).index(block.elements[0])
            clones = []
            for values in party_values:
                for element in block.elements:
                    clone = copy.deepcopy(element)
                    strip_markers(clone)
                    if values is not None:
                        replace = substitute_with(values)
                        for para in clone.iter(_w("p")):
                            Paragraph(para).replace_all(F.PLACEHOLDER_RE, replace)
                    clones.append(clone)
            for offset, clone in enumerate(clones):
                block.parent.insert(index + offset, clone)
            for element in block.elements:
                block.parent.remove(element)
            expanded += len(party_values)
    return expanded


def find_schedules(doc) -> list[dict]:
    """Tables whose rows repeat the same fields — a schedule of bills.

    Two rows saying {{provider.name}} is not a template; it is one matter's
    provider list frozen into one. Reporting them is how a person finds the
    tables worth marking with {{#each provider}}; --collapse-schedules does the
    marking for the clear-cut ones.
    """
    found = []
    for root in doc.parts.values():
        for table in root.iter(_w("tbl")):
            rows = list(table.iter(_w("tr")))
            keyed = [(row, _row_fields(row)) for row in rows]
            data = [(row, keys) for row, keys in keyed if keys]
            if len(data) < 2:
                continue
            fields = data[0][1]
            if any(keys != fields for _row, keys in data):
                continue  # rows differ: not a plain schedule, leave it alone
            role = _row_role(fields)
            found.append({
                "table": table,
                "rows": [row for row, _ in data],
                "fields": sorted(fields),
                "role": role,
            })
    return found


def _row_fields(row) -> set:
    keys = set()
    for para in row.iter(_w("p")):
        keys.update(m.group(1) for m in F.PLACEHOLDER_RE.finditer(Paragraph(para).text))
    return keys


def _row_role(fields) -> str | None:
    prefixes = {key.split(".", 1)[0] for key in fields}
    roles = prefixes & set(ROLES)
    return roles.pop() if len(roles) == 1 else None


def row_level_keys(doc) -> set[str]:
    """Placeholders that belong to a row rather than to the letter.

    A demand letter names providers in its schedule of bills, but it is still
    one letter. Only fields mentioned outside a repeating block or a schedule
    table say who a whole letter is addressed to.
    """
    keys: set[str] = set()
    for root in doc.parts.values():
        for block in find_blocks(root):
            for element in block.elements:
                for para in element.iter(_w("p")):
                    keys.update(m.group(1) for m in F.PLACEHOLDER_RE.finditer(Paragraph(para).text))
    for schedule in find_schedules(doc):
        for row in schedule["rows"]:
            keys.update(_row_fields(row))
    return keys


def collapse_schedule(schedule) -> bool:
    """Keep one row of a schedule and mark it to repeat per party."""
    role = schedule["role"]
    if not role:
        return False
    keeper, *extras = schedule["rows"]
    first = next(keeper.iter(_w("p")), None)
    if first is None:
        return False
    Paragraph(first).prepend("{{#each " + role + "}}")
    for row in extras:
        schedule["table"].remove(row)
    return True
