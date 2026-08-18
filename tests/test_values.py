"""Dates and names: the two value shapes that break 'enter it once'."""

import unittest
from datetime import date

from wpx import fields as F
from wpx.values import (
    LONG, SHORT, canonical, date_style, derive, format_date, mask, parse_date,
    render_date, split_name,
)


class DateTest(unittest.TestCase):
    def test_parses_the_spellings_letters_actually_use(self):
        for text in ("01/26/2026", "1/26/2026", "January 26, 2026", "Jan 26, 2026",
                     "Feb. 3, 2026", "Sept. 5, 2026", "2026-01-26", "01-26-2026"):
            self.assertIsNotNone(parse_date(text), text)

    def test_rejects_text_that_merely_contains_a_date(self):
        self.assertIsNone(parse_date("05/14/2026 to present"))
        self.assertIsNone(parse_date("no date here"))

    def test_two_spellings_share_one_catalog_key(self):
        self.assertEqual(canonical("01/26/2026"), canonical("January 26, 2026"))

    def test_money_spellings_share_one_catalog_key(self):
        self.assertEqual(canonical("$18,412.60"), canonical("$ 18412.6"))

    def test_style_is_read_back_from_the_original_text(self):
        self.assertEqual(date_style("01/26/2026"), SHORT)
        self.assertEqual(date_style("January 26, 2026"), LONG)

    def test_one_stored_date_renders_in_either_spelling(self):
        self.assertEqual(render_date("1/26/2026", LONG), "January 26, 2026")
        self.assertEqual(render_date("January 26, 2026", SHORT), "01/26/2026")

    def test_a_non_date_is_left_alone(self):
        self.assertIsNone(render_date("on file", LONG))

    def test_long_format_has_no_leading_zero_on_the_day(self):
        self.assertEqual(format_date(date(2026, 3, 6), LONG), "March 6, 2026")


class NameTest(unittest.TestCase):
    def test_splits_a_middle_initial(self):
        self.assertEqual(split_name("Dana R. Whitfield"),
                         {"first_name": "Dana", "last_name": "Whitfield"})

    def test_strips_honorifics_and_suffixes(self):
        self.assertEqual(split_name("Ms. Marcy Lindholm")["first_name"], "Marcy")
        self.assertEqual(split_name("Owen T. Marchetti, Jr.")["last_name"], "Marchetti")

    def test_derives_parts_from_a_full_name(self):
        out = derive({"client.name": "Dana R. Whitfield"})
        self.assertEqual(out["client.first_name"], "Dana")
        self.assertEqual(out["client.last_name"], "Whitfield")

    def test_an_explicit_value_is_never_overwritten(self):
        out = derive({"client.name": "Dana R. Whitfield", "client.first_name": "Dee"})
        self.assertEqual(out["client.first_name"], "Dee")

    def test_organizations_are_not_given_surnames(self):
        out = derive({"provider.name": "Valley Regional Medical Center"})
        self.assertNotIn("provider.last_name", out)

    def test_derived_fields_are_marked_as_such(self):
        self.assertTrue(F.BY_KEY["client.last_name"].derived)
        self.assertFalse(F.BY_KEY["client.name"].derived)


class MaskTest(unittest.TestCase):
    def test_masks_both_ssn_spellings(self):
        self.assertEqual(mask("SSN 574-88-1029"), "SSN ***-**-####")
        self.assertEqual(mask("574881029"), "***-**-####")

    def test_leaves_other_numbers_alone(self):
        self.assertEqual(mask("Claim AK-4471-88203"), "Claim AK-4471-88203")
        self.assertEqual(mask("(907) 555-0142"), "(907) 555-0142")


if __name__ == "__main__":
    unittest.main()
