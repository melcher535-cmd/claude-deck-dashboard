// Server: Dateimanager für die Festplatte des Servers: Ordner öffnen, Download, Upload, Textdateien bearbeiten, kopieren, verschieben, umbenennen, löschen. Läuft nach ftp.js.
// Nutzt Helfer aus index.html, control.js, netz.js und ftp.js (esc, karte, leer, api, toast, kritisch, hinweisKarte, SEITEN, .nfeld, .chips, ftGroesse, ftUnter, ftBase64).

const DT_MAX_UPLOAD = 8 * 2 ** 20, DT_MAX_TEXT = 2 ** 20;
const DT_ORTE = ["/", "/root", "/home", "/srv", "/etc", "/var", "/tmp"];
let dtPfad = "/", dtZwischen = null, dtBearbeitet = "";

const dtSymbol = { ordner: "📁", datei: "📄", link: "🔗" };
const dtName = pfad => pfad.split("/").pop() || "/";
const dtKnopf = (aktion, pfad, art, text, extra = "") => `<button class="tab" data-dt="${aktion}" data-pfad="${esc(pfad)}" data-art="${art}" ${extra}>${text}</button>`;
const dtOrt = (pfad, text) => `<span class="nachbar" data-dt="cd" data-pfad="${esc(pfad)}">${esc(text)}</span>`;

function dtKrumen(pfad) {
  const teile = pfad.split("/").filter(Boolean);
  return `<div class="chips">${dtOrt("/", "/")}${teile.map((t, i) => dtOrt("/" + teile.slice(0, i + 1).join("/"), t)).join("")}</div>`;
}
function dtZeile(e) {
  const voll = ftUnter(dtPfad, e.name), offen = e.art !== "datei";
  const text = e.art === "datei" && e.groesse <= DT_MAX_TEXT ? dtKnopf("bearbeiten", voll, e.art, "Bearbeiten") : "";
  return `<tr${offen ? ` class="klick" data-dt="cd" data-pfad="${esc(voll)}"` : ""}><td>${dtSymbol[e.art]} <b>${esc(e.name)}</b></td><td class="r leise">${e.art === "ordner" ? "" : ftGroesse(e.groesse)}</td>` +
    `<td class="leise">${new Date(e.geaendert * 1000).toLocaleString("de-DE")}</td><td class="mono leise">${esc(e.rechte)}</td>` +
    `<td class="ftaktion">${text}${e.art === "ordner" ? "" : dtKnopf("laden", voll, e.art, "Laden")}${dtKnopf("kopieren", voll, e.art, "Kopieren")}${dtKnopf("verschieben", voll, e.art, "Ausschneiden")}` +
    `${dtKnopf("umbenennen", voll, e.art, "Umbenennen")}${dtKnopf("loeschen", voll, e.art, "Löschen")}</td></tr>`;
}
function dtTabelle(r) {
  dtPfad = r.pfad;
  const hoch = dtPfad === "/" ? "" : `<tr class="klick" data-dt="cd" data-pfad="${esc(ftUnter(dtPfad, ".."))}"><td colspan="5">↑ <b>..</b></td></tr>`;
  const ablage = dtZwischen ? `<p class="kontext"><b>${dtZwischen.modus === "kopieren" ? "Kopieren" : "Verschieben"}:</b> <span class="mono">${esc(dtZwischen.pfad)}</span> ` +
    `<button class="tab aktiv" data-dt="einfuegen">Hier einfügen</button><button class="tab" data-dt="ablage-weg">Abbrechen</button></p>` : "";
  return karte(`<span class="mono">${esc(dtPfad)}</span>`,
    dtKrumen(dtPfad) + `<div class="chips">${DT_ORTE.filter(o => o !== dtPfad).map(o => dtOrt(o, o)).join("")}</div>` + ablage + (r.gekuerzt ? `<p class="warnung">Nur die ersten 2000 Einträge werden gezeigt.</p>` : "") +
    `<div class="tabelle"><table><thead><tr><th>Name</th><th class="r">Größe</th><th>Geändert</th><th>Rechte</th><th></th></tr></thead><tbody>${hoch}${r.eintraege.map(dtZeile).join("") || '<tr><td colspan="5" class="leise">leer</td></tr>'}</tbody></table></div>` +
    `<div class="ftzeile"><input class="nfeld" id="dt-ordner" placeholder="Neuer Ordner" maxlength="255"><button class="tab" data-dt="ordner">Ordner anlegen</button>` +
    `<input class="nfeld" id="dt-neudatei" placeholder="Neue leere Datei" maxlength="255"><button class="tab" data-dt="neudatei">Datei anlegen</button></div>` +
    `<div class="ftzeile"><input type="file" id="dt-datei"><button class="tab" data-dt="hochladen">Hochladen</button></div>`);
}
SEITEN["server/dateien"] = ["Server", "Dateien", ({ x }) => hinweisKarte("Zeigt die Festplatte des Servers mit den Rechten des Dashboards. Klick auf einen Ordner öffnet ihn. Laden, Bearbeiten, Kopieren, Ausschneiden, Hochladen, Umbenennen und Löschen verlangen Passwort und Code erneut (5 Minuten) und stehen im Audit. " +
  "Kopieren, Hochladen und Umbenennen überschreiben nichts (bei gleichem Namen wird „Kopie“ angehängt). Ordner direkt unter / sind geschützt. Download bis 50 MB, Upload bis 8 MB, Kopieren bis 500 MB, Bearbeiten bis 1 MB.") +
  `<div id="dt-browser">${x[0].error ? leer("Nicht lesbar", esc(x[0].error)) : dtTabelle(x[0])}</div><div id="dt-editor"></div>`, ["/api/dateien/ls?pfad=%2F"]];

async function dtOeffnen(pfad) {
  const r = await api(`/api/dateien/ls?pfad=${encodeURIComponent(pfad)}`);
  if (r.error) { toast(r.error); return; }
  document.getElementById("dt-browser").innerHTML = dtTabelle(r);
}
async function dtHolen(pfad, titel) {
  const r = await kritisch("/api/dateien/freigabe", { pfad }, { titel, vorschau: pfad, erfolg: "Wird geladen" });
  if (!r) return null;
  const antwort = await fetch(`/api/dateien/download?pfad=${encodeURIComponent(pfad)}`);
  if (!antwort.ok) { toast((await antwort.json()).error || "Fehlgeschlagen"); return null; }
  return antwort.blob();
}
async function dtLaden(pfad) {
  const blob = await dtHolen(pfad, "Datei laden");
  if (!blob) return;
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = dtName(pfad);
  link.click();
  URL.revokeObjectURL(link.href);
}
async function dtBearbeiten(pfad) {
  const blob = await dtHolen(pfad, "Datei bearbeiten");
  if (!blob) return;
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(await blob.arrayBuffer()); } catch { toast("Keine Textdatei"); return; }
  dtBearbeitet = pfad;
  document.getElementById("dt-editor").innerHTML = karte(`Bearbeiten: <span class="mono">${esc(pfad)}</span>`,
    `<textarea class="auswahl mono" id="dt-text" rows="22" spellcheck="false" style="width:100%"></textarea>` +
    `<p style="margin-top:12px"><button class="tab aktiv" data-dt="speichern">Speichern</button><button class="tab" data-dt="editor-zu">Schließen</button></p>`);
  document.getElementById("dt-text").value = text;
  document.getElementById("dt-editor").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function dtAktion(knopf) {
  const d = knopf.dataset, art = d.art === "ordner" ? "Ordner" : "Datei";
  if (d.dt === "cd") return dtOeffnen(d.pfad);
  if (d.dt === "laden") return dtLaden(d.pfad);
  if (d.dt === "bearbeiten") return dtBearbeiten(d.pfad);
  if (d.dt === "editor-zu") { document.getElementById("dt-editor").innerHTML = ""; return; }
  if (d.dt === "kopieren" || d.dt === "verschieben") { dtZwischen = { pfad: d.pfad, modus: d.dt }; return dtOeffnen(dtPfad); }
  if (d.dt === "ablage-weg") { dtZwischen = null; return dtOeffnen(dtPfad); }
  let r;
  if (d.dt === "einfuegen") {
    const kopie = dtZwischen.modus === "kopieren";
    r = await kritisch(`/api/dateien/${dtZwischen.modus}`, { pfad: dtZwischen.pfad, ziel: dtPfad }, { titel: kopie ? "Kopieren" : "Verschieben", vorschau: `${dtZwischen.pfad}\n→ ${dtPfad}`, erfolg: kopie ? "Kopiert" : "Verschoben" });
    if (r && !kopie) dtZwischen = null;
  } else if (d.dt === "speichern") {
    r = await kritisch("/api/dateien/speichern", { pfad: dtBearbeitet, text: document.getElementById("dt-text").value }, { titel: "Datei speichern", vorschau: `${dtBearbeitet}\nDer bisherige Inhalt wird ersetzt.`, erfolg: "Gespeichert" });
    if (r) await dtOeffnen(dtPfad);
    return;
  } else if (d.dt === "ordner") {
    const neu = document.getElementById("dt-ordner").value.trim();
    r = await kritisch("/api/dateien/ordner", { pfad: dtPfad, neu }, { titel: "Ordner anlegen", vorschau: ftUnter(dtPfad, neu) });
  } else if (d.dt === "neudatei") {
    const neu = document.getElementById("dt-neudatei").value.trim();
    r = await kritisch("/api/dateien/hochladen", { pfad: dtPfad, datei: neu, daten: "" }, { titel: "Leere Datei anlegen", vorschau: ftUnter(dtPfad, neu), erfolg: "Angelegt" });
  } else if (d.dt === "loeschen") {
    const ordner = d.art === "ordner";
    r = await kritisch("/api/dateien/loeschen", { pfad: d.pfad, rekursiv: ordner }, { titel: `${art} löschen`, vorschau: `${d.pfad}\n${ordner ? "Der Ordner wird mit ALLEM Inhalt gelöscht." : "Das kann nicht rückgängig gemacht werden."}` });
  } else if (d.dt === "umbenennen") {
    const neu = (prompt(`Neuer Name für ${dtName(d.pfad)}`, dtName(d.pfad)) || "").trim();
    if (!neu || neu === dtName(d.pfad)) return;
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
document.addEventListener("keydown", e => {
  const zeile = e.key === "Enter" && e.target.matches && e.target.matches("tr.klick[data-dt]") ? e.target : null;
  if (zeile) dtAktion(zeile).catch(err => { console.warn("Dateien-Aktion fehlgeschlagen:", err); toast("Fehlgeschlagen"); });
});
