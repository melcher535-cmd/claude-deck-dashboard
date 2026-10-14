// Server: Docker-Container anlegen, bedienen und ansehen, dazu Vorlagen, Logs, Auslastung und Images. Läuft nach firewall.js.
// Nutzt Helfer aus index.html, control.js und netz.js (esc, karte, stat, leer, abzeichen, api, toast, kritisch, hinweisKarte, SEITEN, .nfeld) und die Stile aus firewall.js (.fwform).

const DK_TAKT_MS = 2000;
let dkTimer = null, dkVorlagen = [];

const dkStil = document.createElement("style");
dkStil.textContent = `
  .dkzeile { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 8px; align-items: center; margin-bottom: 8px; } .dkzeile label { display: flex; align-items: center; gap: 6px; font-size: .8125rem; }
  .dkzeile label input { width: auto; } .dkabschnitt { margin: 14px 0 6px; font-weight: 600; } .dkaktion { white-space: nowrap; } .dkaktion .tab { margin: 0 4px 4px 0; }
`;
document.head.appendChild(dkStil);

const dkGroesse = b => b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${Math.round(b / 1e6)} MB` : `${Math.round(b / 1e3)} KB`;
const dkPorts = c => c.ports.filter(([, oeffentlich]) => oeffentlich).map(([ip, oeffentlich, innen, art]) => `<span class="mono">${esc(ip || "–")}:${oeffentlich}→${innen}/${art}</span>`).join("<br>") || '<span class="leise">–</span>';
const dkKnopf = (aktion, name, text) => `<button class="tab" data-dk="${aktion}" data-name="${esc(name)}">${text}</button>`;

function dkListe(v) {
  const laufen = v.container.filter(c => c.zustand === "running").length;
  const jobs = v.auftraege.length ? karte("Anlegen", v.auftraege.slice().reverse().map(j => `<div class="zeile"><div><b>${esc(j.name)}</b>${j.fehler ? `<small>${esc(j.fehler)}</small>` : ""}</div>${abzeichen(j.text, !j.fehler)}</div>`).join("")) : "";
  const zeilen = v.container.map(c => {
    const aktionen = c.verwaltet ? (c.zustand === "running" ? dkKnopf("stop", c.name, "Stopp") + dkKnopf("restart", c.name, "Neustart") : dkKnopf("start", c.name, "Start")) + dkKnopf("logs", c.name, "Logs") + dkKnopf("loeschen", c.name, "Löschen")
      : dkKnopf("logs", c.name, "Logs");
    return `<tr><td><b>${esc(c.name)}</b>${c.verwaltet ? "" : '<br><span class="leise">fremd, nur lesen</span>'}</td><td class="mono">${esc(c.image)}</td><td>${abzeichen(c.zustand, c.zustand === "running")}<br><span class="leise">${esc(c.status)}</span></td>` +
      `<td>${dkPorts(c)}${c.verwaltet && c.extern ? `<br>${abzeichen("offen nach außen", false)}` : ""}</td><td data-last="${esc(c.name)}" class="leise">…</td><td class="dkaktion">${aktionen}</td></tr>`;
  }).join("");
  const tabelle = karte("Container", v.container.length ? `<div class="tabelle"><table><thead><tr><th>Name</th><th>Image</th><th>Zustand</th><th>Ports (Host)</th><th>Last</th><th></th></tr></thead><tbody>${zeilen}</tbody></table></div>`
    : leer("Noch kein Container", "Lege unten einen an, zum Beispiel aus einer Vorlage."));
  const images = karte("Images", (v.images.length ? `<div class="tabelle"><table><thead><tr><th>Image</th><th class="r">Größe</th><th>Benutzt</th></tr></thead><tbody>${v.images.map(i =>
    `<tr><td class="mono">${esc(i.tags.join(", ") || i.id)}</td><td class="r">${dkGroesse(i.groesse)}</td><td>${abzeichen(i.benutzt ? "ja" : "nein", i.benutzt)}</td></tr>`).join("")}</tbody></table></div>` : leer("Keine Images", "")) +
    `<button class="tab" data-dk="aufraeumen">Ungenutzte Images entfernen</button>`);
  return `<div class="raster kpis">${stat(laufen, "Laufen", `von ${v.container.length} Containern`)}${stat(v.images.length, "Images", `Docker ${esc(v.version)}`)}</div>${jobs}${tabelle}<div id="dk-logs"></div>${images}`;
}
const dkPortZeile = (p = {}) => `<div class="dkzeile"><input class="nfeld" data-f="host" placeholder="Host-Port" inputmode="numeric" value="${esc(p.host || "")}"><input class="nfeld" data-f="container" placeholder="Container-Port" inputmode="numeric" value="${esc(p.container || "")}">` +
  `<select class="auswahl" data-f="proto"><option${p.proto === "udp" ? "" : " selected"}>tcp</option><option${p.proto === "udp" ? " selected" : ""}>udp</option></select><label><input type="checkbox" data-f="extern"> nach außen öffnen</label><button class="tab" type="button" data-dk="zeile-weg">×</button></div>`;
const dkOrdnerZeile = (o = {}) => `<div class="dkzeile"><input class="nfeld" data-f="sub" placeholder="Ordner (z. B. data)" value="${esc(o.sub || "")}"><input class="nfeld" data-f="pfad" placeholder="Pfad im Container" value="${esc(o.pfad || "")}"><button class="tab" type="button" data-dk="zeile-weg">×</button></div>`;
const dkVarZeile = (e = {}) => `<div class="dkzeile"><input class="nfeld" data-f="k" placeholder="NAME" autocomplete="off" value="${esc(e.k || "")}"><input class="nfeld" data-f="v" placeholder="Wert" autocomplete="off" value="${esc(e.v || "")}"><button class="tab" type="button" data-dk="zeile-weg">×</button></div>`;

function dkFormular(v) {
  return karte("Container anlegen", `<p class="kontext">Ports sind standardmäßig nur auf dem Server selbst erreichbar (127.0.0.1). „Nach außen öffnen“ macht den Port für alle im Internet sichtbar, denn die Firewall greift bei Docker nicht. Daten liegen unter <span class="mono">${esc(v.ordner)}/&lt;Name&gt;/</span>.</p>` +
    `<div class="fwform"><label>Vorlage<select class="auswahl" id="dk-vorlage"><option value="">– keine –</option>${v.vorlagen.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("")}</select></label>` +
    `<label>Name<input class="nfeld" id="dk-name" placeholder="meine-app" autocomplete="off" maxlength="40"></label><label>Image<input class="nfeld" id="dk-image" placeholder="nginx:alpine" autocomplete="off"></label>` +
    `<label>Neustart<select class="auswahl" id="dk-restart"><option value="unless-stopped">immer, außer nach Stopp</option><option value="always">immer</option><option value="on-failure">bei Fehler</option><option value="no">nie</option></select></label>` +
    `<label>RAM-Grenze (MB, optional)<input class="nfeld" id="dk-mem" inputmode="numeric"></label><label>CPU-Grenze (Kerne, optional)<input class="nfeld" id="dk-cpus" inputmode="decimal" placeholder="0.5"></label></div>` +
    `<p class="dkabschnitt">Ports</p><div id="dk-ports"></div><button class="tab" type="button" data-dk="port-neu">+ Port</button>` +
    `<p class="dkabschnitt">Ordner</p><div id="dk-vols"></div><button class="tab" type="button" data-dk="ordner-neu">+ Ordner</button>` +
    `<p class="dkabschnitt">Umgebungsvariablen</p><div id="dk-env"></div><button class="tab" type="button" data-dk="var-neu">+ Variable</button>` +
    `<p style="margin-top:16px"><button class="tab aktiv" data-dk="neu">Container anlegen</button></p>`);
}
function dkSeite(v) {
  if (!v.verfuegbar) return leer("Docker läuft nicht", 'Auf dem Server prüfen: <span class="mono">systemctl status docker</span>');
  dkVorlagen = v.vorlagen;
  return hinweisKarte("Das Dashboard bedient nur Container, die es selbst angelegt hat. Alles Ändernde verlangt Passwort und Code erneut (5 Minuten) und steht im Audit.") + `<div id="dk-liste">${dkListe(v)}</div>${dkFormular(v)}`;
}

async function dkLast() {
  if (location.hash !== "#server/docker") return;
  const werte = await api("/api/docker/stats");
  document.querySelectorAll("[data-last]").forEach(td => {
    const w = werte[td.dataset.last];
    td.textContent = w ? `${w.cpu} % CPU · ${dkGroesse(w.ram)}${w.ram_max && w.ram_max < 1e12 ? ` von ${dkGroesse(w.ram_max)}` : ""}` : "–";
  });
}
async function dkNeuZeichnen() {
  if (location.hash !== "#server/docker") return;
  const v = await api("/api/docker"), liste = document.getElementById("dk-liste");
  if (liste && v.verfuegbar) { liste.innerHTML = dkListe(v); dkLast(); }
  clearTimeout(dkTimer);
  if (v.verfuegbar && v.auftraege.some(j => !j.fertig)) dkTimer = setTimeout(dkNeuZeichnen, DK_TAKT_MS);
}
SEITEN["server/docker"] = ["Server", "Docker", ({ x }) => { setTimeout(() => { dkLast(); dkNeuZeichnen(); }, 0); return dkSeite(x[0]); }, ["/api/docker"]];

const dkFeldWerte = (behaelter, felder) => [...document.querySelectorAll(`#${behaelter} .dkzeile`)].map(z => Object.fromEntries(felder.map(f => {
  const el = z.querySelector(`[data-f="${f}"]`);
  return [f, el.type === "checkbox" ? el.checked : el.value.trim()];
}))).filter(o => Object.values(o).some(w => w && w !== "tcp"));  // leere Zeilen weglassen
const dkFormularWerte = () => ({
  name: document.getElementById("dk-name").value.trim(), image: document.getElementById("dk-image").value.trim(), restart: document.getElementById("dk-restart").value,
  mem_mb: document.getElementById("dk-mem").value.trim(), cpus: document.getElementById("dk-cpus").value.trim(),
  ports: dkFeldWerte("dk-ports", ["host", "container", "proto", "extern"]), volumes: dkFeldWerte("dk-vols", ["sub", "pfad"]), env: dkFeldWerte("dk-env", ["k", "v"]),
});
const dkVorschau = b => [`Name: ${b.name}`, `Image: ${b.image}`, `Neustart: ${b.restart}`, ...b.ports.map(p => `Port ${p.extern ? "0.0.0.0 (OFFEN FÜR ALLE)" : "127.0.0.1"}:${p.host} → ${p.container}/${p.proto}`),
  ...b.volumes.map(o => `Ordner ${o.sub} → ${o.pfad}`), ...(b.env.length ? [`${b.env.length} Umgebungsvariablen (Werte werden nicht angezeigt)`] : [])].join("\n");
function dkVorlageLaden(id) {
  const t = dkVorlagen.find(x => x.id === id);
  if (!t) return;
  document.getElementById("dk-name").value = t.id; document.getElementById("dk-image").value = t.image;
  document.getElementById("dk-ports").innerHTML = (t.ports || []).map(dkPortZeile).join(""); document.getElementById("dk-vols").innerHTML = (t.volumes || []).map(dkOrdnerZeile).join("");
  document.getElementById("dk-env").innerHTML = (t.env || []).map(dkVarZeile).join("");
}
document.addEventListener("change", e => { if (e.target.id === "dk-vorlage") dkVorlageLaden(e.target.value); });

async function dkLogs(name) {
  const ziel = document.getElementById("dk-logs");
  ziel.innerHTML = karte(`Logs: ${esc(name)}`, `<pre class="terminal" style="max-height:50vh;overflow:auto">Lädt …</pre>`);
  const r = await api(`/api/docker/logs?name=${encodeURIComponent(name)}&n=300`);
  ziel.querySelector("pre").textContent = r.text || "(keine Ausgabe)";
  ziel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
async function dkAktion(knopf) {
  const d = knopf.dataset, name = d.name;
  if (d.dk === "zeile-weg") { knopf.closest(".dkzeile").remove(); return; }
  const zeile = { "port-neu": ["dk-ports", dkPortZeile], "ordner-neu": ["dk-vols", dkOrdnerZeile], "var-neu": ["dk-env", dkVarZeile] }[d.dk];
  if (zeile) { document.getElementById(zeile[0]).insertAdjacentHTML("beforeend", zeile[1]()); return; }
  if (d.dk === "logs") return dkLogs(name);
  let r;
  if (d.dk === "neu") {
    const body = dkFormularWerte();
    r = await kritisch("/api/docker/neu", body, { titel: "Container anlegen", vorschau: dkVorschau(body), erfolg: "Wird angelegt …" });
  } else if (d.dk === "aufraeumen") {
    r = await kritisch("/api/docker/aufraeumen", {}, { titel: "Ungenutzte Images entfernen", vorschau: "Entfernt alle Images, die kein Container braucht.", erfolg: "Aufgeräumt" });
  } else {
    const text = { start: "starten", stop: "stoppen", restart: "neu starten", loeschen: "löschen" }[d.dk];
    r = await kritisch(`/api/docker/${d.dk}`, { name }, { titel: `Container ${name} ${text}`, vorschau: d.dk === "loeschen" ? `Der Container wird gelöscht. Seine Daten unter /srv/deck-apps/${name} bleiben erhalten.` : name });
  }
  if (r) await dkNeuZeichnen();
}
document.addEventListener("click", e => {
  const knopf = e.target.closest("[data-dk]");
  if (knopf) dkAktion(knopf).catch(err => { console.warn("Docker-Aktion fehlgeschlagen:", err); toast("Fehlgeschlagen"); });
});
