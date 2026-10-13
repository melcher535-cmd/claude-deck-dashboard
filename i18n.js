// Sprachumschaltung: Die Texte im Quellcode sind deutsch, die Oberflaeche wird zur Laufzeit per Woerterbuch uebersetzt.
// Eine Sprache ist eine Datei i18n-<code>.js, die window.DECK_SPRACHEN.<code> = { name, locale, exakt } setzt.
// Schluessel in `exakt` sind deutsche Texte, Platzhalter heissen {0}, {1} ... und gelten fuer Zahlen und Namen (werden selbst uebersetzt).
(function (global) {
  const KEY = "deck-sprache";
  const MAX_CACHE = 5000;
  const SKIP = "script,style,textarea,code,pre,.mono,.blase>:not(.wer),[translate=no]";
  const ATTRIBUTE = ["title", "placeholder", "aria-label", "alt"];
  const norm = s => s.replace(/\s+/g, " ").trim();
  const escapen = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function baueUebersetzer(pack) {
    const fest = new Map(), vorlagen = [], cache = new Map();
    for (const [schluessel, ziel] of Object.entries(pack.exakt || {})) {
      const nummern = [...schluessel.matchAll(/\{(\d)\}/g)].map(m => m[1]);
      if (!nummern.length) { fest.set(norm(schluessel), ziel); continue; }
      const teile = norm(schluessel).split(/\{\d\}/);
      vorlagen.push({ re: new RegExp("^" + teile.map(escapen).join("(.+?)") + "$", "s"), nummern, ziel, laenge: teile.join("").length });
    }
    vorlagen.sort((a, b) => b.laenge - a.laenge);

    function suche(kern) {
      if (fest.has(kern)) return fest.get(kern);
      for (const v of vorlagen) {
        const treffer = kern.match(v.re);
        if (treffer) return v.ziel.replace(/\{(\d)\}/g, (_, n) => { const i = v.nummern.indexOf(n); return i < 0 ? "" : uebersetze(treffer[i + 1]); });
      }
      return kern;
    }
    function uebersetze(text) {
      const kern = norm(text);
      if (!kern) return text;
      let neu = cache.get(kern);
      if (neu === undefined) {
        if (cache.size > MAX_CACHE) cache.clear();
        neu = suche(kern);
        cache.set(kern, neu);
      }
      if (neu === kern) return text;
      return text.match(/^\s*/)[0] + neu + text.match(/\s*$/)[0];
    }
    return uebersetze;
  }

  if (typeof document === "undefined") { global.exports = { baueUebersetzer }; return; }

  const sprachen = (global.DECK_SPRACHEN = global.DECK_SPRACHEN || {});
  sprachen.de = { name: "Deutsch", locale: "de-DE" };
  let gespeichert = null;
  try { gespeichert = localStorage.getItem(KEY); } catch (e) { console.warn("Sprache nicht lesbar:", e); }
  const browser = (navigator.language || "").slice(0, 2);
  const code = sprachen[gespeichert] ? gespeichert : sprachen[browser] ? browser : "de";
  const pack = sprachen[code];
  const uebersetze = pack.exakt ? baueUebersetzer(pack) : t => t;
  global.DECK_LOCALE = pack.locale;
  document.documentElement.lang = code;

  global.DECK_I18N = {
    code,
    uebersetze,
    liste: () => Object.entries(sprachen).map(([c, p]) => [c, p.name]).sort((a, b) => a[1].localeCompare(b[1])),
    setzen(neu) {
      try { localStorage.setItem(KEY, neu); } catch (e) { console.warn("Sprache nicht gespeichert:", e); }
      location.reload();
    },
  };
  if (!pack.exakt) return;

  const imUeberspringen = el => el && el.closest(SKIP);
  function text(knoten) {
    if (imUeberspringen(knoten.parentElement)) return;
    const neu = uebersetze(knoten.nodeValue);
    if (neu !== knoten.nodeValue) knoten.nodeValue = neu;
  }
  function attribute(el) {
    for (const a of ATTRIBUTE) {
      const alt = el.getAttribute(a);
      if (!alt) continue;
      const neu = uebersetze(alt);
      if (neu !== alt) el.setAttribute(a, neu);
    }
  }
  function baum(wurzel) {
    if (wurzel.nodeType === 3) return text(wurzel);
    if (wurzel.nodeType !== 1 || imUeberspringen(wurzel)) return;
    attribute(wurzel);
    const filter = { acceptNode: n => n.nodeType === 1 && n.matches(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT };
    const gehe = document.createTreeWalker(wurzel, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, filter);
    for (let n = gehe.nextNode(); n; n = gehe.nextNode()) n.nodeType === 3 ? text(n) : attribute(n);
  }

  for (const name of ["alert", "confirm", "prompt"]) {
    const original = global[name].bind(global);
    global[name] = (meldung, ...rest) => original(uebersetze(String(meldung)), ...rest);
  }
  baum(document.documentElement);
  new MutationObserver(aenderungen => {
    for (const a of aenderungen) {
      if (a.type === "childList") a.addedNodes.forEach(baum);
      else if (a.type === "characterData") text(a.target);
      else attribute(a.target);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRIBUTE });
})(typeof window === "undefined" ? module : window);
