"""The matter store: entered once, overlaid per party."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wpx import matters
from wpx.db import open_db


class MatterStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.con = open_db(self.tmp / "m.db")

    def tearDown(self):
        self.con.close()
        self._tmp.cleanup()

    def test_values_round_trip(self):
        matters.set_values(self.con, "A-1", {"client.name": "Casey Ito"})
        self.assertEqual(matters.values(self.con, "A-1")["client.name"], "Casey Ito")

    def test_unknown_fields_are_reported_not_stored(self):
        stored, unknown = matters.set_values(
            self.con, "A-1", {"client.name": "Casey Ito", "client.favourite_colour": "teal"}
        )
        self.assertEqual(stored, 1)
        self.assertEqual(unknown, ["client.favourite_colour"])
        self.assertNotIn("client.favourite_colour", matters.values(self.con, "A-1"))

    def test_setting_a_value_again_replaces_it(self):
        matters.set_values(self.con, "A-1", {"insurer.claim_no": "OLD"})
        matters.set_values(self.con, "A-1", {"insurer.claim_no": "NEW"})
        self.assertEqual(matters.values(self.con, "A-1")["insurer.claim_no"], "NEW")

    def test_a_party_scope_overlays_the_matter(self):
        matters.set_values(self.con, "A-1", {"client.name": "Casey Ito"})
        matters.set_values(self.con, "A-1", {"provider.name": "Valley Regional"}, "provider:1")
        matters.set_values(self.con, "A-1", {"provider.name": "Larkspur"}, "provider:2")
        first = matters.values(self.con, "A-1", "provider:1")
        second = matters.values(self.con, "A-1", "provider:2")
        self.assertEqual(first["provider.name"], "Valley Regional")
        self.assertEqual(second["provider.name"], "Larkspur")
        # matter-level facts are visible from inside every party scope
        self.assertEqual(first["client.name"], "Casey Ito")

    def test_next_scope_numbers_parties_in_order(self):
        self.assertEqual(matters.next_scope(self.con, "A-1", "provider"), "provider:1")
        matters.set_values(self.con, "A-1", {"provider.name": "One"}, "provider:1")
        self.assertEqual(matters.next_scope(self.con, "A-1", "provider"), "provider:2")

    def test_unscoped_values_ignore_party_data(self):
        matters.set_values(self.con, "A-1", {"provider.name": "Valley Regional"}, "provider:1")
        self.assertNotIn("provider.name", matters.values(self.con, "A-1"))

    def test_missing_matter_raises(self):
        with self.assertRaises(KeyError):
            matters.values(self.con, "nope")

    def test_intake_csv_round_trip(self):
        path = self.tmp / "intake.csv"
        path.write_text('client.name,Casey Ito\ninsurer.claim_no,"AK-1,2"\n', encoding="utf-8")
        loaded = matters.load_file(path)
        self.assertEqual(loaded["client.name"], "Casey Ito")
        self.assertEqual(loaded["insurer.claim_no"], "AK-1,2")

    def test_intake_json_accepts_the_grouped_layout(self):
        path = self.tmp / "intake.json"
        path.write_text('{"client": {"client.name": "Casey Ito"}}', encoding="utf-8")
        self.assertEqual(matters.load_file(path), {"client.name": "Casey Ito"})

    def test_blank_intake_covers_every_field_that_must_be_typed(self):
        from wpx import fields as F

        sheet = matters.flatten(matters.intake_blank())
        self.assertEqual(set(sheet), {k for k, f in F.BY_KEY.items() if not f.derived})

    def test_blank_intake_omits_derived_fields(self):
        sheet = matters.flatten(matters.intake_blank())
        self.assertNotIn("client.first_name", sheet)
        self.assertNotIn("client.last_name", sheet)

    def test_a_stored_full_name_supplies_its_parts(self):
        from wpx.values import derive

        matters.set_values(self.con, "A-1", {"client.name": "Dana R. Whitfield"})
        supplied = derive(matters.values(self.con, "A-1"))
        self.assertEqual(supplied["client.first_name"], "Dana")
        self.assertEqual(supplied["client.last_name"], "Whitfield")


if __name__ == "__main__":
    unittest.main()


class SchemaTest(unittest.TestCase):
    """A database written by an earlier version keeps its audit trail."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_an_older_docs_table_gains_the_flags_column(self):
        import sqlite3

        path = self.tmp / "old.db"
        old = sqlite3.connect(path)
        old.execute(
            "CREATE TABLE docs (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, "
            "name TEXT NOT NULL, sha256 TEXT, para_count INTEGER, scanned_at TEXT)"
        )
        old.execute("INSERT INTO docs(path, name) VALUES ('/x/a.docx', 'a.docx')")
        old.commit()
        old.close()

        con = open_db(path)
        columns = {r[1] for r in con.execute("PRAGMA table_info(docs)")}
        self.assertIn("flags", columns)
        # the row that was already there survives the upgrade
        self.assertEqual(con.execute("SELECT name FROM docs").fetchone()[0], "a.docx")
        con.close()

    def test_the_database_is_not_world_readable(self):
        import os
        import stat

        path = self.tmp / "perm.db"
        open_db(path).close()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode & 0o077, 0, f"mode {mode:o} is readable by others")
