"""Read and rewrite .docx files with the standard library only.

A .docx is a zip of XML parts. We never rebuild a document from scratch — we
open the zip, edit the text inside the parts that carry visible text, and write
every other entry back byte for byte. That keeps styles, headers, tables,
tabs, numbering and WordPerfect's converted formatting exactly as WP produced
them; only characters change.

The awkward part of WordprocessingML is that a single sentence is usually
split across several <w:r> runs (a spell-check mark, a font change, or just
how the converter emitted it), so "John Smith" may live in three elements.
Paragraph below joins the runs into one string, lets callers edit that string
by offset, and writes the result back into the original runs.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


# ElementTree rewrites namespace prefixes on serialization; registering the
# prefixes Word actually uses keeps the output diff-clean and keeps
# mc:Ignorable attribute values (which name prefixes as plain text) valid.
_NAMESPACES = {
    "wpc": "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "cx": "http://schemas.microsoft.com/office/drawing/2014/chartex",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "aink": "http://schemas.microsoft.com/office/drawing/2016/ink",
    "am3d": "http://schemas.microsoft.com/office/drawing/2017/model3d",
    "o": "urn:schemas-microsoft-com:office:office",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "v": "urn:schemas-microsoft-com:vml",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "w10": "urn:schemas-microsoft-com:office:word",
    "w": W,
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "w16": "http://schemas.microsoft.com/office/word/2018/wordml",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "wpi": "http://schemas.microsoft.com/office/word/2010/wordprocessingInk",
    "wne": "http://schemas.microsoft.com/office/word/2006/wordml",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
}
for _prefix, _uri in _NAMESPACES.items():
    ET.register_namespace(_prefix, _uri)


class ReplacementError(ValueError):
    """An edit could not be applied without disturbing non-text markup."""


@dataclass
class _Segment:
    """One run of characters in a paragraph, and where it came from.

    editable segments are <w:t> elements we may rewrite; static segments are
    tabs and line breaks, which occupy a character position in the paragraph
    text but cannot hold replacement text.
    """

    el: ET.Element | None
    text: str
    start: int
    editable: bool


# Elements that contribute characters to a paragraph's visible text.
_STATIC_CHARS = {_w("tab"): "\t", _w("br"): "\n", _w("cr"): "\n"}
# Subtrees whose text is not part of this paragraph's flow.
_OPAQUE = {_w("instrText"), _w("delText")}


class Paragraph:
    """A <w:p> viewed as one editable string."""

    def __init__(self, el: ET.Element, index: int = -1, part: str = ""):
        self.el = el
        self.index = index
        self.part = part
        self._segments = self._scan()

    def _scan(self) -> list[_Segment]:
        segments: list[_Segment] = []
        pos = 0

        def walk(node: ET.Element) -> None:
            nonlocal pos
            for child in node:
                tag = child.tag
                if tag == _w("p"):
                    continue  # nested paragraph (text box); handled on its own
                if tag in _OPAQUE:
                    continue
                if tag == _w("t"):
                    text = child.text or ""
                    segments.append(_Segment(child, text, pos, True))
                    pos += len(text)
                    continue
                if tag in _STATIC_CHARS:
                    text = _STATIC_CHARS[tag]
                    segments.append(_Segment(None, text, pos, False))
                    pos += len(text)
                    continue
                walk(child)

        walk(self.el)
        return segments

    @property
    def text(self) -> str:
        return "".join(s.text for s in self._segments)

    def apply(self, edits: list[tuple[int, int, str]]) -> None:
        """Apply (start, end, replacement) edits stated in self.text offsets.

        Edits must not overlap. Replacement text lands in the first <w:t> the
        span touches and inherits that run's formatting; the rest of the span
        is deleted from the runs that follow.
        """
        if not edits:
            return
        edits = sorted(edits, key=lambda e: e[0])
        length = len(self.text)
        prev_end = 0
        for start, end, _ in edits:
            if not 0 <= start < end <= length:
                raise ReplacementError(f"edit ({start}, {end}) outside paragraph of {length} chars")
            if start < prev_end:
                raise ReplacementError(f"overlapping edits near offset {start}")
            prev_end = end

        for seg in self._segments:
            if seg.editable:
                continue
            seg_end = seg.start + len(seg.text)
            for start, end, _ in edits:
                if start < seg_end and end > seg.start:
                    raise ReplacementError(
                        f"edit ({start}, {end}) spans a tab or line break; "
                        "split the edit or leave this occurrence for review"
                    )

        emitted = [False] * len(edits)
        for seg in self._segments:
            if not seg.editable:
                continue
            seg_end = seg.start + len(seg.text)
            pieces: list[str] = []
            cursor = seg.start
            while cursor < seg_end:
                active = next(
                    (i for i, (s, e, _) in enumerate(edits) if e > cursor and s < seg_end),
                    None,
                )
                if active is None:
                    pieces.append(seg.text[cursor - seg.start : seg_end - seg.start])
                    cursor = seg_end
                    break
                start, end, replacement = edits[active]
                if start > cursor:
                    pieces.append(seg.text[cursor - seg.start : start - seg.start])
                    cursor = start
                if not emitted[active]:
                    pieces.append(replacement)
                    emitted[active] = True
                cursor = min(end, seg_end)
            self._set_text(seg.el, "".join(pieces))
        self._segments = self._scan()

    @staticmethod
    def _set_text(el: ET.Element, text: str) -> None:
        el.text = text
        # Word trims leading/trailing spaces unless told not to.
        if text != text.strip():
            el.set(f"{{{XML_NS}}}space", "preserve")
        else:
            el.attrib.pop(f"{{{XML_NS}}}space", None)

    def replace_all(self, pattern: re.Pattern[str], repl) -> int:
        """Replace every match of pattern; repl(match) returns the new text."""
        edits = [(m.start(), m.end(), repl(m)) for m in pattern.finditer(self.text)]
        edits = [e for e in edits if e[2] is not None]
        self.apply(edits)
        return len(edits)


# Parts that carry text a reader can see. Everything else in the zip is
# copied through untouched.
_TEXT_PART = re.compile(
    r"^word/(document\d*\.xml|header\d*\.xml|footer\d*\.xml|footnotes\.xml|endnotes\.xml)$"
)


class DocxFile:
    """An open .docx: its text parts parsed, every other entry held verbatim."""

    def __init__(self, path):
        self.path = Path(path)
        self._entries: list[zipfile.ZipInfo] = []
        self._raw: dict[str, bytes] = {}
        self.parts: dict[str, ET.Element] = {}
        with zipfile.ZipFile(self.path) as zf:
            for info in zf.infolist():
                self._entries.append(info)
                data = zf.read(info.filename)
                self._raw[info.filename] = data
                if _TEXT_PART.match(info.filename):
                    try:
                        self.parts[info.filename] = ET.fromstring(data)
                    except ET.ParseError:
                        pass  # unparseable part: copy it through unchanged

    def paragraphs(self) -> list[Paragraph]:
        """Every paragraph in the document, in a stable order.

        Paragraph.index is the position in this list, not the position within
        the part, so a detection recorded during a scan still points at the
        same paragraph when the file is re-opened to be templatized.
        """
        out: list[Paragraph] = []
        for name in sorted(self.parts):
            for el in self.parts[name].iter(_w("p")):
                out.append(Paragraph(el, index=len(out), part=name))
        return out

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs())

    def save(self, dest) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as out:
            for info in self._entries:
                name = info.filename
                if name in self.parts:
                    body = ET.tostring(self.parts[name], encoding="UTF-8", xml_declaration=True)
                    data = body.replace(
                        b"<?xml version='1.0' encoding='UTF-8'?>",
                        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n',
                        1,
                    )
                else:
                    data = self._raw[name]
                new_info = zipfile.ZipInfo(name, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr
                out.writestr(new_info, data)
        return dest


def extract_text(path) -> str:
    return DocxFile(path).text


def copy_docx(src, dest) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest
