// Eigene Widgets fuer die Uebersicht: Quellen aus den Dashboard-Daten, fuenf Darstellungen und der Baukasten-Dialog.
// Nutzt Helfer aus index.html (esc, fmt, bytes, vor, dauer, api, postJson, sparkline, saeulen, linien, leer, auswahlMenue, toast, FARBEN).
// Jede Quelle: apis (werden geladen), ansichten, zeitraum (Zeitraum waehlbar), lade(antworten, tage) -> { wert, kontext, prozent, ampel, balken, linien, zeilen }.
const WIDGET_CACHE_MS = 20000;
const WIDGET_TAKT_MS = 30000;
const TAG_MS = 864e5;
const MAX_ZEILEN = 6;
const ZEITRAEUME = [[1, "Heute"], [7, "7 Tage"], [30, "30 Tage"]];
const ZEITRAUM_TEXT = { 1: "heute", 7: "letzte 7 Tage", 30: "letzte 30 Tage" };
const ANSICHTEN = { zahl: "Zahl", ring: "Ring", linie: "Linie", saeulen: "Säulen", liste: "Liste" };
const widgetCache = new Map();

function widgetApi(url) {
  const alt = widgetCache.get(url);
  if (alt && Date.now() - alt.zeit < WIDGET_CACHE_MS) return alt.antwort;
  const antwort = api(url);
  widgetCache.set(url, { zeit: Date.now(), antwort });
  antwort.catch(() => widgetCache.delete(url));
  return antwort;
}

const summe = zahlen => zahlen.reduce((a, b) => a + b, 0);
const seit = tage => new Date(Date.now() - (tage - 1) * TAG_MS).toISOString().slice(0, 10);
const imZeitraum = (paare, tage) => paare.filter(([datum]) => datum >= seit(tage));
const letzteTage = (paare, tage) => paare.filter(([datum]) => datum >= seit(Math.max(tage, 7)));
const ordner = pfad => String(pfad).split("/").filter(Boolean).pop() || pfad;
const kurz = id => String(id).slice(0, 8);

function limitDaten(u, art) {
  const l = u.limits.find(x => x.kind === art);
  if (!l) return { wert: "–", kontext: "kein Limit gemeldet" };
  return { wert: `${l.percent} %`, prozent: l.percent, ampel: true, kontext: l.resets_at ? `Reset in ${dauer(l.resets_at)}` : "kein Reset-Zeitpunkt" };
}
function tagesDaten(tage, paare, text, formatieren = String) {
  const wert = summe(imZeitraum(paare, tage).map(([, v]) => v));
  return { wert: formatieren(wert), kontext: `${text}, ${ZEITRAUM_TEXT[tage]}`, balken: letzteTage(paare, tage).map(([d, v]) => [d, v]) };
}
const aktivitaet = (s, schluessel) => s.daily.map(([datum, v]) => [datum, v[schluessel]]);
const auslastung = (prozent, kontext) => ({ wert: `${prozent} %`, prozent, ampel: true, kontext });

const WIDGET_QUELLEN = {
  limit_session: { name: "Limit: Session (5 Std)", apis: ["/api/usage"], ansichten: ["ring", "zahl"], zeitraum: false, lade: ([u]) => limitDaten(u, "session") },
  limit_woche: { name: "Limit: Woche (alle Modelle)", apis: ["/api/usage"], ansichten: ["ring", "zahl"], zeitraum: false, lade: ([u]) => limitDaten(u, "weekly_all") },
  limit_verlauf: { name: "Limit-Verlauf", apis: ["/api/usage-history"], ansichten: ["linie", "zahl"], zeitraum: true, lade: ([h], tage) => {
    const zeilen = h.filter(r => r.t >= Date.now() - tage * TAG_MS), letzter = zeilen[zeilen.length - 1];
    const reihe = (schluessel, name, farbe) => ({ name, farbe, punkte: zeilen.filter(r => r[schluessel] != null).map(r => [r.t, r[schluessel]]) });
    return { wert: letzter && letzter.session != null ? `${letzter.session} %` : "–", kontext: "Session, zuletzt gemessen",
      linien: [reihe("session", "Session (5 Std)", FARBEN[0]), reihe("weekly_all", "Woche", FARBEN[1])].filter(l => l.punkte.length) };
  } },
  kosten: { name: "Kosten", apis: ["/api/costs"], ansichten: ["zahl", "saeulen", "liste"], zeitraum: true, lade: ([c], tage) => ({
    ...tagesDaten(tage, c.daily.map(([d, v]) => [d, Math.round(v)]), "Geschätzte Kosten", v => `${v} $`),
    zeilen: c.by_model.slice(0, MAX_ZEILEN).map(([modell, v]) => [modell, `${Math.round(v)} $`]) }) },
  tokens: { name: "Output-Tokens", apis: ["/api/sessions"], ansichten: ["zahl", "saeulen", "liste"], zeitraum: true, lade: ([s], tage) => ({
    ...tagesDaten(tage, aktivitaet(s, "output"), "Output-Tokens", fmt),
    zeilen: s.by_model.slice(0, MAX_ZEILEN).map(([modell, v]) => [modell, fmt(v)]) }) },
  nachrichten: { name: "Nachrichten von dir", apis: ["/api/sessions"], ansichten: ["zahl", "saeulen"], zeitraum: true, lade: ([s], tage) => tagesDaten(tage, aktivitaet(s, "messages"), "Nachrichten") },
  sessions_aktiv: { name: "Laufende Sessions", apis: ["/api/live"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([l]) => ({
    wert: String(l.live.length), kontext: `${l.live.filter(r => r.status === "busy").length} arbeiten`,
    zeilen: l.live.map(r => [r.name || kurz(r.sessionId), r.action || (r.status === "busy" ? "arbeitet" : "wartet")]) }) },
  sessions_tag: { name: "Sessions pro Tag", apis: ["/api/sessions"], ansichten: ["zahl", "saeulen"], zeitraum: true, lade: ([s], tage) => tagesDaten(tage, aktivitaet(s, "sessions"), "Sessions") },
  fehler: { name: "Fehlgeschlagene Aufrufe", apis: ["/api/errors"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([e]) => ({
    wert: String(e.total), kontext: "Fehlgeschlagene Aufrufe gesamt", zeilen: e.by_tool.slice(0, MAX_ZEILEN).map(([werkzeug, n]) => [werkzeug, String(n)]) }) },
  gesundheit: { name: "Gesundheit", apis: ["/api/health"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([h]) => {
    const ok = (h.cert_days == null || h.cert_days >= h.cert_warn_days) && h.disk_free / h.disk_total > 0.1 && !h.errors.length;
    return { wert: ok ? "In Ordnung" : "Achtung", kontext: "Zertifikat, Platte und Journal", zeilen: [
      ["Zertifikat", h.cert_days == null ? "–" : `${h.cert_days} Tage`], ["Platte frei", bytes(h.disk_free)], ["Journal-Fehler", String(h.errors.length)], ["Dienst seit", h.service_since]] };
  } },
  zertifikat: { name: "HTTPS-Zertifikat", apis: ["/api/health"], ansichten: ["zahl"], zeitraum: false, lade: ([h]) => ({
    wert: h.cert_days == null ? "–" : `${h.cert_days} Tage`, kontext: "Caddy erneuert es selbst" }) },
  platte: { name: "Festplatte", apis: ["/api/system"], ansichten: ["ring", "zahl"], zeitraum: false, lade: ([s]) =>
    auslastung(Math.round(100 * s.disk_used / s.disk_total), `${bytes(s.disk_total - s.disk_used)} frei`) },
  arbeitszeit: { name: "Arbeitszeit", apis: ["/api/gantt"], ansichten: ["zahl", "saeulen"], zeitraum: true, lade: ([g], tage) =>
    tagesDaten(tage, g.map(([datum, abschnitte]) => [datum, Math.round(10 * summe(abschnitte.map(a => a.to - a.from))) / 10]), "Arbeitszeit", v => `${v.toFixed(1)} Std`) },
  notizen_heute: { name: "Notizen geändert", apis: ["/api/vault"], ansichten: ["zahl", "saeulen"], zeitraum: true, lade: ([v], tage) => tagesDaten(tage, v.per_day, "Notizen") },
  notizen_neu: { name: "Zuletzt geänderte Notizen", apis: ["/api/notes-recent"], ansichten: ["liste", "zahl"], zeitraum: false, lade: ([n]) => ({
    wert: String(n.length), kontext: "zuletzt geänderte Notizen", zeilen: n.slice(0, MAX_ZEILEN).map(x => [x.title, vor(x.mtime * 1000)]) }) },
  repos: { name: "Git-Repositories", apis: ["/api/repos"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([r]) => ({
    wert: String(r.length), kontext: `${r.filter(x => x.dirty).length} mit offenen Änderungen`,
    zeilen: r.slice(0, MAX_ZEILEN).map(x => [ordner(x.path), x.dirty ? `${x.branch} · ${x.dirty} geändert` : x.branch]) }) },
  postfach: { name: "Postfach", apis: ["/api/mail"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([m]) => ({
    wert: String(m.filter(x => x.status === "neu").length), kontext: `neue von ${m.length} Nachrichten`,
    zeilen: m.slice(-MAX_ZEILEN).reverse().map(x => [x.subject || "(ohne Betreff)", x.from]) }) },
  ram: { name: "Arbeitsspeicher", apis: ["/api/system"], ansichten: ["ring", "zahl"], zeitraum: false, lade: ([s]) =>
    auslastung(Math.round(100 * (1 - s.mem_available / s.mem_total)), `${bytes(s.mem_total - s.mem_available)} von ${bytes(s.mem_total)}`) },
  cpu: { name: "Prozessor", apis: ["/api/system"], ansichten: ["ring", "zahl", "liste"], zeitraum: false, lade: ([s]) => ({
    wert: s.load[0].toFixed(2), prozent: Math.min(100, Math.round(100 * s.load[0] / s.cpus)), ampel: true, kontext: `Last (1 Min) · ${s.cpus} Kerne`,
    zeilen: s.top.slice(0, MAX_ZEILEN).map(([, cpu, , name]) => [name, `${cpu} %`]) }) },
  dienste: { name: "Dienste", apis: ["/api/services"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([d]) => ({
    wert: `${d.services.filter(s => s.state === "active").length} von ${d.services.length} laufen`, kontext: "systemd-Dienste", zeilen: d.services.map(s => [s.name, s.state]) }) },
  verbrauch: { name: "Verbrauch nach Session", apis: ["/api/ranking"], ansichten: ["liste", "zahl"], zeitraum: false, lade: ([r]) => ({
    wert: fmt(r.total), kontext: "Tokens, letzte 7 Tage", zeilen: r.sessions.slice(0, MAX_ZEILEN).map(s => [s.title || kurz(s.id), `${s.share} %`]) }) },
  werkzeuge: { name: "Werkzeug-Aufrufe", apis: ["/api/ranking"], ansichten: ["liste", "zahl"], zeitraum: false, lade: ([r]) => ({
    wert: fmt(summe(r.tools.map(([, n]) => n))), kontext: "Aufrufe, letzte 7 Tage", zeilen: r.tools.slice(0, MAX_ZEILEN).map(([name, n]) => [name, String(n)]) }) },
  sicherung: { name: "Sicherung", apis: ["/api/backup-list"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([b]) => ({
    wert: b.length ? vor(b[0].mtime * 1000) : "Noch keine", kontext: `${b.length} Sicherungen`, zeilen: b.slice(0, MAX_ZEILEN).map(x => [vor(x.mtime * 1000), bytes(x.size)]) }) },
};

const ampel = prozent => prozent >= 90 ? "var(--leer)" : prozent >= 70 ? "var(--knapp)" : "var(--ok)";
function widgetRing(prozent, farbe) {
  const R = 15.9155, voll = Math.min(prozent, 100);
  return `<div class="ring"><svg viewBox="0 0 42 42" role="img" aria-label="${prozent} %"><circle cx="21" cy="21" r="${R}" fill="none" stroke="var(--fuellung)" stroke-width="4.4"/>` +
    `<circle cx="21" cy="21" r="${R}" fill="none" stroke="${farbe}" stroke-width="4.4" stroke-linecap="round" stroke-dasharray="${voll} ${100 - voll}" transform="rotate(-90 21 21)"/>` +
    `<text class="prozent" x="21" y="24" text-anchor="middle">${prozent} %</text></svg></div>`;
}
function widgetInhalt(ansicht, d) {
  const kontext = d.kontext ? `<p class="kontext">${esc(d.kontext)}</p>` : "";
  if (ansicht === "ring" && d.prozent != null) return widgetRing(d.prozent, d.ampel ? ampel(d.prozent) : "var(--blau)") + kontext;
  if (ansicht === "linie") return d.linien && d.linien.length ? linien(d.linien) : leer("Noch keine Messpunkte", "Öffne in Claude Code /usage, dann entsteht der erste Punkt.");
  if (ansicht === "saeulen") return d.balken && d.balken.length ? saeulen(d.balken) : '<p class="leise">Keine Daten.</p>';
  if (ansicht === "liste") return d.zeilen && d.zeilen.length ? `<div class="wliste">${d.zeilen.map(([name, wert]) => `<div class="zeile"><div>${esc(name)}</div><span class="leise">${esc(wert)}</span></div>`).join("")}</div>` : '<p class="leise">Keine Daten.</p>';
  const verlauf = d.balken && d.balken.length > 1 ? `<div class="spark">${sparkline(d.balken.map(b => b[1]))}</div>` : "";
  return `<p class="zahl">${esc(d.wert)}</p>${kontext}${verlauf}`;
}
async function widgetFuellen(ziel, w) {
  const quelle = WIDGET_QUELLEN[w.source];
  try {
    const d = quelle.lade(await Promise.all(quelle.apis.map(widgetApi)), w.range);
    ziel.innerHTML = `<p class="label">${esc(w.title)}</p>${widgetInhalt(quelle.ansichten.includes(w.view) ? w.view : quelle.ansichten[0], d)}`;
  } catch (e) {
    console.warn("Widget nicht ladbar:", w.source, e);
    ziel.innerHTML = `<p class="label">${esc(w.title)}</p><p class="leise">Daten nicht verfügbar.</p>`;
  }
}
function widgetBauen(w) {
  const abschnitt = document.createElement("section");
  abschnitt.className = `widget widget-eigen ${w.width}`;
  abschnitt.dataset.widget = "eigen-" + w.id;
  abschnitt.innerHTML = '<div class="karte widget-karte"></div>';
  widgetFuellen(abschnitt.querySelector(".widget-karte"), w);
  return abschnitt;
}
function widgetsAktualisieren(eigene) {
  for (const w of eigene) {
    const ziel = document.querySelector(`[data-widget="eigen-${w.id}"] .widget-karte`);
    if (ziel) widgetFuellen(ziel, w);
  }
}

// Baukasten: Dialog mit Vorschau, Quelle, Darstellung, Zeitraum und Breite
function widgetDialog(start, fertig) {
  const w = { ...start }, neu = !start.title;
  const rahmen = document.createElement("div");
  rahmen.className = "palette-hintergrund w-hintergrund";
  const segment = (id, optionen, aktiv) => `<div class="segment" id="${id}">${optionen.map(([wert, text]) => `<button type="button" class="seg${wert === aktiv ? " aktiv" : ""}" data-wert="${wert}">${text}</button>`).join("")}</div>`;
  rahmen.innerHTML = `<div class="palette sheet" role="dialog" aria-modal="true" aria-label="Widget">
    <div class="sheet-kopf"><button type="button" class="sheet-text" data-a="abbrechen">Abbrechen</button><b>${neu ? "Neues Widget" : "Widget bearbeiten"}</b><button type="button" class="sheet-text fett" data-a="sichern">Sichern</button></div>
    <div class="w-vorschau"><div class="karte widget-karte"></div></div>
    <div class="sheet-felder">
      <label class="zeile"><span>Titel</span><input id="w-titel" type="text" maxlength="40" autocomplete="off"></label>
      <div class="zeile"><span>Quelle</span><button type="button" id="w-quelle" class="auswahlknopf" aria-haspopup="menu" aria-expanded="false"><span></span>${AUSWAHL_PFEIL}</button></div>
      <div class="zeile"><span>Darstellung</span><div id="w-ansicht"></div></div>
      <div class="zeile" id="w-zeitraum-zeile"><span>Zeitraum</span>${segment("w-zeitraum", ZEITRAEUME, w.range)}</div>
      <div class="zeile"><span>Breite</span>${segment("w-breite", [["halb", "Halb"], ["voll", "Voll"]], w.width)}</div>
    </div></div>`;
  document.body.appendChild(rahmen);
  const el = id => rahmen.querySelector("#" + id), titel = el("w-titel"), knopf = el("w-quelle");
  let vorschauTimer = null;
  const schliessen = () => { rahmen.remove(); removeEventListener("keydown", taste, true); };
  const taste = e => { if (e.key === "Escape" && !document.querySelector(".kontextmenue")) schliessen(); };
  const vorschau = () => { clearTimeout(vorschauTimer); vorschauTimer = setTimeout(() => widgetFuellen(rahmen.querySelector(".w-vorschau .widget-karte"), { ...w, title: w.title || WIDGET_QUELLEN[w.source].name }), 120); };
  const zeichnen = () => {
    const q = WIDGET_QUELLEN[w.source];
    if (!q.ansichten.includes(w.view)) w.view = q.ansichten[0];
    knopf.firstElementChild.textContent = q.name;
    el("w-ansicht").innerHTML = segment("w-ansicht-segment", q.ansichten.map(a => [a, ANSICHTEN[a]]), w.view);
    el("w-zeitraum-zeile").hidden = !q.zeitraum;
    el("w-ansicht-segment").onclick = e => { const b = e.target.closest("[data-wert]"); if (b) { w.view = b.dataset.wert; zeichnen(); } };
    vorschau();
  };
  titel.value = w.title;
  titel.oninput = () => { w.title = titel.value; vorschau(); };
  titel.placeholder = WIDGET_QUELLEN[w.source].name;
  const waehlen = id => { const alt = WIDGET_QUELLEN[w.source].name; w.source = id; titel.placeholder = WIDGET_QUELLEN[id].name; if (!w.title || w.title === alt) { w.title = ""; titel.value = ""; } zeichnen(); };
  knopf.onpointerdown = () => { knopf.dataset.war = document.querySelector(".kontextmenue") ? "1" : ""; };
  knopf.onclick = () => { if (knopf.dataset.war) { knopf.dataset.war = ""; return; } auswahlMenue(knopf, Object.entries(WIDGET_QUELLEN).map(([id, q]) => [id, q.name]), w.source, waehlen); };
  for (const [id, schluessel, zahl] of [["w-zeitraum", "range", true], ["w-breite", "width", false]]) {
    el(id).onclick = e => { const b = e.target.closest("[data-wert]"); if (!b) return; w[schluessel] = zahl ? Number(b.dataset.wert) : b.dataset.wert; el(id).querySelectorAll(".seg").forEach(s => s.classList.toggle("aktiv", s === b)); vorschau(); };
  }
  rahmen.onclick = e => {
    if (e.target === rahmen || e.target.dataset.a === "abbrechen") return schliessen();
    if (e.target.dataset.a === "sichern") { fertig({ ...w, title: (w.title.trim() || WIDGET_QUELLEN[w.source].name).slice(0, 40) }); schliessen(); }
  };
  addEventListener("keydown", taste, true);
  zeichnen();
  titel.focus();
}
const widgetNeuerStart = () => ({ id: "w" + Math.random().toString(36).slice(2, 8), title: "", source: "kosten", view: "zahl", range: 7, width: "halb" });
