"""Collapse the templates that are really the same form.

A firm that has been running on WordPerfect for decades does not have twelve
letters — it has three letters and nine copies of them, each saved under the
name of whichever carrier or clinic it was last sent to. Once the
matter-specific values are placeholders, those copies become textually
identical and can be recognized as one form.

This is the step that turns "converted documents" into a form library small
enough for a human to maintain.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from .docxml import DocxFile


def normalized_text(path) -> str:
    """A template's text with spacing and case flattened, for comparison."""
    text = DocxFile(path).text
    return re.sub(r"\s+", " ", text).strip().casefold()


def signature(path) -> str:
    return hashlib.sha256(normalized_text(path).encode("utf-8")).hexdigest()[:16]


def group(template_root) -> dict[str, list[Path]]:
    """signature -> the templates that share it, richest name first."""
    groups: dict[str, list[Path]] = {}
    for path in sorted(Path(template_root).glob("*.docx")):
        if path.name.startswith("~$"):
            continue
        groups.setdefault(signature(path), []).append(path)
    return groups


def dedupe(template_root, outdir=None, echo=print) -> dict:
    """Report — and optionally write — one template per distinct form."""
    groups = group(template_root)
    report = {"forms": [], "templates": sum(len(v) for v in groups.values())}
    for sig, paths in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        keeper = paths[0]
        entry = {
            "form": keeper.stem,
            "signature": sig,
            "members": [p.name for p in paths],
        }
        report["forms"].append(entry)
        if echo:
            if len(paths) > 1:
                others = ", ".join(p.name for p in paths[1:])
                echo(f"  {keeper.name}  <- also: {others}")
            else:
                echo(f"  {keeper.name}")
        if outdir:
            out = Path(outdir)
            out.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(keeper, out / keeper.name)
            sidecar = keeper.with_suffix(".fields.json")
            if sidecar.exists():
                shutil.copyfile(sidecar, out / sidecar.name)
    if outdir:
        (Path(outdir) / "forms.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["distinct"] = len(groups)
    return report
