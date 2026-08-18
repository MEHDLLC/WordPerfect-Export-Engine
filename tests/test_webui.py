"""The interview: the intake file that travels, and the screen in the office."""

import json
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from make_sample_corpus import main as make_corpus  # noqa: E402

from wpx import contacts, fields as F, intake_file, matters, webform  # noqa: E402
from wpx.catalog import build_catalog  # noqa: E402
from wpx.db import open_db  # noqa: E402
from wpx.docxml import DocxFile  # noqa: E402
from wpx.scan import scan_corpus  # noqa: E402
from wpx.serve import serve  # noqa: E402
from wpx.templatize import templatize_corpus  # noqa: E402


class SchemaTest(unittest.TestCase):
    """The form is generated from the dictionary, never hand-listed."""

    def setUp(self):
        self.schema = webform.schema()

    def test_every_field_a_person_types_is_asked(self):
        asked = {f["key"] for s in self.schema["sections"] for f in s["fields"]}
        expected = {k for k, f in F.BY_KEY.items() if not f.derived}
        self.assertEqual(asked, expected)

    def test_derived_fields_are_explained_not_asked(self):
        asked = {f["key"] for s in self.schema["sections"] for f in s["fields"]}
        derived = {d["key"] for d in self.schema["derived"]}
        self.assertTrue(derived)
        self.assertEqual(asked & derived, set())

    def test_providers_are_the_repeating_section(self):
        repeating = [s for s in self.schema["sections"] if s["repeating"]]
        self.assertEqual([s["key"] for s in repeating], ["provider"])
        self.assertEqual(repeating[0]["role"], "provider")

    def test_a_new_field_appears_without_touching_the_form(self):
        # The schema reads FIELDS at call time; nothing about the page enumerates
        # fields itself, which is what keeps the two interfaces in step.
        extra = F.Field("client.nickname", "Nickname", "client")
        F.FIELDS_backup = F.FIELDS
        try:
            F.FIELDS = F.FIELDS + (extra,)
            F.BY_KEY[extra.key] = extra
            keys = {f["key"] for s in webform.schema()["sections"] for f in s["fields"]}
            self.assertIn("client.nickname", keys)
        finally:
            F.FIELDS = F.FIELDS_backup
            del F.BY_KEY[extra.key]


class IntakeFileTest(unittest.TestCase):
    """Intake.html — one file in the client's folder, no install, no network."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.path = intake_file.build(
            self.tmp / "Whitfield" / "Intake.html",
            ref="2026-0114",
            values={"client.name": "Dana R. Whitfield", "client.dob": "04/17/1988"},
            parties=[{"provider.name": "Valley Regional Medical Center"}],
        )
        self.html = self.path.read_text(encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_nothing_is_fetched_from_the_internet(self):
        self.assertNotIn("http://", self.html)
        self.assertNotIn("https://", self.html)
        for tag in ("<link", "<img", "<iframe"):
            self.assertNotIn(tag, self.html)

    def test_it_is_one_file_with_the_questions_inside(self):
        self.assertIn('id="wpx-schema"', self.html)
        self.assertIn("Client name", self.html)
        self.assertIn("Amount billed (this provider)", self.html)

    def test_answers_travel_with_the_file(self):
        answers = intake_file.read_answers(self.path)
        self.assertEqual(answers["matter"], "2026-0114")
        self.assertEqual(answers["values"]["client.name"], "Dana R. Whitfield")
        self.assertEqual(answers["parties"][0]["provider.name"],
                         "Valley Regional Medical Center")

    def test_the_exported_json_reads_back_too(self):
        exported = self.tmp / "answers.json"
        exported.write_text(json.dumps({
            "matter": "2026-0114",
            "values": {"client.name": "Dana R. Whitfield"},
            "parties": [{"provider.name": "Larkspur Chiropractic Clinic"}],
        }), encoding="utf-8")
        answers = intake_file.read_answers(exported)
        self.assertEqual(answers["parties"][0]["provider.name"],
                         "Larkspur Chiropractic Clinic")

    def test_a_file_that_is_not_an_intake_says_so(self):
        stray = self.tmp / "letter.html"
        stray.write_text("<html><body>not this</body></html>", encoding="utf-8")
        with self.assertRaises(ValueError):
            intake_file.read_answers(stray)

    def test_a_returned_file_fills_a_matter_including_its_providers(self):
        con = open_db(self.tmp / "m.db")
        answers = matters.load_answers(self.path)
        matters.set_values(con, "2026-0114", answers["values"])
        for party in answers["parties"]:
            scope = matters.party_scope_for(con, "2026-0114", "provider",
                                            party.get("provider.name", ""))
            matters.set_values(con, "2026-0114", party, scope)
        self.assertEqual(matters.values(con, "2026-0114")["client.name"],
                         "Dana R. Whitfield")
        self.assertEqual(matters.scopes(con, "2026-0114", "provider:"), ["provider:1"])
        con.close()

    def test_client_details_cannot_break_out_of_the_page(self):
        nasty = "</script><script>stolen()</script>"
        path = intake_file.build(self.tmp / "x.html", ref="X", values={"client.name": nasty})
        text = path.read_text(encoding="utf-8")

        # Embedded data escapes a script block only by closing it, so the
        # answers block must contain no closing tag of its own. (An opening
        # <script inside a JSON string is inert — it is the closer that counts.)
        block = re.search(r'<script id="wpx-data"[^>]*>(.*?)</script>', text, re.S).group(1)
        self.assertNotIn("</script", block)
        # Anywhere it is shown to a reader it is text, not markup.
        self.assertIn("&lt;/script&gt;", text)
        # ...and the name still round-trips exactly as the client typed it.
        self.assertEqual(intake_file.read_answers(path)["values"]["client.name"], nasty)


class ServerTest(unittest.TestCase):
    """The office screen: same questions, backed by the matter database."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        converted = cls.tmp / "Converted"
        make_corpus(converted, echo=None)
        cls.db = cls.tmp / "web.db"
        con = open_db(cls.db)
        scan_corpus(con, converted, echo=None)
        build_catalog(con)
        contacts.build(con, echo=None)
        cls.templates = cls.tmp / "Templates"
        templatize_corpus(con, converted, cls.templates, echo=None, collapse_schedules=True)
        con.close()

        cls.outdir = cls.tmp / "Letters"
        cls.server = serve(cls.db, cls.templates, cls.outdir,
                           port=0, open_browser=False, echo=None)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def call(self, path, payload=None, key=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"X-WPX-Key": self.server.key if key is None else key,
                     "Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(request) as response:
            body = response.read().decode()
        return json.loads(body) if body.startswith(("{", "[")) else body

    # ---- the page ------------------------------------------------------

    def test_the_interview_page_carries_the_questions_and_the_answers(self):
        matters.set_values(open_db(self.db), "P-1", {"client.name": "Priya N. Raskin"})
        html = self.call(f"/matter?ref=P-1&k={self.server.key}")
        self.assertIn("Priya N. Raskin", html)
        self.assertIn('id="wpx-schema"', html)
        self.assertIn("Medical providers", html)

    def test_a_matter_name_cannot_inject_markup(self):
        page = self.call(f"/matter?ref=%3C/script%3E%3Cscript%3Ebad()%3C/script%3E"
                         f"&k={self.server.key}")
        block = re.search(r'<script id="wpx-data"[^>]*>(.*?)</script>', page, re.S).group(1)
        self.assertNotIn("</script", block)
        self.assertIn("&lt;/script&gt;", page)

    # ---- saving --------------------------------------------------------

    def test_saving_writes_values_and_allocates_party_scopes(self):
        result = self.call("/api/state?matter=P-2", {
            "values": {"client.name": "Casey Ito", "matter.file_no": "P-2"},
            "parties": [
                {"scope": None, "values": {"provider.name": "Valley Regional Medical Center"}},
                {"scope": None, "values": {"provider.name": "Larkspur Chiropractic Clinic"}},
            ],
        })
        self.assertEqual(result["parties"], ["provider:1", "provider:2"])
        con = open_db(self.db)
        self.assertEqual(matters.values(con, "P-2")["client.name"], "Casey Ito")
        self.assertEqual(matters.values(con, "P-2", "provider:2")["provider.name"],
                         "Larkspur Chiropractic Clinic")
        con.close()

    def test_removing_a_provider_in_the_form_removes_it_from_the_matter(self):
        self.call("/api/state?matter=P-3", {"values": {}, "parties": [
            {"scope": None, "values": {"provider.name": "One Clinic"}},
            {"scope": None, "values": {"provider.name": "Two Clinic"}},
        ]})
        self.call("/api/state?matter=P-3", {"values": {}, "parties": [
            {"scope": "provider:2", "values": {"provider.name": "Two Clinic"}},
        ]})
        con = open_db(self.db)
        self.assertEqual(matters.scopes(con, "P-3", "provider:"), ["provider:2"])
        con.close()

    # ---- contacts and readiness ---------------------------------------

    def test_the_carrier_picker_offers_the_address_book(self):
        rows = self.call("/api/contacts?role=insurer")
        names = {r["name"] for r in rows}
        self.assertIn("Summit Mutual Insurance Company", names)
        summit = next(r for r in rows if r["name"].startswith("Summit Mutual"))
        self.assertEqual(summit["values"]["insurer.city_state_zip"], "Bloomington, IL 61710")
        self.assertEqual(summit["person"], "Marcy Lindholm")

    def test_the_panel_names_what_is_still_missing(self):
        self.call("/api/state?matter=P-4", {"values": {"client.name": "Casey Ito"},
                                            "parties": []})
        forms = self.call("/api/forms?matter=P-4")
        self.assertTrue(forms)
        self.assertFalse(any(row["ready"] for row in forms))
        self.assertIn("insurer.claim_no", forms[0]["needs"] + forms[1]["needs"])

    def test_writing_produces_only_the_letters_it_promised(self):
        self.call("/api/state?matter=P-5", {
            "values": {
                "client.name": "Priya N. Raskin", "client.dob": "08/23/1991",
                "matter.file_no": "P-5", "matter.date_of_loss": "05/14/2026",
                "matter.dates_of_service": "05/14/2026 to present",
            },
            "parties": [{"scope": None, "values": {
                "provider.name": "Valley Regional Medical Center",
                "provider.attn": "Custodian of Records",
                "provider.address": "2500 Palmer-Wasilla Highway",
                "provider.city_state_zip": "Palmer, AK 99645",
                "provider.fax": "(907) 555-0188",
                "provider.account_no": "VR-1",
                "provider.dates_of_service": "05/14/2026 - 06/30/2026",
                "provider.billed_amount": "$19,704.14",
            }}],
        })
        forms = self.call("/api/forms?matter=P-5")
        ready = [row["template"] for row in forms if row["ready"]]
        self.assertTrue(ready, "expected the records requests to be ready")

        result = self.call("/api/generate?matter=P-5", {})
        written = sorted((self.outdir / "P-5").glob("*.docx"))
        self.assertEqual(result["count"], len(written))
        text = DocxFile(written[0]).text
        self.assertIn("Priya N. Raskin", text)
        self.assertIn("Valley Regional Medical Center", text)
        self.assertNotIn("MISSING", text)

    def test_a_matter_with_nothing_ready_is_told_so(self):
        self.call("/api/state?matter=P-6", {"values": {}, "parties": []})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call("/api/generate?matter=P-6", {})
        self.assertEqual(caught.exception.code, 404)

    # ---- staying local -------------------------------------------------

    def test_the_key_is_required(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call("/api/matters", key="wrong-key")
        self.assertEqual(caught.exception.code, 403)

    def test_a_request_from_another_host_is_refused(self):
        # A page on the firm's network resolving this machine by name would
        # arrive with a different Host header; only this computer may read it.
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/matters",
            headers={"Host": "firm-pc.local", "X-WPX-Key": self.server.key},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)

    def test_it_listens_only_on_the_loopback_address(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
