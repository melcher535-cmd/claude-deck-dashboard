// Server: FTP-Dateibrowser mit gespeicherten Verbindungen, Download, Upload, neuen Ordnern und Löschen. Läuft nach docker.js.
// Nutzt Helfer aus index.html, control.js und netz.js (esc, karte, leer, abzeichen, api, toast, kritisch, hinweisKarte, SEITEN, .nfeld) und die Stile aus firewall.js (.fwform).

const FT_MODI = { tls: "TLS, Zertifikat prüfen", "tls-selbst": "TLS, Zertifikat nicht prüfen", klartext: "unverschlüsselt (nicht empfohlen)" };
const FT_MAX_UPLOAD = 8 * 2 ** 20;
let ftName = "", ftPfad = "/";

const ftStil = document.createElement("style");
ftStil.textContent = `.ftname { cursor: pointer; font-weight: 600; } .ftaktion { white-space: nowrap; text-align: right; } .ftaktion .tab { margin: 0 0 4px 4px; } .ftzeile { display: flex; gap: 8px; align-items: center; margin-top: 12px; flex-wrap: wrap; }`;
document.head.appendChild(ftStil);

const ftGroesse = b => b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${Math.round(b / 1e3)} KB` : `${b} B`;
const ftUnter = (pfad, name) => pfad.replace(/\/$/, "") + "/" + name;
const ftKnopf = (aktion, name, text, extra = "") => `<button class="tab" data-ft="${aktion}" data-name="${esc(name)}" ${extra}>${text}</button>`;

function ftListe(v) {
  return karte("Verbindungen", v.verbindungen.length ? `<div class="tabelle"><table><thead><tr><th>Name</th><th>Server</th><th>Modus</th><th>Zustand</th><th></th></tr></thead><tbody>${v.verbindungen.map(c =>
    `<tr><td><b>${esc(c.name)}</b></td><td class="mono">${esc(c.user)}@${esc(c.host)}:${c.port}</td><td>${abzeichen(FT_MODI[c.modus] || c.modus, c.modus === "tls")}</td><td>${abzeichen(c.verbunden ? "verbunden" : "getrennt", c.verbunden)}</td>` +
    `<td class="ftaktion">${c.verbunden ? ftKnopf("oeffnen", c.name, "Öffnen") + ftKnopf("trennen", c.name, "Trennen") : ftKnopf("neu-verbinden", c.name, "Verbinden", `data-host="${esc(c.host)}" data-port="${c.port}" data-user="${esc(c.user)}" data-modus="${esc(c.modus)}"`)}${ftKnopf("entfernen", c.name, "Entfernen")}</td></tr>`).join("")}</tbody></table></div>`
    : leer("Noch keine Verbindung", "Lege unten eine an."));
}
const ftFormular = () => karte("Verbinden", `<p class="kontext">Das Kennwort bleibt nur im Speicher des Servers und ist nach einem Neustart des Dashboards weg. Gespeichert werden Name, Server, Port, Benutzer und Modus.</p>` +
  `<div class="fwform"><label>Name<input class="nfeld" id="ft-name" placeholder="webspace" autocomplete="off" maxlength="40"></label><label>Server<input class="nfeld" id="ft-host" placeholder="ftp.example.org" autocomplete="off"></label>` +
  `<label>Port<input class="nfeld" id="ft-port" inputmode="numeric" value="21"></label><label>Benutzer<input class="nfeld" id="ft-user" autocomplete="off"></label>` +
  `<label>Kennwort<input class="nfeld" id="ft-pw" type="password" autocomplete="new-password"></label>` +
  `<label>Verschlüsselung<select class="auswahl" id="ft-modus">${Object.entries(FT_MODI).map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join("")}</select></label></div>` +
  `<p style="margin-top:16px"><button class="tab aktiv" data-ft="verbinden">Verbinden</button></p>`);
SEITEN["server/ftp"] = ["Server", "FTP", ({ x }) => hinweisKarte("Verbinden, Hochladen, Ordner anlegen und Löschen verlangen Passwort und Code erneut (5 Minuten) und stehen im Audit. Download und Hochladen gehen bis 50 MB beziehungsweise 8 MB.") +
  `<div id="ft-liste">${ftListe(x[0])}</div><div id="ft-browser"></div>${ftFormular()}`, ["/api/ftp"]];

const ftWert = id => document.getElementById(id).value.trim();
async function ftListeNeu() {
  const liste = document.getElementById("ft-liste");
  if (liste) liste.innerHTML = ftListe(await api("/api/ftp"));
}
async function ftOeffnen(name, pfad) {
  const r = await api(`/api/ftp/ls?name=${encodeURIComponent(name)}&pfad=${encodeURIComponent(pfad)}`);
  if (r.error) { toast(r.error); return; }
  ftName = name; ftPfad = r.pfad;
  const hoch = ftPfad === "/" ? "" : `<tr><td colspan="3"><span class="ftname" data-ft="cd" data-pfad="${esc(ftUnter(ftPfad, ".."))}">↑ ..</span></td></tr>`;
  const zeilen = r.eintraege.map(e => {
    const voll = ftUnter(ftPfad, e.name), ordner = e.art === "ordner";
    return `<tr><td>${ordner ? `<span class="ftname" data-ft="cd" data-pfad="${esc(voll)}">📁 ${esc(e.name)}</span>` : `📄 ${esc(e.name)}`}</td><td class="r leise">${ordner ? "" : ftGroesse(e.groesse)}</td>` +
      `<td class="ftaktion">${ordner ? "" : `<a class="tab" href="/api/ftp/download?name=${encodeURIComponent(name)}&pfad=${encodeURIComponent(voll)}">Laden</a>`}<button class="tab" data-ft="loeschen" data-pfad="${esc(voll)}" data-art="${e.art}">Löschen</button></td></tr>`;
  }).join("");
  document.getElementById("ft-browser").innerHTML = karte(`${esc(name)}: <span class="mono">${esc(ftPfad)}</span>`,
    `<div class="tabelle"><table><thead><tr><th>Name</th><th class="r">Größe</th><th></th></tr></thead><tbody>${hoch}${zeilen || '<tr><td colspan="3" class="leise">leer</td></tr>'}</tbody></table></div>` +
    `<div class="ftzeile"><input class="nfeld" id="ft-ordner" placeholder="Neuer Ordner" maxlength="200"><button class="tab" data-ft="ordner">Ordner anlegen</button></div>` +
    `<div class="ftzeile"><input type="file" id="ft-datei"><button class="tab" data-ft="hochladen">Hochladen</button></div>`);
  document.getElementById("ft-browser").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
const ftBase64 = datei => new Promise((ok, fehler) => {
  const leser = new FileReader();
  leser.onload = () => ok(leser.result.split(",")[1]);
  leser.onerror = () => fehler(leser.error);
  leser.readAsDataURL(datei);
});

async function ftAktion(knopf) {
  const d = knopf.dataset;
  if (d.ft === "cd") return ftOeffnen(ftName, d.pfad);
  if (d.ft === "oeffnen") return ftOeffnen(d.name, "/");
  if (d.ft === "neu-verbinden") {
    for (const [feld, wert] of [["name", d.name], ["host", d.host], ["port", d.port], ["user", d.user], ["modus", d.modus]]) document.getElementById(`ft-${feld}`).value = wert;
    document.getElementById("ft-pw").focus();
    return;
  }
  let r;
  if (d.ft === "verbinden") {
    const body = { name: ftWert("ft-name"), host: ftWert("ft-host"), port: ftWert("ft-port"), user: ftWert("ft-user"), password: document.getElementById("ft-pw").value, modus: ftWert("ft-modus") };
    r = await kritisch("/api/ftp/verbinden", body, { titel: `FTP verbinden: ${body.name}`, vorschau: `${body.user}@${body.host}:${body.port}\n${FT_MODI[body.modus] || ""}`, erfolg: "Verbunden" });
    if (r) { document.getElementById("ft-pw").value = ""; await ftListeNeu(); await ftOeffnen(body.name, "/"); }
    return;
  }
  if (d.ft === "trennen" || d.ft === "entfernen") {
    r = await kritisch(`/api/ftp/${d.ft}`, { name: d.name }, { titel: `FTP-Verbindung ${d.name} ${d.ft === "trennen" ? "trennen" : "entfernen"}`, vorschau: d.name });
    if (r) { if (ftName === d.name) document.getElementById("ft-browser").innerHTML = ""; await ftListeNeu(); }
    return;
  }
  if (d.ft === "ordner") {
    const neu = ftWert("ft-ordner");
    r = await kritisch("/api/ftp/ordner", { name: ftName, pfad: ftPfad, neu }, { titel: "Ordner anlegen", vorschau: ftUnter(ftPfad, neu) });
  } else if (d.ft === "loeschen") {
    r = await kritisch("/api/ftp/loeschen", { name: ftName, pfad: d.pfad, art: d.art }, { titel: `${d.art === "ordner" ? "Ordner" : "Datei"} löschen`, vorschau: `${d.pfad}\n${d.art === "ordner" ? "Nur leere Ordner lassen sich löschen." : "Das kann nicht rückgängig gemacht werden."}` });
  } else if (d.ft === "hochladen") {
    const datei = document.getElementById("ft-datei").files[0];
    if (!datei) { toast("Erst eine Datei wählen"); return; }
    if (datei.size > FT_MAX_UPLOAD) { toast("Datei ist größer als 8 MB"); return; }
    r = await kritisch("/api/ftp/hochladen", { name: ftName, pfad: ftPfad, datei: datei.name, daten: await ftBase64(datei) }, { titel: "Datei hochladen", vorschau: `${ftUnter(ftPfad, datei.name)}\n${ftGroesse(datei.size)}\nEine gleichnamige Datei wird überschrieben.`, erfolg: "Hochgeladen" });
  }
  if (r) await ftOeffnen(ftName, ftPfad);
}
document.addEventListener("click", e => {
  const knopf = e.target.closest("[data-ft]");
  if (knopf) ftAktion(knopf).catch(err => { console.warn("FTP-Aktion fehlgeschlagen:", err); toast("Fehlgeschlagen"); });
});
