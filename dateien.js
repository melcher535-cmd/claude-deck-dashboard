// Server: Dateibrowser für die Festplatte des Servers mit Download, Upload, neuen Ordnern, Umbenennen und Löschen. Läuft nach ftp.js.
// Nutzt Helfer aus index.html, control.js, netz.js und ftp.js (esc, karte, leer, api, toast, kritisch, hinweisKarte, SEITEN, .nfeld, ftGroesse, ftUnter, ftBase64).

const DT_MAX_UPLOAD = 8 * 2 ** 20;
let dtPfad = "/";

const dtSymbol = { ordner: "📁", datei: "📄", link: "🔗" };
const dtKnopf = (aktion, pfad, art, text) => `<button class="tab" data-dt="${aktion}" data-pfad="${esc(pfad)}" data-art="${art}">${text}</button>`;

function dtTabelle(r) {
  dtPfad = r.pfad;
  const hoch = dtPfad === "/" ? "" : `<tr><td colspan="4"><span class="ftname" data-dt="cd" data-pfad="${esc(ftUnter(dtPfad, ".."))}">↑ ..</span></td></tr>`;
  const zeilen = r.eintraege.map(e => {
    const voll = ftUnter(dtPfad, e.name), nameHtml = e.art === "datei" ? esc(e.name) : `<span class="ftname" data-dt="cd" data-pfad="${esc(voll)}">${esc(e.name)}</span>`;
    return `<tr><td>${dtSymbol[e.art]} ${nameHtml}</td><td class="r leise">${e.art === "ordner" ? "" : ftGroesse(e.groesse)}</td><td class="leise">${new Date(e.geaendert * 1000).toLocaleString("de-DE")}</td>` +
      `<td class="ftaktion">${e.art === "ordner" ? "" : dtKnopf("laden", voll, e.art, "Laden")}${dtKnopf("umbenennen", voll, e.art, "Umbenennen")}${dtKnopf("loeschen", voll, e.art, "Löschen")}</td></tr>`;
  }).join("");
  return karte(`<span class="mono">${esc(dtPfad)}</span>`,
    (r.gekuerzt ? `<p class="warnung">Nur die ersten 2000 Einträge werden gezeigt.</p>` : "") +
    `<div class="tabelle"><table><thead><tr><th>Name</th><th class="r">Größe</th><th>Geändert</th><th></th></tr></thead><tbody>${hoch}${zeilen || '<tr><td colspan="4" class="leise">leer</td></tr>'}</tbody></table></div>` +
    `<div class="ftzeile"><input class="nfeld" id="dt-ordner" placeholder="Neuer Ordner" maxlength="255"><button class="tab" data-dt="ordner">Ordner anlegen</button></div>` +
    `<div class="ftzeile"><input type="file" id="dt-datei"><button class="tab" data-dt="hochladen">Hochladen</button></div>`);
}
SEITEN["server/dateien"] = ["Server", "Dateien", ({ x }) => hinweisKarte("Zeigt die Festplatte des Servers mit den Rechten des Dashboards. Laden, Hochladen, Ordner anlegen, Umbenennen und Löschen verlangen Passwort und Code erneut (5 Minuten) und stehen im Audit. " +
  "Gelöscht werden nur Dateien und leere Ordner. Hochladen und Umbenennen überschreiben nichts. Ordner direkt unter / sind geschützt. Download bis 50 MB, Upload bis 8 MB.") +
  `<div id="dt-browser">${x[0].error ? leer("Nicht lesbar", esc(x[0].error)) : dtTabelle(x[0])}</div>`, ["/api/dateien/ls?pfad=%2F"]];

async function dtOeffnen(pfad) {
  const r = await api(`/api/dateien/ls?pfad=${encodeURIComponent(pfad)}`);
  if (r.error) { toast(r.error); return; }
  document.getElementById("dt-browser").innerHTML = dtTabelle(r);
}
async function dtLaden(pfad) {
  const r = await kritisch("/api/dateien/freigabe", { pfad }, { titel: "Datei laden", vorschau: pfad, erfolg: "Download startet" });
  if (!r) return;
  const antwort = await fetch(`/api/dateien/download?pfad=${encodeURIComponent(pfad)}`);
  if (!antwort.ok) { toast((await antwort.json()).error || "Fehlgeschlagen"); return; }
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await antwort.blob());
  link.download = pfad.split("/").pop();
  link.click();
  URL.revokeObjectURL(link.href);
}

async function dtAktion(knopf) {
  const d = knopf.dataset;
  if (d.dt === "cd") return dtOeffnen(d.pfad);
  if (d.dt === "laden") return dtLaden(d.pfad);
  const art = d.art === "ordner" ? "Ordner" : "Datei";
  let r;
  if (d.dt === "ordner") {
    const neu = document.getElementById("dt-ordner").value.trim();
    r = await kritisch("/api/dateien/ordner", { pfad: dtPfad, neu }, { titel: "Ordner anlegen", vorschau: ftUnter(dtPfad, neu) });
  } else if (d.dt === "loeschen") {
    r = await kritisch("/api/dateien/loeschen", { pfad: d.pfad }, { titel: `${art} löschen`, vorschau: `${d.pfad}\n${d.art === "ordner" ? "Nur leere Ordner lassen sich löschen." : "Das kann nicht rückgängig gemacht werden."}` });
  } else if (d.dt === "umbenennen") {
    const neu = (prompt(`Neuer Name für ${d.pfad.split("/").pop()}`, d.pfad.split("/").pop()) || "").trim();
    if (!neu) return;
    r = await kritisch("/api/dateien/umbenennen", { pfad: d.pfad, neu }, { titel: `${art} umbenennen`, vorschau: `${d.pfad}\n→ ${neu}`, erfolg: "Umbenannt" });
  } else if (d.dt === "hochladen") {
    const datei = document.getElementById("dt-datei").files[0];
    if (!datei) { toast("Erst eine Datei wählen"); return; }
    if (datei.size > DT_MAX_UPLOAD) { toast("Datei ist größer als 8 MB"); return; }
    r = await kritisch("/api/dateien/hochladen", { pfad: dtPfad, datei: datei.name, daten: await ftBase64(datei) }, { titel: "Datei hochladen", vorschau: `${ftUnter(dtPfad, datei.name)}\n${ftGroesse(datei.size)}`, erfolg: "Hochgeladen" });
  }
  if (r) await dtOeffnen(dtPfad);
}
document.addEventListener("click", e => {
  const knopf = e.target.closest("[data-dt]");
  if (knopf) dtAktion(knopf).catch(err => { console.warn("Dateien-Aktion fehlgeschlagen:", err); toast("Fehlgeschlagen"); });
});
