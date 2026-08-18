"""The interview, as a browser form — shared by the local app and Intake.html.

Both interfaces ask the same questions in the same order, because both are
generated from the same field dictionary: add a field to wpx/fields.py and it
appears in the office app and in every intake file written afterwards, with no
markup to update.

What differs is only where the answers go. The local app posts them to the
matter database; the intake file bakes them back into a copy of itself, so a
client folder holds one file that opens on any computer, needs no install, and
still has everything typed into it a month later.

No webfonts, no CDN, no network of any kind: an intake file has to work on a
laptop at a client meeting with no connection, and client details should never
leave the machine they were typed on.
"""

from __future__ import annotations

import html
import json

from . import fields as F

# Order and wording of the interview. The dictionary supplies the fields; this
# supplies the shape of the conversation — how a file is actually opened.
SECTIONS = (
    ("client", "Client", "Who we act for.", False),
    ("matter", "The matter", "What happened, and our file number.", False),
    ("insurer", "Carrier and adjuster", "Who we are writing to about the claim.", False),
    ("provider", "Medical providers", "One card per clinic. Each gets its own records request.", True),
    ("demand", "Damages", "The figures the demand letter quotes.", False),
    ("firm", "Firm details", "Set once — shared by every matter.", False),
)

PARTY_ROLE = {"provider": "provider"}

KIND_HINTS = {
    F.DATE: "MM/DD/YYYY",
    F.MONEY: "$0.00",
    F.PHONE: "(907) 555-0100",
}


def schema() -> dict:
    """Everything the browser needs to draw the interview."""
    by_group = F.groups()
    sections = []
    for key, label, hint, repeating in SECTIONS:
        group = by_group.get(key, [])
        entries = [
            {
                "key": f.key,
                "label": f.label,
                "kind": f.kind,
                "hint": KIND_HINTS.get(f.kind, ""),
                "note": f.note,
                "contact": f.contact,
            }
            for f in group
            if not f.derived
        ]
        if not entries:
            continue
        sections.append({
            "key": key,
            "label": label,
            "hint": hint,
            "repeating": repeating,
            "role": PARTY_ROLE.get(key, ""),
            "fields": entries,
        })
    derived = [
        {"key": f.key, "label": f.label, "from": f.key.rsplit(".", 1)[0] + ".name"}
        for f in F.FIELDS if f.derived
    ]
    return {"sections": sections, "derived": derived}


CSS = """
:root {
  --ground:#F1F4F7; --surface:#FFFFFF; --sunk:#E8EDF3;
  --ink:#131820; --soft:#4C5765; --faint:#737F8D;
  --line:#D7DEE7; --line-soft:#E6EBF1;
  --accent:#1B3A8C; --accent-ink:#1B3A8C; --accent-soft:#E5EBF8; --btn-ink:#FFFFFF;
  --ok:#1D6B4A; --ok-soft:#DDEFE5; --warn:#8A5A0B; --warn-soft:#F6EBD6;
  --stop:#9E2C2C; --stop-soft:#F6E2E2;
  --serif:Georgia,"Iowan Old Style","Times New Roman",serif;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,"Cascadia Mono",Consolas,"DejaVu Sans Mono",monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground:#0F131A; --surface:#171D26; --sunk:#1E2531;
    --ink:#E6EBF2; --soft:#A6B1C0; --faint:#7C8899;
    --line:#2A3341; --line-soft:#212936;
    --accent:#8AA8F2; --accent-ink:#A8BEF7; --accent-soft:#1C2740; --btn-ink:#0F131A;
    --ok:#6DC49B; --ok-soft:#142A21; --warn:#D9A757; --warn-soft:#2C2417;
    --stop:#E38B8B; --stop-soft:#2E1A1A;
  }
}
:root[data-theme="dark"] {
  --ground:#0F131A; --surface:#171D26; --sunk:#1E2531;
  --ink:#E6EBF2; --soft:#A6B1C0; --faint:#7C8899;
  --line:#2A3341; --line-soft:#212936;
  --accent:#8AA8F2; --accent-ink:#A8BEF7; --accent-soft:#1C2740; --btn-ink:#0F131A;
  --ok:#6DC49B; --ok-soft:#142A21; --warn:#D9A757; --warn-soft:#2C2417;
  --stop:#E38B8B; --stop-soft:#2E1A1A;
}
* { box-sizing:border-box; }
body {
  margin:0; background:var(--ground); color:var(--ink);
  font-family:var(--sans); font-size:15px; line-height:1.55;
}
h1,h2 { font-family:var(--serif); margin:0; font-weight:600; text-wrap:balance; }
h1 { font-size:1.5rem; letter-spacing:-.01em; }
h2 { font-size:1.15rem; }
button { font:inherit; cursor:pointer; }
a { color:var(--accent-ink); }

.wrap { max-width:1120px; margin:0 auto; padding:0 20px 80px; }

header.top {
  position:sticky; top:0; z-index:20; background:var(--surface);
  border-bottom:1px solid var(--line); margin-bottom:22px;
}
.top-inner {
  max-width:1120px; margin:0 auto; padding:12px 20px;
  display:flex; gap:16px; align-items:center; flex-wrap:wrap;
}
.top .who { display:flex; flex-direction:column; gap:2px; margin-right:auto; }
.top .who .sub { font-size:.78rem; color:var(--faint); font-family:var(--mono); }

.btn {
  border:1px solid var(--accent); background:var(--accent); color:var(--btn-ink);
  border-radius:5px; padding:8px 15px; font-size:.86rem; font-weight:500;
}
.btn:hover { filter:brightness(1.08); }
.btn.ghost { background:transparent; color:var(--accent-ink); border-color:var(--line); }
.btn.small { padding:5px 10px; font-size:.78rem; }
.btn:disabled { opacity:.45; cursor:not-allowed; filter:none; }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }

.layout { display:grid; grid-template-columns:1fr 260px; gap:22px; align-items:start; }
@media (max-width:900px) { .layout { grid-template-columns:1fr; } }

.sectionbar { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:18px; }
.sectionbar a {
  font-size:.8rem; padding:4px 10px; border-radius:999px;
  background:var(--sunk); color:var(--faint); text-decoration:none;
}
.sectionbar a.done { background:var(--ok-soft); color:var(--ok); }

section.card {
  background:var(--surface); border:1px solid var(--line); border-radius:8px;
  padding:20px 22px; margin-bottom:16px; scroll-margin-top:74px;
}
section.card > header {
  display:flex; justify-content:space-between; align-items:baseline;
  gap:12px; margin-bottom:4px; flex-wrap:wrap;
}
section.card .hint { color:var(--faint); font-size:.85rem; margin:0 0 16px; }

.fields { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
.fld { display:flex; flex-direction:column; gap:5px; }
.fld label { font-size:.72rem; letter-spacing:.05em; text-transform:uppercase; color:var(--faint); }
.fld input {
  font:inherit; font-size:.92rem; color:var(--ink); background:var(--surface);
  border:1px solid var(--line); border-radius:5px; padding:8px 10px; width:100%;
}
.fld input:focus { border-color:var(--accent); outline:none; box-shadow:0 0 0 3px var(--accent-soft); }
.fld input::placeholder { color:var(--faint); }
.fld .note { font-size:.75rem; color:var(--faint); }
.fld.filled label { color:var(--soft); }

.derived { font-size:.78rem; color:var(--faint); margin-top:12px; }
.derived b { color:var(--soft); font-weight:500; }

.party {
  border:1px solid var(--line); border-left:3px solid var(--accent);
  border-radius:6px; padding:14px 16px; margin-bottom:12px;
}
.party > header { display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:12px; }
.party .who { font-weight:600; }
.party .who.unnamed { color:var(--faint); font-weight:400; font-style:italic; }
.empty-note { color:var(--faint); font-size:.88rem; margin:0 0 12px; }

.pill {
  display:inline-flex; align-items:center; gap:5px; padding:2px 9px;
  border-radius:999px; font-size:.72rem; font-weight:500; white-space:nowrap;
}
.pill.ok { background:var(--ok-soft); color:var(--ok); }
.pill.hold { background:var(--warn-soft); color:var(--warn); }
.pill.mute { background:var(--sunk); color:var(--faint); }

aside { position:sticky; top:74px; display:flex; flex-direction:column; gap:14px; }
aside .panel {
  background:var(--surface); border:1px solid var(--line);
  border-radius:8px; padding:16px;
}
aside h3 {
  margin:0 0 10px; font-size:.72rem; letter-spacing:.08em;
  text-transform:uppercase; color:var(--faint); font-weight:500;
}
.track { height:6px; border-radius:3px; background:var(--sunk); overflow:hidden; margin-bottom:8px; }
.track > div { height:100%; background:var(--accent); width:0; transition:width .25s ease; }
.count { font-size:.85rem; color:var(--soft); }
.count b { color:var(--ink); font-variant-numeric:tabular-nums; }

.fills { display:flex; flex-direction:column; gap:8px; font-size:.82rem; }
.fills .row { display:flex; justify-content:space-between; gap:8px; align-items:center; color:var(--soft); }
.fills .row .need { font-size:.74rem; color:var(--warn); }

.status { font-size:.8rem; color:var(--faint); }
.status.saved { color:var(--ok); }
.status.dirty { color:var(--warn); }
.status.error { color:var(--stop); }

.helpbox {
  background:var(--sunk); border-radius:6px; padding:12px 14px;
  font-size:.82rem; color:var(--soft); display:flex; flex-direction:column; gap:8px;
}
.helpbox b { color:var(--ink); }
dialog {
  border:1px solid var(--line); border-radius:8px; background:var(--surface);
  color:var(--ink); max-width:540px; width:calc(100% - 40px); padding:0;
}
dialog::backdrop { background:rgba(10,14,20,.45); }
.dlg { padding:20px 22px; display:flex; flex-direction:column; gap:14px; }
.pickitem {
  display:flex; justify-content:space-between; gap:10px; align-items:center;
  padding:9px 10px; border:1px solid var(--line); border-radius:6px; background:var(--surface);
  width:100%; text-align:left; color:var(--ink);
}
.pickitem:hover { border-color:var(--accent); background:var(--accent-soft); }
.pickitem .place { font-size:.78rem; color:var(--faint); }
.picklist { display:flex; flex-direction:column; gap:6px; max-height:44vh; overflow:auto; }
textarea.blob {
  width:100%; height:130px; font-family:var(--mono); font-size:.75rem;
  border:1px solid var(--line); border-radius:6px; padding:10px;
  background:var(--sunk); color:var(--ink); resize:vertical;
}
@media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
"""


# The renderer. Both hosts hand it a schema, the current answers, and an
# adapter object; everything else on the page is drawn from those.
JS = r"""
'use strict';

const WPX = (function () {
  let schema = null, state = null, host = null;
  let saveTimer = null;
  // Live counters are updated in place rather than by redrawing: a redraw on
  // every keystroke would take the focus out of the field being typed into.
  const refs = { track: null, count: null, pills: {}, links: {} };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };

  function blankParty() { return { id: 'p' + Math.random().toString(36).slice(2, 9), values: {} }; }

  function partySections() { return schema.sections.filter(s => s.repeating); }
  function flatSections() { return schema.sections.filter(s => !s.repeating); }

  function answered(values, fields) {
    return fields.filter(f => (values[f.key] || '').trim() !== '').length;
  }

  function sectionCounts(section) {
    if (!section.repeating) {
      return { done: answered(state.values, section.fields), total: section.fields.length };
    }
    const parties = state.parties || [];
    let done = 0;
    parties.forEach(p => { done += answered(p.values, section.fields); });
    return { done: done, total: parties.length * section.fields.length };
  }

  function totals() {
    let done = 0, total = 0;
    schema.sections.forEach(s => {
      const c = sectionCounts(s);
      done += c.done; total += c.total;
    });
    return { done: done, total: total };
  }

  function partyName(party, section) {
    return (party.values[section.role + '.name'] || '').trim();
  }

  // ---- rendering ---------------------------------------------------------

  function fieldNode(field, values, onchange) {
    const wrap = el('div', 'fld');
    const id = 'f-' + field.key.replace(/\W/g, '-') + '-' + Math.random().toString(36).slice(2, 6);
    const label = el('label', null, field.label);
    label.htmlFor = id;
    const input = el('input');
    input.id = id;
    input.type = 'text';
    input.value = values[field.key] || '';
    input.placeholder = field.hint || '';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.dataset.key = field.key;
    if ((input.value || '').trim() !== '') wrap.classList.add('filled');
    input.addEventListener('input', () => {
      values[field.key] = input.value;
      wrap.classList.toggle('filled', input.value.trim() !== '');
      onchange();
    });
    wrap.appendChild(label);
    wrap.appendChild(input);
    if (field.note) wrap.appendChild(el('div', 'note', field.note));
    return wrap;
  }

  function derivedNote(section) {
    const mine = (schema.derived || []).filter(d => d.from.split('.')[0] === section.key);
    if (!mine.length) return null;
    const node = el('div', 'derived');
    node.appendChild(document.createTextNode('Worked out from the full name: '));
    mine.forEach((d, i) => {
      if (i) node.appendChild(document.createTextNode(', '));
      node.appendChild(el('b', null, d.label.toLowerCase()));
    });
    node.appendChild(document.createTextNode('. Never asked twice.'));
    return node;
  }

  function flatSection(section) {
    const card = el('section', 'card');
    card.id = 'sec-' + section.key;
    const head = el('header');
    head.appendChild(el('h2', null, section.label));
    const count = el('span', 'pill mute');
    head.appendChild(count);
    card.appendChild(head);
    card.appendChild(el('p', 'hint', section.hint));

    if (host.contactPicker && section.key === 'insurer') {
      card.appendChild(host.contactPicker('insurer', state.values, redraw));
    }

    const fields = el('div', 'fields');
    section.fields.forEach(f => fields.appendChild(fieldNode(f, state.values, changed)));
    card.appendChild(fields);
    const derived = derivedNote(section);
    if (derived) card.appendChild(derived);

    refs.pills[section.key] = count;
    return card;
  }

  function partySection(section) {
    const card = el('section', 'card');
    card.id = 'sec-' + section.key;
    const head = el('header');
    head.appendChild(el('h2', null, section.label));
    const count = el('span', 'pill mute');
    const parties = state.parties || (state.parties = []);
    refs.pills[section.key] = count;
    head.appendChild(count);
    card.appendChild(head);
    card.appendChild(el('p', 'hint', section.hint));

    if (!parties.length) {
      card.appendChild(el('p', 'empty-note',
        'No providers on this file yet. Add one for each clinic that treated the client.'));
    }

    parties.forEach((party, index) => {
      const box = el('div', 'party');
      const top = el('header');
      const name = partyName(party, section);
      const who = el('span', 'who' + (name ? '' : ' unnamed'), name || 'Provider ' + (index + 1));
      top.appendChild(who);
      const tools = el('div');
      tools.style.display = 'flex';
      tools.style.gap = '6px';
      if (host.contactPickerButton) {
        tools.appendChild(host.contactPickerButton('provider', party.values, redraw));
      }
      const remove = el('button', 'btn ghost small', 'Remove');
      remove.addEventListener('click', () => {
        if (name && !confirm('Remove ' + name + ' from this file?')) return;
        parties.splice(index, 1);
        changed();
        redraw();
      });
      tools.appendChild(remove);
      top.appendChild(tools);
      box.appendChild(top);

      const fields = el('div', 'fields');
      section.fields.forEach(f => fields.appendChild(fieldNode(f, party.values, () => {
        changed();
        const now = partyName(party, section);
        who.textContent = now || 'Provider ' + (index + 1);
        who.className = 'who' + (now ? '' : ' unnamed');
      })));
      box.appendChild(fields);
      card.appendChild(box);
    });

    const add = el('button', 'btn ghost', 'Add a provider');
    add.addEventListener('click', () => {
      parties.push(blankParty());
      changed();
      redraw();
    });
    card.appendChild(add);
    return card;
  }

  function sectionBar() {
    const bar = el('nav', 'sectionbar');
    schema.sections.forEach(s => {
      const link = el('a', '', s.label);
      link.href = '#sec-' + s.key;
      refs.links[s.key] = link;
      bar.appendChild(link);
    });
    return bar;
  }

  // Recount everything and update the numbers already on screen.
  function refreshCounts() {
    schema.sections.forEach(s => {
      const counts = sectionCounts(s);
      const complete = counts.total > 0 && counts.done === counts.total;
      const pill = refs.pills[s.key];
      if (pill) {
        if (s.repeating) {
          const n = (state.parties || []).length;
          pill.textContent = n + (n === 1 ? ' provider' : ' providers');
          pill.className = 'pill ' + (n ? 'ok' : 'mute');
        } else {
          pill.textContent = counts.done + ' of ' + counts.total;
          pill.className = 'pill ' + (complete ? 'ok' : 'mute');
        }
      }
      const link = refs.links[s.key];
      if (link) link.className = complete ? 'done' : '';
    });
    const t = totals();
    if (refs.track) refs.track.style.width = (t.total ? Math.round(100 * t.done / t.total) : 0) + '%';
    if (refs.count) {
      refs.count.textContent = '';
      refs.count.appendChild(el('b', null, String(t.done)));
      refs.count.appendChild(document.createTextNode(' of ' + t.total + ' answered'));
    }
    if (host.onCount) host.onCount(state);
  }

  function progressPanel() {
    const panel = el('div', 'panel');
    panel.appendChild(el('h3', null, 'Progress'));
    const track = el('div', 'track');
    const fill = el('div');
    track.appendChild(fill);
    panel.appendChild(track);
    const count = el('div', 'count');
    panel.appendChild(count);
    refs.track = fill;
    refs.count = count;
    const status = el('div', 'status');
    status.id = 'save-status';
    status.textContent = state.statusText || '';
    panel.appendChild(status);
    return panel;
  }

  function redraw() {
    const main = $('#main');
    main.textContent = '';
    main.appendChild(sectionBar());
    schema.sections.forEach(s => {
      main.appendChild(s.repeating ? partySection(s) : flatSection(s));
    });

    const side = $('#side');
    side.textContent = '';
    side.appendChild(progressPanel());
    (host.panels ? host.panels(state) : []).forEach(p => side.appendChild(p));
    refreshCounts();
    if (host.afterDraw) host.afterDraw(state);
  }

  function setStatus(text, kind) {
    state.statusText = text;
    const node = document.getElementById('save-status');
    if (node) {
      node.textContent = text;
      node.className = 'status' + (kind ? ' ' + kind : '');
    }
  }

  function changed() {
    refreshCounts();
    setStatus(host.dirtyText || 'Not saved yet', 'dirty');
    if (host.autosave === false) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => save(), 600);
  }

  function save() {
    clearTimeout(saveTimer);
    return Promise.resolve(host.save(state)).then(
      msg => setStatus(msg || 'Saved', 'saved'),
      err => setStatus(String(err && err.message ? err.message : err), 'error')
    );
  }

  function start(options) {
    schema = options.schema;
    host = options.host;
    state = options.state || {};
    state.values = state.values || {};
    state.parties = state.parties || [];
    if (host.init) host.init(state);
    redraw();
    setStatus(host.readyText || '', '');
  }

  return {
    start: start, redraw: redraw, save: save, setStatus: setStatus,
    el: el, get state() { return state; }, get schema() { return schema; },
    totals: totals, changed: changed, refreshCounts: refreshCounts
  };
})();
"""


def page(title: str, body_html: str, data_json: str, host_js: str,
         extra_css: str = "") -> str:
    """Assemble a complete, self-contained page.

    The title is escaped here rather than at each call site: it is built from a
    client's name, and a name is exactly the kind of text that eventually
    contains an apostrophe, an ampersand or worse.
    """
    title = html.escape(title)
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{title}</title>\n"
        f"<style>{CSS}{extra_css}</style>\n"
        "</head>\n<body>\n"
        f"{body_html}\n"
        f"<script id=\"wpx-schema\" type=\"application/json\">{json.dumps(schema())}</script>\n"
        f"<script id=\"wpx-data\" type=\"application/json\">{data_json}</script>\n"
        f"<script>{JS}</script>\n"
        f"<script>{host_js}</script>\n"
        "</body>\n</html>\n"
    )


def json_for_html(value) -> str:
    """JSON safe to embed in a <script> block."""
    return json.dumps(value).replace("</", "<\\/")
