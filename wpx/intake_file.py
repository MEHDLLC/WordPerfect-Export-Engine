"""Intake.html — one self-contained file, saved in a client's folder.

Double-clicked a month later it opens with every answer still in it, on any
computer, with no Python, no WordPerfect and no connection. That is the whole
point: the firm is leaving a format that stopped opening, so the thing that
holds a client's answers has to be plain HTML that any browser can still read
in ten years.

Answers are saved by writing a fresh copy of this same file with the answers
baked into it. No database, no localStorage to be cleared by an IT policy, no
second file to keep alongside the first — the document is its own save file.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .webform import json_for_html, page

HOST_JS = r"""
(function () {
  const schema = JSON.parse(document.getElementById('wpx-schema').textContent);
  const saved = JSON.parse(document.getElementById('wpx-data').textContent);

  function safeName(text) {
    return (text || '').replace(/[\\/:*?"<>|]+/g, '-').replace(/\s+/g, ' ').trim();
  }

  function fileStem() {
    const s = WPX.state;
    return 'Intake - ' + (safeName(s.values['client.name']) || safeName(s.ref) || 'client');
  }

  function download(name, text, type) {
    const blob = new Blob([text], { type: type || 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  // The saved file is this file, with the answers written into it and the
  // drawn page emptied — reopening redraws it from the answers.
  function bakedHtml() {
    const clone = document.documentElement.cloneNode(true);
    clone.querySelector('#wpx-data').textContent = JSON.stringify(WPX.state, null, 1);
    clone.querySelector('#main').textContent = '';
    clone.querySelector('#side').textContent = '';
    const banner = clone.querySelector('#unsaved');
    if (banner) banner.remove();
    return '<!doctype html>\n' + clone.outerHTML + '\n';
  }

  function answersJson() {
    const s = WPX.state;
    return JSON.stringify({
      matter: s.ref || '',
      values: s.values,
      parties: (s.parties || []).map(p => p.values)
    }, null, 2);
  }

  function saveFile() {
    download(fileStem() + '.html', bakedHtml(), 'text/html;charset=utf-8');
    WPX.setStatus('Saved — put the downloaded file in the client folder', 'saved');
    localStorage_try();
  }

  function localStorage_try() {
    // A convenience where the browser allows it; never the real save.
    try {
      window.localStorage.setItem('wpx-intake-' + (WPX.state.ref || 'client'),
                                  JSON.stringify(WPX.state));
    } catch (e) { /* file:// origins often forbid this; the file save is what counts */ }
  }

  function copyForOffice() {
    const text = answersJson();
    const done = () => WPX.setStatus('Answers copied — paste them into the office app', 'saved');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, () => showBlob(text));
    } else {
      showBlob(text);
    }
  }

  function showBlob(text) {
    const box = document.getElementById('blob');
    box.style.display = 'block';
    box.value = text;
    box.focus();
    box.select();
    WPX.setStatus('Copy the text below by hand (Ctrl+C)', 'dirty');
  }

  const host = {
    autosave: false,
    readyText: saved.savedAt ? 'Last saved ' + saved.savedAt : 'Nothing saved yet',
    dirtyText: 'Unsaved changes',
    save: function (state) {
      state.savedAt = new Date().toLocaleString();
      saveFile();
      return 'Saved ' + state.savedAt;
    },
    panels: function () {
      const box = WPX.el('div', 'panel');
      box.appendChild(WPX.el('h3', null, 'Keeping these answers'));

      const save = WPX.el('button', 'btn', 'Save answers to this folder');
      save.addEventListener('click', () => WPX.save());
      box.appendChild(save);

      const help = WPX.el('div', 'helpbox');
      const line = WPX.el('div');
      line.appendChild(document.createTextNode('Saving downloads a fresh copy of this file. '));
      line.appendChild(WPX.el('b', null, 'Put it in the client folder, replacing the old one.'));
      help.appendChild(line);
      help.appendChild(document.createTextNode(
        'The copy carries the answers inside it, so opening it later brings everything back.'));
      box.appendChild(help);

      const row = WPX.el('div');
      row.style.display = 'flex';
      row.style.gap = '6px';
      row.style.flexWrap = 'wrap';
      const asJson = WPX.el('button', 'btn ghost small', 'Download answers (.json)');
      asJson.addEventListener('click', () => {
        download(fileStem() + '.json', answersJson(), 'application/json');
        WPX.setStatus('Answers file downloaded', 'saved');
      });
      const copy = WPX.el('button', 'btn ghost small', 'Copy for the office');
      copy.addEventListener('click', copyForOffice);
      row.appendChild(asJson);
      row.appendChild(copy);
      box.appendChild(row);

      const note = WPX.el('div', 'panel');
      note.appendChild(WPX.el('h3', null, 'What happens next'));
      const p = WPX.el('div', 'helpbox');
      p.appendChild(document.createTextNode(
        'Letters are written back at the office. Hand over this file, or the answers file, ' +
        'and every form for the matter is produced from what is typed here.'));
      p.appendChild(document.createTextNode(
        'Nothing on this page reaches the internet — it is a file on this computer.'));
      note.appendChild(p);

      return [box, note];
    }
  };

  window.addEventListener('beforeunload', function (event) {
    const status = document.getElementById('save-status');
    if (status && status.classList.contains('dirty')) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  WPX.start({ schema: schema, state: saved, host: host });
})();
"""

EXTRA_CSS = """
.offline-note {
  display:inline-flex; align-items:center; gap:7px; font-size:.78rem; color:var(--faint);
}
.offline-note::before {
  content:""; width:7px; height:7px; border-radius:50%; background:var(--ok); display:block;
}
textarea.blob { display:none; margin-top:10px; }
"""


def _body(ref: str, client: str) -> str:
    ref, client = html.escape(ref or ""), html.escape(client or "")
    heading = client or ref or "Client intake"
    sub = f"File {ref}" if ref else "No file number yet"
    return f"""<header class="top"><div class="top-inner">
  <div class="who">
    <h1>{heading}</h1>
    <span class="sub">{sub} &middot; client intake</span>
  </div>
  <span class="offline-note">Offline copy &mdash; nothing here reaches the internet</span>
</div></header>
<div class="wrap">
  <div class="layout">
    <div id="main"></div>
    <aside id="side"></aside>
  </div>
  <textarea class="blob" id="blob" readonly></textarea>
</div>"""


def safe_stem(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", text or "").strip()
    return re.sub(r"\s+", " ", cleaned) or "client"


def build(path, ref: str = "", values: dict | None = None,
          parties: list | None = None) -> Path:
    """Write a self-contained intake file, optionally pre-filled."""
    path = Path(path)
    values = dict(values or {})
    state = {
        "ref": ref,
        "values": values,
        "parties": [{"id": f"p{i}", "values": dict(p)} for i, p in enumerate(parties or [], 1)],
        "savedAt": "",
    }
    client = values.get("client.name", "")
    html = page(
        title=f"Intake — {client or ref or 'client'}",
        body_html=_body(ref, client),
        data_json=json_for_html(state),
        host_js=HOST_JS,
        extra_css=EXTRA_CSS,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def read_answers(path) -> dict:
    """Read the answers back out of a saved intake file (or its .json)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
    else:
        match = re.search(
            r'<script id="wpx-data" type="application/json">(.*?)</script>', text, re.S
        )
        if not match:
            raise ValueError(f"{path.name} does not look like an intake file")
        payload = json.loads(match.group(1))
    parties = payload.get("parties") or []
    return {
        "matter": payload.get("matter") or payload.get("ref") or "",
        "values": payload.get("values") or {},
        "parties": [p.get("values", p) if isinstance(p, dict) else {} for p in parties],
    }
