#!/usr/bin/env python3
"""Interface web du démonstrateur — http://127.0.0.1:8099

    python3 app.py            (construit demo.sqlite au premier lancement)
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import brain
import chat

EXEMPLES = [
    "combien de patientes ont un cancer du sein confirmé entre 25 et 36 ans, sans grossesse antérieure ?",
    "combien de patientes ont un cancer du sein confirmé entre 25 et 36 ans, sans grossesse antérieure, "
    "et elles ont toutes l expression du gène X ?",
    "combien de patientes ont un cancer du sein confirmé entre 25 et 36 ans, sans grossesse antérieure, "
    "dans le service C ?",
    "combien de patientes ont une allergie aux arachides ?",
    "quel est l âge moyen des patientes avec un cancer du sein confirmé ?",
]

PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Noyau d'analyse — démonstrateur</title>
<style>
:root { --ink:#1b1b1b; --blue:#0f3d63; --line:#dfe4ea; --bg:#f7f9fb; --ok:#1c7c54; --warn:#a4441c; --bad:#8d2b2b; }
* { box-sizing: border-box; }
body { margin:0; font-family:"DejaVu Sans",Arial,sans-serif; color:var(--ink); background:var(--bg); font-size:15px; }
header { background:#fff; border-bottom:1px solid var(--line); padding:18px 28px; }
h1 { margin:0 0 4px; font-size:19px; color:var(--blue); letter-spacing:-.2px; }
.sub { color:#5b6773; font-size:13px; }
main { display:grid; grid-template-columns: 1.25fr .75fr; gap:18px; padding:18px 28px 40px; align-items:start; }
@media (max-width: 860px) {
  main { grid-template-columns: 1fr; padding:12px 12px 32px; gap:12px; }
  header { padding:14px 14px; } h1 { font-size:16px; } .sub { font-size:12px; }
  input[type=text] { font-size:16px; padding:13px 12px; }
  button { width:100%; padding:14px; font-size:15px; margin-top:8px; }
  .controls { flex-direction:column; align-items:stretch; gap:10px; }
  select { font-size:15px; padding:10px; }
  pre { font-size:11px; } table { font-size:12px; }
  .chip { font-size:12px; padding:8px 10px; }
}
.card { background:#fff; border:1px solid var(--line); border-radius:10px; padding:16px 18px; }
.card h2 { margin:0 0 12px; font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:#5b6773; font-weight:600; }
input[type=text] { width:100%; padding:11px 12px; border:1px solid var(--line); border-radius:8px; font-size:15px; font-family:inherit; }
button { background:var(--blue); color:#fff; border:0; padding:11px 18px; border-radius:8px; font-size:14px; cursor:pointer; font-family:inherit; }
button.ghost { background:#eef2f7; color:var(--blue); }
.chips { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
.chip { background:#eef2f7; color:#274b6b; border:1px solid #dbe4ee; border-radius:999px; padding:6px 12px; font-size:12.5px; cursor:pointer; }
.chip:hover { background:#e2ebf5; }
.badge { display:inline-block; padding:3px 10px; border-radius:999px; font-size:12px; font-weight:600; letter-spacing:.03em; }
.b-ok { background:#e6f4ec; color:var(--ok); } .b-ref { background:#fbeee8; color:var(--warn); }
.b-clar { background:#eef3fb; color:#274b6b; } .opt { display:inline-block; margin:10px 8px 0 0; background:#eef2f7;
  color:#0f3d63; border:1px solid #cfdcea; border-radius:8px; padding:8px 12px; font-size:13.5px; cursor:pointer; }
.opt:hover { background:#e2ebf5; }
.b-rej { background:#f7e6e6; color:var(--bad); }
.answer { font-size:16px; line-height:1.5; margin:10px 0 4px; }
.trace { border-top:1px dashed var(--line); margin-top:14px; padding-top:12px; }
.trace h3 { font-size:12px; text-transform:uppercase; letter-spacing:.07em; color:#5b6773; margin:14px 0 6px; }
pre { background:#f6f7f9; border:1px solid var(--line); border-radius:8px; padding:10px 12px; font-size:12px; overflow-x:auto; white-space:pre-wrap; word-break:break-word; margin:0; }
.meta { display:flex; gap:18px; flex-wrap:wrap; font-size:12.5px; color:#5b6773; margin-top:10px; }
.def { font-size:12.5px; margin:0 0 8px; }
.def b { color:var(--blue); }
table { width:100%; border-collapse:collapse; font-size:12.5px; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:#5b6773; font-weight:600; font-size:11.5px; text-transform:uppercase; letter-spacing:.05em; }
.controls { display:flex; gap:16px; align-items:center; margin-top:12px; font-size:13px; color:#44515c; }
select { padding:7px 9px; border:1px solid var(--line); border-radius:7px; font-family:inherit; font-size:13px; }
small.hint { color:#7a8794; }
</style></head><body>
<header>
  <h1>Noyau d'analyse sur données de santé — démonstrateur</h1>
  <div class="sub">3 000 patientes synthétiques · <b>aucune donnée patient réelle</b> · chaque réponse est calculée par la base, pas par le modèle</div>
</header>
<main>
  <section>
    <div class="card">
      <h2>Question</h2>
      <input id="q" type="text" placeholder="ex. combien de patientes ont un cancer du sein confirmé entre 25 et 36 ans ?">
      <div class="controls">
        <label>rôle
          <select id="role">
            <option value="chercheur">chercheur (identifiants opaques)</option>
            <option value="clinicien">clinicien</option>
          </select>
        </label>
        <label>traducteur
          <select id="mode">
            <option value="regles">règles (déterministe)</option>
            <option value="double">double lecture (règles + modèle)</option>
            <option value="llm">modèle local seul (qwen3-1.7b)</option>
          </select>
        </label>
        <label><input id="hall" type="checkbox"> simuler une hallucination</label>
        <button id="go">Analyser</button>
        <button id="replay" class="ghost">Rejouer la même question</button>
      </div>
      <div class="chips" id="chips"></div>
    </div>
    <div class="card" id="result" style="margin-top:14px">
      <h2>Réponse</h2>
      <div id="replaynote" style="display:none; margin:10px 0 0; font-size:13px; color:#274b6b"></div>
      <div id="body"><small class="hint">Posez une question : vous verrez le plan de requête, le SQL, et la vérification des nombres.</small></div>
    </div>
  </section>
  <aside>
    <div class="card">
      <h2>Catalogue des définitions</h2>
      <div id="catalogue"><small class="hint">—</small></div>
      <div style="margin-top:10px"><small class="hint">Fichier <code>semantique.yml</code> — versionné, lisible par un clinicien.</small></div>
    </div>
    <div class="card" style="margin-top:14px">
      <h2>Journal des requêtes</h2>
      <div id="journal"><small class="hint">—</small></div>
    </div>
  </aside>
</main>
<script>
const EX = __EXEMPLES__;
const chips = document.getElementById('chips');
EX.forEach(t => {
  const d = document.createElement('div'); d.className = 'chip';
  d.textContent = t.length > 62 ? t.slice(0, 60) + '…' : t;
  d.title = t;
  d.onclick = () => { document.getElementById('q').value = t; ask(); };
  chips.appendChild(d);
});

const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

let lastQ = '';
async function ask(override) {
  const q = (override || document.getElementById('q').value).trim();
  if (!q) return;
  lastQ = q;
  if (override) document.getElementById('q').value = q;
  const r = await fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({q, sid: window.__sid, role: document.getElementById('role').value,
                            halluciner: document.getElementById('hall').checked,
                            mode: document.getElementById('mode').value})});
  const d = await r.json(); window.__sid = d.sid || window.__sid; render(d); loadJournal();

async function loadCatalogue() {
  const r = await fetch('/api/catalogue'); const cat = await r.json();
  let h = '<table>';
  cat.forEach(c => {
    h += '<tr><td><b>' + esc(c.nom) + '</b> <small class="hint">' + esc(c.version) + '</small><br>'
       + '<small class="hint">' + esc(c.libelle) + '</small><br>'
       + '<small class="hint">' + esc(c.valide_par) + '</small></td></tr>';
  });
  document.getElementById('catalogue').innerHTML = h + '</table>';
}
loadCatalogue();
}

function render(d) {
  const badge = d.statut === 'verifie' ? '<span class="badge b-ok">VÉRIFIÉ</span>'
             : d.statut === 'refus' ? '<span class="badge b-ref">REFUS</span>'
             : d.statut === 'clarification' ? '<span class="badge b-clar">CLARIFICATION</span>'
             : '<span class="badge b-rej">REJETÉ</span>';
  let h = badge + '<div class="answer">' + esc(d.message || '') + '</div>';
  h += '<div class="meta"><span>référence : ' + (d.date_reference || '—') + '</span>'
     + '<span>seuil de restitution : n ≥ ' + (d.seuil || '—') + '</span>'
     + '<span>' + d.ms + ' ms</span></div>';
  if (d.plan) {
    h += '<div class="trace"><h3>1 · Plan de requête (intention structurée)</h3><pre>'
       + esc(JSON.stringify(d.plan, null, 1)) + '</pre>';
  }
  if (d.sql) {
    h += '<h3>2 · SQL compilé (paramétré, journalisé)</h3><pre>' + esc(d.sql.replace(/ FROM /, '\\n  FROM '))
       + '\\n-- paramètres : ' + esc(JSON.stringify(d.params)) + '</pre>';
  }
  if (d.definitions) {
    h += '<h3>3 · Définitions employées</h3>';
    for (const [k, v] of Object.entries(d.definitions)) {
      h += '<div class="def"><b>' + esc(k) + '</b> ' + esc(v.version) + ' — ' + esc(v.libelle)
         + '<br><small class="hint">' + esc(v.source) + ' · ' + esc(v.valide_par) + '</small></div>';
    }
  }
  if (d.resultat) {
    h += '<h3>4 · Résultat vérifié</h3><pre>' + esc(JSON.stringify(d.resultat)) + '</pre>';
  }
  if (d.options) {
    h += '<div style="margin-top:6px"><small class="hint">Deux définitions existent — à vous de trancher :</small><br>';
    d.options.forEach((o, i) => { h += '<span class="opt" data-suf="' + esc(o.suffixe) + '">' + esc(o.label) + '</span>'; });
    h += '</div>';
  }
  if (d.cohorte_courante) {
    h += '<div class="meta"><span>cohorte courante : ' + d.cohorte_courante.n + ' patientes — '
       + esc(d.cohorte_courante.label) + '</span></div>';
  }
  if (d.suggestions && d.suggestions.length) {
    h += '<h3>Je peux répondre à ceci à la place</h3><div>';
    d.suggestions.forEach(x => {
      h += '<div class="def" style="cursor:pointer" onclick="document.getElementById(\'q\').value=this.dataset.q;ask()" data-q="'
         + esc(x) + '">→ ' + esc(x) + '</div>';
    });
    h += '</div>';
  }
  if (d.suite && d.suite.length) {
    h += '<h3>Et ensuite ?</h3><div>';
    d.suite.forEach(x => { h += '<div class="def">→ ' + esc(x) + '</div>'; });
    h += '</div>';
  }
  if (d.raison) {
    h += '<h3>Pourquoi pas de réponse</h3><pre>' + esc(JSON.stringify(d.raison, null, 1)) + '</pre>';
  }
  window.__lastN = (d.resultat && d.resultat.n !== undefined) ? d.resultat.n : null;
  const rn = document.getElementById('replaynote');
  if (rn) { rn.style.display = 'none'; }
  document.getElementById('body').innerHTML = h;
  document.querySelectorAll('.opt').forEach(el => {
    el.onclick = () => ask(lastQ + ' ' + el.dataset.suf);
  });
}

async function loadJournal() {
  const r = await fetch('/api/journal'); const rows = await r.json();
  if (!rows.length) return;
  let h = '<table><tr><th>heure</th><th>question</th><th>statut</th><th>n</th></tr>';
  rows.forEach(e => {
    h += '<tr><td>' + e.ts.slice(11) + '</td><td>' + esc((e.question || '').slice(0, 46)) + '…</td><td>'
       + esc(e.statut) + '</td><td>' + (e.resultat && e.resultat.n !== undefined ? e.resultat.n : '—') + '</td></tr>';
  });
  document.getElementById('journal').innerHTML = h + '</table>';
}

document.getElementById('go').onclick = ask;

async function replay() {
  if (!lastQ) { return; }
  const r = await fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({q: lastQ, role: document.getElementById('role').value,
                            halluciner: false, mode: document.getElementById('mode').value})});
  const d = await r.json();
  const now = (d.resultat && d.resultat.n !== undefined) ? d.resultat.n : null;
  const prev = window.__lastN;
  let msg;
  if (now === null || prev === null || prev === undefined) { msg = 'Rejeu effectué.'; }
  else if (now === prev) { msg = 'Rejoué : ' + now + ' — même question, même chiffre, même requête.'; }
  else { msg = 'Écart détecté : ' + prev + ' puis ' + now + ' — à investiguer.'; }
  const el = document.getElementById('replaynote');
  el.style.display = 'block';
  el.textContent = msg;
}
document.getElementById('replay').onclick = replay;
document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') ask(); });
loadJournal();

async function loadCatalogue() {
  const r = await fetch('/api/catalogue'); const cat = await r.json();
  let h = '<table>';
  cat.forEach(c => {
    h += '<tr><td><b>' + esc(c.nom) + '</b> <small class="hint">' + esc(c.version) + '</small><br>'
       + '<small class="hint">' + esc(c.libelle) + '</small><br>'
       + '<small class="hint">' + esc(c.valide_par) + '</small></td></tr>';
  });
  document.getElementById('catalogue').innerHTML = h + '</table>';
}
loadCatalogue();
const pre = new URLSearchParams(location.search).get('q');
if (pre) { document.getElementById('q').value = pre; ask(); }
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/catalogue"):
            cat = [{"nom": k, "version": v.get("version"), "libelle": v.get("libelle"),
                    "valide_par": v.get("valide_par"), "source": v.get("source")}
                   for k, v in brain.SEMANTIQUE.items()]
            self._send(200, json.dumps(cat, ensure_ascii=False).encode(), "application/json")
            return
        if self.path.startswith("/api/journal"):
            rows = []
            if brain.JOURNAL.exists():
                for line in brain.JOURNAL.read_text(encoding="utf-8").splitlines()[-12:]:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            self._send(200, json.dumps(rows[::-1], ensure_ascii=False).encode(), "application/json")
            return
        page = PAGE.replace("__EXEMPLES__", json.dumps(EXEMPLES, ensure_ascii=False))
        self._send(200, page.encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if not self.path.startswith("/api/ask"):
            self._send(404, b"{}", "application/json")
            return
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n) or b"{}")
        try:
            res = chat.traiter(payload.get("q", ""), payload.get("role", "chercheur"),
                               bool(payload.get("halluciner")), payload.get("mode", "regles"),
                               payload.get("sid") or "")
        except Exception as e:                                   # noqa: BLE001
            res = {"statut": "rejete", "message": f"Erreur interne : {type(e).__name__}", "ms": 0}
        self._send(200, json.dumps(res, ensure_ascii=False).encode(), "application/json")

    def log_message(self, *a):                                   # silence
        pass


if __name__ == "__main__":
    if not brain.DB.exists():
        brain.build_db()
    print("http://127.0.0.1:8099")
    ThreadingHTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
