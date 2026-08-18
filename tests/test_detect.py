"""Field detection: labels, letter structure, and propagation through the body."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wpx import fields as F
from wpx.docxml import DocxFile
from wpx.minidocx import build
from wpx.scan import detect_document

LETTER = [
    "NORTHSTAR INJURY LAW, LLC",
    "R. Cole Halvorsen, Attorney at Law",
    "412 W. Denali Avenue, Suite 300",
    "Anchorage, AK 99501",
    "Phone: (907) 555-0142   Fax: (907) 555-0143",
    "",
    "February 3, 2026",
    "",
    "Marcy Lindholm",
    "Summit Mutual Insurance Company",
    "P.O. Box 21188",
    "Bloomington, IL 61710",
    "",
    "Re:  Our Client:  Dana R. Whitfield",
    "     Claim No.:  AK-4471-88203",
    "     Date of Loss:  01/26/2026",
    "",
    "Dear Marcy Lindholm:",
    "",
    ["This office represents ", "Dana ", "R. Whitfield for injuries."],
    "Dana has not given a recorded statement.",
]

RECORDS_REQUEST = [
    "NORTHSTAR INJURY LAW, LLC",
    "412 W. Denali Avenue, Suite 300",
    "",
    "February 10, 2026",
    "",
    "Valley Regional Medical Center",
    "Attn:  Custodian of Records",
    "2500 Palmer-Wasilla Highway",
    "Palmer, AK 99645",
    "Fax:  (907) 555-0188",
    "",
    "Re:  Patient:  Dana R. Whitfield",
    "     Date of Birth:  04/17/1988",
    "",
    "To Whom It May Concern:",
]


class DetectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def hits(self, paragraphs, name="letter.docx"):
        path = build(self.tmp / name, paragraphs)
        return detect_document(DocxFile(path).paragraphs())

    def values_for(self, hits, key):
        return [h.value for h in hits if h.field_key == key]

    def test_labels_map_to_canonical_fields(self):
        hits = self.hits(LETTER)
        self.assertIn("AK-4471-88203", self.values_for(hits, "insurer.claim_no"))
        self.assertIn("01/26/2026", self.values_for(hits, "matter.date_of_loss"))
        self.assertIn("Dana R. Whitfield", self.values_for(hits, "client.name"))

    def test_letterhead_becomes_firm_fields(self):
        hits = self.hits(LETTER)
        self.assertIn("NORTHSTAR INJURY LAW, LLC", self.values_for(hits, "firm.name"))
        self.assertIn("R. Cole Halvorsen", self.values_for(hits, "firm.attorney"))
        self.assertIn("(907) 555-0142", self.values_for(hits, "firm.phone"))
        self.assertIn("(907) 555-0143", self.values_for(hits, "firm.fax"))

    def test_addressee_block_identifies_the_carrier(self):
        hits = self.hits(LETTER)
        self.assertIn("Summit Mutual Insurance Company", self.values_for(hits, "insurer.name"))
        self.assertIn("Marcy Lindholm", self.values_for(hits, "adjuster.name"))
        self.assertIn("P.O. Box 21188", self.values_for(hits, "insurer.address"))
        self.assertIn("Bloomington, IL 61710", self.values_for(hits, "insurer.city_state_zip"))

    def test_addressee_block_identifies_a_medical_provider(self):
        hits = self.hits(RECORDS_REQUEST, "records.docx")
        self.assertIn("Valley Regional Medical Center", self.values_for(hits, "provider.name"))
        self.assertIn("Custodian of Records", self.values_for(hits, "provider.attn"))
        self.assertIn("(907) 555-0188", self.values_for(hits, "provider.fax"))
        self.assertEqual(self.values_for(hits, "insurer.name"), [])

    def test_letter_date_is_distinct_from_the_date_of_loss(self):
        hits = self.hits(LETTER)
        self.assertIn("February 3, 2026", self.values_for(hits, "matter.letter_date"))
        self.assertNotIn("February 3, 2026", self.values_for(hits, "matter.date_of_loss"))

    def test_client_name_propagates_into_the_body_across_runs(self):
        hits = self.hits(LETTER)
        self.assertGreaterEqual(len(self.values_for(hits, "client.name")), 2)

    def test_bare_first_name_maps_to_the_first_name_field(self):
        hits = self.hits(LETTER)
        self.assertIn("Dana", self.values_for(hits, "client.first_name"))

    def test_hits_never_overlap(self):
        hits = self.hits(LETTER)
        for i, a in enumerate(hits):
            for b in hits[i + 1:]:
                self.assertFalse(a.overlaps(b), f"{a} overlaps {b}")

    def test_every_detected_key_exists_in_the_dictionary(self):
        for paragraphs in (LETTER, RECORDS_REQUEST):
            for hit in self.hits(paragraphs, "x.docx"):
                if hit.field_key:
                    self.assertIn(hit.field_key, F.BY_KEY)


class LabelTest(unittest.TestCase):
    def test_label_aliases(self):
        cases = {
            "Claim No.": "insurer.claim_no",
            "CLAIM #": "insurer.claim_no",
            "D.O.L.": "matter.date_of_loss",
            "Date of Accident": "matter.date_of_loss",
            "Our File No.": "matter.file_no",
            "Your Insured": "insured.name",
            "Patient": "client.name",
            "Adjustor": "adjuster.name",
        }
        for label, key in cases.items():
            self.assertEqual(F.lookup_label(label), key, label)

    def test_unknown_labels_are_not_guessed(self):
        self.assertIsNone(F.lookup_label("Please note the following"))


if __name__ == "__main__":
    unittest.main()
