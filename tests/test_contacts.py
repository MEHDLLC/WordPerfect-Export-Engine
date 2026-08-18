"""The address book derived from the corpus."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from make_sample_corpus import main as make_corpus  # noqa: E402

from wpx import contacts, matters, render  # noqa: E402
from wpx.db import open_db  # noqa: E402
from wpx.docxml import DocxFile  # noqa: E402
from wpx.scan import scan_corpus  # noqa: E402
from wpx.templatize import templatize_corpus  # noqa: E402
from wpx.catalog import build_catalog  # noqa: E402


class ContactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.converted = cls.tmp / "Converted"
        make_corpus(cls.converted, echo=None)
        cls.con = open_db(cls.tmp / "contacts.db")
        scan_corpus(cls.con, cls.converted, echo=None)
        build_catalog(cls.con)
        cls.counts = contacts.build(cls.con, echo=None)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls._tmp.cleanup()

    def find(self, name, role=None):
        return contacts.find(self.con, name, role)

    def test_every_entity_the_firm_wrote_to_is_listed(self):
        names = {c["name"] for c in contacts.listing(self.con)}
        self.assertIn("Summit Mutual Insurance Company", names)
        self.assertIn("Cascade Casualty Group", names)
        self.assertIn("Valley Regional Medical Center", names)
        self.assertIn("Larkspur Chiropractic Clinic", names)

    def test_a_carrier_carries_its_intake_address(self):
        contact = self.find("Summit Mutual")
        values = contacts.values_for(self.con, contact["id"])
        self.assertEqual(values["insurer.address"], "P.O. Box 21188")
        self.assertEqual(values["insurer.city_state_zip"], "Bloomington, IL 61710")

    def test_people_are_listed_against_their_entity(self):
        contact = self.find("Summit Mutual")
        people = contacts.people_for(self.con, contact["id"])
        self.assertEqual([p["name"] for p in people], ["Marcy Lindholm"])
        self.assertEqual(people[0]["field_key"], "adjuster.name")

    def test_matter_facts_never_reach_a_contact_card(self):
        for contact in contacts.listing(self.con):
            keys = set(contacts.values_for(self.con, contact["id"]))
            for forbidden in ("insurer.claim_no", "insurer.policy_no",
                              "provider.account_no", "provider.billed_amount",
                              "provider.dates_of_service", "client.name"):
                self.assertNotIn(forbidden, keys, contact["name"])

    def test_a_provider_named_only_in_a_table_gets_no_address(self):
        # Summit Physical Therapy appears in one demand letter's bill schedule.
        # The table says it treated the client, not where to write to it.
        contact = self.find("Summit Physical")
        self.assertEqual(contacts.values_for(self.con, contact["id"]), {})

    def test_lookup_is_partial_and_case_insensitive(self):
        self.assertEqual(self.find("summit mutual")["name"],
                         "Summit Mutual Insurance Company")
        self.assertEqual(self.find("VALLEY")["name"], "Valley Regional Medical Center")

    def test_an_ambiguous_name_is_refused_not_guessed(self):
        with self.assertRaises(contacts.Ambiguous):
            self.find("summit")
        self.assertEqual(self.find("summit", "insurer")["name"],
                         "Summit Mutual Insurance Company")

    def test_an_unknown_name_raises(self):
        with self.assertRaises(contacts.NotFound):
            self.find("Nowhere Mutual")

    def test_building_twice_does_not_duplicate(self):
        before = len(contacts.listing(self.con))
        contacts.build(self.con, echo=None)
        self.assertEqual(len(contacts.listing(self.con)), before)

    def test_a_manual_contact_survives_a_rebuild(self):
        contacts.upsert(self.con, "provider", "Chugach Imaging",
                        {"provider.address": "77 Northern Lights Blvd",
                         "provider.city_state_zip": "Anchorage, AK 99503"},
                        people=["Records Department"])
        contacts.build(self.con, echo=None)
        contact = self.find("Chugach")
        self.assertEqual(contact["source"], "manual")
        self.assertEqual(contacts.values_for(self.con, contact["id"])["provider.address"],
                         "77 Northern Lights Blvd")

    def test_a_manual_edit_cannot_smuggle_in_matter_facts(self):
        contacts.upsert(self.con, "provider", "Chugach Imaging",
                        {"provider.account_no": "ACCT-1", "provider.fax": "(907) 555-0100"})
        values = contacts.values_for(self.con, self.find("Chugach")["id"])
        self.assertNotIn("provider.account_no", values)
        self.assertEqual(values["provider.fax"], "(907) 555-0100")
        self.assertEqual(contacts.rejected_keys("provider", {"provider.account_no": "x"}),
                         ["provider.account_no"])

    def test_an_address_block_names_a_person_by_default(self):
        block = contacts.address_block(self.con, self.find("Summit Mutual"))
        self.assertEqual(block["adjuster.name"], "Marcy Lindholm")
        self.assertEqual(block["insurer.name"], "Summit Mutual Insurance Company")

    def test_a_different_person_can_be_named(self):
        block = contacts.address_block(self.con, self.find("Summit Mutual"), "Ken Ober")
        self.assertEqual(block["adjuster.name"], "Ken Ober")


class AddressMatterTest(unittest.TestCase):
    """Addressing a matter from a contact, and generating from it."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        converted = cls.tmp / "Converted"
        make_corpus(converted, echo=None)
        cls.con = open_db(cls.tmp / "address.db")
        scan_corpus(cls.con, converted, echo=None)
        build_catalog(cls.con)
        contacts.build(cls.con, echo=None)
        cls.templates = cls.tmp / "Templates"
        templatize_corpus(cls.con, converted, cls.templates, echo=None,
                          collapse_schedules=True)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls._tmp.cleanup()

    def address(self, ref, role, name, person=None):
        contact = contacts.find(self.con, name, role)
        values = contacts.address_block(self.con, contact, person)
        scope = ("" if role == "insurer"
                 else matters.party_scope_for(self.con, ref, role, contact["name"]))
        return matters.set_values(self.con, ref, values, scope)

    def test_addressing_fills_the_carrier_block(self):
        matters.set_values(self.con, "A-1", {"client.name": "Priya N. Raskin"})
        self.address("A-1", "insurer", "Summit Mutual")
        values = matters.values(self.con, "A-1")
        self.assertEqual(values["insurer.name"], "Summit Mutual Insurance Company")
        self.assertEqual(values["insurer.city_state_zip"], "Bloomington, IL 61710")
        self.assertEqual(values["adjuster.name"], "Marcy Lindholm")

    def test_adding_the_same_provider_twice_updates_one_party(self):
        self.address("A-2", "provider", "Valley Regional")
        self.address("A-2", "provider", "Valley Regional", person="Release of Information")
        self.assertEqual(matters.scopes(self.con, "A-2", "provider:"), ["provider:1"])
        values = matters.values(self.con, "A-2", "provider:1")
        self.assertEqual(values["provider.attn"], "Release of Information")

    def test_two_different_providers_become_two_parties(self):
        self.address("A-3", "provider", "Valley Regional")
        self.address("A-3", "provider", "Larkspur")
        self.assertEqual(matters.scopes(self.con, "A-3", "provider:"),
                         ["provider:1", "provider:2"])

    def test_a_records_request_comes_out_addressed(self):
        matters.set_values(self.con, "A-4", {
            "client.name": "Priya N. Raskin",
            "client.dob": "08/23/1991",
            "matter.date_of_loss": "05/14/2026",
            "matter.dates_of_service": "05/14/2026 to present",
        })
        self.address("A-4", "provider", "Larkspur")
        results = render.generate(
            self.con, "A-4", self.templates, self.tmp / "Out",
            only=["ML Larkspur Chiropractic"], echo=None,
        )
        text = DocxFile(results[0].path).text
        self.assertIn("Larkspur Chiropractic Clinic", text)
        self.assertIn("781 Bogard Road, Suite B", text)
        self.assertIn("Wasilla, AK 99654", text)
        self.assertIn("Attn:  Medical Records", text)
        self.assertIn("(907) 555-0170", text)
        # Only the genuinely matter-specific field is still open.
        self.assertEqual(results[0].missing, ["provider.account_no"])


if __name__ == "__main__":
    unittest.main()
