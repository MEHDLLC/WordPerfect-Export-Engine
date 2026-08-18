"""Build simple .docx files from plain text.

Used for the sample corpus and the test suite — not part of the conversion
path, where documents always come from WordPerfect. It exists so the whole
pipeline can be exercised on realistic-looking letters without shipping a
single line of real client data.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _run(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _paragraph(spec) -> str:
    """spec is a string, or a list of strings to emit as separate runs.

    Multi-run paragraphs matter: WordPerfect's exporter splits text mid-name
    all the time, and a templatizer that only searches run-by-run misses those.
    """
    runs = [spec] if isinstance(spec, str) else list(spec)
    return "<w:p>" + "".join(_run(r) for r in runs) + "</w:p>"


def build(path, paragraphs) -> Path:
    """Write a .docx at path whose body is the given paragraph specs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(_paragraph(p) for p in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<w:document xmlns:w="{W}"><w:body>{body}'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", document)
    return path


def from_text(path, text: str) -> Path:
    """Build a .docx from a blank-line-free block of text, one line per paragraph."""
    return build(path, text.split("\n"))
