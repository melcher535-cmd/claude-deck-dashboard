// Netzwerk: Seiten (Uebersicht, Geraete, Speicher, Router, Dienste) und Widget-Quellen. Laeuft nach control.js und widgets.js.
// Nutzt Helfer aus index.html und control.js (esc, karte, stat, leer, abzeichen, api, postJson, toast, vor, linien, saeulen, FARBEN, SEITEN, GRUPPEN, gruppeAnlegen, chatTimer).
// Die Einstellungen (Betriebsart, Quellen, Agent) stehen in netz-einstellungen.js.

const NETZ_TAKT_MS = 15000;
const NETZ_TAGE = 7;
const NETZ_ZEILEN = 6;
const NETZ_SYMBOL = '<rect x="9" y="3" width="6" height="5" rx="1.2"/><rect x="3" y="16" width="6" height="5" rx="1.2"/><rect x="15" y="16" width="6" height="5" rx="1.2"/><path d="M12 8v4M6 16v-4h12v4"/>';
let netzFilter = "";

GRUPPEN.netz = ["Netzwerk", "#netz/uebersicht"];
gruppeAnlegen("netz", "Netzwerk", NETZ_SYMBOL, [["#netz/uebersicht", "Übersicht"], ["#netz/geraete", "Geräte"], ["#netz/speicher", "Speicher & Platten"], ["#netz/router", "Router & Internet"],
  ["#netz/dienste", "Dienste & Zubehör"], ["#netz/einstellungen", "Einstellungen"]]);

const netzStil = document.createElement("style");
netzStil.textContent = `
  .neinheit { margin-left: 4px; font-size: .5em; font-weight: 500; color: var(--leise); } td.nstatus { white-space: nowrap; }
  .nzeile { margin: 0 0 14px; } .nzeile:last-child { margin-bottom: 0; }
  .nkopf { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 6px; } .nkopf b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .nspur { height: 8px; overflow: hidden; border-radius: 4px; background: var(--fuellung); } .nfuell { width: 100%; height: 100%; transform-origin: left; transition: transform .4s var(--feder); }
  .npunkt { display: inline-block; width: 9px; height: 9px; margin-right: 8px; border-radius: 50%; background: var(--fuellung2); } .npunkt.an { background: var(--ok); }
  .nmodus { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; margin-bottom: 14px; }
  .nmodus button { padding: 14px; border: 2px solid transparent; border-radius: 14px; background: var(--fuellung); color: var(--text); font: inherit; text-align: left; cursor: pointer; }
  .nmodus button.aktiv { border-color: var(--blau); } .nmodus b { display: block; margin-bottom: 4px; }
  .nmodus small { display: block; color: var(--leise); font-size: .8125rem; }
  .nschritte { margin: 0 0 12px; padding-left: 20px; } .nschritte li { margin: 6px 0; }
  .nbtn { margin: 0 6px 6px 0; } .nzeile-aktion { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
  .nfeld { width: 100%; padding: 9px 12px; border: 0; border-radius: 10px; outline: none; background: var(--eingabe); color: var(--text); font: 1rem var(--schrift); }
  textarea.nfeld { min-height: 120px; font: .75rem/1.5 var(--mono); resize: vertical; }
`;
document.head.appendChild(netzStil);

const netzSpeicherText = b => b >= 1e12 ? `${(b / 1e12).toFixed(2)} TB` : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${Math.round(b / 1e6)} MB` : `${Math.round(b / 1e3)} KB`;
const netzRate = b => { const bit = b * 8; return bit >= 1e9 ? `${(bit / 1e9).toFixed(2)} Gbit/s` : bit >= 1e6 ? `${(bit / 1e6).toFixed(1)} Mbit/s` : `${Math.round(bit / 1e3)} kbit/s`; };
const netzFarbe = p => p >= 90 ? "var(--leer)" : p >= 75 ? "var(--knapp)" : "var(--ok)";
const netzDauer = s => s >= 86400 ? `${(s / 86400).toFixed(1)} Tage` : s >= 3600 ? `${(s / 3600).toFixed(1)} Std` : `${Math.round(s / 60)} Min`;
const netzTn = text => `<span translate="no">${esc(text)}</span>`;  // Namen und Meldungen der Geraete werden nicht uebersetzt
const netzRateHtml = b => { const t = netzRate(b), i = t.indexOf(" "); return `${t.slice(0, i)}<span class="neinheit">${esc(t.slice(i))}</span>`; };
const NETZ_PRUEFUNGEN = ["http", "tcp", "ping", "backup", "zertifikat", "speedtest"];  // Typen, die auf der Dienste-Seite stehen statt als Geraetekarte
function netzSkala(hoechst) {
  const e = Math.pow(10, Math.floor(Math.log10(hoechst || 1))), m = hoechst / e;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * e;
}
// Liniendiagramm mit eigener Skala: reihen = [{name, farbe, punkte: [[zeit_ms, wert]]}], einheit z. B. "%" oder "Mbit/s", hoechst = feste Obergrenze (sonst automatisch)
function netzLinien(reihen, einheit, hoechst = null) {
  const alle = reihen.flatMap(r => r.punkte);
  if (alle.length < 2) return leer("Noch keine Messpunkte", "Der Verlauf füllt sich alle 5 Minuten.");
  const W = 600, H = 200, L = 62, B = 24, tmin = Math.min(...alle.map(p => p[0])), tmax = Math.max(...alle.map(p => p[0])), ymax = hoechst || netzSkala(Math.max(...alle.map(p => p[1])));
  const x = t => L + (t - tmin) / ((tmax - tmin) || 1) * (W - L - 8), y = v => 8 + (H - B - 8) * (1 - v / ymax), zahl = v => (Math.round(v * 10) / 10).toLocaleString(DECK_LOCALE);
  const gitter = [0, ymax / 2, ymax].map(v => `<line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="var(--trenner)"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${zahl(v)} ${esc(einheit)}</text>`).join("");
  const zeit = t => new Date(t).toLocaleString(DECK_LOCALE, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  const kurven = reihen.map(r => `<polyline fill="none" stroke="${r.farbe}" stroke-width="2" stroke-linejoin="round" points="${r.punkte.map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}"/>`).join("");
  const schritt = Math.max(1, Math.ceil(alle.length / reihen.length / 60));  // nicht mehr als etwa 60 Messpunkte je Kurve zum Anfassen
  const punkte = reihen.map(r => r.punkte.filter((_, i) => i % schritt === 0).map(([t, v]) => `<circle cx="${x(t).toFixed(1)}" cy="${y(v).toFixed(1)}" r="7" fill="transparent" data-tip="${esc(r.name)}: ${zahl(v)} ${esc(einheit)} (${zeit(t)})"/>`).join("")).join("");
  return `<svg viewBox="0 0 ${W} ${H}">${gitter}${kurven}${punkte}<text x="${L}" y="${H - 6}">${zeit(tmin)}</text><text x="${W}" y="${H - 6}" text-anchor="end">${zeit(tmax)}</text></svg>` +
    `<ul class="legende" style="margin-top:8px">${reihen.map(r => `<li><span class="punkt" style="background:${r.farbe}"></span>${esc(r.name)}</li>`).join("")}</ul>`;
}
const netzSchluessel = q => `${q.herkunft}/${q.id}`;
const netzProzent = (teil, gesamt) => gesamt ? Math.round(100 * teil / gesamt) : 0;
const netzZeile = (links, rechts) => `<div class="zeile"><div>${links}</div><span class="leise">${rechts}</span></div>`;
const netzMitHinweisen = n => n.quellen.filter(q => q.hinweise && q.hinweise.length);

function netzPrognose(n, q, volume) {
  const key = `${netzSchluessel(q)}|${volume.name}`;
  if (!(key in n.prognosen)) return "";
  const tage = n.prognosen[key];
  return tage == null ? "wächst nicht" : tage < 1 ? "voll in unter 1 Tag" : tage < 180 ? `voll in etwa ${Math.round(tage)} Tagen` : "voll in über einem halben Jahr";
}
function netzBalken(name, belegt, gesamt, prozent, zusatz = "") {
  return `<div class="nzeile"><div class="nkopf"><b>${netzTn(name)}</b><span class="leise">${netzSpeicherText(belegt)} von ${netzSpeicherText(gesamt)} · ${prozent} %</span></div>` +
    `<div class="nspur"><div class="nfuell" style="transform:scaleX(${Math.min(100, prozent) / 100});background:${netzFarbe(prozent)}"></div></div>${zusatz ? `<small class="leise">${esc(zusatz)}</small>` : ""}</div>`;
}
const netzFehlerKarte = q => karte(netzTn(q.name), `<p class="dienst-aus">antwortet nicht</p><p class="leise">${netzTn(q.fehler || "")}</p>`);
function netzLeer(n) {
  const text = n.modus === "agent" ? "Warte auf den Agenten im Heimnetz. Richte ihn unter Einstellungen ein." : "Wähle unter Einstellungen die Betriebsart und füge deine ersten Quellen hinzu, zum Beispiel dein NAS oder deinen Router.";
  return karte("Netzwerk", leer("Noch keine Daten", `${text}<br><br><a class="tab aktiv" href="#netz/einstellungen">Einstellungen öffnen</a>`));
}
function netzPlattenTabelle(platten) {
  return platten.length ? `<table><thead><tr><th>Platte</th><th>Modell</th><th class="r">Temperatur</th><th>Zustand</th></tr></thead><tbody>${platten.map(p =>
    `<tr><td>${esc(p.name)}</td><td class="leise">${esc(p.modell || "")}</td><td class="r">${p.temp != null ? `${p.temp} °C` : "–"}</td><td>${abzeichen(p.status === "ok" ? "gesund" : p.status === "fail" ? "Fehler" : p.status === "warn" ? "Warnung" : "unbekannt", p.status === "ok")}</td></tr>`).join("")}</tbody></table>` : "";
}
function netzNasKarte(n, q) {
  const kopfzeile = [q.modell, q.version].filter(Boolean).map(netzTn).concat(q.uptime != null ? [`<span>läuft seit ${netzDauer(q.uptime)}</span>`] : []).join(" · ");
  const werte = [q.cpu != null ? `CPU ${Math.round(q.cpu)} %` : "", q.ram ? `RAM ${q.ram.prozent} %` : "", q.temp != null ? `${q.temp} °C` : "", q.traffic ? `↓ ${netzRate(q.traffic.rx)} · ↑ ${netzRate(q.traffic.tx)}` : ""].filter(Boolean).map(esc).join(" · ");
  const raid = (q.raid || []).map(r => abzeichen(`${r.name}: ${r.detail || r.status}`, r.status === "ok")).join(" ");
  return karte(netzTn(q.name), (kopfzeile ? `<p class="kontext">${kopfzeile}</p>` : "") + (q.volumes || []).map(v => netzBalken(v.name, v.belegt, v.gesamt, v.prozent, netzPrognose(n, q, v))).join("") +
    (werte ? `<p class="kontext">${werte}</p>` : "") + (raid ? `<p class="kontext">${raid}</p>` : "") + (q.hinweise || []).map(h => `<p class="warnung">${netzTn(h)}</p>`).join(""));
}

// --- Uebersicht ---------------------------------------------------------------------------
function netzUebersichtBauen(n) {
  if (!n.quellen.length && !n.geraete.length) return netzLeer(n);
  const z = n.zusammenfassung, speicherProzent = netzProzent(z.speicher_belegt, z.speicher_gesamt), nas = n.quellen.filter(q => q.ok && (q.volumes || []).length), fehler = n.quellen.filter(q => !q.ok && !NETZ_PRUEFUNGEN.includes(q.typ));
  const kacheln = stat(String(z.geraete_online), "Geräte online", `von ${z.geraete_gesamt} bekannten${z.neue ? ` · ${z.neue} neu` : ""}`) +
    (z.speicher_gesamt ? stat(`${speicherProzent} %`, "NAS-Speicher", `${netzSpeicherText(z.speicher_belegt)} von ${netzSpeicherText(z.speicher_gesamt)}`) : "") +
    (z.wan && z.wan.rx != null ? stat(netzRateHtml(z.wan.rx), "Internet ↓", `↑ ${netzRate(z.wan.tx)}${z.wan.ip ? ` · ${esc(z.wan.ip)}` : ""}`) : "") +
    (n.quellen.some(q => q.strom) ? stat(`${z.strom_w} W`, "Stromverbrauch", "Steckdosen mit Messung") : "") +
    stat(`${z.quellen_ok} von ${z.quellen_gesamt}`, "Quellen erreichbar", n.stand ? `Stand ${vor(n.stand * 1000)}` : "");
  const alarme = n.alarme.length ? `<div class="warnung rot"><b>Achtung</b>${n.alarme.map(a => `<div>${netzTn(a.text)}</div>`).join("")}</div>` : "";
  const neue = n.geraete.filter(d => d.neu && d.online);
  const neueKarte = neue.length ? karte("Neue Geräte im Netz", neue.map(d => `<div class="zeile"><div><b>${netzTn(d.name || d.ip)}</b><small>${netzTn([d.ip, d.hersteller, d.mac].filter(Boolean).join(" · "))}</small></div>` +
    `<button class="tab" type="button" data-netz="bekannt" data-key="${esc(d.key)}">Bekannt</button></div>`).join("")) : "";
  const agent = n.modus === "agent" && !n.agent.aktiv && n.agent.zuletzt ? `<p class="warnung">Der Agent im Heimnetz hat sich seit ${vor(n.agent.zuletzt * 1000)} nicht gemeldet.</p>` : "";
  return `${alarme}${agent}<div class="raster kpis">${kacheln}</div>${neueKarte}<div class="raster zwei">${nas.map(q => netzNasKarte(n, q)).join("")}${fehler.map(netzFehlerKarte).join("")}</div>`;
}

// --- Geraete ------------------------------------------------------------------------------
function netzGeraeteBauen(n) {
  const sucheText = netzFilter.toLowerCase(), treffer = n.geraete.filter(d => !sucheText || [d.name, d.ip, d.mac, d.hersteller, d.typname].join(" ").toLowerCase().includes(sucheText));
  const scanFehler = n.herkuenfte.flatMap(h => h.fehler_scan || []);
  const hinweis = !n.herkuenfte.some(h => h.scan) ? `<p class="warnung">Noch kein Scan gelaufen. ${n.modus === "tailscale" ? "Trage unter Einstellungen dein Heimnetz ein (z. B. 192.168.1.0/24)." : "Das dauert beim ersten Mal einen Moment."}</p>` : "";
  const zeilen = treffer.map(d => `<tr><td class="nstatus"><span class="npunkt${d.online ? " an" : ""}"></span>${d.online ? "online" : `<span class="leise">${d.gesehen ? vor(d.gesehen * 1000) : "offline"}</span>`}</td>` +
    `<td><b>${netzTn(d.name || d.ip || d.mac)}</b>${d.neu ? ` ${abzeichen("neu", false)}` : ""}${d.wichtig ? " ★" : ""}<br><span class="leise">${netzTn([d.hersteller, d.typname].filter(Boolean).join(" · "))}</span></td>` +
    `<td class="mono">${esc(d.ip)}</td><td class="mono leise">${esc(d.mac)}</td><td>${d.verbindung ? esc(d.verbindung === "wlan" ? "WLAN" : "LAN") + (d.signal != null ? ` <span class="leise">${d.signal} dBm</span>` : "") : ""}</td>` +
    `<td><div class="nzeile-aktion">${d.bekannt ? "" : `<button class="tab" type="button" data-netz="bekannt" data-key="${esc(d.key)}">Bekannt</button>`}` +
    `<button class="tab" type="button" data-netz="wichtig" data-key="${esc(d.key)}" data-an="${d.wichtig ? "" : "1"}" title="Meldung, wenn das Gerät ausfällt">${d.wichtig ? "★ Wichtig" : "☆ Wichtig"}</button>` +
    `<button class="tab" type="button" data-netz="name" data-key="${esc(d.key)}" data-name="${esc(d.name)}">Umbenennen</button>` +
    `${!d.online && d.mac ? `<button class="tab" type="button" data-netz="wol" data-key="${esc(d.mac)}">Aufwecken</button>` : ""}</div></td></tr>`).join("");
  return `${hinweis}${scanFehler.map(f => `<p class="warnung">${esc(f)}</p>`).join("")}<div class="chatwerkzeug">${filterFeld("ng-filter", "Geräte filtern").replace('<input ', `<input value="${esc(netzFilter)}" `)}` +
    `<button class="tab" type="button" data-netz="scan">Jetzt suchen</button></div>` +
    karte(`${n.zusammenfassung.geraete_online} von ${n.zusammenfassung.geraete_gesamt} online`, treffer.length ? `<div class="tabelle"><table><thead><tr><th>Status</th><th>Gerät</th><th>IP</th><th>MAC</th><th>Verbindung</th><th></th></tr></thead><tbody>${zeilen}</tbody></table></div>`
      : leer("Keine Geräte", "Es wurde noch nichts gefunden oder der Filter passt auf nichts."));
}

// --- Speicher und Platten -----------------------------------------------------------------
function netzSpeicherBauen(n, verlauf) {
  const nas = n.quellen.filter(q => q.ok && ((q.volumes || []).length || (q.platten || []).length || (q.raid || []).length));
  if (!nas.length) return karte("Speicher & Platten", leer("Keine Speicherdaten", "Füge unter Einstellungen ein NAS hinzu (SNMP, SSH oder der passende Hersteller-Typ)."));
  const reihen = [];
  nas.forEach(q => (q.volumes || []).forEach(v => {
    const key = netzSchluessel(q), punkte = verlauf.filter(z => z.q[key] && z.q[key].vol && z.q[key].vol[v.name]).map(z => [z.t * 1000, netzProzent(...z.q[key].vol[v.name])]);
    if (punkte.length > 1) reihen.push({ name: `${q.name} ${v.name}`, farbe: FARBEN[reihen.length % FARBEN.length], punkte });
  }));
  return `<div class="raster zwei">${nas.map(q => karte(esc(q.name), (q.volumes || []).map(v => netzBalken(v.name, v.belegt, v.gesamt, v.prozent, netzPrognose(n, q, v))).join("") +
    ((q.raid || []).length ? `<p class="kontext">${q.raid.map(r => abzeichen(`${r.name}: ${r.detail || r.status}`, r.status === "ok")).join(" ")}</p>` : "") + `<div class="tabelle">${netzPlattenTabelle(q.platten || [])}</div>`)).join("")}</div>` +
    (reihen.length ? karte(`Belegung der letzten ${NETZ_TAGE} Tage`, netzLinien(reihen, "%", 100)) : "");
}

// --- Router und Internet ------------------------------------------------------------------
function netzRouterBauen(n, verlauf) {
  const router = n.quellen.filter(q => q.ok && q.wan), dns = n.quellen.filter(q => q.ok && q.dns), speed = n.quellen.filter(q => q.ok && q.speedtest);
  if (!router.length && !dns.length && !speed.length) return karte("Router & Internet", leer("Keine Router-Daten", "Füge unter Einstellungen deinen Router hinzu (Fritz!Box, UniFi, OpenWrt, MikroTik, OPNsense, UPnP oder SNMP)."));
  const reihen = [];
  router.forEach(q => {
    const key = netzSchluessel(q), punkte = (feld) => verlauf.filter(z => z.q[key] && z.q[key][feld] != null).map(z => [z.t * 1000, Math.round(z.q[key][feld] * 8 / 1e4) / 100]);
    reihen.push({ name: `${q.name} ↓`, farbe: FARBEN[0], punkte: punkte("rx") }, { name: `${q.name} ↑`, farbe: FARBEN[1], punkte: punkte("tx") });
  });
  const wanKarten = router.map(q => karte(esc(q.name), `<p class="zahl" style="font-size:26px"><span class="${q.wan.verbunden === false ? "dienst-aus" : "dienst-ok"}">${q.wan.verbunden === false ? "getrennt" : "verbunden"}</span></p>` +
    `<div class="wliste">${netzZeile("Öffentliche IP", esc(q.wan.ip || "–"))}${netzZeile("Download jetzt", q.wan.rx != null ? netzRate(q.wan.rx) : "–")}${netzZeile("Upload jetzt", q.wan.tx != null ? netzRate(q.wan.tx) : "–")}` +
    `${q.wan.down_max ? netzZeile("Leitung", `${Math.round(q.wan.down_max / 1e6)} / ${Math.round((q.wan.up_max || 0) / 1e6)} Mbit/s`) : ""}${(q.clients || []).length ? netzZeile("Verbundene Geräte", String(q.clients.length)) : ""}</div>`)).join("");
  const dnsKarten = dns.map(q => karte(esc(q.name), `<p class="zahl">${q.dns.prozent} %</p><p class="kontext">${fmt(q.dns.blockiert)} von ${fmt(q.dns.anfragen)} Anfragen blockiert${q.dns.aktiv === false ? " · Schutz ist aus" : ""}</p>` +
    (q.dns.domains ? `<p class="kontext">${fmt(q.dns.domains)} Domains auf der Sperrliste</p>` : ""))).join("");
  const speedKarten = speed.map(q => karte(esc(q.name), `<p class="zahl">${netzRate(q.speedtest.down)}</p><p class="kontext">Upload ${netzRate(q.speedtest.up)} · Ping ${q.speedtest.ping_ms} ms · gemessen ${vor(q.speedtest.t * 1000)}</p>`)).join("");
  return `<div class="raster zwei">${wanKarten}${dnsKarten}${speedKarten}</div>` + (reihen.some(r => r.punkte.length > 1) ? karte("Datenrate der letzten 24 Stunden", netzLinien(reihen.filter(r => r.punkte.length > 1), "Mbit/s")) : "");
}

// --- Dienste und Zubehoer -----------------------------------------------------------------
function netzDiensteBauen(n) {
  const alle = n.quellen.filter(q => q.ok || q.fehler), dienste = n.quellen.flatMap(q => (q.dienste || []).map(d => ({ ...d, quelle: q.name })));
  const ausfaelle = n.quellen.filter(q => !q.ok && ["http", "tcp", "ping"].includes(q.typ));
  const usv = n.quellen.filter(q => q.ok && q.usv), strom = n.quellen.filter(q => q.ok && q.strom), cert = n.quellen.filter(q => q.ok && q.zertifikat), backup = n.quellen.filter(q => q.backup);
  const werte = n.quellen.filter(q => q.ok && (q.werte || []).length), hinweise = netzMitHinweisen(n);
  if (!alle.length) return karte("Dienste & Zubehör", leer("Noch nichts eingerichtet", "Hier erscheinen Webdienste, Ports, USV, Steckdosen, Zertifikate, Backups, Immich und Home Assistant."));
  const dienstKarte = dienste.length || ausfaelle.length ? karte("Dienste", `<div class="wliste">${dienste.map(d => `<div class="zeile"><div>${abzeichen("●", d.ok)} ${esc(d.name)}<small>${esc(d.quelle)}</small></div><span class="leise">${esc(d.detail || "")}</span></div>`).join("")}` +
    `${ausfaelle.map(q => `<div class="zeile"><div>${abzeichen("●", false)} ${esc(q.name)}</div><span class="dienst-aus">${esc(q.fehler || "")}</span></div>`).join("")}</div>`) : "";
  const usvKarten = usv.map(q => karte(netzTn(q.name), `<p class="zahl">${q.usv.ladung != null ? q.usv.ladung + " %" : "–"}</p><p class="kontext"><span class="${q.usv.netz === false ? "dienst-aus" : "dienst-ok"}">${q.usv.netz === false ? "läuft auf Batterie" : "Netzbetrieb"}</span>` +
    `${q.usv.laufzeit != null ? ` · noch ${netzDauer(q.usv.laufzeit)}` : ""}${q.usv.last != null ? ` · Last ${q.usv.last} %` : ""}</p>`)).join("");
  const stromKarte = strom.length ? karte("Strom", `<div class="wliste">${strom.map(q => netzZeile(`${abzeichen("●", q.strom.ein)} ${esc(q.name)}`, `${q.strom.leistung_w} W · ${Math.round(q.strom.energie_wh || 0)} Wh gesamt`)).join("")}</div>`) : "";
  const zertKarte = cert.length ? karte("Zertifikate", `<div class="wliste">${cert.map(q => netzZeile(esc(q.name), `<span class="${q.zertifikat.tage <= 14 ? "dienst-aus" : ""}">noch ${q.zertifikat.tage} Tage (bis ${esc(q.zertifikat.bis)})</span>`)).join("")}</div>`) : "";
  const backupKarte = backup.length ? karte("Backups", `<div class="wliste">${backup.map(q => netzZeile(esc(q.name), q.backup ? `<span class="${q.backup.ok ? "dienst-ok" : "dienst-aus"}">vor ${q.backup.alter_h} Std</span> ${esc(q.backup.datei)}` : `<span class="dienst-aus">${esc(q.fehler || "")}</span>`)).join("")}</div>`) : "";
  const werteKarten = werte.map(q => karte(esc(q.name), `<div class="wliste">${q.werte.map(([name, wert]) => netzZeile(esc(name), esc(wert))).join("")}</div>`)).join("");
  const hinweisKarten = hinweise.map(q => karte(`Meldungen von ${esc(q.name)}`, q.hinweise.map(h => `<p class="warnung">${esc(h)}</p>`).join(""))).join("");
  return `<div class="raster zwei">${dienstKarte}${usvKarten}${stromKarte}${zertKarte}${backupKarte}${werteKarten}${hinweisKarten}</div>`;
}

// --- Seiten registrieren, automatisch aktualisieren ---------------------------------------
const NETZ_SEITEN = {
  "netz/uebersicht": ["Übersicht", netzUebersichtBauen], "netz/geraete": ["Geräte im Netz", netzGeraeteBauen], "netz/speicher": ["Speicher & Platten", netzSpeicherBauen, true],
  "netz/router": ["Router & Internet", netzRouterBauen, true], "netz/dienste": ["Dienste & Zubehör", netzDiensteBauen],
};
const netzTage = route => route === "netz/router" ? 1 : NETZ_TAGE;
async function netzLaden(route) {
  const [, bauen, mitVerlauf] = NETZ_SEITEN[route];
  const [n, verlauf] = await Promise.all([api("/api/netz"), mitVerlauf ? api(`/api/netz/verlauf?tage=${netzTage(route)}`) : Promise.resolve([])]);
  return bauen(n, verlauf);
}
function netzAutoAktualisieren(route) {
  clearInterval(chatTimer);
  chatTimer = setInterval(async () => {
    if (location.hash.slice(1) !== route || (document.activeElement && ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName))) return;
    try { document.getElementById("seite").innerHTML = await netzLaden(route); } catch (e) { console.warn("Netzwerk nicht aktualisiert:", e); }
  }, NETZ_TAKT_MS);
}
for (const [route, [titel, bauen, mitVerlauf]] of Object.entries(NETZ_SEITEN)) {
  SEITEN[route] = ["Netzwerk", titel, ({ x }) => { netzAutoAktualisieren(route); return bauen(x[0], x[1] || []); },
    mitVerlauf ? ["/api/netz", `/api/netz/verlauf?tage=${netzTage(route)}`] : ["/api/netz"]];
}

// --- Aktionen auf den Seiten (ein Handler fuer alle Knoepfe) --------------------------------
async function netzNeuZeichnen() {
  const route = location.hash.slice(1);
  if (NETZ_SEITEN[route]) document.getElementById("seite").innerHTML = await netzLaden(route);
}
function netzEingabe(titel, wert) {
  return new Promise(fertig => {
    const rahmen = document.createElement("div");
    rahmen.className = "palette-hintergrund";
    rahmen.innerHTML = `<form class="palette hilfe formular" style="max-width:420px;padding:20px;gap:12px"><p class="label" style="margin:0">${esc(titel)}</p><input id="ne-wert" maxlength="40" autocomplete="off">` +
      `<div style="display:flex;gap:10px;justify-content:flex-end"><button class="tab" type="button" id="ne-abbruch">Abbrechen</button><button class="tab aktiv" type="submit">Sichern</button></div></form>`;
    document.body.appendChild(rahmen);
    const feld = rahmen.querySelector("#ne-wert"), zu = ergebnis => { rahmen.remove(); fertig(ergebnis); };
    feld.value = wert; feld.focus(); feld.select();
    rahmen.querySelector("#ne-abbruch").onclick = () => zu(null);
    rahmen.onclick = e => { if (e.target === rahmen) zu(null); };
    rahmen.querySelector("form").onsubmit = e => { e.preventDefault(); zu(feld.value.trim()); };
  });
}
async function netzAktion(knopf) {
  const { netz: aktion, key } = knopf.dataset;
  let antwort;
  if (aktion === "scan") { antwort = await postJson("/api/netz/scan", {}); if (antwort.ok) { toast("Suche gestartet"); setTimeout(netzNeuZeichnen, 9000); } }
  else if (aktion === "bekannt") antwort = await postJson("/api/netz/geraet", { key, bekannt: true });
  else if (aktion === "wichtig") antwort = await postJson("/api/netz/geraet", { key, wichtig: knopf.dataset.an === "1" });
  else if (aktion === "name") {
    const name = await netzEingabe("Name des Geräts", knopf.dataset.name || "");
    if (name === null) return;
    antwort = await postJson("/api/netz/geraet", { key, name });
  } else if (aktion === "wol") { antwort = await postJson("/api/netz/wol", { mac: key }); if (antwort.ok) toast(antwort.weg === "agent" ? "Aufwecken an den Agenten übergeben" : "Aufwecken gesendet"); }
  else return;
  if (!antwort.ok) { toast(antwort.error || "Fehlgeschlagen"); return; }
  if (aktion !== "scan") await netzNeuZeichnen();
}
document.addEventListener("click", e => {
  const knopf = e.target.closest("[data-netz]");
  if (knopf) netzAktion(knopf).catch(err => { console.warn("Netzwerk-Aktion fehlgeschlagen:", err); toast("Fehlgeschlagen"); });
});
document.addEventListener("input", e => {
  if (e.target.id !== "ng-filter") return;
  netzFilter = e.target.value;
  const stelle = e.target.selectionStart;
  netzNeuZeichnen().then(() => { const feld = document.getElementById("ng-filter"); if (feld) { feld.focus(); feld.setSelectionRange(stelle, stelle); } });
});

// --- Widget-Quellen -------------------------------------------------------------------------
const netzWidgetDaten = n => ({ z: n.zusammenfassung, ok: n.quellen.filter(q => q.ok) });
const netzVerlaufPunkte = (verlauf, tage, wert) => verlauf.filter(z => z.t * 1000 >= Date.now() - tage * 864e5).map(z => [z.t * 1000, wert(z)]).filter(([, w]) => w != null);
const netzSummeQ = (z, feld) => Object.values(z.q).reduce((s, q) => s + (q[feld] || 0), 0);
Object.assign(WIDGET_QUELLEN, {
  netz_geraete: { name: "Netzwerk: Geräte online", apis: ["/api/netz"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([n]) => ({
    wert: String(n.zusammenfassung.geraete_online), kontext: `von ${n.zusammenfassung.geraete_gesamt} bekannten${n.zusammenfassung.neue ? ` · ${n.zusammenfassung.neue} neu` : ""}`,
    zeilen: n.geraete.filter(d => d.online).slice(0, NETZ_ZEILEN).map(d => [d.name || d.ip, d.ip]) }) },
  netz_speicher: { name: "Netzwerk: NAS-Speicher", apis: ["/api/netz"], ansichten: ["ring", "zahl", "liste"], zeitraum: false, lade: ([n]) => {
    const z = n.zusammenfassung, p = netzProzent(z.speicher_belegt, z.speicher_gesamt);
    return { wert: z.speicher_gesamt ? `${p} %` : "–", prozent: p, ampel: true, kontext: z.speicher_gesamt ? `${netzSpeicherText(z.speicher_belegt)} von ${netzSpeicherText(z.speicher_gesamt)}` : "kein NAS verbunden",
      zeilen: n.quellen.filter(q => q.ok).flatMap(q => (q.volumes || []).map(v => [`${q.name} ${v.name}`, `${v.prozent} %`])).slice(0, NETZ_ZEILEN) };
  } },
  netz_prognose: { name: "Netzwerk: Speicher voll in", apis: ["/api/netz"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([n]) => {
    const tage = Object.entries(n.prognosen).filter(([, t]) => t != null).sort((a, b) => a[1] - b[1]);
    return { wert: tage.length ? `${Math.round(tage[0][1])} Tage` : "–", kontext: tage.length ? "bei dem Tempo der letzten Tage" : "noch zu wenig Verlauf oder kein Wachstum",
      zeilen: tage.slice(0, NETZ_ZEILEN).map(([k, t]) => [k.split("|")[1] + " (" + k.split("|")[0].split("/")[1] + ")", `${Math.round(t)} Tage`]) };
  } },
  netz_temp: { name: "Netzwerk: Plattentemperatur", apis: ["/api/netz"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([n]) => {
    const platten = n.quellen.filter(q => q.ok).flatMap(q => (q.platten || []).filter(p => p.temp != null).map(p => ({ ...p, quelle: q.name }))).sort((a, b) => b.temp - a.temp);
    return { wert: platten.length ? `${platten[0].temp} °C` : "–", kontext: platten.length ? `heißeste Platte: ${platten[0].name} (${platten[0].quelle})` : "keine Platten gemeldet", zeilen: platten.slice(0, NETZ_ZEILEN).map(p => [`${p.quelle} ${p.name}`, `${p.temp} °C`]) };
  } },
  netz_wan: { name: "Netzwerk: Internet-Datenrate", apis: ["/api/netz"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([n]) => {
    const w = n.zusammenfassung.wan;
    return { wert: w && w.rx != null ? netzRate(w.rx) : "–", kontext: w ? `↑ ${netzRate(w.tx || 0)}${w.ip ? ` · ${w.ip}` : ""}` : "kein Router verbunden",
      zeilen: n.quellen.filter(q => q.ok && q.wan).map(q => [q.name, `↓ ${netzRate(q.wan.rx || 0)} ↑ ${netzRate(q.wan.tx || 0)}`]) };
  } },
  netz_speedtest: { name: "Netzwerk: Speedtest", apis: ["/api/netz"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([n]) => {
    const s = n.quellen.find(q => q.ok && q.speedtest);
    return s ? { wert: netzRate(s.speedtest.down), kontext: `Upload ${netzRate(s.speedtest.up)} · Ping ${s.speedtest.ping_ms} ms`, zeilen: [["Download", netzRate(s.speedtest.down)], ["Upload", netzRate(s.speedtest.up)], ["Ping", `${s.speedtest.ping_ms} ms`]] }
      : { wert: "–", kontext: "Speedtest-Quelle fehlt" };
  } },
  netz_alarme: { name: "Netzwerk: Hinweise", apis: ["/api/netz"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([n]) => ({
    wert: n.alarme.length ? String(n.alarme.length) : "Alles ruhig", kontext: n.alarme.length ? "offene Hinweise" : "keine Probleme im Netz", zeilen: n.alarme.slice(0, NETZ_ZEILEN).map(a => [a.text, ""]) }) },
  netz_dienste: { name: "Netzwerk: Dienste", apis: ["/api/netz"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([n]) => {
    const d = n.quellen.flatMap(q => (q.dienste || []).map(x => ({ ...x, quelle: q.name }))).concat(n.quellen.filter(q => !q.ok && ["http", "tcp", "ping"].includes(q.typ)).map(q => ({ name: q.name, ok: false, detail: q.fehler })));
    return { wert: d.length ? `${d.filter(x => x.ok).length} von ${d.length} ok` : "–", kontext: "Webdienste, Ports und Geräte", zeilen: d.slice(0, NETZ_ZEILEN).map(x => [x.name, x.ok ? "ok" : "aus"]) };
  } },
  netz_strom: { name: "Netzwerk: Stromverbrauch", apis: ["/api/netz"], ansichten: ["zahl", "liste"], zeitraum: false, lade: ([n]) => ({
    wert: `${n.zusammenfassung.strom_w} W`, kontext: "Summe aller Steckdosen mit Messung", zeilen: n.quellen.filter(q => q.ok && q.strom).map(q => [q.name, `${q.strom.leistung_w} W`]).slice(0, NETZ_ZEILEN) }) },
  netz_usv: { name: "Netzwerk: USV", apis: ["/api/netz"], ansichten: ["ring", "zahl"], zeitraum: false, lade: ([n]) => {
    const u = n.quellen.find(q => q.ok && q.usv);
    return u ? { wert: u.usv.ladung != null ? `${u.usv.ladung} %` : "–", prozent: u.usv.ladung || 0, kontext: u.usv.netz === false ? "läuft auf Batterie" : "Netzbetrieb" } : { wert: "–", prozent: 0, kontext: "keine USV verbunden" };
  } },
  netz_dns: { name: "Netzwerk: Werbeblocker", apis: ["/api/netz"], ansichten: ["ring", "zahl", "liste"], zeitraum: false, lade: ([n]) => {
    const d = n.quellen.filter(q => q.ok && q.dns);
    return d.length ? { wert: `${d[0].dns.prozent} %`, prozent: Math.round(d[0].dns.prozent), kontext: `${fmt(d[0].dns.blockiert)} von ${fmt(d[0].dns.anfragen)} blockiert`, zeilen: d.map(q => [q.name, `${q.dns.prozent} %`]) }
      : { wert: "–", prozent: 0, kontext: "Pi-hole oder AdGuard fehlt" };
  } },
  netz_verlauf_wan: { name: "Netzwerk: Datenrate im Verlauf", apis: ["/api/netz/verlauf?tage=30"], ansichten: ["linie", "zahl"], zeitraum: true, lade: ([v], tage) => {
    const rate = feld => netzVerlaufPunkte(v, tage, z => { const w = Object.values(z.q).map(q => q[feld]).filter(x => x != null); return w.length ? Math.round(Math.max(...w) * 8 / 1e4) / 100 : null; });
    const rx = rate("rx"), letzter = rx[rx.length - 1];
    return { wert: letzter ? `${letzter[1]} Mbit/s` : "–", kontext: "Download, zuletzt gemessen", diagramm: netzLinien([{ name: "Download", farbe: FARBEN[0], punkte: rx }, { name: "Upload", farbe: FARBEN[1], punkte: rate("tx") }].filter(l => l.punkte.length), "Mbit/s") };
  } },
  netz_verlauf_geraete: { name: "Netzwerk: Geräte im Verlauf", apis: ["/api/netz/verlauf?tage=30"], ansichten: ["linie", "zahl"], zeitraum: true, lade: ([v], tage) => {
    const punkte = netzVerlaufPunkte(v, tage, z => z.g);
    return { wert: punkte.length ? String(punkte[punkte.length - 1][1]) : "–", kontext: "Geräte online", diagramm: netzLinien(punkte.length ? [{ name: "Geräte online", farbe: FARBEN[2], punkte }] : [], "") };
  } },
});
