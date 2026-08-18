"""The office app: the interview screen in a browser, on this machine only.

    python3 -m wpx serve

opens a page at 127.0.0.1 that asks the same questions as Intake.html, but
backed by the matter database — so the carrier can be filled from the contact
list, the panel can say which letters are ready, and the packet can be written
without leaving the page.

Deliberately small: the standard library's own HTTP server, bound to the
loopback address, no framework and nothing to install. Client data never
leaves the machine.

Two habits keep a local tool honest about that:

* the address is 127.0.0.1, never 0.0.0.0, so nothing on the firm's network
  can reach it;
* every request carries a key printed with the URL at start-up. Any web page
  in any tab can POST to localhost, and this database holds dates of birth and
  claim numbers — the key is what stops a passing website from reading them.
"""

from __future__ import annotations

import html
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import contacts, matters, render
from .db import open_db
from .webform import json_for_html, page

HOST_JS = r"""
(function () {
  const schema = JSON.parse(document.getElementById('wpx-schema').textContent);
  const boot = JSON.parse(document.getElementById('wpx-data').textContent);
  const KEY = boot.key;

  function api(path, options) {
    const opts = options || {};
    opts.headers = Object.assign({ 'X-WPX-Key': KEY, 'Content-Type': 'application/json' },
                                 opts.headers || {});
    return fetch(path, opts).then(r => {
      if (!r.ok) return r.text().then(t => { throw new Error(t || ('HTTP ' + r.status)); });
      return r.json();
    });
  }

  function payload(state) {
    return JSON.stringify({
      values: state.values,
      parties: (state.parties || []).map(p => ({ scope: p.scope || null, values: p.values }))
    });
  }

  // ---- contact pickers --------------------------------------------------

  function openPicker(role, target, done) {
    const dlg = document.getElementById('picker');
    const list = document.getElementById('picklist');
    document.getElementById('picktitle').textContent =
      role === 'insurer' ? 'Choose a carrier' : 'Choose a provider';
    list.textContent = 'Loading…';
    dlg.showModal();
    api('/api/contacts?role=' + role).then(rows => {
      list.textContent = '';
      if (!rows.length) {
        list.appendChild(WPX.el('p', 'empty-note',
          'No contacts yet. Run "wpx contacts build" once the letters are scanned.'));
        return;
      }
      rows.forEach(row => {
        const item = WPX.el('button', 'pickitem');
        const left = WPX.el('span');
        left.appendChild(WPX.el('span', null, row.name));
        if (row.person) {
          const who = WPX.el('span', 'place', ' · ' + row.person);
          left.appendChild(who);
        }
        item.appendChild(left);
        item.appendChild(WPX.el('span', 'place', row.place || 'no address on file'));
        item.addEventListener('click', () => {
          Object.keys(row.values).forEach(k => { target[k] = row.values[k]; });
          dlg.close();
          WPX.changed();
          WPX.redraw();
          if (done) done();
        });
        list.appendChild(item);
      });
    }, err => { list.textContent = err.message; });
  }

  // ---- side panels ------------------------------------------------------

  function formsPanel(state) {
    const panel = WPX.el('div', 'panel');
    panel.appendChild(WPX.el('h3', null, 'What this writes'));
    const body = WPX.el('div', 'fills');
    body.id = 'forms-body';
    body.textContent = 'Checking…';
    panel.appendChild(body);

    const write = WPX.el('button', 'btn', 'Write the letters');
    write.id = 'write-btn';
    write.addEventListener('click', () => {
      write.disabled = true;
      WPX.setStatus('Writing…', '');
      WPX.save()
        .then(() => api('/api/generate?matter=' + encodeURIComponent(state.ref), { method: 'POST' }))
        .then(result => {
          WPX.setStatus(result.count + ' letter(s) written to ' + result.outdir, 'saved');
          write.disabled = false;
          refreshForms();
        }, err => { WPX.setStatus(err.message, 'error'); write.disabled = false; });
    });
    panel.appendChild(write);
    return panel;
  }

  function refreshForms() {
    const body = document.getElementById('forms-body');
    if (!body) return;
    api('/api/forms?matter=' + encodeURIComponent(WPX.state.ref)).then(rows => {
      body.textContent = '';
      if (!rows.length) {
        body.appendChild(WPX.el('div', 'row', 'No templates yet — build them first.'));
        return;
      }
      let ready = 0;
      rows.forEach(row => {
        if (row.ready) ready += 1;
        const line = WPX.el('div', 'row');
        line.appendChild(WPX.el('span', null, row.template.replace(/\.docx$/, '')));
        line.appendChild(WPX.el('span', 'pill ' + (row.ready ? 'ok' : 'hold'),
                                row.ready ? 'Ready' : 'Waiting'));
        body.appendChild(line);
        if (!row.ready) {
          const needs = row.needs.slice();
          row.parties.forEach(p => needs.push(p.label + ': ' + p.needs.length + ' missing'));
          body.appendChild(WPX.el('div', 'need', needs.join(', ')));
        }
      });
      const btn = document.getElementById('write-btn');
      if (btn) {
        btn.textContent = ready ? 'Write ' + ready + ' letter' + (ready === 1 ? '' : 's') : 'Nothing ready yet';
        btn.disabled = ready === 0;
      }
    }, err => { body.textContent = err.message; });
  }

  const host = {
    readyText: 'Saved',
    dirtyText: 'Saving…',
    save: function (state) {
      return api('/api/state?matter=' + encodeURIComponent(state.ref),
                 { method: 'POST', body: payload(state) })
        .then(fresh => {
          (state.parties || []).forEach((p, i) => { p.scope = fresh.parties[i]; });
          refreshForms();
          return 'Saved ' + new Date().toLocaleTimeString();
        });
    },
    panels: function (state) { return [formsPanel(state)]; },
    afterDraw: function () { refreshForms(); },
    contactPicker: function (role, values) {
      const row = WPX.el('div');
      row.style.marginBottom = '14px';
      const btn = WPX.el('button', 'btn ghost small', 'Fill from the contact list');
      btn.addEventListener('click', () => openPicker(role, values));
      row.appendChild(btn);
      return row;
    },
    contactPickerButton: function (role, values) {
      const btn = WPX.el('button', 'btn ghost small', 'From contacts');
      btn.addEventListener('click', () => openPicker(role, values));
      return btn;
    }
  };

  document.getElementById('pickclose').addEventListener('click',
    () => document.getElementById('picker').close());

  WPX.start({ schema: schema, state: boot.state, host: host });
})();
"""

EXTRA_CSS = """
.tabs { display:flex; gap:2px; margin-left:auto; }
.tabs a, .tabs span {
  padding:7px 12px; font-size:.84rem; border-radius:5px 5px 0 0;
  color:var(--faint); text-decoration:none;
}
.tabs a.on { color:var(--ink); font-weight:500; border-bottom:2px solid var(--accent); }
.tabs span { opacity:.5; cursor:not-allowed; }
.matterbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.matterbar select {
  font:inherit; font-size:.86rem; padding:6px 8px; border-radius:5px;
  border:1px solid var(--line); background:var(--surface); color:var(--ink);
}
.index { max-width:640px; }
.index li { margin-bottom:6px; }
"""


def _body(ref: str, client: str) -> str:
    ref, client = html.escape(ref), html.escape(client)
    title = client or ref
    return f"""<header class="top"><div class="top-inner">
  <div class="who">
    <h1>{title}</h1>
    <span class="sub">file {ref} &middot; interview</span>
  </div>
  <nav class="tabs">
    <span title="Not built yet">Convert</span>
    <span title="Not built yet">Templates</span>
    <a class="on" href="#">Interview</a>
    <a href="/">Matters</a>
  </nav>
</div></header>
<div class="wrap">
  <div class="layout">
    <div id="main"></div>
    <aside id="side"></aside>
  </div>
</div>
<dialog id="picker"><div class="dlg">
  <h2 id="picktitle">Choose</h2>
  <div class="picklist" id="picklist"></div>
  <button class="btn ghost" id="pickclose">Close</button>
</div></dialog>"""


INDEX_CSS = EXTRA_CSS + """
body { padding:40px 20px; }
.newform { display:flex; gap:8px; margin-top:16px; flex-wrap:wrap; }
.newform input {
  font:inherit; padding:8px 10px; border:1px solid var(--line);
  border-radius:5px; background:var(--surface); color:var(--ink);
}
ul.matters { list-style:none; padding:0; display:flex; flex-direction:column; gap:8px; }
ul.matters a { display:flex; justify-content:space-between; gap:12px; padding:12px 14px;
  border:1px solid var(--line); border-radius:6px; text-decoration:none; color:var(--ink);
  background:var(--surface); }
ul.matters a:hover { border-color:var(--accent); background:var(--accent-soft); }
ul.matters .n { color:var(--faint); font-size:.82rem; }
"""


class Server(ThreadingHTTPServer):
    """Holds the settings the handler needs; one database connection per thread."""

    daemon_threads = True

    def __init__(self, address, handler, db_path, templates, outdir, key):
        super().__init__(address, handler)
        self.db_path = str(db_path)
        self.templates = Path(templates)
        self.outdir = Path(outdir)
        self.key = key
        self._local = threading.local()

    def db(self):
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._local.con = open_db(self.db_path)
        return con


class Handler(BaseHTTPRequestHandler):
    server_version = "wpx"

    # ---- plumbing ------------------------------------------------------

    def log_message(self, fmt, *args):
        pass  # the console is for the user's instructions, not a request log

    def _send(self, status, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def html(self, markup: str, status: int = 200):
        self._send(status, markup.encode("utf-8"), "text/html; charset=utf-8")

    def json(self, payload, status: int = 200):
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def fail(self, status: int, message: str):
        self._send(status, message.encode("utf-8"), "text/plain; charset=utf-8")

    def local_only(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")

    def authorized(self, query) -> bool:
        given = self.headers.get("X-WPX-Key") or (query.get("k", [""])[0])
        return secrets.compare_digest(given, self.server.key)

    def body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---- routing -------------------------------------------------------

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        if not self.local_only():
            return self.fail(403, "This page is only served to this computer.")
        url = urlparse(self.path)
        query = parse_qs(url.query)
        path = url.path
        try:
            if path == "/" and method == "GET":
                if not self.authorized(query):
                    return self.fail(403, KEY_HELP)
                return self.html(self.index_page())
            if path == "/matter" and method == "GET":
                if not self.authorized(query):
                    return self.fail(403, KEY_HELP)
                return self.html(self.interview_page(query.get("ref", [""])[0]))
            if path.startswith("/api/"):
                if not self.authorized(query):
                    return self.fail(403, "Missing or wrong key.")
                return self.api(method, path, query)
            self.fail(404, "No such page.")
        except KeyError as exc:
            self.fail(404, str(exc))
        except Exception as exc:  # a broken request must not take the server down
            self.fail(500, f"{type(exc).__name__}: {exc}")

    def api(self, method, path, query):
        con = self.server.db()
        ref = query.get("matter", [""])[0]
        if path == "/api/contacts" and method == "GET":
            return self.json(self.contact_rows(con, query.get("role", ["provider"])[0]))
        if path == "/api/state" and method == "POST":
            return self.json(self.save_state(con, ref, self.body_json()))
        if path == "/api/forms" and method == "GET":
            supplied = render.default_values(con).keys()
            return self.json(matters.readiness(con, ref, supplied))
        if path == "/api/generate" and method == "POST":
            return self.json(self.generate(con, ref))
        if path == "/api/matters" and method == "GET":
            return self.json(matters.list_matters(con))
        return self.fail(404, "No such endpoint.")

    # ---- actions -------------------------------------------------------

    @staticmethod
    def contact_rows(con, role):
        rows = []
        for contact in contacts.listing(con, role):
            values = contacts.address_block(con, contact)
            people = contacts.people_for(con, contact["id"])
            rows.append({
                "id": contact["id"],
                "name": contact["name"],
                "place": values.get(f"{role}.city_state_zip", ""),
                "person": people[0]["name"] if people else "",
                "values": values,
            })
        return rows

    @staticmethod
    def save_state(con, ref, payload):
        if not ref:
            raise KeyError("no matter given")
        matters.ensure_matter(con, ref)
        matters.set_values(con, ref, payload.get("values", {}))
        parties = matters.replace_parties(con, ref, "provider", payload.get("parties", []))
        return {"parties": parties}

    def generate(self, con, ref):
        """Write the letters this matter can complete — and only those.

        The button says how many are ready; writing extra copies stamped
        [MISSING: ...] on top of that would not match what it promised.
        """
        templates = self.server.templates
        if not templates.is_dir():
            raise KeyError(f"no templates in {templates} — build them first")
        supplied = render.default_values(con).keys()
        ready = [row["template"] for row in matters.readiness(con, ref, supplied) if row["ready"]]
        if not ready:
            raise KeyError("nothing is ready to write yet")
        outdir = self.server.outdir / render.safe_name(ref)
        results = render.generate(con, ref, templates, outdir, only=ready, echo=None)
        return {
            "count": len(results),
            "written": [Path(r.path).name for r in results],
            "outdir": str(outdir),
        }

    # ---- pages ---------------------------------------------------------

    def index_page(self):
        con = self.server.db()
        rows = matters.list_matters(con)
        items = "".join(
            f'<li><a href="/matter?ref={quote(row["ref"])}&k={self.server.key}">'
            f'<span>{html.escape(row["ref"])}</span>'
            f'<span class="n">{row["filled"]} answer(s)</span></a></li>'
            for row in rows
        ) or '<li class="n">No matters yet — open one below.</li>'
        return page(
            title="Matters — Letter Desk",
            body_html=f"""<div class="wrap index">
  <h1>Matters</h1>
  <p class="hint">Open a file to take its interview, or start a new one.</p>
  <ul class="matters">{items}</ul>
  <form class="newform" action="/matter" method="get">
    <input name="ref" placeholder="New file number, e.g. 2026-0620" required>
    <input type="hidden" name="k" value="{self.server.key}">
    <button class="btn" type="submit">Open</button>
  </form>
</div>""",
            data_json="{}",
            host_js="",
            extra_css=INDEX_CSS,
        )

    def interview_page(self, ref):
        if not ref:
            raise KeyError("no matter given")
        con = self.server.db()
        matters.ensure_matter(con, ref)
        values = matters.values(con, ref)
        parties = []
        for scope in matters.scopes(con, ref, "provider:"):
            keys = matters.scoped_keys(con, ref, scope)
            party = matters.values(con, ref, scope)
            parties.append({
                "id": scope,
                "scope": scope,
                "values": {k: v for k, v in party.items() if k in keys},
            })
        state = {"ref": ref, "values": values, "parties": parties}
        client = values.get("client.name", "")
        return page(
            title=f"{client or ref} — Letter Desk",
            body_html=_body(ref, client),
            data_json=json_for_html({"key": self.server.key, "state": state}),
            host_js=HOST_JS,
            extra_css=EXTRA_CSS,
        )


KEY_HELP = (
    "This page needs the key printed in the window where the app was started.\n"
    "Open the link shown there, or restart with: python3 -m wpx serve"
)


def serve(db_path, templates="Templates", outdir="Letters", port: int = 8765,
          open_browser: bool = True, echo=print) -> Server:
    """Start the office app. Returns the server; call shutdown() to stop it."""
    key = secrets.token_urlsafe(18)
    server = Server(("127.0.0.1", port), Handler, db_path, templates, outdir, key)
    url = f"http://127.0.0.1:{server.server_address[1]}/?k={key}"
    if echo:
        echo(f"Letter Desk is running.\n\n  {url}\n")
        echo(f"  matters   {db_path}")
        echo(f"  templates {templates}")
        echo(f"  letters   {outdir}\n")
        echo("Only this computer can reach it. Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    return server
