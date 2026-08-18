"""The whole path: scan a corpus, templatize it, fill one matter, generate."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from make_sample_corpus import main as make_corpus  # noqa: E402

from wpx import forms, matters, render  # noqa: E402
from wpx.catalog import CONSTANT, VARIABLE, build_catalog, classify, kinds  # noqa: E402
from wpx.db import open_db  # noqa: E402
from wpx.docxml import DocxFile  # noqa: E402
from wpx.scan import scan_corpus  # noqa: E402
from wpx.templatize import templatize_corpus  # noqa: E402


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.converted = cls.tmp / "Converted"
        make_corpus(cls.converted, echo=None)
        cls.con = open_db(cls.tmp / "test.db")
        cls.scan_totals = scan_corpus(cls.con, cls.converted, echo=None)
        cls.catalog_summary = build_catalog(cls.con)
        cls.templates = cls.tmp / "Templates"
        cls.results = templatize_corpus(
            cls.con, cls.converted, cls.templates, echo=None
        )

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls._tmp.cleanup()

    # --- scan -------------------------------------------------------------
    def test_scan_reads_the_whole_corpus(self):
        self.assertEqual(self.scan_totals["docs"], 8)
        self.assertEqual(self.scan_totals["failed"], 0)

    def test_nearly_every_value_is_mapped_to_a_field(self):
        share = self.scan_totals["named"] / self.scan_totals["hits"]
        self.assertGreater(share, 0.95, f"only {share:.0%} of values were recognized")

    # --- catalog ----------------------------------------------------------
    def test_letterhead_is_classified_as_firm_boilerplate(self):
        by_value = kinds(self.con)
        self.assertEqual(by_value["northstar injury law, llc"], CONSTANT)
        self.assertEqual(by_value["r. cole halvorsen"], CONSTANT)

    def test_claim_numbers_are_classified_as_per_matter_variables(self):
        self.assertEqual(kinds(self.con)["ak-4471-88203"], VARIABLE)

    # --- templatize -------------------------------------------------------
    def test_templates_keep_the_letterhead_and_parameterize_the_rest(self):
        text = DocxFile(self.templates / "L LOR Summit Mutual.docx").text
        self.assertIn("NORTHSTAR INJURY LAW, LLC", text)
        self.assertIn("{{client.name}}", text)
        self.assertIn("{{insurer.claim_no}}", text)
        self.assertNotIn("Dana R. Whitfield", text)
        self.assertNotIn("AK-4471-88203", text)

    def test_a_name_split_across_runs_is_still_replaced(self):
        text = DocxFile(self.templates / "L LOR Summit Mutual.docx").text
        self.assertIn("this office represents {{client.name}} for injuries", text)

    def test_first_name_references_use_the_first_name_field(self):
        text = DocxFile(self.templates / "L LOR Summit Mutual.docx").text
        self.assertIn("{{client.first_name}} has not given", text)

    def test_each_template_reports_what_it_left_alone(self):
        sidecar = (self.templates / "L LOR Summit Mutual.fields.json")
        self.assertTrue(sidecar.exists())

    def test_nothing_was_left_unmapped_in_this_corpus(self):
        unresolved = [
            item for result in self.results for item in result.left_in_place
            if item["reason"] in ("unmapped", "low-confidence", "split-across-markup")
        ]
        self.assertEqual(unresolved, [])

    # --- forms ------------------------------------------------------------
    def test_duplicate_letters_collapse_into_one_form(self):
        report = forms.dedupe(self.templates, echo=None)
        self.assertEqual(report["templates"], 8)
        self.assertEqual(report["distinct"], 4)

    # --- matter and generation -------------------------------------------
    def test_one_intake_generates_the_whole_packet(self):
        matters.set_values(self.con, "2026-9001", {
            "client.name": "Priya N. Raskin",
            "client.first_name": "Priya",
            "client.dob": "08/23/1991",
            "matter.file_no": "2026-9001",
            "matter.date_of_loss": "05/14/2026",
            "matter.dates_of_service": "05/14/2026 to present",
            "insurer.name": "Summit Mutual Insurance Company",
            "insurer.claim_no": "AK-5520-11947",
            "insurer.policy_no": "SM 8820 431 C",
            "insurer.address": "P.O. Box 21188",
            "insurer.city_state_zip": "Bloomington, IL 61710",
            "adjuster.name": "Marcy Lindholm",
            "insured.name": "Garrett Fenwick",
            "demand.amount": "$120,000.00",
            "demand.medicals_total": "$31,204.85",
            "demand.lost_wages": "$7,600.00",
            "demand.response_deadline": "October 1, 2026",
        })
        for provider in (
            {"provider.name": "Valley Regional Medical Center",
             "provider.attn": "Custodian of Records",
             "provider.address": "2500 Palmer-Wasilla Highway",
             "provider.city_state_zip": "Palmer, AK 99645",
             "provider.fax": "(907) 555-0188",
             "provider.account_no": "VR-991233"},
            {"provider.name": "Larkspur Chiropractic Clinic",
             "provider.attn": "Medical Records",
             "provider.address": "781 Bogard Road, Suite B",
             "provider.city_state_zip": "Wasilla, AK 99654",
             "provider.fax": "(907) 555-0170",
             "provider.account_no": "LC-55810"},
        ):
            scope = matters.next_scope(self.con, "2026-9001", "provider")
            matters.set_values(self.con, "2026-9001", provider, scope)

        outdir = self.tmp / "Letters"
        forms_dir = self.tmp / "Forms"
        forms.dedupe(self.templates, forms_dir, echo=None)
        results = render.generate(
            self.con, "2026-9001", forms_dir, outdir, echo=None
        )
        # Four distinct forms, one of which is generated once per provider.
        self.assertEqual(len(results), 5)
        self.assertEqual([r.missing for r in results], [[]] * 5)

        demand = next(p for p in outdir.glob("*.docx") if "Demand" in p.name)
        text = DocxFile(demand).text
        self.assertIn("Priya N. Raskin", text)
        self.assertIn("AK-5520-11947", text)
        self.assertIn("Priya is prepared to resolve", text)
        self.assertNotIn("{{", text)
        self.assertNotIn("MISSING", text)

        names = sorted(p.name for p in outdir.glob("*.docx"))
        self.assertTrue(any("Valley Regional Medical Center" in n for n in names))
        self.assertTrue(any("Larkspur Chiropractic Clinic" in n for n in names))

    def test_a_missing_value_is_marked_not_silently_blank(self):
        matters.set_values(self.con, "2026-9002", {"client.name": "Casey Ito"})
        outdir = self.tmp / "Partial"
        results = render.generate(
            self.con, "2026-9002", self.templates, outdir,
            only=["L LOR Summit Mutual"], echo=None,
        )
        text = DocxFile(results[0].path).text
        self.assertIn("Casey Ito", text)
        self.assertIn("[MISSING: Claim number]", text)
        self.assertIn("insurer.claim_no", results[0].missing)

    def test_check_reports_what_a_matter_still_needs(self):
        supplied = render.default_values(self.con).keys()
        missing = matters.missing_for(self.con, "2026-9002", supplied=supplied)
        self.assertIn("insurer.claim_no", missing["L LOR Summit Mutual.docx"])
        self.assertNotIn("matter.letter_date", missing["L LOR Summit Mutual.docx"])


if __name__ == "__main__":
    unittest.main()


class ClassificationTest(unittest.TestCase):
    """The dictionary outranks the evidence — see wpx/catalog.py."""

    def test_a_per_matter_field_stays_variable_even_if_it_is_everywhere(self):
        self.assertEqual(classify("insurer.claim_no", doc_count=40, total_docs=40), VARIABLE)

    def test_firm_fields_are_constant_even_if_rare(self):
        self.assertEqual(classify("firm.name", doc_count=1, total_docs=40), CONSTANT)

    def test_an_unnamed_value_in_every_letter_is_treated_as_boilerplate(self):
        self.assertEqual(classify(None, doc_count=40, total_docs=40), CONSTANT)

    def test_an_unnamed_value_in_a_few_letters_needs_review(self):
        from wpx.catalog import UNKNOWN

        self.assertEqual(classify(None, doc_count=2, total_docs=40), UNKNOWN)
