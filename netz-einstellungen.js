// Netzwerk-Einstellungen: Betriebsart (Heimnetz, Tailscale, Agent), Quellen mit Formular und Test, Geraete-Entdeckung, Alarme und Agent-Zugang. Laeuft nach netz.js.
// Alles, was etwas aendert oder Zugangsdaten beruehrt, geht durch kritisch() (Vorschau, Passwort und Code, Audit) aus control.js.

const NETZ_MASKE = "********";
const NETZ_PFAD_ALARM = "alarme.";
let netzEntwurf = null, netzOriginal = "", netzUe = null, netzFunde = null;

const netzModi = [
  ["lokal", "Im Heimnetz", "Das Dashboard läuft bei dir zu Hause (NAS, Mini-PC, Raspberry Pi) und fragt die Geräte direkt ab. Die einfachste Variante."],
  ["tailscale", "Über Tailscale", "Das Dashboard läuft auf einem Server im Internet. Tailscale verbindet es sicher mit deinem Heimnetz, ohne dass du Ports öffnest."],
  ["agent", "Mit Agent", "Ein kleines Programm läuft zu Hause und schickt die Daten an das Dashboard. Von außen muss nichts erreichbar sein."],
];
const netzHilfe = {
  lokal: `<ol class="nschritte"><li>Installiere das Dashboard auf einem Gerät in deinem Heimnetz, das immer läuft (Anleitung in der README unter „Installation“).</li>
    <li>Lass das Feld „Netze“ unten leer: das Dashboard erkennt das Netz selbst.</li><li>Füge deine Quellen hinzu, zum Beispiel NAS und Router, oder lass das Netz durchsuchen.</li></ol>`,
  tailscale: `<ol class="nschritte"><li>Auf dem Server mit dem Dashboard Tailscale installieren und anmelden:<pre class="roh"># Installation: tailscale.com/download\nsudo tailscale up --accept-routes</pre></li>
    <li>Zu Hause auf einem Gerät, das immer läuft (NAS, Raspberry Pi, Mini-PC), Tailscale installieren und das Heimnetz anbieten (hier 192.168.1.0/24, passe es an):<pre class="roh">sudo tailscale up --advertise-routes=192.168.1.0/24\necho 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-tailscale.conf\nsudo sysctl -p /etc/sysctl.d/99-tailscale.conf</pre></li>
    <li>In der Tailscale-Verwaltung (login.tailscale.com/admin/machines) beim Heimnetz-Gerät unter „Edit route settings“ die Route freigeben.</li>
    <li>Unten bei „Netze“ dein Heimnetz eintragen, zum Beispiel 192.168.1.0/24.</li></ol>
    <p class="kontext">Aufwecken (Wake-on-LAN), UPnP-Abfragen des Routers und der Speedtest wirken nur im Heimnetz selbst. Dafür den Agenten zusätzlich einrichten.</p>`,
  agent: `<ol class="nschritte"><li>Speichere die Quellen unten und erzeuge dann den „Agent-Zugang“.</li><li>Lade den Agenten herunter und starte ihn zu Hause (Anleitung im Abschnitt „Agent“).</li>
    <li>Das Dashboard fragt dann nichts mehr selbst ab, es zeigt, was der Agent meldet.</li></ol>`,
};

const netzNeueKennung = () => "q" + Math.random().toString(36).slice(2, 8);
const netzTyp = typ => (netzEntwurf.typen || {})[typ] || { name: typ, felder: [], hinweis: "", gruppe: "" };
const netzNeueQuelle = (typ, host = "", name = "") => ({ id: netzNeueKennung(), typ, name, aktiv: true, ...Object.fromEntries(netzTyp(typ).felder.map(f => [f.key, f.key === "host" && host ? host : f.standard])) });
const netzMaskiert = cfg => ({ ...cfg, typen: undefined, agent: undefined, domain: undefined, quellen: cfg.quellen.map(q => Object.fromEntries(Object.entries(q).map(([k, v]) =>
  [k, netzTyp(q.typ).felder.some(f => f.key === k && f.geheim) && v ? "***" : v]))) });
const netzKorper = () => ({ modus: netzEntwurf.modus, intervall: netzEntwurf.intervall, scan_intervall: netzEntwurf.scan_intervall, netze: netzEntwurf.netze, quellen: netzEntwurf.quellen, alarme: netzEntwurf.alarme, wol_broadcast: netzEntwurf.wol_broadcast });

function netzFeldZeile(titel, hinweis, pfad, art = "number", breite = 130, platzhalter = "") {
  const wert = pfad.split(".").reduce((o, k) => o[k], netzEntwurf);
  return `<label class="zeile"><span>${titel}${hinweis ? `<small>${hinweis}</small>` : ""}</span><input class="nfeld" style="width:${breite}px" type="${art}" data-ne-feld="${pfad}" data-art="${art}" value="${esc(Array.isArray(wert) ? wert.join(", ") : wert ?? "")}" placeholder="${platzhalter}" autocomplete="off"></label>`;
}
function netzModusKarte() {
  const ts = netzUe && (netzUe.herkuenfte.find(h => h.name === "server") || {}).tailscale;
  const lage = netzEntwurf.modus !== "tailscale" ? "" : !ts ? `<p class="kontext">Tailscale-Status: wird nach dem ersten Durchlauf angezeigt.</p>`
    : !ts.installiert ? `<p class="warnung">Tailscale ist auf diesem Server nicht installiert.</p>` : !ts.angemeldet ? `<p class="warnung">Tailscale ist installiert, aber nicht angemeldet (<span class="mono">sudo tailscale up</span>).</p>`
    : `<p class="kontext">${abzeichen("verbunden", true)} als ${esc(ts.name)} (${esc(ts.ip)}) · ${ts.geraete.length} weitere Geräte im Tailnet${ts.geraete.some(g => (g.routen || []).length) ? " · Heimnetz-Routen werden angeboten" : " · noch keine Heimnetz-Route freigegeben"}</p>`;
  return karte("Betriebsart", `<div class="nmodus">${netzModi.map(([id, name, text]) => `<button type="button" data-ne="modus" data-wert="${id}" class="${netzEntwurf.modus === id ? "aktiv" : ""}"><b>${name}</b><small>${text}</small></button>`).join("")}</div>${netzHilfe[netzEntwurf.modus]}${lage}`);
}
function netzQuellenKarte() {
  const zeile = q => {
    const stand = netzUe && netzUe.quellen.find(x => x.id === q.id), status = !stand ? abzeichen("noch nicht abgefragt", false) : stand.ok ? abzeichen("erreichbar", true) : abzeichen(stand.fehler || "Fehler", false);
    return `<div class="zeile"><div><b>${netzTn(q.name || netzTyp(q.typ).name)}</b>${q.aktiv === false ? " (aus)" : ""}<small><span>${esc(netzTyp(q.typ).name)}</span>${q.host ? ` · ${netzTn(q.host)}` : ""} · ${status}</small></div>` +
      `<div class="nzeile-aktion"><button class="tab" type="button" data-ne="quelle-bearbeiten" data-id="${esc(q.id)}">Bearbeiten</button><button class="tab" type="button" data-ne="quelle-entfernen" data-id="${esc(q.id)}">Entfernen</button></div></div>`;
  };
  return karte("Quellen", (netzEntwurf.quellen.length ? netzEntwurf.quellen.map(zeile).join("") : leer("Noch keine Quellen", "Füge dein NAS, deinen Router oder einen Dienst hinzu, oder lass das Netz durchsuchen.")) +
    `<div style="margin-top:12px"><button class="tab aktiv nbtn" type="button" data-ne="quelle-neu">Quelle hinzufügen</button><button class="tab nbtn" type="button" data-ne="entdecken">Netz durchsuchen</button></div><div id="ne-funde">${netzFundeHtml()}</div>`);
}
function netzFundeHtml() {
  if (!netzFunde) return "";
  if (!netzFunde.length) return `<p class="kontext">Nichts gefunden.</p>`;
  return `<div class="tabelle"><table><thead><tr><th>Adresse</th><th>Gerät</th><th>Vorschlag</th><th></th></tr></thead><tbody>${netzFunde.map((g, i) => `<tr><td class="mono">${esc(g.ip)}</td><td>${esc(g.name || "–")}<br><span class="leise">${esc(g.hersteller || "")}</span></td>` +
    `<td class="leise">${esc(g.typname || (g.typ ? netzTyp(g.typ).name : "–"))}</td><td><button class="tab" type="button" data-ne="fund-hinzu" data-i="${i}">Hinzufügen</button></td></tr>`).join("")}</tbody></table></div>`;
}
function netzAlarmKarte() {
  return karte("Hinweise und Alarme", `<p class="kontext" style="margin:0 0 8px">Meldungen kommen über die Wege, die du unter Konto eingerichtet hast (Push, ntfy).</p>` +
    schalterZeile("ne-neu", "Neues Gerät im Netz", "Meldung, sobald ein unbekanntes Gerät auftaucht (der erste Scan gilt als bekannt)", netzEntwurf.alarme.neues_geraet).replace('id="ne-neu"', 'id="ne-neu" data-ne-schalter="neues_geraet"') +
    netzFeldZeile("Speicher voll ab", "Prozent", NETZ_PFAD_ALARM + "voll_prozent") + netzFeldZeile("Platte zu heiß ab", "Grad Celsius", NETZ_PFAD_ALARM + "temp_platte") + netzFeldZeile("Wichtiges Gerät offline nach", "Minuten", NETZ_PFAD_ALARM + "offline_min") +
    netzFeldZeile("Zertifikat läuft ab in", "Tage", NETZ_PFAD_ALARM + "zertifikat_tage") + netzFeldZeile("Quelle gilt als ausgefallen nach", "fehlgeschlagenen Abfragen", NETZ_PFAD_ALARM + "ausfall_versuche"));
}
function netzHeimnetzKarte() {
  return karte("Abfrage", netzFeldZeile("Netze", "Leer = automatisch. Mehrere mit Komma, z. B. 192.168.1.0/24", "netze", "text", 220, "automatisch") + netzFeldZeile("Quellen abfragen alle", "Sekunden (30 bis 3600)", "intervall") +
    netzFeldZeile("Geräte suchen alle", "Sekunden (30 bis 14400)", "scan_intervall") + netzFeldZeile("Aufwecken über", "Broadcast-Adresse, nur wenn es nicht 255.255.255.255 sein soll", "wol_broadcast", "text", 160, "255.255.255.255"));
}
function netzAgentKarte() {
  const a = netzEntwurf.agent || {}, stand = a.eingerichtet ? (a.zuletzt ? `Letzte Meldung ${vor(a.zuletzt * 1000)}` : "eingerichtet, hat sich noch nicht gemeldet") : "noch nicht eingerichtet";
  return karte("Agent (Heimnetz)", `<p class="kontext">Ein kleines Python-Programm für ein Gerät zu Hause. Es nutzt nur die Standardbibliothek, ruft nach draußen und öffnet keinen Port.</p><p class="kontext">Status: ${abzeichen(stand, !!a.zuletzt)}</p>` +
    `<div><a class="tab nbtn" href="/netz-agent.zip" download>Agent herunterladen</a><button class="tab aktiv nbtn" type="button" data-ne="agent-token">${a.eingerichtet ? "Neuen Zugang erzeugen" : "Zugang erzeugen"}</button>` +
    `${a.eingerichtet ? '<button class="tab nbtn" type="button" data-ne="agent-loeschen">Zugang widerrufen</button>' : ""}</div>`);
}
function netzEinstellungenBauen() {
  return `${netzModusKarte()}<div class="raster zwei">${netzHeimnetzKarte()}${netzAlarmKarte()}</div>${netzQuellenKarte()}${netzAgentKarte()}` +
    `<div style="display:flex;gap:10px;margin:8px 0 24px"><button class="tab aktiv" type="button" data-ne="speichern">Speichern</button><button class="tab" type="button" data-ne="verwerfen">Verwerfen</button></div>`;
}
SEITEN["netz/einstellungen"] = ["Netzwerk", "Einstellungen", ({ x }) => {
  clearInterval(chatTimer);
  netzEntwurf = JSON.parse(JSON.stringify(x[0]));
  netzOriginal = JSON.stringify(netzMaskiert(netzEntwurf), null, 1);
  netzUe = x[1];
  netzFunde = null;
  return netzEinstellungenBauen();
}, ["/api/netz/config", "/api/netz"]];

// --- Quellen-Dialog -----------------------------------------------------------------------
function netzFeldHtml(f, wert) {
  const id = `nq-${f.key}`;
  if (f.art === "schalter") return schalterZeile(id, esc(f.label), esc(f.hilfe), !!wert);
  if (f.art === "auswahl") return `<div class="zeile"><span>${esc(f.label)}</span><div class="segment" id="${id}">${f.optionen.map(o => `<button type="button" class="seg${o === wert ? " aktiv" : ""}" data-wert="${esc(o)}">${esc(o)}</button>`).join("")}</div></div>`;
  return `<label class="zeile"><span>${esc(f.label)}${f.hilfe ? `<small>${esc(f.hilfe)}</small>` : ""}</span><input id="${id}" type="${f.art === "zahl" ? "number" : f.geheim ? "password" : "text"}" value="${esc(wert ?? "")}" placeholder="${f.geheim ? "nicht gesetzt" : esc(f.standard || "")}" autocomplete="${f.geheim ? "new-password" : "off"}"></label>`;
}
function netzErgebnisText(e) {
  if (!e.ok) return `<span class="dienst-aus">${esc(e.fehler || "Fehler")}</span>`;
  const teile = [e.modell, (e.volumes || []).length && `${e.volumes.length} Laufwerk(e)`, (e.platten || []).length && `${e.platten.length} Platte(n)`, (e.raid || []).length && `${e.raid.length} RAID`, e.cpu != null && `CPU ${Math.round(e.cpu)} %`,
    e.ram && `RAM ${e.ram.prozent} %`, e.temp != null && `${e.temp} °C`, e.wan && `Internet ${e.wan.verbunden === false ? "getrennt" : "verbunden"}`, (e.clients || []).length && `${e.clients.length} Geräte`, e.dns && `${e.dns.prozent} % blockiert`,
    e.strom && `${e.strom.leistung_w} W`, e.usv && `USV ${e.usv.ladung ?? "?"} %`, e.zertifikat && `Zertifikat noch ${e.zertifikat.tage} Tage`, e.speedtest && `${netzRate(e.speedtest.down)}`, (e.dienste || []).length && `${e.dienste.length} Dienst(e)`, (e.werte || []).length && `${e.werte.length} Werte`].filter(Boolean);
  return `<span class="dienst-ok">Erreichbar</span> in ${e.ms} ms${teile.length ? ` · ${esc(teile.join(" · "))}` : ""}`;
}
function netzQuelleDialog(start, fertig) {
  let q = { ...start };
  const rahmen = document.createElement("div");
  rahmen.className = "palette-hintergrund w-hintergrund";
  rahmen.innerHTML = `<div class="palette sheet" role="dialog" aria-modal="true" aria-label="Quelle"><div class="sheet-kopf"><button type="button" class="sheet-text" data-a="abbrechen">Abbrechen</button><b>Quelle</b><button type="button" class="sheet-text fett" data-a="sichern">Sichern</button></div>` +
    `<div class="sheet-felder"><label class="zeile"><span>Name</span><input id="nq-name" maxlength="40" autocomplete="off"></label><div class="zeile"><span>Typ</span><button type="button" id="nq-typ" class="auswahlknopf" aria-haspopup="menu" aria-expanded="false"><span></span>${AUSWAHL_PFEIL}</button></div>` +
    `${schalterZeile("nq-aktiv", "Abfragen", "", q.aktiv !== false)}</div><p class="kontext" id="nq-hinweis" style="margin:0 20px 8px"></p><div class="sheet-felder" id="nq-felder"></div>` +
    `<div style="margin:0 16px 16px"><button class="tab nbtn" type="button" data-a="testen">Testen</button><span id="nq-test" class="kontext"></span></div></div>`;
  document.body.appendChild(rahmen);
  const el = id => rahmen.querySelector("#" + id), knopf = el("nq-typ"), schliessen = () => { rahmen.remove(); removeEventListener("keydown", taste, true); };
  const taste = e => { if (e.key === "Escape" && !document.querySelector(".kontextmenue")) schliessen(); };
  const lesen = () => {
    const ergebnis = { id: q.id, typ: q.typ, name: el("nq-name").value.trim(), aktiv: el("nq-aktiv").getAttribute("aria-checked") === "true" };
    for (const f of netzTyp(q.typ).felder) {
      const feld = el(`nq-${f.key}`);
      ergebnis[f.key] = f.art === "schalter" ? feld.getAttribute("aria-checked") === "true" : f.art === "auswahl" ? (feld.querySelector(".seg.aktiv") || feld.firstElementChild).dataset.wert : f.art === "zahl" ? feld.value : feld.value.trim();
    }
    return ergebnis;
  };
  const zeichnen = () => {
    const t = netzTyp(q.typ);
    knopf.firstElementChild.textContent = t.name;
    el("nq-name").value = q.name; el("nq-name").placeholder = t.name;
    el("nq-hinweis").textContent = t.hinweis;
    el("nq-felder").innerHTML = t.felder.map(f => netzFeldHtml(f, q[f.key])).join("");
    el("nq-felder").hidden = !t.felder.length;
    el("nq-test").innerHTML = "";
  };
  const typWaehlen = typ => { const alt = lesen(); q = { ...netzNeueQuelle(typ, alt.host), id: q.id, name: alt.name, aktiv: alt.aktiv }; zeichnen(); };
  knopf.onpointerdown = () => { knopf.dataset.war = document.querySelector(".kontextmenue") ? "1" : ""; };
  knopf.onclick = () => { if (knopf.dataset.war) { knopf.dataset.war = ""; return; }
    auswahlMenue(knopf, Object.entries(netzEntwurf.typen).sort((a, b) => a[1].gruppe.localeCompare(b[1].gruppe) || a[1].name.localeCompare(b[1].name)).map(([id, t]) => [id, `${t.name}`]), q.typ, typWaehlen); };
  rahmen.onclick = async e => {
    const schalter = e.target.closest(".schalter"), segment = e.target.closest(".seg");
    if (schalter) schalter.setAttribute("aria-checked", schalter.getAttribute("aria-checked") !== "true");
    if (segment) { segment.parentElement.querySelectorAll(".seg").forEach(s => s.classList.toggle("aktiv", s === segment)); }
    if (e.target === rahmen || e.target.dataset.a === "abbrechen") return schliessen();
    if (e.target.dataset.a === "sichern") {
      const neu = lesen();
      if (netzTyp(neu.typ).felder.some(f => f.key === "host") && !neu.host) { toast("Bitte eine Adresse eintragen"); return; }
      fertig({ ...neu, name: neu.name || netzTyp(neu.typ).name });
      schliessen();
    }
    if (e.target.dataset.a === "testen") {
      const r = await kritisch("/api/netz/test", { quelle: lesen() }, { titel: "Quelle testen", vorschau: "Das Dashboard fragt das Gerät jetzt einmal ab und zeigt das Ergebnis. Es wird nichts gespeichert.", erfolg: "Test abgeschlossen" });
      if (r) el("nq-test").innerHTML = netzErgebnisText(r.ergebnis);
    }
  };
  addEventListener("keydown", taste, true);
  zeichnen();
  el("nq-name").focus();
}
const netzSeiteNeu = () => { document.getElementById("seite").innerHTML = netzEinstellungenBauen(); };
function netzQuelleSetzen(q) {
  const i = netzEntwurf.quellen.findIndex(x => x.id === q.id);
  if (i < 0) netzEntwurf.quellen.push(q); else netzEntwurf.quellen[i] = q;
  netzSeiteNeu();
}

// --- Agent-Zugang -------------------------------------------------------------------------
function netzAgentDialog(konfig) {
  const json = JSON.stringify(konfig, null, 2), rahmen = document.createElement("div");
  rahmen.className = "palette-hintergrund w-hintergrund";
  rahmen.innerHTML = `<div class="palette sheet" role="dialog" aria-modal="true" aria-label="Agent" style="padding:0 0 16px"><div class="sheet-kopf"><span></span><b>Agent einrichten</b><button type="button" class="sheet-text fett" data-a="zu">Fertig</button></div>` +
    `<div style="padding:0 20px"><p class="warnung">Diese Konfiguration enthält den Zugang und die Passwörter deiner Quellen. Sie wird nur jetzt angezeigt. Speichere sie nur auf dem Gerät, auf dem der Agent läuft.</p>` +
    `<ol class="nschritte"><li><a href="/netz-agent.zip" download>netz-agent.zip</a> laden und zu Hause entpacken (Python 3.9 oder neuer).</li><li>Die Konfiguration unten als <span class="mono">netz-agent.json</span> daneben speichern.</li>` +
    `<li>Testen: <span class="mono">python3 netz_agent.py --trocken</span> zeigt, was abgefragt wird, ohne zu senden.</li><li>Dauerhaft: <span class="mono">python3 netz_agent.py</span>, oder als Dienst:<pre class="roh">[Unit]\nDescription=Claude Deck Netzwerk-Agent\nAfter=network-online.target\n\n[Service]\nWorkingDirectory=/opt/netz-agent\nExecStart=/usr/bin/python3 netz_agent.py\nRestart=always\n\n[Install]\nWantedBy=multi-user.target</pre></li></ol>` +
    `<textarea class="nfeld" readonly id="na-json">${esc(json)}</textarea><div style="margin-top:10px"><button class="tab aktiv nbtn" type="button" data-a="kopieren">Kopieren</button><button class="tab nbtn" type="button" data-a="datei">Als Datei speichern</button></div></div></div>`;
  document.body.appendChild(rahmen);
  rahmen.onclick = e => {
    if (e.target === rahmen || e.target.dataset.a === "zu") { rahmen.remove(); zeige(); return; }
    if (e.target.dataset.a === "kopieren") kopieren(json, "Konfiguration kopiert");
    if (e.target.dataset.a === "datei") {
      const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([json], { type: "application/json" })), download: "netz-agent.json" });
      link.click();
      URL.revokeObjectURL(link.href);
    }
  };
}

// --- Aktionen -----------------------------------------------------------------------------
async function netzEinstellungAktion(knopf) {
  const { ne: aktion, id, wert, i } = knopf.dataset;
  if (aktion === "modus") { netzEntwurf.modus = wert; netzSeiteNeu(); }
  else if (aktion === "quelle-neu") netzQuelleDialog(netzNeueQuelle("snmp"), netzQuelleSetzen);
  else if (aktion === "quelle-bearbeiten") netzQuelleDialog(netzEntwurf.quellen.find(q => q.id === id), netzQuelleSetzen);
  else if (aktion === "quelle-entfernen") { netzEntwurf.quellen = netzEntwurf.quellen.filter(q => q.id !== id); netzSeiteNeu(); }
  else if (aktion === "fund-hinzu") { const g = netzFunde[Number(i)]; netzQuelleDialog(netzNeueQuelle(g.typ || "ping", g.ip, g.typname || g.name || g.ip), netzQuelleSetzen); }
  else if (aktion === "entdecken") {
    const r = await kritisch("/api/netz/entdecken", { netz: String(netzEntwurf.netze[0] || "") }, { titel: "Netz durchsuchen", vorschau: "Das Dashboard sucht jetzt Geräte im eingetragenen Netz (oder im automatisch erkannten). Das dauert bis zu einer halben Minute.", erfolg: "Suche abgeschlossen" });
    if (r) { netzFunde = r.geraete; document.getElementById("ne-funde").innerHTML = netzFundeHtml(); }
  } else if (aktion === "agent-token") {
    const r = await kritisch("/api/netz/token", {}, { titel: "Agent-Zugang erzeugen", vorschau: "Ein bisheriger Zugang des Agenten wird damit ungültig. Die Konfiguration enthält die zuletzt GESPEICHERTEN Quellen: speichere Änderungen daher zuerst.", erfolg: "Zugang erzeugt" });
    if (r) netzAgentDialog(r.konfig);
  } else if (aktion === "agent-loeschen") { if (await kritisch("/api/netz/token/loeschen", {}, { titel: "Agent-Zugang widerrufen", vorschau: "Der Agent kann danach keine Daten mehr senden.", erfolg: "Zugang widerrufen" })) zeige(); }
  else if (aktion === "verwerfen") zeige();
  else if (aktion === "speichern") {
    const neu = JSON.stringify(netzMaskiert(netzEntwurf), null, 1);
    const r = await kritisch("/api/netz/config", netzKorper(), { titel: "Netzwerk-Einstellungen speichern", vorschau: diffZeilen(netzOriginal, neu), erfolg: "Gespeichert" });
    if (r) zeige();
  }
}
document.addEventListener("click", e => {
  const knopf = e.target.closest("[data-ne]"), schalter = e.target.closest("[data-ne-schalter]");
  if (schalter && netzEntwurf) { const an = schalter.getAttribute("aria-checked") !== "true"; schalter.setAttribute("aria-checked", an); netzEntwurf.alarme[schalter.dataset.neSchalter] = an; }
  if (knopf && netzEntwurf) netzEinstellungAktion(knopf).catch(err => { console.warn("Einstellung fehlgeschlagen:", err); toast("Fehlgeschlagen"); });
});
document.addEventListener("input", e => {
  const feld = e.target.dataset && e.target.dataset.neFeld;
  if (!feld || !netzEntwurf) return;
  const teile = feld.split("."), letzter = teile.pop(), ziel = teile.reduce((o, k) => o[k], netzEntwurf);
  ziel[letzter] = feld === "netze" ? e.target.value.split(",").map(s => s.trim()).filter(Boolean) : e.target.dataset.art === "number" ? Number(e.target.value) : e.target.value;
});
