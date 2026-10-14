// Server: Firewall (ufw) mit Regeln, Ports und Rücknahme-Timer. Läuft nach netz-einstellungen.js.
// Nutzt Helfer aus index.html, control.js und netz.js (esc, karte, leer, abzeichen, api, postJson, toast, kritisch, hinweisKarte, SEITEN, GRUPPEN, gruppeAnlegen, .nfeld).

GRUPPEN.server = ["Server", "#server/firewall"];
gruppeAnlegen("server", "Server", '<rect x="4" y="4" width="16" height="6" rx="1.5"/><rect x="4" y="14" width="16" height="6" rx="1.5"/><path d="M8 7h.01M8 17h.01"/>',
  [["#server/firewall", "Firewall"], ["#server/docker", "Docker"]]);

const FW_VORLAGEN = [["SSH", "22", "tcp"], ["HTTP", "80", "tcp"], ["HTTPS", "443", "tcp"], ["HTTP/3 (UDP 443)", "443", "udp"]];
const FW_ZUSTAND = { lokal: ["nur lokal", true], offen: ["offen", true], eingeschraenkt: ["eingeschränkt", true], blockiert: ["von der Firewall blockiert", true], docker: ["Docker: ufw greift hier nicht", false] };
let fwTimer = null;

const fwStil = document.createElement("style");
fwStil.textContent = `
  .fwform { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 12px; } .fwform label { display: block; font-size: .8125rem; color: var(--leise); }
  .fwform .nfeld { margin-top: 4px; } .fwpause { display: flex; align-items: center; gap: 8px; margin: 0 0 12px; } .fwpause input { width: auto; }
  .fwwarte { border: 2px solid var(--knapp); }
`;
document.head.appendChild(fwStil);

const fwGeschuetzt = (view, port) => { const [von, bis] = String(port).split(":").map(Number); return view.geschuetzt.some(p => von <= p && p <= (bis || von)); };

function fwBauen(v) {
  if (!v.verfuegbar) return leer("ufw ist nicht installiert", 'Auf dem Server: <span class="mono">apt install ufw</span>');
  const warte = v.ausstehend ? `<div class="karte fwwarte"><p class="label">Änderung wartet auf Bestätigung</p><p>${esc(v.ausstehend.text)}</p>` +
    `<p class="kontext">Ohne Bestätigung wird sie in <b id="fw-rest">${v.ausstehend.sekunden}</b> Sekunden zurückgenommen. Funktioniert alles noch, bestätige.</p>` +
    `<button class="tab aktiv" data-fw="bestaetigen" data-id="${v.ausstehend.id}">Bestätigen</button> <button class="tab" data-fw="zuruecknehmen" data-id="${v.ausstehend.id}">Jetzt zurücknehmen</button></div>` : "";
  const standard = Object.entries(v.standard).map(([k, p]) => `${k}: ${esc(p)}`).join(" · ");
  const status = karte("Status", `<p>${abzeichen(v.aktiv ? "Firewall ist an" : "Firewall ist aus", v.aktiv)}</p><p class="kontext">Standard: ${standard}. Geschützt gegen Aussperren: Ports ${v.geschuetzt.join(", ")}.</p>` +
    `<button class="tab" data-fw="schalten" data-an="${v.aktiv ? "0" : "1"}">${v.aktiv ? "Firewall ausschalten" : "Firewall einschalten"}</button>`);
  const neu = karte("Regel hinzufügen", `<div class="chips">${FW_VORLAGEN.map(([n, p, pr]) => `<span class="nachbar" data-fw="vorlage" data-port="${p}" data-proto="${pr}">${esc(n)}</span>`).join("")}</div>` +
    `<div class="fwform"><label>Aktion<select class="nfeld" id="fw-aktion"><option value="allow">erlauben</option><option value="deny">verwerfen</option><option value="reject">ablehnen</option><option value="limit">begrenzen (SSH-Schutz)</option></select></label>` +
    `<label>Port oder Bereich<input class="nfeld" id="fw-port" placeholder="8080 oder 8000:8100" autocomplete="off" inputmode="numeric"></label>` +
    `<label>Protokoll<select class="nfeld" id="fw-proto"><option>tcp</option><option>udp</option><option value="any">beide</option></select></label>` +
    `<label>Nur von (leer: alle)<input class="nfeld" id="fw-quelle" placeholder="203.0.113.0/24" autocomplete="off"></label>` +
    `<label>Kommentar<input class="nfeld" id="fw-kommentar" maxlength="40" autocomplete="off"></label></div>` +
    `<label class="fwpause"><input type="checkbox" id="fw-timer" checked> Mit Rücknahme-Timer (60 Sekunden, ohne Bestätigung wird zurückgenommen)</label><button class="tab aktiv" data-fw="neu">Regel hinzufügen</button>`);
  const regeln = karte("Regeln", v.regeln.length ? `<div class="tabelle"><table><thead><tr><th>Nr.</th><th>Ziel</th><th>Aktion</th><th>Von</th><th>Kommentar</th><th></th></tr></thead><tbody>` +
    v.regeln.map(r => `<tr><td>${r.nr}</td><td class="mono">${esc(r.nach)}</td><td>${esc(r.aktion)}</td><td>${esc(r.von)}</td><td>${esc(r.kommentar)}</td>` +
      `<td><button class="tab" data-fw="loeschen" data-nr="${r.nr}" data-nach="${esc(r.nach)}" data-aktion="${esc(r.aktion)}" data-von="${esc(r.von)}" data-port="${esc(r.port)}">Löschen</button></td></tr>`).join("") + "</tbody></table></div>"
    : leer("Keine Regeln", "Eingehend ist alles gesperrt, was nicht erlaubt ist."));
  const ports = karte("Lauschende Ports", `<p class="kontext">Was auf dem Server auf Verbindungen wartet und was die Firewall davon durchlässt. Docker-Ports umgehen ufw: Sie sind nur dann von außen erreichbar, wenn du sie beim Anlegen ausdrücklich freigibst.</p>` +
    `<div class="tabelle"><table><thead><tr><th>Port</th><th>Protokoll</th><th>Programm</th><th>Zustand</th></tr></thead><tbody>` +
    v.ports.map(p => { const [text, ok] = FW_ZUSTAND[p.zustand]; return `<tr><td class="mono">${p.port}</td><td>${p.proto}</td><td>${esc(p.prozess)}</td><td>${abzeichen(text, ok)}</td></tr>`; }).join("") + "</tbody></table></div>");
  return hinweisKarte("Änderungen verlangen Passwort und Code erneut (5 Minuten) und stehen im Audit. Mit Rücknahme-Timer stellt der Server den alten Stand selbst wieder her, falls du dich aussperrst.") +
    warte + `<div class="raster zwei">${status}${neu}</div>${regeln}${ports}`;
}

async function fwNeuZeichnen() {
  if (location.hash !== "#server/firewall") return;
  document.getElementById("seite").innerHTML = fwBauen(await api("/api/firewall"));
  fwZaehlen();
}
function fwZaehlen() {
  clearInterval(fwTimer);
  if (!document.getElementById("fw-rest")) return;
  fwTimer = setInterval(() => {
    const feld = document.getElementById("fw-rest");
    if (!feld || location.hash !== "#server/firewall") { clearInterval(fwTimer); return; }
    const rest = Number(feld.textContent) - 1;
    feld.textContent = Math.max(rest, 0);
    if (rest <= -2) { clearInterval(fwTimer); fwNeuZeichnen(); }  // der Server hat zurückgenommen, Liste neu laden
  }, 1000);
}
SEITEN["server/firewall"] = ["Server", "Firewall", ({ x }) => { setTimeout(fwZaehlen, 0); return fwBauen(x[0]); }, ["/api/firewall"]];

const fwFeld = id => document.getElementById(id).value.trim();
async function fwAktion(knopf) {
  const d = knopf.dataset;
  if (d.fw === "vorlage") { document.getElementById("fw-port").value = d.port; document.getElementById("fw-proto").value = d.proto; return; }
  const v = await api("/api/firewall"), timer = (document.getElementById("fw-timer") || {}).checked !== false;
  if (d.fw === "bestaetigen" || d.fw === "zuruecknehmen") {
    const r = await postJson(`/api/firewall/${d.fw}`, { id: d.id });
    toast(r.ok ? (d.fw === "bestaetigen" ? "Bestätigt" : "Zurückgenommen") : r.error || "Fehlgeschlagen");
  } else if (d.fw === "neu") {
    const body = { aktion: fwFeld("fw-aktion"), port: fwFeld("fw-port"), proto: fwFeld("fw-proto"), quelle: fwFeld("fw-quelle"), kommentar: fwFeld("fw-kommentar"), ruecknahme: timer };
    const heikel = body.aktion !== "allow" && !body.quelle && fwGeschuetzt(v, body.port);
    body.bestaetigt = heikel;
    await kritisch("/api/firewall/regel", body, { titel: "Firewall-Regel hinzufügen", vorschau: (heikel ? "ACHTUNG: Das sperrt SSH, 80 oder 443 für alle. Der Rücknahme-Timer schützt dich nur, wenn er an ist.\n\n" : "") +
      `${body.aktion} ${body.port}/${body.proto} von ${body.quelle || "überall"}${body.kommentar ? `\n# ${body.kommentar}` : ""}` });
  } else if (d.fw === "loeschen") {
    const heikel = d.aktion === "ALLOW" && fwGeschuetzt(v, d.port);
    await kritisch("/api/firewall/loeschen", { nr: Number(d.nr), nach: d.nach, aktion: d.aktion, von: d.von, ruecknahme: timer, bestaetigt: heikel },
      { titel: `Regel ${d.nr} löschen`, vorschau: (heikel ? "ACHTUNG: Das ist die Freigabe für SSH, 80 oder 443.\n\n" : "") + `${d.aktion} ${d.nach} von ${d.von}` });
  } else if (d.fw === "schalten") {
    const an = d.an === "1";
    await kritisch("/api/firewall/schalten", { an, ruecknahme: timer }, { titel: an ? "Firewall einschalten" : "Firewall ausschalten", vorschau: an ? "ufw enable" : "ACHTUNG: Ohne Firewall ist jeder offene Port von außen erreichbar.\n\nufw disable" });
  }
  await fwNeuZeichnen();
}
document.addEventListener("click", e => {
  const knopf = e.target.closest("[data-fw]");
  if (knopf) fwAktion(knopf).catch(err => { console.warn("Firewall-Aktion fehlgeschlagen:", err); toast("Fehlgeschlagen"); });
});
