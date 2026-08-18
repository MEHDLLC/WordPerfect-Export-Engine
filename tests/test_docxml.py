"""The zip/XML layer: editing text without disturbing anything else."""

import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from wpx.docxml import DocxFile, ReplacementError
from wpx.minidocx import build


class DocxEditingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, paragraphs, name="in.docx"):
        return build(self.tmp / name, paragraphs)

    def test_reads_text_across_runs(self):
        path = self.make(["Re: ", ["Dana ", "R. ", "Whitfield"]])
        self.assertEqual(DocxFile(path).text, "Re: \nDana R. Whitfield")

    def test_replaces_a_value_split_across_runs(self):
        path = self.make([["Dana ", "R. ", "Whitfield"], "unchanged"])
        doc = DocxFile(path)
        doc.paragraphs()[0].apply([(0, 17, "{{client.name}}")])
        self.assertEqual(doc.text, "{{client.name}}\nunchanged")

    def test_replacement_survives_a_save_reload(self):
        path = self.make([["Claim No.: ", "AK-4471", "-88203"]])
        doc = DocxFile(path)
        doc.paragraphs()[0].apply([(11, 24, "{{insurer.claim_no}}")])
        out = doc.save(self.tmp / "out.docx")
        self.assertEqual(DocxFile(out).text, "Claim No.: {{insurer.claim_no}}")

    def test_save_preserves_every_other_zip_entry(self):
        path = self.make(["hello"])
        before = set(zipfile.ZipFile(path).namelist())
        out = DocxFile(path).save(self.tmp / "out.docx")
        self.assertEqual(set(zipfile.ZipFile(out).namelist()), before)

    def test_leading_whitespace_is_preserved(self):
        path = self.make(["     indented value"])
        doc = DocxFile(path)
        doc.paragraphs()[0].apply([(5, 19, "{{x}}")])
        out = doc.save(self.tmp / "out.docx")
        self.assertEqual(DocxFile(out).text, "     {{x}}")

    def test_multiple_edits_in_one_paragraph(self):
        path = self.make(["A and B"])
        doc = DocxFile(path)
        doc.paragraphs()[0].apply([(0, 1, "{{one}}"), (6, 7, "{{two}}")])
        self.assertEqual(doc.text, "{{one}} and {{two}}")

    def test_overlapping_edits_are_rejected(self):
        path = self.make(["abcdef"])
        para = DocxFile(path).paragraphs()[0]
        with self.assertRaises(ReplacementError):
            para.apply([(0, 4, "x"), (2, 6, "y")])

    def test_edit_past_the_end_is_rejected(self):
        path = self.make(["short"])
        para = DocxFile(path).paragraphs()[0]
        with self.assertRaises(ReplacementError):
            para.apply([(0, 99, "x")])

    def test_paragraph_indexes_are_stable_across_reopen(self):
        path = self.make(["one", "two", "three"])
        first = [p.index for p in DocxFile(path).paragraphs()]
        second = [p.index for p in DocxFile(path).paragraphs()]
        self.assertEqual(first, second)
        self.assertEqual(first, [0, 1, 2])


if __name__ == "__main__":
    unittest.main()


class DocumentFeatureTest(unittest.TestCase):
    """Markup this tool steps over has to be reported, not silently ignored."""

    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make_raw(self, body: str, name="raw.docx") -> Path:
        path = build(self.tmp / name, ["placeholder"])
        document = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:document {self.W}><w:body>{body}</w:body></w:document>"
        )
        data = {}
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                data[info.filename] = zf.read(info.filename)
        data["word/document.xml"] = document.encode("utf-8")
        with zipfile.ZipFile(path, "w") as zf:
            for filename, blob in data.items():
                zf.writestr(filename, blob)
        return path

    def test_reports_tracked_changes(self):
        path = self.make_raw(
            "<w:p><w:ins><w:r><w:t>inserted</w:t></w:r></w:ins></w:p>"
        )
        self.assertIn("tracked-changes", DocxFile(path).features())

    def test_reports_field_codes(self):
        path = self.make_raw(
            "<w:p><w:r><w:instrText> DATE </w:instrText></w:r></w:p>"
        )
        self.assertIn("field-codes", DocxFile(path).features())

    def test_field_code_text_is_not_treated_as_body_text(self):
        path = self.make_raw(
            "<w:p><w:r><w:t>Date: </w:t></w:r>"
            "<w:r><w:instrText> DATE \\@ \"M/d/yyyy\" </w:instrText></w:r></w:p>"
        )
        self.assertEqual(DocxFile(path).text, "Date: ")

    def test_a_plain_document_is_reported_clean(self):
        path = build(self.tmp / "clean.docx", ["nothing unusual here"])
        self.assertEqual(DocxFile(path).features(), set())
