// Verwaltung und Betrieb: Agents (tmux bedienen, Teams, Postfach), Einstellungen, MCP, Sicherung, Befehle, Styles, Harnesses, Repositories, Aufgaben, Anbieter, Audit, Orchestrierung.
// Alles, was etwas ausfuehrt oder aendert, geht durch kritisch(): Vorschau, erneutes Passwort mit Code (Sicherheitsstufe) und ein Eintrag im Audit. Laeuft nach features.js.

const gruppeAnlegen = (key, name, symbol, links) => {
  const gruppe = document.createElement("details");
  gruppe.className = "dropdown";
  gruppe.innerHTML = `<summary data-gruppe="${key}"><span class="sym"><svg viewBox="0 0 24 24" aria-hidden="true">${symbol}</svg></span>${name}</summary>` + links.map(([href, label]) => `<a class="menuelink" href="${href}">${label}</a>`).join("");
  document.querySelector('.seitenleiste > a[href="#system"]').before(gruppe);
  gruppe.addEventListener("toggle", () => { if (gruppe.open) document.querySelectorAll(".seitenleiste details").forEach(o => { if (o !== gruppe) o.removeAttribute("open"); }); });
  document.querySelectorAll(".seitenleiste details:not([data-bind])").forEach(d => { d.dataset.bind = "1"; d.addEventListener("toggle", () => { if (d.open) document.querySelectorAll(".seitenleiste details").forEach(o => { if (o !== d) o.removeAttribute("open"); }); }); });
};
GRUPPEN.agents = ["Agents", "#agents/bridge"];
GRUPPEN.betrieb = ["Betrieb", "#betrieb/repos"];
gruppeAnlegen("agents", "Agents", '<circle cx="12" cy="8" r="3.2"/><circle cx="5.5" cy="17" r="2.4"/><circle cx="18.5" cy="17" r="2.4"/><path d="M10 10.8 6.8 15M14 10.8 17.2 15"/>', [["#agents/bridge", "Bridge (tmux)"], ["#agents/teams", "Teams"], ["#agents/post", "Postfach"]]);
gruppeAnlegen("betrieb", "Betrieb", '<path d="M4 18V9M10 18V5M16 18v-7M22 18H2"/>', [["#betrieb/repos", "Repositories"], ["#betrieb/aufgaben", "Aufgaben"], ["#betrieb/anbieter", "Anbieter-Status"], ["#betrieb/audit", "Audit"], ["#betrieb/doku", "Orchestrierung"]]);
for (const [href, label] of [["#konfig/befehle", "Slash-Befehle"], ["#konfig/styles", "Styles & Statusline"], ["#konfig/editor", "Einstellungen bearbeiten"], ["#konfig/mcpverwalten", "MCP-Server verwalten"], ["#konfig/wiederherstellen", "Sicherung zurückspielen"]]) {
  const a = document.createElement("a"); a.className = "menuelink"; a.href = href; a.textContent = label;
  document.querySelector('[data-gruppe="konfig"]').parentElement.appendChild(a);
}


// --- Sicherheitsstufe: Vorschau und erneute Anmeldung vor jeder kritischen Aktion ---------------------
function dialogAbfrage(titel, vorschau, passwortNoetig, codeNoetig) {
  return new Promise(fertig => {
    const rahmen = document.createElement("div");
    rahmen.className = "palette-hintergrund";
    rahmen.innerHTML = `<form class="palette hilfe formular" style="max-width:520px;padding:20px;gap:12px"><p class="label" style="margin:0">${esc(titel)}</p>` + (vorschau ? `<pre class="roh" style="max-height:36vh;padding:10px;border-radius:10px;background:var(--code)">${esc(vorschau)}</pre>` : "") +
      (passwortNoetig ? `<p class="kontext">Zur Sicherheit: Passwort${codeNoetig ? " und Code" : ""} erneut eingeben. Das gilt danach 5 Minuten.</p><input id="sx-pw" type="password" placeholder="Passwort" autocomplete="current-password" required>` + (codeNoetig ? `<input id="sx-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="Code aus der App">` : "") : '<p class="kontext">Sicherheitsstufe aktiv. Bitte nur bestätigen, wenn du das wirklich willst.</p>') +
      `<div style="display:flex;gap:10px;justify-content:flex-end"><button class="tab" type="button" id="sx-abbruch">Abbrechen</button><button class="tab aktiv" type="submit">Ausführen</button></div></form>`;
    document.body.appendChild(rahmen);
    const feld = rahmen.querySelector("input"); if (feld) feld.focus();
    const zu = wert => { rahmen.remove(); fertig(wert); };
    rahmen.querySelector("#sx-abbruch").onclick = () => zu(null);
    rahmen.onclick = e => { if (e.target === rahmen) zu(null); };
    rahmen.querySelector("form").onsubmit = e => { e.preventDefault(); zu({ password: (rahmen.querySelector("#sx-pw") || {}).value || "", code: (rahmen.querySelector("#sx-code") || {}).value || "" }); };
  });
}
async function kritisch(url, body, { titel, vorschau = "", erfolg = "Erledigt" }) {
  const status = await api("/api/stepup/status");
  const eingabe = await dialogAbfrage(titel, vorschau, !status.elevated, status.totp);
  if (!eingabe) return null;
  if (!status.elevated) {
    const stufe = await postJson("/api/stepup", eingabe);
    if (!stufe.ok) { toast(stufe.error || "Anmeldung abgelehnt"); return null; }
  }
  const r = await postJson(url, body);
  if (r.error === "stepup") { toast("Die Sicherheitsstufe ist abgelaufen. Bitte noch einmal."); return null; }
  toast(r.ok ? erfolg : r.error || "Fehlgeschlagen");
  return r.ok ? r : null;
}
const diffZeilen = (alt, neu) => {   // einfacher Zeilenvergleich (laengste gemeinsame Teilfolge)
  const a = alt.split("\n"), b = neu.split("\n"), l = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) l[i][j] = a[i] === b[j] ? l[i + 1][j + 1] + 1 : Math.max(l[i + 1][j], l[i][j + 1]);
  const aus = []; let i = 0, j = 0;
  while (i < a.length && j < b.length) { if (a[i] === b[j]) { i++; j++; } else if (l[i + 1][j] >= l[i][j + 1]) aus.push("- " + a[i++]); else aus.push("+ " + b[j++]); }
  while (i < a.length) aus.push("- " + a[i++]);
  while (j < b.length) aus.push("+ " + b[j++]);
  return aus.join("\n") || "(keine Änderung)";
};

const abzeichen = (text, ok) => `<span class="${ok ? "dienst-ok" : "dienst-aus"}">${esc(text)}</span>`;
const filterFeld = (id, hinweis) => `<div class="suche suche-gross"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg><input id="${id}" placeholder="${hinweis}" autocomplete="off"></div>`;
const hinweisKarte = text => `<p class="kontext" style="margin:0 0 14px">${text}</p>`;

// --- Bridge: tmux-Sessions ansehen und bedienen --------------------------------------------------------
let bridgeName = "";
const TASTEN = [["Enter", "Enter"], ["Esc", "Escape"], ["Strg+C", "C-c"], ["Strg+D", "C-d"], ["Tab", "Tab"], ["↑", "Up"], ["↓", "Down"], ["j", "y"], ["n", "n"]];
SEITEN["agents/bridge"] = ["Agents", "Bridge (tmux)", ({ x }) => {
  const [sessions, agents] = x;
  return hinweisKarte("Der Terminaltext wird alle 2 Sekunden neu gelesen. Eingaben, Starten und Beenden verlangen Passwort und Code erneut (5 Minuten) und stehen im Audit.") + (sessions.length ?
    `<div class="raster zwei"><div class="karte">${sessions.map(s => `<div class="zeile bridge-zeile${s.name === bridgeName ? " aktiv" : ""}" data-name="${esc(s.name)}" tabindex="0" role="button"><div><b>${esc(s.name)}</b><small>${esc(s.command)} · ${esc(s.path)} · ${s.windows} Fenster</small></div>${s.attached ? abzeichen("verbunden", true) : '<span class="leise">frei</span>'}</div>`).join("")}</div>` +
    `<div class="karte"><p class="label" id="bridge-titel">Terminal</p><pre class="terminal" id="bridge-text">Wähle links eine Session.</pre>` +
    `<form id="bridge-senden" class="bridge-eingabe"><input id="bridge-eingabe" placeholder="Text an die Session senden" autocomplete="off" maxlength="2000"><button class="tab aktiv" type="submit">Senden</button></form>` +
    `<div class="chips" id="bridge-tasten">${TASTEN.map(([l, k]) => `<span class="nachbar" data-taste="${k}">${l}</span>`).join("")}<span class="nachbar" id="bridge-beenden">Session beenden</span></div></div></div>`
    : leer("Keine tmux-Session", "Starte unten eine neue oder öffne auf dem VPS <span class=\"mono\">tmux new -s arbeit</span> und darin <span class=\"mono\">claude</span>.")) +
    karte("Neue Claude-Session starten", `<form id="bridge-start" class="formular"><label class="feld">Name (wird zu deck-…)<input id="bs-name" required maxlength="40" pattern="[A-Za-z0-9_-]+"></label><label class="feld">Ordner<input id="bs-cwd" value="/root"></label>` +
      `<label class="feld">Agent (optional)<select id="bs-agent" class="auswahl"><option value="">– keiner –</option>${agents.map(a => `<option>${esc(a)}</option>`).join("")}</select></label><label class="feld">Auftrag (optional)<input id="bs-prompt" maxlength="4000"></label><button class="tab aktiv" type="submit">Starten</button></form>`);
}, ["/api/tmux", "/api/agents-list"]];
SEITEN_HAKEN["agents/bridge"] = () => {
  const start = document.getElementById("bridge-start");
  if (!start) return;
  start.onsubmit = async e => {
    e.preventDefault();
    const daten = { name: document.getElementById("bs-name").value, cwd: document.getElementById("bs-cwd").value, agent: document.getElementById("bs-agent").value, prompt: document.getElementById("bs-prompt").value };
    const r = await kritisch("/api/tmux/start", daten, { titel: "Neue Claude-Session starten", vorschau: `tmux new-session -s deck-${daten.name} -c ${daten.cwd}\nclaude${daten.agent ? " --agent " + daten.agent : ""}${daten.prompt ? "\nAuftrag: " + daten.prompt : ""}`, erfolg: "Session gestartet" });
    if (r) { bridgeName = r.name; setTimeout(zeige, 600); }
  };
  const zeilen = document.querySelectorAll(".bridge-zeile");
  if (!zeilen.length) return;
  const laden = async () => {
    if (!bridgeName) return;
    try {
      const r = await api("/api/tmux/pane?name=" + encodeURIComponent(bridgeName) + "&lines=300"), el = document.getElementById("bridge-text");
      if (!el) return;
      const unten = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
      document.getElementById("bridge-titel").textContent = "Terminal · " + bridgeName;
      el.textContent = r.text.replace(/\s+$/, "") || "(leer)";
      if (unten) el.scrollTop = el.scrollHeight;
    } catch (e) { console.warn("Terminal:", e); }
  };
  const senden = async (daten, titel, vorschau) => { const r = await kritisch("/api/tmux/send", { name: bridgeName, ...daten }, { titel, vorschau, erfolg: "Gesendet" }); if (r) laden(); return r; };
  const waehlen = el => { bridgeName = el.dataset.name; zeilen.forEach(z => z.classList.toggle("aktiv", z === el)); laden(); };
  zeilen.forEach(z => { z.onclick = () => waehlen(z); z.onkeydown = e => { if (e.key === "Enter") waehlen(z); }; });
  if (!bridgeName || ![...zeilen].some(z => z.dataset.name === bridgeName)) bridgeName = zeilen[0].dataset.name;
  zeilen.forEach(z => z.classList.toggle("aktiv", z.dataset.name === bridgeName));
  document.getElementById("bridge-senden").onsubmit = async e => {
    e.preventDefault();
    const feld = document.getElementById("bridge-eingabe"), text = feld.value;
    if (text && await senden({ text, key: "Enter" }, `Eingabe an ${bridgeName}`, `${text}\n⏎ Enter`)) feld.value = "";
  };
  document.querySelectorAll("[data-taste]").forEach(b => { b.onclick = () => senden({ key: b.dataset.taste }, `Taste an ${bridgeName}`, `Taste: ${b.textContent}`); });
  document.getElementById("bridge-beenden").onclick = async () => {
    if (!bridgeName.startsWith("deck-")) { toast("Beendet werden nur Sessions, die das Dashboard gestartet hat (deck-…)."); return; }
    if (await kritisch("/api/tmux/kill", { name: bridgeName }, { titel: `Session ${bridgeName} beenden`, vorschau: `tmux kill-session -t ${bridgeName}`, erfolg: "Session beendet" })) { bridgeName = ""; setTimeout(zeige, 600); }
  };
  clearInterval(chatTimer);
  chatTimer = setInterval(laden, 2000);
  laden();
};

// --- Teams: Aufstellungen speichern, Plan ansehen, starten -------------------------------------------
SEITEN["agents/teams"] = ["Agents", "Teams", ({ x }) => {
  const [teams, agents] = x;
  return hinweisKarte("Speichert Aufstellungen. „Plan“ zeigt die Befehle, „Starten“ legt für jedes Mitglied eine tmux-Session an (Leiter zuerst, nur er bekommt den Auftrag). Starten verlangt die Sicherheitsstufe.") + `<div class="raster zwei">` +
    karte("Gespeicherte Teams", Object.keys(teams).length ? Object.entries(teams).map(([name, t]) => `<div class="zeile"><div><b>${esc(name)}</b><small>Leiter ${esc(t.leader)} · ${t.members.length} Agents · ${esc(t.cwd)}</small></div><div><button class="tab" data-plan="${esc(name)}" type="button">Plan</button> <button class="tab aktiv" data-start="${esc(name)}" type="button">Starten</button> <button class="tab" data-weg="${esc(name)}" type="button">Löschen</button></div></div>`).join("") : leer("Noch kein Team", "Lege rechts eins an."))
    + karte("Neues Team", `<form id="team-form" class="formular"><label class="feld">Name<input id="tm-name" required maxlength="40" pattern="[A-Za-z0-9_-]+"></label><label class="feld">Agents (Mehrfachauswahl, bis 8)<select id="tm-members" class="auswahl" multiple size="6">${agents.map(a => `<option>${esc(a)}</option>`).join("")}</select></label>` +
      `<label class="feld">Leiter<select id="tm-leader" class="auswahl">${agents.map(a => `<option>${esc(a)}</option>`).join("")}</select></label><label class="feld">Ordner<input id="tm-cwd" value="/root"></label><label class="feld">Auftrag für den Leiter (optional)<input id="tm-prompt"></label><button class="tab aktiv" type="submit">Speichern</button></form>`)
    + `</div><div id="team-plan"></div>`;
}, ["/api/teams", "/api/agents-list"]];
SEITEN_HAKEN["agents/teams"] = () => {
  const form = document.getElementById("team-form");
  if (!form) return;
  form.onsubmit = async e => {
    e.preventDefault();
    const members = [...document.getElementById("tm-members").selectedOptions].map(o => o.value);
    const r = await postJson("/api/team/save", { name: document.getElementById("tm-name").value, leader: document.getElementById("tm-leader").value, members, cwd: document.getElementById("tm-cwd").value, prompt: document.getElementById("tm-prompt").value });
    toast(r.ok ? "Team gespeichert" : r.error); if (r.ok) zeige();
  };
  const planHolen = name => api("/api/team-plan?name=" + encodeURIComponent(name));
  document.querySelectorAll("[data-plan]").forEach(b => { b.onclick = async () => {
    const p = await planHolen(b.dataset.plan);
    document.getElementById("team-plan").innerHTML = karte(`Startplan · ${esc(p.team)} in ${esc(p.cwd)}`, `<table><thead><tr><th>Reihenfolge</th><th>tmux-Session</th><th>Befehl</th></tr></thead><tbody>${p.steps.map((s, i) => `<tr><td>${i + 1}${s.leader ? " · Leiter" : ""}</td><td class="mono">${esc(s.session)}</td><td class="mono">${esc(s.command)}</td></tr>`).join("")}</tbody></table><button class="tab" id="plan-kopieren" type="button">Alle Befehle kopieren</button>`);
    document.getElementById("plan-kopieren").onclick = () => kopieren(p.steps.map(s => `tmux new -d -s ${s.session} -c ${p.cwd} ${s.command}`).join("\n"), "Befehle kopiert");
  }; });
  document.querySelectorAll("[data-start]").forEach(b => { b.onclick = async () => {
    const p = await planHolen(b.dataset.start);
    if (await kritisch("/api/team/start", { name: b.dataset.start }, { titel: `Team ${b.dataset.start} starten`, vorschau: p.steps.map((s, i) => `${i + 1}. ${s.session}: ${s.command}`).join("\n") + `\nOrdner: ${p.cwd}`, erfolg: "Team gestartet" })) location.hash = "agents/bridge";
  }; });
  document.querySelectorAll("[data-weg]").forEach(b => { b.onclick = async () => { if (!confirm(`Team „${b.dataset.weg}“ löschen?`)) return; await postJson("/api/team/delete", { name: b.dataset.weg }); toast("Team gelöscht"); zeige(); }; });
};

// --- Postfach: Nachrichten und Uebergaben ----------------------------------------------------------------
SEITEN["agents/post"] = ["Agents", "Postfach", ({ x }) => {
  const [mail, tmux] = x;
  return hinweisKarte("Ein Postfach für Übergaben und Kontextanfragen. „Zustellen“ tippt die Nachricht samt Enter in die tmux-Session des Empfängers (Sicherheitsstufe).") + `<div class="raster zwei">` +
    karte(`${mail.length} Nachrichten`, mail.length ? mail.slice().reverse().map(m => `<div class="zeile post-zeile"><div><b>${esc(m.subject || "(ohne Betreff)")}</b> ${m.status === "neu" ? abzeichen("neu", true) : `<span class="leise">${esc(m.status)}</span>`}<small>${esc(m.from)} → ${esc(m.to || "alle")} · ${new Date(m.t * 1000).toLocaleString("de-DE")}</small><p style="margin:6px 0 0;white-space:pre-wrap;overflow-wrap:anywhere">${esc(m.body)}</p></div>` +
      `<div><button class="tab" data-kopie="${m.id}" type="button">Kopieren</button> ${m.to ? `<button class="tab aktiv" data-zustellen="${m.id}" type="button">Zustellen</button>` : ""} ${m.status === "neu" ? `<button class="tab" data-gelesen="${m.id}" type="button">Gelesen</button>` : ""} <button class="tab" data-weg="${m.id}" type="button">Löschen</button></div></div>`).join("") : leer("Keine Nachrichten", "Schreibe rechts die erste."))
    + karte("Neue Nachricht", `<form id="post-form" class="formular"><label class="feld">An (Agent oder Session)<input id="po-an" list="po-liste" placeholder="z. B. deck-agent-a"><datalist id="po-liste">${tmux.map(s => `<option>${esc(s.name)}</option>`).join("")}</datalist></label>` +
      `<label class="feld">Betreff<input id="po-betreff" maxlength="120"></label><label class="feld">Nachricht<textarea id="po-text" class="auswahl" rows="5" required maxlength="4000"></textarea></label><button class="tab aktiv" type="submit">Ablegen</button></form>`) + "</div>";
}, ["/api/mail", "/api/tmux"]];
SEITEN_HAKEN["agents/post"] = () => {
  const form = document.getElementById("post-form");
  if (!form) return;
  const mail = seitenDaten["agents/post"] ? seitenDaten["agents/post"][0] : [];
  form.onsubmit = async e => { e.preventDefault(); const r = await postJson("/api/mail/send", { to: document.getElementById("po-an").value, subject: document.getElementById("po-betreff").value, body: document.getElementById("po-text").value }); toast(r.ok ? "Abgelegt" : r.error); if (r.ok) zeige(); };
  document.querySelectorAll("[data-gelesen]").forEach(b => { b.onclick = async () => { await postJson("/api/mail/read", { id: +b.dataset.gelesen }); zeige(); }; });
  document.querySelectorAll(".post-zeile [data-weg]").forEach(b => { b.onclick = async () => { await postJson("/api/mail/delete", { id: +b.dataset.weg }); zeige(); }; });
  document.querySelectorAll("[data-kopie]").forEach(b => { b.onclick = () => { const m = mail.find(x => x.id === +b.dataset.kopie); kopieren(m.body, "Nachricht kopiert"); }; });
  document.querySelectorAll("[data-zustellen]").forEach(b => { b.onclick = async () => {
    const m = mail.find(x => x.id === +b.dataset.zustellen);
    if (await kritisch("/api/mail/deliver", { id: m.id }, { titel: `In ${m.to} tippen`, vorschau: `${m.body}\n⏎ Enter`, erfolg: "Zugestellt" })) zeige();
  }; });
};

// --- Einstellungen, Hooks und Rechte bearbeiten ------------------------------------------------------------
SEITEN["konfig/editor"] = ["Konfiguration", "Einstellungen bearbeiten", ({ x }) => {
  const e = JSON.parse(x.text), rechte = e.permissions || {};
  const liste = art => (rechte[art] || []).join("\n");
  return hinweisKarte("Bearbeitet <span class=\"mono\">~/.claude/settings.json</span>. Vor dem Speichern siehst du die Änderungen, die alte Datei wird gesichert (letzte 20), und es ist die Sicherheitsstufe nötig. Claude Code liest die Datei beim nächsten Start neu.") + `<div class="raster zwei">` +
    karte("Berechtigungen (eine Regel pro Zeile)", `<label class="feld">Erlaubt (allow)<textarea id="ed-allow" class="auswahl mono" rows="6">${esc(liste("allow"))}</textarea></label><label class="feld">Nachfragen (ask)<textarea id="ed-ask" class="auswahl mono" rows="3">${esc(liste("ask"))}</textarea></label><label class="feld">Verboten (deny)<textarea id="ed-deny" class="auswahl mono" rows="3">${esc(liste("deny"))}</textarea></label><button class="tab" id="ed-rechte" type="button">In den Text übernehmen</button>`) +
    karte("Hooks (JSON)", `<textarea id="ed-hooks" class="auswahl mono" rows="12">${esc(JSON.stringify(e.hooks || {}, null, 2))}</textarea><button class="tab" id="ed-hooks-ok" type="button">In den Text übernehmen</button>`) + "</div>" +
    karte("Gesamte settings.json", `<textarea id="ed-text" class="auswahl mono" rows="22" spellcheck="false">${esc(JSON.stringify(e, null, 2))}</textarea><p class="kontext" id="ed-meldung"></p><button class="tab aktiv" id="ed-speichern" type="button">Prüfen und speichern</button>`);
}, "/api/settings-raw"];
SEITEN_HAKEN["konfig/editor"] = () => {
  const text = document.getElementById("ed-text");
  if (!text) return;
  const original = JSON.stringify(JSON.parse(seitenDaten["konfig/editor"].text), null, 2), meldung = (s, ok) => { const el = document.getElementById("ed-meldung"); el.textContent = s; el.style.color = ok ? "var(--ok)" : "var(--leer)"; };
  const aendern = fn => { try { const o = JSON.parse(text.value); fn(o); text.value = JSON.stringify(o, null, 2); meldung("Übernommen. Noch nicht gespeichert.", true); } catch (e) { meldung("Der Text ist kein gültiges JSON: " + e.message, false); } };
  const zeilen = id => document.getElementById(id).value.split("\n").map(z => z.trim()).filter(Boolean);
  document.getElementById("ed-rechte").onclick = () => aendern(o => { o.permissions = { ...(o.permissions || {}) }; for (const [art, id] of [["allow", "ed-allow"], ["ask", "ed-ask"], ["deny", "ed-deny"]]) { const z = zeilen(id); if (z.length) o.permissions[art] = z; else delete o.permissions[art]; } if (!Object.keys(o.permissions).length) delete o.permissions; });
  document.getElementById("ed-hooks-ok").onclick = () => { let h; try { h = JSON.parse(document.getElementById("ed-hooks").value || "{}"); } catch (e) { meldung("Hooks sind kein gültiges JSON: " + e.message, false); return; } aendern(o => { if (Object.keys(h).length) o.hooks = h; else delete o.hooks; }); };
  document.getElementById("ed-speichern").onclick = async () => {
    let neu; try { neu = JSON.stringify(JSON.parse(text.value), null, 2); } catch (e) { meldung("Kein gültiges JSON: " + e.message, false); return; }
    if (neu === original) { meldung("Keine Änderung.", true); return; }
    const r = await kritisch("/api/settings", { text: neu }, { titel: "settings.json speichern", vorschau: diffZeilen(original, neu), erfolg: "Gespeichert" });
    if (r) { meldung("Gespeichert. Sicherung: " + r.backup, true); }
  };
};

SEITEN["konfig/editor"][2] = mitDaten("konfig/editor", SEITEN["konfig/editor"][2]);

// --- MCP-Server verwalten ----------------------------------------------------------------------------------
SEITEN["konfig/mcpverwalten"] = ["Konfiguration", "MCP-Server verwalten", ({ x }) => hinweisKarte("Verwaltet MCP-Server im Benutzerbereich (<span class=\"mono\">claude mcp</span>). Hinzufügen und Entfernen verlangen die Sicherheitsstufe. Ein stdio-Server führt beim Start seinen Befehl aus.") + `<div class="raster zwei">` +
  karte(`${x.servers.length} Server (Gesundheitsprüfung gerade eben)`, x.servers.length ? x.servers.map(s => `<div class="zeile"><div><b>${esc(s.name)}</b><small class="mono">${esc(s.target)}</small></div><div>${abzeichen(s.status, /Connected|✔/.test(s.status))} ${/^[A-Za-z0-9_-]{1,40}$/.test(s.name) ? `<button class="tab" data-mcp-weg="${esc(s.name)}" type="button">Entfernen</button>` : ""}</div></div>`).join("") : leer("Keine MCP-Server", "Füge rechts einen hinzu."))
  + karte("Server hinzufügen", `<form id="mcp-form" class="formular"><label class="feld">Name<input id="mc-name" required maxlength="40" pattern="[A-Za-z0-9_-]+"></label><label class="feld">Art<select id="mc-art" class="auswahl"><option value="stdio">Lokaler Befehl (stdio)</option><option value="http">Adresse (http)</option></select></label>` +
    `<label class="feld" id="mc-l1">Befehl<input id="mc-ziel" required placeholder="npx"></label><label class="feld" id="mc-l2">Argumente (durch Leerzeichen getrennt, optional)<input id="mc-args"></label><button class="tab aktiv" type="submit">Hinzufügen</button></form>`) + "</div>", "/api/mcp-list"];
SEITEN_HAKEN["konfig/mcpverwalten"] = () => {
  const form = document.getElementById("mcp-form");
  if (!form) return;
  const art = document.getElementById("mc-art");
  art.onchange = () => { const http = art.value === "http"; document.getElementById("mc-l1").firstChild.textContent = http ? "Adresse" : "Befehl"; document.getElementById("mc-l2").hidden = http; document.getElementById("mc-ziel").placeholder = http ? "https://…/mcp" : "npx"; };
  form.onsubmit = async e => {
    e.preventDefault();
    const ziel = document.getElementById("mc-ziel").value.trim(), args = document.getElementById("mc-args").value.trim().split(/\s+/).filter(Boolean);
    const config = art.value === "http" ? { type: "http", url: ziel } : { type: "stdio", command: ziel, ...(args.length ? { args } : {}) }, name = document.getElementById("mc-name").value;
    if (await kritisch("/api/mcp/add", { name, config }, { titel: `MCP-Server ${name} hinzufügen`, vorschau: JSON.stringify(config, null, 2), erfolg: "Hinzugefügt" })) zeige();
  };
  document.querySelectorAll("[data-mcp-weg]").forEach(b => { b.onclick = async () => { if (await kritisch("/api/mcp/remove", { name: b.dataset.mcpWeg }, { titel: `MCP-Server ${b.dataset.mcpWeg} entfernen`, vorschau: `claude mcp remove ${b.dataset.mcpWeg} --scope user`, erfolg: "Entfernt" })) zeige(); }; });
};

// --- Sicherung zurueckspielen ------------------------------------------------------------------------------
const TEILE = [["CLAUDE.md", "CLAUDE.md"], ["settings.json", "settings.json"], ["agents", "agents/"], ["skills", "skills/"], ["rules", "rules/"]];
SEITEN["konfig/wiederherstellen"] = ["Konfiguration", "Sicherung zurückspielen", ({ x }) => hinweisKarte("Spielt Teile einer Sicherung nach <span class=\"mono\">~/.claude</span> zurück. Vorher wird der jetzige Stand gesichert. Das Dashboard selbst (<span class=\"mono\">claude-deck/</span>) wird nicht zurückgespielt. Sicherheitsstufe nötig.") +
  (x.length ? karte("Sicherung wählen", `<form id="rs-form" class="formular"><label class="feld">Sicherung<select id="rs-name" class="auswahl">${x.map(b => `<option value="${esc(b.name)}">${esc(b.name)} · ${GROESSE(b.size)}</option>`).join("")}</select></label>` +
    `<div class="feld">Teile${TEILE.map(([k, l]) => `<div class="zeile" style="padding:6px 0"><span>${l}</span><button class="schalter" type="button" role="switch" aria-checked="${k === "settings.json"}" data-teil="${k}" aria-label="${l}"></button></div>`).join("")}</div><button class="tab aktiv" type="submit">Zurückspielen</button></form>`) : leer("Noch keine Sicherung", "Lege unter Konto → Sicherung eine an.")), "/api/backup-list"];
SEITEN_HAKEN["konfig/wiederherstellen"] = () => {
  const form = document.getElementById("rs-form");
  if (!form) return;
  document.querySelectorAll("[data-teil]").forEach(s => { s.onclick = () => s.setAttribute("aria-checked", s.getAttribute("aria-checked") !== "true"); });
  form.onsubmit = async e => {
    e.preventDefault();
    const parts = [...document.querySelectorAll("[data-teil]")].filter(s => s.getAttribute("aria-checked") === "true").map(s => s.dataset.teil), name = document.getElementById("rs-name").value;
    if (!parts.length) { toast("Wähle mindestens einen Teil."); return; }
    const r = await kritisch("/api/restore", { name, parts }, { titel: "Sicherung zurückspielen", vorschau: `Aus: ${name}\nTeile: ${parts.join(", ")}\nVorher wird der jetzige Stand gesichert.`, erfolg: "Zurückgespielt" });
    if (r) toast("Vorab-Sicherung: " + r.safety);
  };
};

// --- Konfiguration: Slash-Befehle, Styles und Statusline, Harnesses --------------------------------
SEITEN["konfig/befehle"] = ["Konfiguration", "Slash-Befehle", ({ x }) => filterFeld("bf-filter", "Befehle filtern") + karte(`${x.length} Befehle aus eigenen Ordnern und Plugins`, `<table id="bf-tabelle"><thead><tr><th>Befehl</th><th>Quelle</th><th>Beschreibung</th></tr></thead><tbody>${x.map(r => `<tr><td class="mono">${esc(r.name)}</td><td class="leise">${esc(r.source)}</td><td class="leise">${esc(r.description)}</td></tr>`).join("")}</tbody></table>`), "/api/commands"];
SEITEN_HAKEN["konfig/befehle"] = () => {
  const feld = document.getElementById("bf-filter");
  if (!feld) return;
  feld.oninput = () => { const q = feld.value.trim().toLowerCase(); document.querySelectorAll("#bf-tabelle tbody tr").forEach(tr => { tr.hidden = q && !tr.textContent.toLowerCase().includes(q); }); };
};
SEITEN["konfig/styles"] = ["Konfiguration", "Styles & Statusline", ({ x }) => `<div class="raster zwei">` + karte(`Output-Styles${x.active ? " · aktiv: " + esc(x.active) : ""}`, x.styles.length ? `<table>${x.styles.map(s => `<tr><td><b>${esc(s.name)}</b> ${s.name === x.active ? abzeichen("aktiv", true) : ""}<br><span class="leise">${esc(s.description)}</span></td><td class="leise">${esc(s.source)}</td></tr>`).join("")}</table>` : leer("Keine Output-Styles", "Eigene liegen in ~/.claude/output-styles, Plugins bringen welche mit."))
  + karte("Statusline", x.statusline ? `<p class="kontext">${esc(JSON.stringify(x.statusline))}</p>${x.statusline_script ? `<pre class="roh">${esc(x.statusline_script)}</pre>` : ""}` : leer("Keine Statusline eingerichtet", "Sie wird in settings.json unter statusLine festgelegt.")) + "</div>", "/api/styles"];
SEITEN["konfig/harnesses"] = ["Konfiguration", "Harnesses", ({ x }) => karte("Werkzeuge und Bereitschaft", `<table><thead><tr><th>Werkzeug</th><th>Status</th><th>Version</th><th>Pfad</th></tr></thead><tbody>${x.map(r => `<tr><td>${esc(r.name)}</td><td>${abzeichen(r.path ? "bereit" : "nicht installiert", !!r.path)}</td><td class="leise">${esc(r.version)}</td><td class="mono leise">${esc(r.path || "")}</td></tr>`).join("")}</tbody></table>`), "/api/harnesses"];

// --- Betrieb: Repositories, Aufgaben, Anbieter-Status, Audit, Doku ----------------------------------
SEITEN["betrieb/repos"] = ["Betrieb", "Repositories", ({ x }) => x.length ? karte(`${x.length} Repositories unter /root`, `<table><thead><tr><th>Ordner</th><th>Branch</th><th>Änderungen</th><th>Letzter Commit</th></tr></thead><tbody>${x.map(r => { const [hash, titel, wann] = r.last.split("|"); return `<tr><td class="mono">${esc(r.path)}<br><span class="leise">${esc(r.remote)}</span></td><td>${esc(r.branch)} ${r.ahead_behind ? `<span class="leise">(${esc(r.ahead_behind)})</span>` : ""}</td><td>${r.dirty ? abzeichen(r.dirty + " offen", false) : abzeichen("sauber", true)}</td><td><span class="mono">${esc(hash || "")}</span> ${esc(titel || "")}<br><span class="leise">${esc(wann || "")}</span></td></tr>`; }).join("")}</tbody></table>`)
  : leer("Keine Git-Repositories gefunden", "Gesucht wird bis zu drei Ebenen tief unter /root, ohne .claude und node_modules. Lege ein Projekt mit git init an oder klone eins."), "/api/repos"];
SEITEN["betrieb/aufgaben"] = ["Betrieb", "Aufgaben", ({ x }) => !x.available ? leer("GitHub nicht angebunden", esc(x.hint)) : karte(`${x.issues.length} Aufgaben`, x.issues.length ? `<table>${x.issues.map(i => `<tr><td><b>#${i.number}</b> ${esc(i.title)}<br><span class="leise">${esc(i.repo)} · ${i.labels.map(l => esc(l.name)).join(", ")}</span></td><td>${abzeichen(i.state, i.state === "OPEN")}</td></tr>`).join("")}</table>` : leer("Keine offenen Aufgaben", "Für die Repositories mit GitHub-Adresse gibt es nichts.")), "/api/issues"];
const STATUS_TEXT = { operational: "in Ordnung", degraded_performance: "langsam", partial_outage: "teilweise gestört", major_outage: "gestört", under_maintenance: "Wartung" };
SEITEN["betrieb/anbieter"] = ["Betrieb", "Anbieter-Status", ({ x }) => !x.ok ? leer("Statusseite nicht erreichbar", esc(x.error)) : `<div class="raster kpis">${stat(esc(x.description), "Gesamtlage", "status.claude.com")}</div>` +
  `<div class="raster zwei">${karte("Komponenten", `<table>${x.components.map(c => `<tr><td>${esc(c.name)}</td><td class="r">${abzeichen(STATUS_TEXT[c.status] || c.status, c.status === "operational")}</td></tr>`).join("")}</table>`)}` +
  `${karte("Aktuelle Vorfälle", x.incidents.length ? `<table>${x.incidents.map(i => `<tr><td>${esc(i.name)}</td><td class="leise r">${esc(i.status)}</td></tr>`).join("")}</table>` : leer("Keine Vorfälle", "Der Anbieter meldet gerade keine."))}</div>`, "/api/provider"];
SEITEN["betrieb/audit"] = ["Betrieb", "Audit", ({ x }) => karte(`Letzte ${x.length} Aktionen über das Dashboard`, x.length ? `<table><thead><tr><th>Zeit</th><th>Aktion</th><th>Ergebnis</th><th>Details</th><th>IP</th></tr></thead><tbody>${x.map(r => `<tr><td class="leise">${new Date(r.t).toLocaleString("de-DE")}</td><td class="mono">${esc(r.action)}</td><td>${abzeichen(String(r.status), r.status < 300)}</td><td class="leise">${esc(r.detail)}</td><td class="mono leise">${esc(r.ip)}</td></tr>`).join("")}</tbody></table>` : leer("Noch nichts protokolliert", "Hier erscheinen Markieren, Wecker, Sicherungen, Notizen, Teams und Postfach.")), "/api/audit"];
SEITEN["betrieb/doku"] = ["Betrieb", "Orchestrierung", () => `<div class="raster zwei">` + karte("Agents von außen steuern", `<div class="notiz"><p>Das Dashboard bedient tmux für dich. Auf dem VPS selbst geht dasselbe von Hand:</p><pre class="roh">tmux new -d -s arbeit -c /root/projekt claude
tmux attach -t arbeit        # live ansehen (Strg+B, dann D zum Lösen)
tmux send-keys -t arbeit 'Mach X' Enter
tmux kill-session -t arbeit</pre><p>Laufende tmux-Sessions erscheinen unter <b>Agents → Bridge</b>, Teams und Postfach unter <b>Agents</b>.</p></div>`)
  + karte("Schnittstellen", `<div class="notiz"><p>Alle Aufrufe brauchen die Anmeldung (Cookie). Schreibende Aufrufe verlangen zusätzlich den Header <span class="mono">X-Deck: 1</span> und stehen im Audit.</p><table><tbody>${[["GET /api/live", "laufende Sessions"], ["GET /api/usage", "Limits in Prozent"], ["GET /api/stream", "Live-Ereignisse (SSE)"], ["GET /api/tmux", "tmux-Sessions"], ["GET /api/audit", "Aktionsprotokoll"], ["GET /api/shortcut/usage", "nur mit Kurzbefehl-Schlüssel (Bearer)"]].map(([a, b]) => `<tr><td class="mono">${a}</td><td class="leise">${b}</td></tr>`).join("")}</tbody></table></div>`)
  + karte("Sicherheitsstufe", `<div class="notiz"><p>Alles, was etwas ausführt oder ändert (tmux-Eingaben, Sessions und Teams starten, Einstellungen, MCP, Sicherung zurückspielen, Postfach zustellen), verlangt Passwort und Code erneut. Das gilt 5 Minuten, steht im Audit und zeigt vorher eine Vorschau.</p></div>`) + "</div>"];

SEITEN["agents/post"][2] = mitDaten("agents/post", SEITEN["agents/post"][2]);
