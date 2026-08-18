"""{{#each}} blocks: one row per party, inside one letter."""

import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from wpx import fields as F, render, repeat, templatize
from wpx.docxml import DocxFile
from wpx.minidocx import Table, build

PROVIDERS = [
    {"provider.name": "Valley Regional Medical Center",
     "provider.billed_amount": "$12,480.00"},
    {"provider.name": "Larkspur Chiropractic Clinic",
     "provider.billed_amount": "$5,932.60"},
    {"provider.name": "Summit Physical Therapy",
     "provider.billed_amount": "$7,201.00"},
]


class RepeatTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def schedule_template(self, name="t.docx", rows=1):
        data_row = ["{{#each provider}}{{provider.name}}", "{{provider.billed_amount}}"]
        return build(self.tmp / name, [
            "MEDICAL SPECIALS",
            Table([["Provider", "Amount Billed"], data_row][: 1 + rows]),
            "Total: {{demand.medicals_total}}",
        ])

    def render(self, template, values, parties, on_missing=render.MARK):
        return render.render(template, values, self.tmp / "out.docx", on_missing, parties)

    def test_a_marked_row_repeats_once_per_party(self):
        template = self.schedule_template()
        result = self.render(template, {"demand.medicals_total": "$25,613.60"},
                             {"provider": PROVIDERS})
        text = DocxFile(result.path).text
        self.assertEqual(result.repeated, 3)
        for party in PROVIDERS:
            self.assertIn(party["provider.name"], text)
            self.assertIn(party["provider.billed_amount"], text)
        self.assertIn("Total: $25,613.60", text)

    def test_the_repeated_rows_are_real_table_rows(self):
        template = self.schedule_template()
        result = self.render(template, {}, {"provider": PROVIDERS})
        body = zipfile.ZipFile(result.path).read("word/document.xml").decode()
        table = body[body.index("<w:tbl>"): body.index("</w:tbl>")]
        self.assertEqual(table.count("<w:tr>"), 4)      # header + one per provider
        self.assertEqual(table.count("<w:tcPr"), 8)     # cell formatting survives cloning

    def test_the_marker_never_reaches_the_finished_letter(self):
        template = self.schedule_template()
        result = self.render(template, {}, {"provider": PROVIDERS})
        self.assertNotIn("#each", DocxFile(result.path).text)

    def test_a_role_with_no_parties_leaves_one_visible_gap(self):
        template = self.schedule_template()
        result = self.render(template, {}, {})
        text = DocxFile(result.path).text
        self.assertNotIn("#each", text)
        self.assertIn("[MISSING: Provider name]", text)
        self.assertIn("provider.name", result.missing)

    def test_a_block_can_span_several_rows(self):
        template = build(self.tmp / "multi.docx", [
            Table([
                ["Provider", "Amount"],
                ["{{#each provider}}{{provider.name}}", "{{provider.billed_amount}}"],
                ["Records requested from this provider{{/each}}", ""],
            ]),
        ])
        result = self.render(template, {}, {"provider": PROVIDERS[:2]})
        body = zipfile.ZipFile(result.path).read("word/document.xml").decode()
        table = body[body.index("<w:tbl>"): body.index("</w:tbl>")]
        self.assertEqual(table.count("<w:tr>"), 5)  # header + two rows per provider
        self.assertEqual(DocxFile(result.path).text.count("Records requested"), 2)

    def test_a_paragraph_can_repeat_without_a_table(self):
        template = build(self.tmp / "paras.docx", [
            "Enclosed authorizations:",
            "{{#each provider}}  - {{provider.name}}",
            "Please call with questions.",
        ])
        result = self.render(template, {}, {"provider": PROVIDERS})
        lines = [p.text for p in DocxFile(result.path).paragraphs()]
        self.assertEqual(lines, [
            "Enclosed authorizations:",
            "  - Valley Regional Medical Center",
            "  - Larkspur Chiropractic Clinic",
            "  - Summit Physical Therapy",
            "Please call with questions.",
        ])

    def test_each_row_gets_its_own_derived_values(self):
        template = build(self.tmp / "names.docx", [
            "{{#each provider}}{{adjuster.last_name}} / {{provider.name}}",
        ])
        result = self.render(template, {"adjuster.name": "Marcy Lindholm"},
                             {"provider": PROVIDERS[:1]})
        self.assertIn("Lindholm / Valley Regional", DocxFile(result.path).text)

    def test_roles_are_read_back_from_a_template(self):
        self.assertEqual(render.repeat_roles(self.schedule_template()), {"provider"})
        plain = build(self.tmp / "plain.docx", ["no markers here"])
        self.assertEqual(render.repeat_roles(plain), set())

    def test_a_repeated_role_does_not_also_fan_out_the_letter(self):
        # The demand letter consumes its providers in the table, so it stays
        # one letter; a records request has no marker, so it fans out.
        keys = {"provider.name", "client.name"}
        self.assertIsNone(render._party_role(keys, {"provider"}))
        self.assertEqual(render._party_role(keys, set()), "provider")


class ScheduleDetectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def frozen_schedule(self, second_row=None):
        second_row = second_row or ["{{provider.name}}", "{{provider.billed_amount}}"]
        return build(self.tmp / "frozen.docx", [
            Table([
                ["Provider", "Amount Billed"],
                ["{{provider.name}}", "{{provider.billed_amount}}"],
                second_row,
            ]),
        ])

    def test_identical_rows_are_reported_as_a_schedule(self):
        doc = DocxFile(self.frozen_schedule())
        found = templatize.find_schedules(doc)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["role"], "provider")
        self.assertEqual(found[0]["fields"],
                         ["provider.billed_amount", "provider.name"])

    def test_rows_with_different_fields_are_left_alone(self):
        doc = DocxFile(self.frozen_schedule(["{{demand.amount}}", "{{client.name}}"]))
        self.assertEqual(templatize.find_schedules(doc), [])

    def test_collapsing_keeps_one_row_and_marks_it(self):
        doc = DocxFile(self.frozen_schedule())
        schedule = templatize.find_schedules(doc)[0]
        self.assertTrue(templatize.collapse_schedule(schedule))
        out = doc.save(self.tmp / "collapsed.docx")
        text = DocxFile(out).text
        self.assertIn("{{#each provider}}", text)
        self.assertEqual(text.count("{{provider.name}}"), 1)
        self.assertEqual(repeat.roles(DocxFile(out)), {"provider"})

    def test_a_collapsed_template_renders_one_row_per_party(self):
        doc = DocxFile(self.frozen_schedule())
        templatize.collapse_schedule(templatize.find_schedules(doc)[0])
        template = doc.save(self.tmp / "collapsed.docx")
        result = render.render(template, {}, self.tmp / "out.docx",
                               render.BLANK, {"provider": PROVIDERS})
        self.assertEqual(result.repeated, 3)
        self.assertIn("Summit Physical Therapy", DocxFile(result.path).text)


if __name__ == "__main__":
    unittest.main()
