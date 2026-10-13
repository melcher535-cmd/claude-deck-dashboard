// Zusatzfunktionen: Push, Wecker, Feed, Vergleich, Arbeitszeit, Kosten-Details, Notizen, Graph, Gesundheit, Sicherung, Kurzbefehl, Widgets.
// Laeuft nach dem Hauptskript und nutzt dessen Funktionen (esc, fmt, karte, stat, api, postJson, toast, SEITEN, daten ...).

// --- Hilfen -------------------------------------------------------------------------------------
const SEITEN_HAKEN = {};            // Seite -> Funktion, die nach dem Rendern Ereignisse verdrahtet
const seitenDaten = {};             // Seite -> zuletzt geladene Daten
const mitDaten = (route, bauen) => args => { seitenDaten[route] = args.x; return bauen(args); };
const speicherLesen = (schluessel, standard) => { try { return JSON.parse(localStorage.getItem(schluessel)) ?? standard; } catch (e) { console.warn("Speicher nicht lesbar:", e); return standard; } };
const speicherSchreiben = (schluessel, wert) => { try { localStorage.setItem(schluessel, JSON.stringify(wert)); } catch (e) { console.warn("Speicher nicht beschreibbar:", e); } };
const datum = ms => new Date(ms).toLocaleString(DECK_LOCALE, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const usd = n => "$" + n.toFixed(2);
const wartend = () => LADEN;

function linkEinfuegen(gruppe, href, label) {
  const ziel = gruppe ? document.querySelector(`[data-gruppe="${gruppe}"]`).parentElement : document.querySelector('.seitenleiste > a[href="#konto"]');
  const a = document.createElement("a");
  a.className = "menuelink"; a.href = href; a.textContent = label;
  if (gruppe) ziel.appendChild(a); else { a.innerHTML = '<span class="sym"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 5.6-7 10-7 10z"/></svg></span>' + label; ziel.before(a); }
}
linkEinfuegen("sessions", "#sessions/vergleich", "Vergleich");
linkEinfuegen("sessions", "#sessions/arbeitszeit", "Arbeitszeit");
linkEinfuegen("sessions", "#sessions/kostendetails", "Kosten nach Projekt");
linkEinfuegen("vault", "#vault/notizen", "Notizen lesen");
linkEinfuegen("vault", "#vault/graphmobil", "Graph zum Anfassen");
linkEinfuegen(null, "#gesundheit", "Gesundheit");

// --- Kosten nach Projekt ------------------------------------------------------------------------
SEITEN["sessions/kostendetails"] = ["Claude Code", "Kosten nach Projekt", ({ x }) => `<div class="raster kpis">${stat(usd(x.total), "Geschätzt gesamt", "laut costs.jsonl")}</div><div class="raster zwei">` +
  karte("Nach Projekt (Ordner der Verläufe)", balkenzeilen(x.projects.map(([k, v]) => [k, v]))) + karte("Pro Woche in $", saeulen(x.weeks.map(([k, v]) => [k, v]), FARBEN[1])) +
  karte("Teuerste Sessions", `<table>${x.sessions.map(([id, titel, v]) => `<tr class="klick" onclick="location.hash='chat/${esc(id)}'" data-session="${esc(id)}"><td>${esc(titel)}</td><td class="r">${usd(v)}</td></tr>`).join("")}</table>`) + "</div>", "/api/costs-detail"];

// --- Arbeitszeit (Gantt) ------------------------------------------------------------------------
SEITEN["sessions/arbeitszeit"] = ["Claude Code", "Arbeitszeit", ({ x }) => {
  const B = 600, Z = 26, L = 44, farbe = id => FARBEN[[...id].reduce((a, c) => a + c.charCodeAt(0), 0) % FARBEN.length];
  const zeilen = x.slice().reverse().map(([tag, balken], i) => `<text x="${L - 6}" y="${i * Z + 17}" text-anchor="end">${tag.slice(5)}</text>` +
    balken.map(b => `<rect class="gantt-balken" x="${L + b.from / 24 * (B - L)}" y="${i * Z + 4}" width="${Math.max(2, (b.to - b.from) / 24 * (B - L))}" height="${Z - 8}" rx="4" fill="${farbe(b.id)}" data-tip="${esc(b.title || b.id.slice(0, 8))} · ${Math.floor(b.from)}:${String(Math.round(b.from % 1 * 60)).padStart(2, "0")}–${Math.floor(b.to)}:${String(Math.round(b.to % 1 * 60)).padStart(2, "0")} Uhr (UTC)" data-session="${esc(b.id)}" onclick="location.hash='chat/${esc(b.id)}'"/>`).join("")).join("");
  const gitter = [0, 6, 12, 18, 24].map(h => `<line x1="${L + h / 24 * (B - L)}" x2="${L + h / 24 * (B - L)}" y1="0" y2="${x.length * Z}" stroke="var(--trenner)"/><text x="${L + h / 24 * (B - L)}" y="${x.length * Z + 14}" text-anchor="${h === 24 ? "end" : h === 0 ? "start" : "middle"}">${h}</text>`).join("");
  return karte("Wann liefen Sessions (UTC, letzte 7 Tage)", `<svg viewBox="0 0 ${B} ${x.length * Z + 20}">${gitter}${zeilen}</svg><p class="kontext">Eine Pause von mehr als 20 Minuten beendet einen Abschnitt. Tippe auf einen Balken für den Chat.</p>`);
}, "/api/gantt"];

// --- Fehler-Muster --------------------------------------------------------------------------------
{
  const fehlerAlt = SEITEN["sessions/fehler"][2];
  SEITEN["sessions/fehler"][2] = args => `<div class="raster">${karte("Häufige Ursachen", args.x.patterns.length ? `<table>${args.x.patterns.map(p => `<tr><td><b>${esc(p.tool)}</b> <span class="leise">· ${p.anzahl}×</span><br><span class="leise mono">${esc(p.beispiel)}</span></td>` +
    `<td style="width:90px">${saeulen(p.wochen.map(([w, n]) => [w.slice(5), n]), FARBEN[5]).replace(/<text[^>]*>.*?<\/text>/g, "")}</td></tr>`).join("")}</table>` : leer("Keine Fehler", "Es gibt nichts zu gruppieren."))}</div>` + fehlerAlt(args);
}

// --- Vergleich zweier Sessions ------------------------------------------------------------------
let vergleichA = "", vergleichB = "";
SEITEN["sessions/vergleich"] = ["Claude Code", "Vergleich", ({ s }) => {
  const optionen = id => `<option value="">– Session wählen –</option>` + s.sessions.slice(0, 120).map(r => `<option value="${esc(r.id)}"${r.id === id ? " selected" : ""}>${esc((r.title || r.id.slice(0, 8)).slice(0, 48))} · ${new Date(r.last).toLocaleDateString(DECK_LOCALE)}</option>`).join("");
  return `<div class="raster zwei"><label class="feld">Session A<select class="auswahl" id="vg-a">${optionen(vergleichA)}</select></label><label class="feld">Session B<select class="auswahl" id="vg-b">${optionen(vergleichB)}</select></label></div><div id="vg-ergebnis"></div>`;
}];
SEITEN_HAKEN["sessions/vergleich"] = async () => {
  if (!document.getElementById("vg-a")) return;
  const zeigen = async () => {
    vergleichA = document.getElementById("vg-a").value; vergleichB = document.getElementById("vg-b").value;
    const ziel = document.getElementById("vg-ergebnis");
    if (!vergleichA || !vergleichB) { ziel.innerHTML = leer("Zwei Sessions wählen", "Dann stehen Dauer, Tokens, Werkzeuge und Dateien nebeneinander."); return; }
    ziel.innerHTML = LADEN;
    const [a, b] = await Promise.all([vergleichA, vergleichB].map(id => api("/api/session-info/" + id)));
    const dauer = r => r.first && r.last ? minuten(Math.round((new Date(r.last) - new Date(r.first)) / 60000)) : "–";
    const zeilen = [["Titel", r => esc(r.title)], ["Ordner", r => `<span class="mono">${esc(r.cwd)}</span>`], ["Dauer", dauer], ["Nachrichten von dir", r => r.messages], ["Output-Tokens", r => fmt(r.output)],
      ["Neuer Input", r => fmt(r.input + r.cache_write)], ["Cache gelesen", r => fmt(r.cache_read)], ["Geschätzte Kosten", r => r.cost ? usd(r.cost) : "–"],
      ["Werkzeuge", r => r.tools.map(([n, c]) => `${esc(n)} ${c}`).join(", ") || "–"], ["Geänderte Dateien", r => r.files.length]];
    ziel.innerHTML = karte("Nebeneinander", `<table><thead><tr><th></th><th>A</th><th>B</th></tr></thead><tbody>${zeilen.map(([name, f]) => `<tr><td class="leise">${name}</td><td>${f(a)}</td><td>${f(b)}</td></tr>`).join("")}</tbody></table>`);
  };
  document.getElementById("vg-a").onchange = zeigen; document.getElementById("vg-b").onchange = zeigen;
  zeigen();
};

// --- Gesundheit -----------------------------------------------------------------------------------
const gesundheitKarten = h => {
  const zertFarbe = h.cert_days == null ? "dienst-aus" : h.cert_days < h.cert_warn_days ? "dienst-aus" : "dienst-ok";
  return `<div class="raster kpis">${stat(h.cert_days == null ? "?" : h.cert_days + " T", "Zertifikat gültig", `<span class="${zertFarbe}">${h.cert_days == null ? "nicht lesbar" : h.cert_days < h.cert_warn_days ? "bald erneuern" : "in Ordnung"}</span>`)}` +
    `${stat(GB(h.disk_free), "Platte frei", `von ${GB(h.disk_total)}`)}${stat(h.journal || "?", "Journal", "Größe der Logs")}${stat((h.backups[0] ? vor(h.backups[0].mtime * 1000) : "nie"), "Letzte Sicherung", h.backups.length + " vorhanden")}</div>` +
    `<div class="raster zwei">${karte("Fehler des Dashboard-Servers (letzte Zeilen)", h.errors.length ? `<pre class="roh">${esc(h.errors.join("\n"))}</pre>` : leer("Keine Fehler", "Im Journal von claude-deck steht nichts Auffälliges."))}` +
    `${karte("Dienst", `<p>Läuft seit<br><b>${esc(h.service_since)}</b></p>`)}</div>`;
};
SEITEN["gesundheit"] = ["VPS", "Gesundheit", ({ x }) => gesundheitKarten(x), "/api/health"];

GRUPPEN.gesundheit = ["VPS", "#gesundheit"];

// --- Notizen lesen --------------------------------------------------------------------------------
function markdownHtml(text) {
  const rein = text.replace(/^---\n[\s\S]*?\n---\n/, "");
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, '<span class="mono">$1</span>').replace(/\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]/g, (_, n) => `<a href="#" class="wiki" data-ziel="${n.trim()}">${n.trim()}</a>`);
  let html = "", liste = false, code = false;
  for (const zeile of rein.split("\n")) {
    if (zeile.startsWith("```")) { html += code ? "</pre>" : '<pre class="roh">'; code = !code; continue; }
    if (code) { html += esc(zeile) + "\n"; continue; }
    const kopfzeile = zeile.match(/^(#{1,4})\s+(.*)/), punkt = zeile.match(/^\s*[-*]\s+(.*)/);
    if (liste && !punkt) { html += "</ul>"; liste = false; }
    if (kopfzeile) html += `<h${kopfzeile[1].length + 1} class="notiz-h">${inline(kopfzeile[2])}</h${kopfzeile[1].length + 1}>`;
    else if (punkt) { if (!liste) { html += "<ul>"; liste = true; } html += `<li>${inline(punkt[1])}</li>`; }
    else if (zeile.trim()) html += `<p>${inline(zeile)}</p>`;
  }
  return html + (liste ? "</ul>" : "") + (code ? "</pre>" : "");
}
async function notizOeffnen(pfad) {
  const n = await api("/api/note?path=" + encodeURIComponent(pfad));
  document.getElementById("notiz-lesen").innerHTML = karte(esc(n.title), `<p class="kontext">${esc(n.path)} · ${datum(n.mtime * 1000)}</p><div class="notiz">${markdownHtml(n.text)}</div>`);
  document.getElementById("notiz-lesen").scrollIntoView({ behavior: "smooth", block: "start" });
}
const notizListe = liste => liste.length ? `<table>${liste.map(n => `<tr class="klick notiz-zeile" data-pfad="${esc(n.path)}"><td>${esc(n.title)}${n.snippet ? `<br><span class="leise">${esc(n.snippet)}</span>` : ""}</td><td class="leise r">${datum(n.mtime * 1000)}</td></tr>`).join("")}</table>` : leer("Keine Notizen", "Versuche einen anderen Begriff.");
SEITEN["vault/notizen"] = ["Obsidian · graphify", "Notizen", ({ x }) => `<div class="suche suche-gross"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg><input id="nz-suche" placeholder="Volltext im Vault suchen" autocomplete="off"></div>` +
  `<div id="nz-treffer">${karte("Zuletzt bearbeitet", notizListe(x))}</div><div id="notiz-lesen"></div>`, "/api/notes-recent"];
SEITEN_HAKEN["vault/notizen"] = () => {
  if (!document.getElementById("nz-suche")) return;
  const feld = document.getElementById("nz-suche"), treffer = document.getElementById("nz-treffer"), recent = seitenDaten["vault/notizen"];
  let timer;
  feld.oninput = () => { clearTimeout(timer); timer = setTimeout(async () => {
    const q = feld.value.trim();
    treffer.innerHTML = karte(q.length >= 2 ? "Treffer" : "Zuletzt bearbeitet", notizListe(q.length >= 2 ? await api("/api/notes-search?q=" + encodeURIComponent(q)) : recent));
  }, 250); };
  document.getElementById("seite").onclick = async e => {
    const zeile = e.target.closest(".notiz-zeile"), wiki = e.target.closest(".wiki");
    if (zeile) notizOeffnen(zeile.dataset.pfad);
    if (wiki) { e.preventDefault(); const treffer = await api("/api/notes-search?q=" + encodeURIComponent(wiki.dataset.ziel)); const genau = treffer.find(t => t.title === wiki.dataset.ziel) || treffer[0]; if (genau) notizOeffnen(genau.path); else toast("Notiz nicht gefunden"); }
  };
  if (window.deckNotizPfad) { notizOeffnen(window.deckNotizPfad); window.deckNotizPfad = null; }
};

// --- Graph zum Anfassen -----------------------------------------------------------------------------
SEITEN["vault/graphmobil"] = ["Obsidian · graphify", "Graph zum Anfassen", ({ x }) => `<div class="suche suche-gross"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg><input id="gr-suche" placeholder="Knoten hervorheben" autocomplete="off"></div>` +
  `<div class="graphrahmen"><canvas id="gr-leinwand"></canvas></div><div id="gr-info">${leer("Tippe einen Knoten an", "Ziehen verschiebt, zwei Finger zoomen, Doppeltipp setzt zurück.")}</div>`, "/api/graph"];
SEITEN_HAKEN["vault/graphmobil"] = () => {
  if (!document.getElementById("gr-leinwand")) return;
  const g = seitenDaten["vault/graphmobil"], N = g.nodes.length, K = g.communities.length, leinwand = document.getElementById("gr-leinwand"), ctx = leinwand.getContext("2d");
  const pos = new Float32Array(N * 2), nachbarn = Array.from({ length: N }, () => []);
  g.edges.forEach(([a, b]) => { nachbarn[a].push(b); nachbarn[b].push(a); });
  // Kraeftelayout nach Fruchterman und Reingold: alle Paare stossen sich ab, Kanten ziehen, eine schwache Schwerkraft haelt alles zusammen
  const ideal = 30;
  for (let i = 0; i < N; i++) { const r = Math.sqrt(i / N) * 380, t = i * 2.399963; pos[2 * i] = Math.cos(t) * r; pos[2 * i + 1] = Math.sin(t) * r; }
  const verschiebung = new Float32Array(N * 2);
  for (let durchlauf = 0, hitze = 70; durchlauf < 220; durchlauf++, hitze *= .985) {
    verschiebung.fill(0);
    for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
      const dx = pos[2 * i] - pos[2 * j], dy = pos[2 * i + 1] - pos[2 * j + 1], d2 = dx * dx + dy * dy + .01;
      if (d2 > 90000) continue;                                      // weiter als 300 px: ohne Wirkung
      const f = ideal * ideal / d2;
      verschiebung[2 * i] += dx * f; verschiebung[2 * i + 1] += dy * f; verschiebung[2 * j] -= dx * f; verschiebung[2 * j + 1] -= dy * f;
    }
    g.edges.forEach(([a, b]) => {
      const dx = pos[2 * a] - pos[2 * b], dy = pos[2 * a + 1] - pos[2 * b + 1], d = Math.hypot(dx, dy) || .1, f = d / ideal;
      verschiebung[2 * a] -= dx * f; verschiebung[2 * a + 1] -= dy * f; verschiebung[2 * b] += dx * f; verschiebung[2 * b + 1] += dy * f;
    });
    for (let i = 0; i < N; i++) {
      const vx = verschiebung[2 * i] - pos[2 * i] * .015, vy = verschiebung[2 * i + 1] - pos[2 * i + 1] * .015, l = Math.hypot(vx, vy) || 1, s = Math.min(l, hitze) / l;
      pos[2 * i] += vx * s; pos[2 * i + 1] += vy * s;
    }
  }
  const ansicht = { x: 0, y: 0, z: 1 }, farbe = c => `hsl(${Math.round(c / K * 360)} 70% 55%)`;
  let gewaehlt = -1, suche = "";
  const groesse = () => { const r = leinwand.parentElement.getBoundingClientRect(), dpr = devicePixelRatio || 1; leinwand.width = r.width * dpr; leinwand.height = r.height * dpr; leinwand.style.width = r.width + "px"; leinwand.style.height = r.height + "px"; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); zeichnen(); };
  const zuBild = (x, y) => [(x - ansicht.x) * ansicht.z + leinwand.clientWidth / 2, (y - ansicht.y) * ansicht.z + leinwand.clientHeight / 2];
  function zeichnen() {
    ctx.clearRect(0, 0, leinwand.clientWidth, leinwand.clientHeight);
    const aktiv = new Set(gewaehlt >= 0 ? [gewaehlt, ...nachbarn[gewaehlt]] : []), treffer = suche ? new Set(g.nodes.map((n, i) => n.l.toLowerCase().includes(suche) ? i : -1).filter(i => i >= 0)) : null;
    ctx.lineWidth = 1;
    g.edges.forEach(([a, b]) => { const hell = aktiv.has(a) && aktiv.has(b); ctx.strokeStyle = hell ? "rgba(120,130,150,.9)" : "rgba(120,130,150,.18)"; const [x1, y1] = zuBild(pos[2 * a], pos[2 * a + 1]), [x2, y2] = zuBild(pos[2 * b], pos[2 * b + 1]); ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); });
    g.nodes.forEach((n, i) => {
      const [x, y] = zuBild(pos[2 * i], pos[2 * i + 1]), r = (2.5 + Math.sqrt(n.d) * 1.1) * Math.max(.7, Math.min(1.6, ansicht.z));
      ctx.globalAlpha = (gewaehlt >= 0 && !aktiv.has(i)) || (treffer && !treffer.has(i)) ? .25 : 1;
      ctx.fillStyle = farbe(n.c); ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
      if (i === gewaehlt) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke(); ctx.lineWidth = 1; }
      if (aktiv.has(i) || (treffer && treffer.has(i)) || n.d >= 14 || ansicht.z > 2.2 && n.d >= 4) { ctx.globalAlpha = 1; ctx.fillStyle = getComputedStyle(document.body).color; ctx.font = "11px system-ui"; ctx.fillText(n.l.slice(0, 26), x + r + 3, y + 4); }
    });
    ctx.globalAlpha = 1;
  }
  function info() {
    const n = g.nodes[gewaehlt];
    document.getElementById("gr-info").innerHTML = karte(esc(n.l), `<p class="kontext">${esc(g.communities[n.c])} · ${n.d} Verbindungen</p>` +
      `<div class="chips">${nachbarn[gewaehlt].slice(0, 30).map(i => `<span class="nachbar" data-i="${i}">${esc(g.nodes[i].l.slice(0, 30))}</span>`).join("")}</div>` + (n.f ? `<button class="tab aktiv" id="gr-notiz" type="button">Notiz lesen</button>` : ""));
    const knopf = document.getElementById("gr-notiz"); if (knopf) knopf.onclick = () => { window.deckNotizPfad = n.f; location.hash = "vault/notizen"; };
    document.querySelectorAll(".nachbar").forEach(el => { el.onclick = () => { gewaehlt = +el.dataset.i; ansicht.x = pos[2 * gewaehlt]; ansicht.y = pos[2 * gewaehlt + 1]; info(); zeichnen(); }; });
  }
  const zeiger = new Map();
  let abstand = 0, bewegt = false, letzterTipp = 0, letzterOrt = [0, 0];
  leinwand.addEventListener("pointerdown", e => { leinwand.setPointerCapture(e.pointerId); zeiger.set(e.pointerId, [e.clientX, e.clientY]); bewegt = false; abstand = zeiger.size === 2 ? Math.hypot(...[...zeiger.values()].reduce((a, p) => [a[0] - p[0], a[1] - p[1]], [0, 0])) : 0; });
  leinwand.addEventListener("pointermove", e => {
    if (!zeiger.has(e.pointerId)) return;
    const alt = zeiger.get(e.pointerId); zeiger.set(e.pointerId, [e.clientX, e.clientY]);
    if (zeiger.size === 1) { ansicht.x -= (e.clientX - alt[0]) / ansicht.z; ansicht.y -= (e.clientY - alt[1]) / ansicht.z; bewegt = bewegt || Math.hypot(e.clientX - alt[0], e.clientY - alt[1]) > 1; }
    else if (zeiger.size === 2) { const [p, q] = [...zeiger.values()], neu = Math.hypot(p[0] - q[0], p[1] - q[1]); if (abstand) ansicht.z = Math.min(6, Math.max(.15, ansicht.z * neu / abstand)); abstand = neu; bewegt = true; }
    zeichnen();
  });
  leinwand.addEventListener("pointerup", e => {
    zeiger.delete(e.pointerId);
    if (bewegt || zeiger.size) return;
    const r = leinwand.getBoundingClientRect(); let beste = -1, bd = 22;
    for (let i = 0; i < N; i++) { const [x, y] = zuBild(pos[2 * i], pos[2 * i + 1]), d = Math.hypot(x - (e.clientX - r.left), y - (e.clientY - r.top)); if (d < bd) { bd = d; beste = i; } }
    if (Date.now() - letzterTipp < 300 && Math.hypot(e.clientX - letzterOrt[0], e.clientY - letzterOrt[1]) < 30) { einpassen(); gewaehlt = -1; }   // Doppeltipp: zuruecksetzen
    else if (beste >= 0) { gewaehlt = beste; info(); }
    letzterTipp = Date.now(); letzterOrt = [e.clientX, e.clientY]; zeichnen();
  });
  leinwand.addEventListener("wheel", e => { e.preventDefault(); ansicht.z = Math.min(6, Math.max(.15, ansicht.z * (e.deltaY < 0 ? 1.12 : .89))); zeichnen(); }, { passive: false });
  document.getElementById("gr-suche").oninput = e => { suche = e.target.value.trim().toLowerCase(); zeichnen(); };
  const einpassen = () => {
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    for (let i = 0; i < N; i++) { x0 = Math.min(x0, pos[2 * i]); x1 = Math.max(x1, pos[2 * i]); y0 = Math.min(y0, pos[2 * i + 1]); y1 = Math.max(y1, pos[2 * i + 1]); }
    ansicht.x = (x0 + x1) / 2; ansicht.y = (y0 + y1) / 2;
    ansicht.z = .92 * Math.min(leinwand.clientWidth / (x1 - x0 + 40), leinwand.clientHeight / (y1 - y0 + 40)) || 1;
  };
  groesse();
  einpassen();
  zeichnen();
  addEventListener("resize", () => { groesse(); });
};

["vault/notizen", "vault/graphmobil"].forEach(route => { SEITEN[route][2] = mitDaten(route, SEITEN[route][2]); });

// --- Seiten-Haken: nach jedem Rendern der Seite verdrahten -----------------------------------------
const aktuelleRoute = () => SEITEN[location.hash.slice(1)] ? location.hash.slice(1) : "sessions/uebersicht";
new MutationObserver(() => { const haken = SEITEN_HAKEN[aktuelleRoute()]; if (haken && document.getElementById("seite").children.length) haken(); }).observe(document.getElementById("seite"), { childList: true });

// --- Wecker: Bescheid sagen, wenn eine Session fertig ist -----------------------------------------
let weckerListe = {};
async function weckerLaden() { try { weckerListe = await api("/api/watch"); } catch (e) { console.warn("Wecker nicht ladbar:", e); } }
async function weckerSetzen(id, an) {
  await postJson("/api/watch/" + id, { on: an });
  await weckerLaden();
  toast(an ? "Ich melde mich, wenn die Session fertig ist" : "Wecker aus");
}
{
  const menueAlt = menueOeffnen;
  menueOeffnen = function (x, y, id) {
    menueAlt(x, y, id);
    const menue = document.querySelector(".kontextmenue");
    if (!menue) return;
    const zusatz = [[weckerListe[id] ? "Wecker ausschalten" : "Bescheid sagen, wenn fertig", () => weckerSetzen(id, !weckerListe[id])],
      [vergleichA && vergleichA !== id ? "Mit vorgemerkter Session vergleichen" : "Zum Vergleich vormerken", () => { if (vergleichA && vergleichA !== id) { vergleichB = id; location.hash = "sessions/vergleich"; } else { vergleichA = id; toast("Vorgemerkt: wähle eine zweite Session"); } }]];
    for (const [text, aktion] of zusatz) {
      const knopf = document.createElement("button");
      knopf.type = "button"; knopf.setAttribute("role", "menuitem"); knopf.textContent = text;
      knopf.addEventListener("click", e => { e.stopPropagation(); menueSchliessen(); aktion(); });
      menue.appendChild(knopf);
    }
  };
}
{
  const chatAlt = chatSeite;
  chatSeite = function (id) {
    const lauf = chatAlt(id);
    const export_ = document.getElementById("export");
    if (export_) {
      export_.insertAdjacentHTML("afterend", '<button class="tab" id="cx-wecker" type="button"></button><button class="tab" id="cx-note" type="button">In Vault</button>');
      const wecker = document.getElementById("cx-wecker"), zeigen = () => { wecker.textContent = weckerListe[id] ? "Wecker aus" : "Wecker"; };
      zeigen();
      wecker.onclick = async () => { await weckerSetzen(id, !weckerListe[id]); zeigen(); };
      document.getElementById("cx-note").onclick = async () => {
        if (!confirm("Eine Notiz mit den Kennzahlen dieses Chats im Vault anlegen?")) return;
        const r = await postJson("/api/note-from-chat/" + id, {});
        toast(r.ok ? "Notiz angelegt: " + r.path : r.error || "Notiz nicht angelegt");
      };
    }
    return lauf;
  };
}

// --- Aktivitaets-Feed und Live-Verbindung ---------------------------------------------------------
let feed = [];
const feedPanel = document.createElement("div");
feedPanel.id = "feedpanel"; feedPanel.hidden = true;
document.getElementById("ticker").after(feedPanel);
document.getElementById("ticker").onclick = () => { feedPanel.hidden = !feedPanel.hidden; feedZeigen(); };
function feedZeigen() {
  if (feedPanel.hidden) return;
  feedPanel.innerHTML = `<p class="label">Letzte Aktionen</p>` + (feed.length ? `<table>${feed.slice(0, 20).map(e => `<tr class="klick" onclick="location.hash='chat/${esc(e.id)}'" data-session="${esc(e.id)}"><td class="leise">${new Date(e.t * 1000).toLocaleTimeString(DECK_LOCALE)}</td><td><b>${esc(e.name)}</b></td><td>${esc(e.action)}</td></tr>`).join("")}</table>` : leer("Noch nichts", "Sobald eine Session etwas tut, steht es hier."));
}
function streamStarten() {
  if (!window.EventSource) return;
  const quelle = new EventSource("/api/stream");
  quelle.onmessage = e => {
    if (!daten) return;
    const d = JSON.parse(e.data);
    daten.live = d.live; feed = d.feed;
    tickerZeigen(); feedZeigen();
    const el = document.getElementById("live");
    if (el) { el.innerHTML = liveKarte(); labelTabellen(); }
  };
  quelle.onerror = () => { quelle.close(); api("/api/account").then(() => setTimeout(streamStarten, 5000)).catch(e => console.warn("Live-Verbindung:", e)); };
}

// --- Konto: Push, Kurzbefehl-Schluessel, Sicherung --------------------------------------------------
const base64ZuBytes = text => Uint8Array.from(atob(text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - text.length % 4) % 4)), c => c.charCodeAt(0));
async function pushStatus() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return { unterstuetzt: false, aktiv: false };
  const reg = await navigator.serviceWorker.getRegistration();
  return { unterstuetzt: true, aktiv: !!(reg && await reg.pushManager.getSubscription()) };
}
async function pushAnschalten() {
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  if (await Notification.requestPermission() !== "granted") throw new Error("Die Erlaubnis für Benachrichtigungen fehlt.");
  const { key } = await api("/api/push/key");
  const abo = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: base64ZuBytes(key) });
  const r = await postJson("/api/push/subscribe", abo.toJSON());
  if (!r.ok) throw new Error(r.error || "Abo nicht gespeichert");
}
async function pushAusschalten() {
  const reg = await navigator.serviceWorker.getRegistration(), abo = reg && await reg.pushManager.getSubscription();
  if (abo) { await postJson("/api/push/unsubscribe", { endpoint: abo.endpoint }); await abo.unsubscribe(); }
}
const GROESSE = n => (n / 1e6).toFixed(1) + " MB";
async function kontoErgaenzen() {
  const raster = document.querySelector("#seite .raster");
  if (!raster) return;
  const [status, schluessel, sicherungen] = await Promise.all([pushStatus(), api("/api/apikey"), api("/api/backup-list")]);
  raster.insertAdjacentHTML("beforeend",
    karte("Benachrichtigungen", schalterZeile("s-push", "Push auf diesem Gerät", status.unterstuetzt ? "Limits, fertige Sessions und gesperrte IPs" : "Nicht verfügbar: auf dem iPhone das Dashboard zuerst zum Home-Bildschirm hinzufügen (iOS 16.4 oder neuer)", status.aktiv) +
      `<div class="zeile"><div>Testnachricht<small>An alle angemeldeten Geräte</small></div><button class="tab" id="push-test" type="button">Senden</button></div>`) +
    karte("Kurzbefehl-Schlüssel", `<p class="kontext">Für einen Siri-Kurzbefehl „Wie viel Limit habe ich noch?“. Der Schlüssel darf nur diese eine Abfrage lesen: <span class="mono">/api/shortcut/usage</span>. Gespeichert wird nur eine Prüfsumme, er wird einmal angezeigt.</p>` +
      `<div class="zeile"><div>${schluessel.count} Schlüssel aktiv</div><div><button class="tab" id="key-neu" type="button">Neu erzeugen</button> <button class="tab" id="key-weg" type="button">Alle widerrufen</button></div></div><div id="key-anzeige"></div>`) +
    karte("Sicherung", `<p class="kontext">Sichert dieses Dashboard und deine Claude-Einstellungen nach <span class="mono">/root/deck-backups</span>. Passwörter, Schlüssel und Zugangsdateien bleiben draußen. Die letzten 10 bleiben erhalten.</p>` +
      `<div class="zeile"><div>${sicherungen.length ? "Zuletzt: " + vor(sicherungen[0].mtime * 1000) + " · " + GROESSE(sicherungen[0].size) : "Noch keine Sicherung"}</div><button class="tab aktiv" id="sicherung-neu" type="button">Jetzt sichern</button></div>`));
  const schalter = document.getElementById("s-push");
  schalter.onclick = async () => {
    const an = schalter.getAttribute("aria-checked") !== "true";
    try { if (an) await pushAnschalten(); else await pushAusschalten(); schalter.setAttribute("aria-checked", an); toast(an ? "Push ist an" : "Push ist aus"); }
    catch (e) { console.warn("Push:", e); schalter.setAttribute("aria-checked", !an); toast(e.message); }
  };
  document.getElementById("push-test").onclick = async () => { const r = await postJson("/api/push/test", {}); toast(r.sent ? `Gesendet an ${r.sent} Gerät(e)` : "Kein Gerät angemeldet oder Zustellung fehlgeschlagen"); };
  document.getElementById("key-neu").onclick = async () => {
    const r = await postJson("/api/apikey/create", {});
    document.getElementById("key-anzeige").innerHTML = `<p class="kontext">Kopiere ihn jetzt, er wird nicht noch einmal gezeigt:</p><p class="mono" style="overflow-wrap:anywhere;user-select:all">${esc(r.key)}</p>` +
      `<p class="kontext">Kurzbefehl: „Inhalt von URL abrufen“ mit <span class="mono">${esc(location.origin)}/api/shortcut/usage</span>, Methode GET, Kopfzeile <span class="mono">Authorization: Bearer …</span>, dann „Wert für text im Wörterbuch abrufen“ und „Text sprechen“.</p>`;
  };
  document.getElementById("key-weg").onclick = async () => { await postJson("/api/apikey/revoke", {}); toast("Alle Schlüssel widerrufen"); kontoSeite(); };
  document.getElementById("sicherung-neu").onclick = async e => { e.target.disabled = true; const r = await postJson("/api/backup", {}); toast(r.ok ? "Gesichert: " + r.file : r.error || "Sicherung fehlgeschlagen"); kontoSeite(); };
}
{
  const kontoAlt = kontoSeite;
  kontoSeite = async function () { await kontoAlt(); await kontoErgaenzen(); };
}

// --- Widgets auf der Uebersicht, zuletzt besucht, Hinweise ---------------------------------------
const WIDGET_NAMEN = { hinweise: "Hinweise", zuletzt: "Zuletzt besucht", usage: "Limits", live: "Laufende Sessions", markiert: "Markierte Sessions", heute: "Heute", gesamt: "Gesamt", diagramme: "Diagramme" };
let gesundheitStand = { zeit: 0, daten: null };
async function gesundheitHinweis() {
  if (Date.now() - gesundheitStand.zeit > 600000) { try { gesundheitStand = { zeit: Date.now(), daten: await api("/api/health") }; } catch (e) { console.warn("Gesundheit:", e); return ""; } }
  const h = gesundheitStand.daten;
  return h && h.cert_days != null && h.cert_days < h.cert_warn_days ? `<p class="warnung${h.cert_days < 5 ? " rot" : ""}">Das HTTPS-Zertifikat läuft in ${h.cert_days} Tagen ab. Caddy erneuert es normalerweise selbst, prüfe es unter Gesundheit.</p>` : "";
}
function zuletztChips() {
  const liste = speicherLesen("deck-zuletzt", []).filter(r => SEITEN[r]);
  return liste.length ? `<div class="chips">${liste.map(r => `<a class="chip" href="#${r}">${esc(SEITEN[r][1])}</a>`).join("")}</div>` : "";
}
async function widgetLayoutLaden() {
  const vorgabe = { order: [], hidden: [], custom: [] };
  try {
    let layout = await api("/api/widgets");
    const alt = speicherLesen("deck-widgets", null);
    if (alt && !layout.order.length && !layout.hidden.length && !layout.custom.length) {  // einmalig: bisherige Anordnung aus dem Browser auf den Server uebernehmen
      layout = { order: alt.order || [], hidden: alt.hidden || [], custom: [] };
      if ((await postJson("/api/widgets", layout)).ok) speicherSchreiben("deck-widgets", null);
    }
    return layout;
  } catch (e) { console.warn("Widgets nicht ladbar:", e); return vorgabe; }
}
const ZIEH_SCHWELLE = 4;
const FEDER = "cubic-bezier(.32,.72,0,1)";
// Ziehen wie auf dem iPhone: Widget an der Kopfleiste fassen, die anderen weichen aus (FLIP), beim Loslassen rastet es im Raster ein
function widgetZiehen(buehne, speichern) {
  const ruhig = () => document.body.classList.contains("ruhe") || matchMedia("(prefers-reduced-motion: reduce)").matches;
  const sichtbar = () => [...buehne.children].filter(w => getComputedStyle(w).display !== "none");
  buehne.addEventListener("pointerdown", e => {
    const kopf = e.target.closest(".widget-steuer");
    if (!kopf || e.target.closest("button") || !document.getElementById("seite").classList.contains("widgets-bearbeiten") || e.button > 0) return;
    const s = kopf.closest(".widget");
    const start = { x: e.clientX, y: e.clientY, ziehen: false, versatz: null, sperre: null };
    const natuerlich = () => { s.style.transform = ""; return s.getBoundingClientRect(); };
    const bewegen = ev => {
      if (!start.ziehen) {
        if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < ZIEH_SCHWELLE) return;
        start.ziehen = true; s.classList.add("zieht"); s.classList.remove("landet");
        const r = natuerlich(); start.versatz = { x: start.x - r.left, y: start.y - r.top };
        if (navigator.vibrate) navigator.vibrate(8);
      }
      const r = natuerlich();
      const unterZeiger = w => { const b = w.getBoundingClientRect(); return ev.clientX > b.left && ev.clientX < b.right && ev.clientY > b.top && ev.clientY < b.bottom; };
      if (start.sperre && !unterZeiger(start.sperre)) start.sperre = null;  // ein getauschtes Widget bleibt gesperrt, bis der Zeiger es verlassen hat: sonst wird es sofort zurueckgetauscht (Hin-und-her-Springen)
      const ziel = sichtbar().find(w => w !== s && w !== start.sperre && unterZeiger(w));
      if (ziel) {
        start.sperre = ziel;
        const alle = sichtbar(), vorher = new Map(alle.filter(w => w !== s).map(w => [w, w.getBoundingClientRect()]));
        alle.indexOf(ziel) < alle.indexOf(s) ? ziel.before(s) : ziel.after(s);
        if (!ruhig()) for (const [w, alt] of vorher) {
          const neu = w.getBoundingClientRect(), dx = alt.left - neu.left, dy = alt.top - neu.top;
          if (dx || dy) w.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: 320, easing: FEDER });
        }
      }
      const slot = natuerlich();
      s.style.transform = `translate(${ev.clientX - start.versatz.x - slot.left}px, ${ev.clientY - start.versatz.y - slot.top}px)`;
      const rand = 60;
      if (ev.clientY < rand) scrollBy(0, -8); else if (ev.clientY > innerHeight - rand) scrollBy(0, 8);
    };
    const ende = ev => {
      removeEventListener("pointermove", bewegen); removeEventListener("pointerup", ende); removeEventListener("pointercancel", ende);
      if (!start.ziehen) return;
      s.classList.add("landet"); s.style.transform = "";
      setTimeout(() => s.classList.remove("zieht", "landet"), ruhig() ? 0 : 330);
      speichern();
    };
    addEventListener("pointermove", bewegen); addEventListener("pointerup", ende); addEventListener("pointercancel", ende);  // am window: Umsortieren im DOM beendet jeden Pointer-Capture
  });
}
let widgetsLaden = false, widgetTimer = null;
async function widgetsAnwenden() {
  const seite = document.getElementById("seite");
  if (!document.getElementById("usage") || seite.querySelector(".widget") || widgetsLaden) return;
  widgetsLaden = true;
  const layout = await widgetLayoutLaden();
  widgetsLaden = false;
  if (!document.getElementById("usage") || seite.querySelector(".widget")) return;
  const gruppen = { usage: [], live: [], markiert: [], heute: [], gesamt: [], diagramme: [] };
  let nachKopf = null;
  for (const el of [...seite.children]) {
    if (el.id === "usage") gruppen.usage.push(el);
    else if (el.id === "live") gruppen.live.push(el);
    else if (el.classList.contains("kleinzeile")) { nachKopf = el.dataset.abschnitt; gruppen[nachKopf].push(el); }
    else if (nachKopf && el.classList.contains("kpis")) { gruppen[nachKopf].push(el); nachKopf = null; }
    else if (el.classList.contains("zwei")) gruppen.diagramme.push(el);
    else if (el.classList.contains("karte")) gruppen.markiert.push(el);
  }
  const abschnitt = name => { const s = document.createElement("section"); s.className = "widget"; s.dataset.widget = name; return s; };
  const alle = {};
  for (const [name, teile] of Object.entries(gruppen)) { if (!teile.length) continue; const s = abschnitt(name); teile[0].before(s); teile.forEach(t => s.appendChild(t)); alle[name] = s; }
  alle.zuletzt = abschnitt("zuletzt"); const chips = zuletztChips();
  alle.zuletzt.innerHTML = chips ? `<p class="kleinzeile">Zuletzt besucht</p>${chips}` : "";
  alle.hinweise = abschnitt("hinweise");
  gesundheitHinweis().then(html => { alle.hinweise.innerHTML = html; });
  for (const w of layout.custom) alle["eigen-" + w.id] = widgetBauen(w);
  const ordnung = [...layout.order, ...Object.keys(alle).filter(n => !layout.order.includes(n))];
  const buehne = document.createElement("div"); buehne.id = "widgets";
  seite.prepend(buehne);
  const eigen = id => layout.custom.find(w => "eigen-" + w.id === id);
  const speichern = async () => {
    layout.order = [...buehne.children].map(s => s.dataset.widget);
    layout.hidden = [...buehne.children].filter(s => s.classList.contains("widget-aus")).map(s => s.dataset.widget);
    try { const antwort = await postJson("/api/widgets", layout); if (!antwort.ok) throw new Error(antwort.error); }
    catch (e) { console.warn("Widgets nicht gespeichert:", e); toast("Widgets nicht gespeichert"); }
  };
  const steuer = s => {
    s.querySelector(".widget-steuer") && s.querySelector(".widget-steuer").remove();
    const id = s.dataset.widget, w = eigen(id), name = w ? w.title : WIDGET_NAMEN[id];
    const eigene = w ? '<button type="button" data-a="bearbeiten">Bearbeiten</button><button type="button" data-a="loeschen">Löschen</button>' : "";
    s.insertAdjacentHTML("afterbegin", `<div class="widget-steuer"><b><span class="griff" aria-hidden="true">⋮⋮</span>${esc(name)}</b><span>${eigene}<button type="button" data-a="hoch" aria-label="Nach oben">↑</button><button type="button" data-a="runter" aria-label="Nach unten">↓</button><button type="button" data-a="aus">${s.classList.contains("widget-aus") ? "Einblenden" : "Ausblenden"}</button></span></div>`);
  };
  for (const name of ordnung) if (alle[name]) { buehne.appendChild(alle[name]); alle[name].classList.toggle("widget-aus", layout.hidden.includes(name)); steuer(alle[name]); }
  const knopf = document.createElement("button"); knopf.className = "tab"; knopf.type = "button"; knopf.id = "widgets-bearbeiten"; knopf.textContent = "Anpassen";
  const neuKnopf = document.createElement("button"); neuKnopf.className = "tab"; neuKnopf.type = "button"; neuKnopf.id = "widget-neu"; neuKnopf.textContent = "Widget hinzufügen";
  buehne.before(knopf, neuKnopf);
  knopf.onclick = () => { const an = seite.classList.toggle("widgets-bearbeiten"); knopf.textContent = an ? "Fertig" : "Anpassen"; knopf.classList.toggle("aktiv", an); };
  neuKnopf.onclick = () => widgetDialog(widgetNeuerStart(), w => { layout.custom.push(w); const s = widgetBauen(w); buehne.appendChild(s); steuer(s); speichern(); });
  buehne.onclick = e => {
    const b = e.target.closest("[data-a]"); if (!b) return;
    const s = b.closest(".widget");
    if (b.dataset.a === "hoch" && s.previousElementSibling) s.previousElementSibling.before(s);
    if (b.dataset.a === "runter" && s.nextElementSibling) s.nextElementSibling.after(s);
    if (b.dataset.a === "aus") { s.classList.toggle("widget-aus"); b.textContent = s.classList.contains("widget-aus") ? "Einblenden" : "Ausblenden"; }
    if (b.dataset.a === "bearbeiten") {
      const alt = eigen(s.dataset.widget);
      return widgetDialog(alt, neu => { layout.custom[layout.custom.indexOf(alt)] = neu; const ersatz = widgetBauen(neu); ersatz.classList.toggle("widget-aus", s.classList.contains("widget-aus")); s.replaceWith(ersatz); steuer(ersatz); speichern(); });
    }
    if (b.dataset.a === "loeschen") {
      if (!confirm(`Widget „${eigen(s.dataset.widget).title}“ löschen?`)) return;
      layout.custom = layout.custom.filter(w => "eigen-" + w.id !== s.dataset.widget); s.remove();
    }
    speichern();
  };
  widgetZiehen(buehne, speichern);
  clearInterval(widgetTimer);
  widgetTimer = setInterval(() => { if (document.getElementById("widgets")) widgetsAktualisieren(layout.custom); else clearInterval(widgetTimer); }, WIDGET_TAKT_MS);
}
SEITEN_HAKEN["sessions/uebersicht"] = widgetsAnwenden;
addEventListener("hashchange", () => {
  const route = location.hash.slice(1);
  if (SEITEN[route]) speicherSchreiben("deck-zuletzt", [route, ...speicherLesen("deck-zuletzt", []).filter(r => r !== route)].slice(0, 6));
});

// --- Tastenkuerzel-Uebersicht (?) -----------------------------------------------------------------
addEventListener("keydown", e => {
  if (e.key !== "?" || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || document.querySelector(".palette-hintergrund")) return;
  const rahmen = document.createElement("div");
  rahmen.className = "palette-hintergrund";
  rahmen.innerHTML = `<div class="palette hilfe"><p class="label" style="padding:16px 20px 0">Tastenkürzel und Gesten</p><table style="margin:8px 12px 16px;width:calc(100% - 24px)">${[
    ["Strg + K", "Befehlspalette: Seiten, Sessions, Suche"], ["/", "Suchfeld fokussieren"], ["?", "Diese Übersicht"], ["Esc", "Menü, Palette oder Übersicht schließen"], ["Enter", "Fokussierte Zeile öffnen"],
    ["Rechtsklick / lang drücken", "Kontextmenü einer Session"], ["Vom linken Rand wischen", "Seitenleiste öffnen (Handy)"], ["Nach links wischen", "Seitenleiste schließen"]].map(([k, v]) => `<tr><td class="mono">${k}</td><td class="leise">${v}</td></tr>`).join("")}</table></div>`;
  document.body.appendChild(rahmen);
  const zu = () => { rahmen.remove(); removeEventListener("keydown", esc_, true); };
  const esc_ = ev => { if (ev.key === "Escape") zu(); };
  addEventListener("keydown", esc_, true);
  rahmen.onclick = ev => { if (ev.target === rahmen) zu(); };
});

// --- Start ------------------------------------------------------------------------------------------
{ const haken = SEITEN_HAKEN[aktuelleRoute()]; if (haken && document.getElementById("usage")) haken(); }
weckerLaden();
streamStarten();
{ const aktuell = location.hash.slice(1); if (SEITEN[aktuell] && !speicherLesen("deck-zuletzt", []).includes(aktuell)) speicherSchreiben("deck-zuletzt", [aktuell, ...speicherLesen("deck-zuletzt", [])].slice(0, 6)); }
