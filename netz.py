"""Netzwerk-Modul des Dashboards: Konfiguration, drei Betriebsarten, Abfrage der Quellen, Geraeteliste, Alarme, Verlauf mit Speicher-Prognose, Agent-Push, Wake-on-LAN.

Betriebsarten (cfg["modus"]):
  lokal      Das Dashboard laeuft im Heimnetz und fragt Geraete direkt ab.
  tailscale  Das Dashboard laeuft woanders (VPS) und erreicht das Heimnetz ueber Tailscale (Subnet-Router). Gleiche Abfragen, andere Einrichtung.
  agent      Ein Agent im Heimnetz fragt ab und schickt die Ergebnisse per HTTPS an das Dashboard (Push). Das Dashboard selbst fragt nichts ab.
Pushes des Agenten werden in jeder Betriebsart angenommen, sobald ein Agent-Token existiert."""
import concurrent.futures
import hashlib
import hmac
import io
import ipaddress
import json
import math
import os
import re
import secrets
import sys
import threading
import time
import zipfile

import netz_dienste  # registriert alle Quellentypen
from netz_http import HEIMNETZE, NetzFehler, ziel_erlaubt
from netz_scan import local_subnets, mac_text, normal_mac, sweep, tailscale_geraete, wol
from netz_typen import GESCHUETZT, TYPEN, abfragen, ergaenzen, katalog
from storage import read_json, write_json

MODI = ("lokal", "tailscale", "agent")
PRIVATE_NETZE = tuple(n for n in HEIMNETZE if n.version == 4 and not n.is_loopback)
ID = re.compile(r"^[a-z0-9-]{1,32}$")
AGENT_NAME = "heimnetz"  # fester Name: jeder Name waere ein eigener Eintrag im Stand
GERAET_KEY = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$|^ip:\d{1,3}(?:\.\d{1,3}){3}$")
IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
MIN_INTERVALL, MAX_INTERVALL = 30, 3600
MAX_QUELLEN, MAX_GERAETE, MAX_NETZE, MAX_TEXT, MAX_NAME, MAX_LISTE = 60, 500, 8, 300, 40, 500
MAX_GESEHEN, GESEHEN_TAGE, HISTORIE_VOLUMES, HISTORIE_NAME = 2000, 30, 40, 60
MIN_PREFIX = 22
TICK_SECONDS = 2
WORKERS = 8
VERLAUF_ABSTAND, VERLAUF_MAX_ZEILEN, VERLAUF_BEHALTEN, VERLAUF_MAX_TAGE, VERLAUF_PUNKTE = 300, 10000, 8000, 30, 600
PROGNOSE_ABSTAND, PROGNOSE_TAGE, PROGNOSE_MIN_PUNKTE, PROGNOSE_MIN_SPANNE = 1800, 7, 12, 12 * 3600
SPERRE_FEHLER, SPERRE_SEKUNDEN = 10, 900
AGENT_AKTIV_SEKUNDEN = 300
MAX_BEFEHLE = 20
AGENT_DATEIEN = ("netz_agent.py", "netz_http.py", "netz_snmp.py", "netz_scan.py", "netz_linux.py", "netz_typen.py", "netz_geraete.py", "netz_dienste.py")
STEPUP = ("config", "token", "token/loeschen", "test", "entdecken")
MELDEN_ENDE = ("quelle", "voll", "platte", "raid", "wan", "usv", "aus", "agent")  # Alarmarten, bei denen auch das Ende gemeldet wird
STANDARD_ALARME = {"neues_geraet": True, "voll_prozent": 90, "temp_platte": 55, "offline_min": 10, "zertifikat_tage": 14, "ausfall_versuche": 3}
STANDARD = {"modus": "lokal", "intervall": 60, "scan_intervall": 300, "netze": [], "quellen": [], "geraete": {}, "alarme": STANDARD_ALARME, "baseline": False, "wol_broadcast": "", "agent_hash": ""}

# Form der Daten, die Uebersicht und Alarme auswerten. "s" Text, "n" Zahl, "b" Wahrheitswert, [x] Liste, {"!k": ...} Objekt (! = Pflichtfeld).
# Alles, was ein Geraet oder der Agent liefert, wird auf diese Form zugeschnitten; Unpassendes faellt weg.
FORM_QUELLE = {
    "modell": "s", "version": "s", "uptime": "n", "cpu": "n", "temp": "n", "kerne": "n", "fehler": "s", "ms": "n", "last": ["n"], "hinweise": ["s"], "werte": [["s"]],
    "ram": {"!gesamt": "n", "!belegt": "n", "prozent": "n"}, "traffic": {"!rx": "n", "!tx": "n"},
    "volumes": [{"!name": "s", "!gesamt": "n", "!belegt": "n", "prozent": "n"}],
    "platten": [{"!name": "s", "modell": "s", "temp": "n", "status": "s", "kapazitaet": "n"}],
    "raid": [{"!name": "s", "!status": "s", "detail": "s"}],
    "wan": {"verbunden": "b", "ip": "s", "rx": "n", "tx": "n", "down_max": "n", "up_max": "n"},
    "clients": [{"mac": "s", "ip": "s", "name": "s", "verbindung": "s", "signal": "n", "netz": "s"}],
    "dienste": [{"!name": "s", "!ok": "b", "detail": "s"}],
    "strom": {"!leistung_w": "n", "ein": "b", "energie_wh": "n"},
    "usv": {"status": "s", "ladung": "n", "laufzeit": "n", "last": "n", "netz": "b"},
    "dns": {"!anfragen": "n", "!blockiert": "n", "prozent": "n", "domains": "n", "aktiv": "b", "clients": "n"},
    "zertifikat": {"!tage": "n", "bis": "s"}, "speedtest": {"!down": "n", "!up": "n", "ping_ms": "n", "t": "n"}, "backup": {"!alter_h": "n", "datei": "s", "ok": "b"},
}
FORM_GERAET = {"!ip": "s", "mac": "s", "name": "s", "hersteller": "s", "ports": ["n"], "typ": "s", "typname": "s"}


def forme(wert, muster):
    """Schneidet wert auf muster zu. Gibt None zurueck, wenn er nicht passt (bei Objekten auch, wenn ein Pflichtfeld fehlt)."""
    if muster == "s":
        return wert[:MAX_TEXT] if isinstance(wert, str) else None
    if muster == "n":  # Ganzzahlen ab 2**53 sind kein Messwert, sondern ein Fehler oder ein Angriff (math.isfinite wuerde sogar OverflowError werfen)
        return wert if isinstance(wert, (int, float)) and not isinstance(wert, bool) and abs(wert) < 2 ** 53 and (isinstance(wert, int) or math.isfinite(wert)) else None
    if muster == "b":
        return wert if isinstance(wert, bool) else None
    if isinstance(muster, list):
        return [x for x in (forme(e, muster[0]) for e in wert[:MAX_LISTE]) if x is not None] if isinstance(wert, list) else None
    if not isinstance(wert, dict):
        return None
    ergebnis = {}
    for schluessel, teil in muster.items():
        name = schluessel.lstrip("!")
        wert_teil = forme(wert[name], teil) if name in wert else None
        if wert_teil is not None:
            ergebnis[name] = wert_teil
        elif schluessel.startswith("!"):
            return None
    return ergebnis


def normalisieren(q):
    """Eine Quelle (eigene Abfrage oder vom Agenten) auf das bekannte Schema bringen. None, wenn Kennung oder Typ fehlen."""
    if not (isinstance(q, dict) and isinstance(q.get("id"), str) and isinstance(q.get("typ"), str)):
        return None
    return ergaenzen({"id": q["id"][:32], "typ": q["typ"][:32], "name": str(q.get("name") or q["id"])[:MAX_NAME], "ok": q.get("ok") is True, **forme(q, FORM_QUELLE)})


def zahl(wert, lo, hi, name):
    try:
        n = int(wert)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name}: Zahl erwartet") from e
    if not lo <= n <= hi:
        raise ValueError(f"{name}: {lo} bis {hi}")
    return n


def feldwert(f, wert, alt, erbe):
    """Ein Feld aus dem Formular. erbe: darf ein maskiertes Geheimnis (********) aus der gespeicherten Quelle uebernommen werden (nur bei gleichem Typ und gleicher Adresse)."""
    art = f["art"]
    if art == "schalter":
        return bool(wert)
    if art == "zahl":
        if wert in ("", None):
            return ""
        return zahl(wert, 1, 65535, f["label"]) if f["key"] == "port" else zahl(wert, 0, 10 ** 9, f["label"])
    if art == "auswahl":
        return wert if wert in f.get("optionen", ()) else f["standard"]
    text = str(wert if wert is not None else "").strip()[:MAX_TEXT]
    if f["geheim"] and text == GESCHUETZT:
        if not erbe:
            raise ValueError(f"{f['label']}: bitte neu eingeben, weil sich Typ oder Adresse geändert haben")
        return alt.get(f["key"], "")
    return text


def bereinige_quelle(raw, alt):
    if not isinstance(raw, dict):
        raise ValueError("ungültige Quelle")
    qid, art = raw.get("id"), raw.get("typ")
    if not (isinstance(qid, str) and ID.fullmatch(qid)):
        raise ValueError("ungültige Kennung")
    if art not in TYPEN:
        raise ValueError(f"unbekannter Typ: {art}")
    quelle = {"id": qid, "typ": art, "name": str(raw.get("name") or TYPEN[art]["name"]).strip()[:MAX_NAME], "aktiv": raw.get("aktiv", True) is not False}
    erbe = bool(alt) and alt.get("typ") == art and alt.get("host", "") == str(raw.get("host", "")).strip()
    for f in TYPEN[art]["felder"]:
        quelle[f["key"]] = feldwert(f, raw.get(f["key"], f["standard"]), alt, erbe)
    if "host" in quelle and not quelle["host"]:
        raise ValueError(f"{quelle['name']}: Adresse fehlt")
    return quelle


def bereinige_netz(text):
    try:
        netz = ipaddress.ip_network(str(text).strip(), strict=False)
    except ValueError as e:
        raise ValueError(f"ungültiges Netz: {text}") from e
    if netz.version != 4 or netz.prefixlen < MIN_PREFIX:
        raise ValueError(f"{netz}: IPv4 mit höchstens 1024 Adressen (/22 oder kleiner)")
    if not any(netz.subnet_of(n) for n in PRIVATE_NETZE):
        raise ValueError(f"{netz}: nur private Netze (10.x, 172.16-31.x, 192.168.x, 100.64-127.x)")
    return str(netz)


def bereinige_geraet(eintrag):
    return {"name": str(eintrag.get("name", "")).strip()[:MAX_NAME], "bekannt": bool(eintrag.get("bekannt")), "wichtig": bool(eintrag.get("wichtig"))}


def geraete_key(mac, ip):
    roh = normal_mac(mac)
    return mac_text(roh) if len(roh) == 12 else f"ip:{ip}"


def ip_sortierung(ip):
    return tuple(int(x) for x in ip.split(".")) if IPV4.fullmatch(ip) else (999,)


def leeres_geraet(key, ip="", mac=""):
    return {"key": key, "ip": ip, "mac": mac, "name": "", "hersteller": "", "ports": [], "typ": "", "typname": "", "verbindung": "", "signal": None}


def trend(punkte):
    """Zuwachs pro Tag (Steigung der Ausgleichsgeraden) aus [(sekunden, wert)]."""
    n = len(punkte)
    mt, mw = sum(t for t, _ in punkte) / n, sum(w for _, w in punkte) / n
    nenner = sum((t - mt) ** 2 for t, _ in punkte)
    return 86400 * sum((t - mt) * (w - mw) for t, w in punkte) / nenner if nenner else 0.0


class Netz:
    def __init__(self, ordner, auth, notify, domain):
        self.cfg_pfad, self.stand_pfad, self.verlauf_pfad = (f"{ordner}/{n}" for n in ("netzwerk.json", "netz-stand.json", "netz-verlauf.jsonl"))
        self.ordner, self.auth, self.notify, self.domain = ordner, auth, notify, domain
        self.lock = threading.RLock()
        self.states, self.ausfaelle, self.befehle, self.sperre, self.prognosen = {}, {}, {}, {}, {}
        self.scan_jetzt, self.letzter_verlauf, self.letzte_prognose = threading.Event(), 0, 0
        self.stand = read_json(self.stand_pfad, {})
        for schluessel, standard in (("snapshots", {}), ("gesehen", {}), ("letzte", {}), ("alarme", {}), ("agent", {})):
            self.stand.setdefault(schluessel, standard)

    # --- Konfiguration ---------------------------------------------------------------
    def config(self):
        gespeichert = read_json(self.cfg_pfad, {})
        return {**STANDARD, **gespeichert, "alarme": {**STANDARD_ALARME, **gespeichert.get("alarme", {})}}

    def config_ansicht(self):
        """Konfiguration fuer die Oberflaeche: Geheimnisse maskiert, Agent-Hash weg."""
        cfg = self.config()
        quellen = []
        for q in cfg["quellen"]:
            geheim = {f["key"] for f in TYPEN[q["typ"]]["felder"] if f["geheim"]} if q["typ"] in TYPEN else set()
            quellen.append({k: (GESCHUETZT if k in geheim and v else v) for k, v in q.items()})
        agent = self.stand["agent"]
        return {**{k: v for k, v in cfg.items() if k != "agent_hash"}, "quellen": quellen, "agent": {"eingerichtet": bool(cfg["agent_hash"]), "name": agent.get("name", ""), "zuletzt": agent.get("zuletzt")},
                "typen": katalog(), "domain": self.domain}

    def speichern(self, data):
        with self.lock:  # lesen, pruefen und schreiben als Einheit, sonst ueberschreibt es gleichzeitige Aenderungen (Agent-Token, Basis)
            return self._speichern(data)

    def _speichern(self, data):
        alt = self.config()
        alte_quellen = {q["id"]: q for q in alt["quellen"]}
        roh_quellen = data.get("quellen", alt["quellen"])
        if not isinstance(roh_quellen, list) or len(roh_quellen) > MAX_QUELLEN:
            raise ValueError(f"höchstens {MAX_QUELLEN} Quellen")
        quellen = [bereinige_quelle(q, alte_quellen.get(q.get("id") if isinstance(q, dict) else None, {})) for q in roh_quellen]
        if len({q["id"] for q in quellen}) != len(quellen):
            raise ValueError("doppelte Kennung")
        modus = data.get("modus", alt["modus"])
        if modus not in MODI:
            raise ValueError("unbekannte Betriebsart")
        netze = data.get("netze", alt["netze"])
        if not isinstance(netze, list) or len(netze) > MAX_NETZE:
            raise ValueError(f"höchstens {MAX_NETZE} Netze")
        geraete = data.get("geraete", alt["geraete"])
        if not isinstance(geraete, dict) or len(geraete) > MAX_GERAETE or not all(GERAET_KEY.fullmatch(k) for k in geraete):
            raise ValueError("ungültige Geräteliste")
        roh_alarme = {**alt["alarme"], **(data.get("alarme") or {})}
        alarme = {"neues_geraet": bool(roh_alarme["neues_geraet"]), "voll_prozent": zahl(roh_alarme["voll_prozent"], 50, 100, "Speicher voll ab (%)"),
                  "temp_platte": zahl(roh_alarme["temp_platte"], 30, 90, "Plattentemperatur"), "offline_min": zahl(roh_alarme["offline_min"], 1, 1440, "Offline nach (Min)"),
                  "zertifikat_tage": zahl(roh_alarme["zertifikat_tage"], 1, 90, "Zertifikat (Tage)"), "ausfall_versuche": zahl(roh_alarme["ausfall_versuche"], 1, 20, "Ausfall nach Versuchen")}
        broadcast = str(data.get("wol_broadcast", alt["wol_broadcast"])).strip()
        if broadcast and not (ipaddress.ip_address(broadcast).version == 4 and ziel_erlaubt(broadcast)):
            raise ValueError("Broadcast-Adresse muss eine private IPv4-Adresse sein")
        neu = {**alt, "modus": modus, "intervall": zahl(data.get("intervall", alt["intervall"]), MIN_INTERVALL, MAX_INTERVALL, "Abfrage-Abstand"),
               "scan_intervall": zahl(data.get("scan_intervall", alt["scan_intervall"]), MIN_INTERVALL, MAX_INTERVALL * 4, "Geräte-Scan-Abstand"),
               "netze": [bereinige_netz(n) for n in netze], "quellen": quellen, "geraete": {k: bereinige_geraet(v) for k, v in geraete.items()}, "alarme": alarme, "wol_broadcast": broadcast}
        with self.lock:
            write_json(self.cfg_pfad, neu)
        return self.config_ansicht()

    # --- Agent ------------------------------------------------------------------------
    def token_neu(self):
        """Neuer Agent-Token (der alte ist damit ungueltig). Gibt Token und fertige Agent-Konfiguration zurueck; gespeichert wird nur der Hash."""
        token = secrets.token_urlsafe(32)
        with self.lock:
            cfg = self.config()
            write_json(self.cfg_pfad, {**cfg, "agent_hash": hashlib.sha256(token.encode()).hexdigest()})
        return {"token": token, "konfig": {"server": f"https://{self.domain}", "token": token, "name": AGENT_NAME, "intervall": cfg["intervall"], "scan_intervall": cfg["scan_intervall"],
                                           "netze": cfg["netze"], "quellen": cfg["quellen"]}}

    def token_loeschen(self):
        with self.lock:
            write_json(self.cfg_pfad, {**self.config(), "agent_hash": ""})
        return {"ok": True}

    def token_pruefen(self, ip, token):
        """Wirft PermissionError bei falschem Token; nach SPERRE_FEHLER Fehlversuchen ist die Adresse SPERRE_SEKUNDEN lang gesperrt."""
        fehler, bis = self.sperre.get(ip, (0, 0))
        if bis > time.time():
            raise PermissionError("gesperrt")
        hash_ = self.config()["agent_hash"]
        if hash_ and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), hash_):
            self.sperre.pop(ip, None)
            return
        self.sperre[ip] = (fehler + 1, time.time() + SPERRE_SEKUNDEN if fehler + 1 >= SPERRE_FEHLER else 0)
        raise PermissionError("token")

    def push(self, ip, token, roh):
        """Nimmt den Stand eines Agenten an und gibt die fuer ihn wartenden Befehle zurueck. PermissionError bei falschem Token, ValueError bei kaputten Daten."""
        self.token_pruefen(ip, token)
        try:
            daten = json.loads(roh)
        except ValueError as e:
            raise ValueError("keine JSON-Daten") from e
        if not isinstance(daten, dict) or daten.get("name", AGENT_NAME) != AGENT_NAME:
            raise ValueError(f"ungültige Daten oder Agent-Name (muss \"{AGENT_NAME}\" sein)")
        name = AGENT_NAME
        if not isinstance(daten.get("quellen", []), list):
            raise ValueError("quellen muss eine Liste sein")
        teil = {"quellen": daten.get("quellen", [])}
        if "geraete" in daten:
            teil.update({"geraete": forme(daten["geraete"], [FORM_GERAET]) or [], "fehler_scan": forme(daten.get("fehler_scan"), ["s"]) or [], "netze": forme(daten.get("netze"), ["s"]) or [], "ts": time.time()})
        with self.lock:
            self.stand["agent"] = {"name": name, "zuletzt": time.time(), "ip": ip}
        self.einlesen(f"agent:{name}", teil)
        with self.lock:
            return {"ok": True, "befehle": self.befehle.pop(f"agent:{name}", [])}

    def agent_origin(self):
        """Herkunftsname des Agenten, solange er sich regelmaessig meldet, sonst None."""
        agent = self.stand["agent"]
        return f"agent:{agent['name']}" if agent.get("zuletzt") and time.time() - agent["zuletzt"] < AGENT_AKTIV_SEKUNDEN else None

    def agent_zip(self):
        puffer = io.BytesIO()
        with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
            for name in AGENT_DATEIEN:
                z.write(os.path.join(self.ordner, name), name)
        return puffer.getvalue()

    # --- Abfragen ---------------------------------------------------------------------
    def quellen_abfragen(self, cfg):
        aktive = [q for q in cfg["quellen"] if q.get("aktiv", True)]
        with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
            return list(pool.map(lambda q: abfragen(q, self.states.setdefault(q["id"], {})), aktive))

    def geraete_scannen(self, cfg):
        netze, gefunden, fehler = cfg["netze"] or local_subnets(), [], []
        for netz in netze:
            try:
                gefunden += sweep(netz)
            except (NetzFehler, ValueError, OSError) as e:
                fehler.append(f"{netz}: {e}")
        tailnet, lage = tailscale_geraete()
        return {"geraete": gefunden, "fehler_scan": fehler, "tailscale": {"geraete": tailnet, **lage}, "ts": time.time(), "netze": netze}

    def einlesen(self, herkunft, teil):
        """Fuehrt einen Teilstand (Quellen und/oder Geraete) in den Stand ein und prueft danach Alarme und Verlauf."""
        jetzt = time.time()
        teil = dict(teil)
        if "quellen" in teil:
            teil["quellen"] = [q for q in (normalisieren(q) for q in teil["quellen"][:MAX_QUELLEN]) if q]
            for q in teil["quellen"]:
                if "clients" in q:
                    q["clients"] = [c for c in q["clients"] if not c.get("ip") or IPV4.fullmatch(c["ip"])]
            teil["tq"] = jetzt
        if "geraete" in teil:
            teil["geraete"] = [g for g in teil["geraete"] if IPV4.fullmatch(g["ip"])]
        with self.lock:
            snap = {**self.stand["snapshots"].get(herkunft, {}), **teil, "t": jetzt}
            self.stand["snapshots"][herkunft] = snap
            self.basis_festlegen(snap)
            bekannt = self.config()["geraete"]
            if "quellen" in teil:  # Zaehler entfernter Quellen verwerfen
                ids = {q["id"] for q in teil["quellen"]}
                self.ausfaelle = {k: v for k, v in self.ausfaelle.items() if k[0] != herkunft or k[1] in ids}
            for g in teil.get("geraete", []):
                self.geraet_gesehen(geraete_key(g.get("mac", ""), g["ip"]), jetzt, g, bekannt)
            for q in teil.get("quellen", []):
                self.ausfaelle[(herkunft, q["id"])] = 0 if q["ok"] else self.ausfaelle.get((herkunft, q["id"]), 0) + 1
                for c in q.get("clients", []):
                    self.geraet_gesehen(geraete_key(c.get("mac", ""), c.get("ip", "")), jetzt, c, bekannt)
            frisch = sorted(((k, t) for k, t in self.stand["gesehen"].items() if t > jetzt - GESEHEN_TAGE * 86400), key=lambda kv: -kv[1])[:MAX_GESEHEN]
            self.stand["gesehen"] = dict(frisch)
            self.stand["letzte"] = {k: v for k, v in self.stand["letzte"].items() if k in bekannt}
            write_json(self.stand_pfad, self.stand)
        ue = self.uebersicht()
        self.alarme_pruefen(ue)
        self.verlauf_schreiben(ue)

    def geraet_gesehen(self, key, jetzt, eintrag, bekannt):
        """Merkt sich, wann ein Geraet zuletzt da war, und bei bekannten Geraeten Name und Adresse fuer die Anzeige im Offline-Fall."""
        self.stand["gesehen"][key] = jetzt
        if key in bekannt:
            alt = self.stand["letzte"].get(key, {})
            self.stand["letzte"][key] = {k: eintrag.get(k) or alt.get(k, "") for k in ("ip", "name", "hersteller")}

    def basis_festlegen(self, snap):
        """Beim ersten Scan gelten alle gefundenen Geraete als bekannt, damit nur spaeter neue einen Alarm ausloesen."""
        cfg = self.config()
        if cfg["baseline"] or not snap.get("geraete"):
            return
        bekannt = {geraete_key(g.get("mac", ""), g["ip"]): {"name": "", "bekannt": True, "wichtig": False} for g in snap["geraete"]}
        write_json(self.cfg_pfad, {**cfg, "baseline": True, "geraete": {**bekannt, **cfg["geraete"]}})

    def schritt(self, letzte):
        cfg, jetzt = self.config(), time.time()
        if cfg["modus"] == "agent":
            return
        if jetzt - letzte["quellen"] >= cfg["intervall"]:
            letzte["quellen"] = jetzt
            self.einlesen("server", {"quellen": self.quellen_abfragen(cfg)})
        if self.scan_jetzt.is_set() or jetzt - letzte["scan"] >= cfg["scan_intervall"]:
            self.scan_jetzt.clear()
            letzte["scan"] = jetzt
            self.einlesen("server", self.geraete_scannen(cfg))

    def schleife(self):
        letzte = {"quellen": 0, "scan": 0}
        while True:
            try:
                self.schritt(letzte)
            except Exception as e:  # der Dienst-Thread darf nie sterben; der Fehler wird protokolliert
                print(f"netz: Durchlauf fehlgeschlagen: {e!r}", file=sys.stderr)
            time.sleep(TICK_SECONDS)

    # --- Uebersicht -------------------------------------------------------------------
    def geraete_liste(self, cfg, stand, jetzt):
        nach_key, nach_ip = {}, {}

        def aufnehmen(ip, mac, **felder):
            d = nach_ip.get(ip) if ip else None
            d = d or nach_key.get(geraete_key(mac, ip))
            if d is None:
                d = nach_key[geraete_key(mac, ip)] = leeres_geraet(geraete_key(mac, ip), ip)
            if ip:
                d["ip"] = d["ip"] or ip
                nach_ip[ip] = d
            roh = normal_mac(mac)
            if len(roh) == 12 and not d["mac"]:  # ein Geraet, das zuerst nur per IP bekannt war, bekommt seine MAC als Schluessel
                nach_key.pop(d["key"], None)
                d["mac"] = d["key"] = mac_text(roh)
                nach_key[d["key"]] = d
            for k, v in felder.items():
                if v and not d[k]:
                    d[k] = v

        snaps = list(stand["snapshots"].values())
        for snap in snaps:  # Namen aus Router-Listen vor Namen aus der Namensaufloesung
            for q in snap.get("quellen", []):
                for c in q.get("clients", []):
                    aufnehmen(c.get("ip", ""), c.get("mac", ""), name=c.get("name", ""), verbindung=c.get("verbindung", ""), signal=c.get("signal"))
        for snap in snaps:
            for g in snap.get("geraete", []):
                aufnehmen(g["ip"], g.get("mac", ""), name=g.get("name", ""), hersteller=g.get("hersteller", ""), ports=g.get("ports"), typ=g.get("typ", ""), typname=g.get("typname", ""))
        vorhanden = set(nach_key) | {f"ip:{d['ip']}" for d in nach_key.values()}
        for key in cfg["geraete"]:  # bekannte Geraete, die gerade nicht auftauchen
            if key not in vorhanden:
                info = stand["letzte"].get(key, {})
                nach_key[key] = {**leeres_geraet(key, info.get("ip") or (key[3:] if key.startswith("ip:") else ""), "" if key.startswith("ip:") else key), "name": info.get("name", ""), "hersteller": info.get("hersteller", "")}
        fenster, liste = 2 * cfg["scan_intervall"] + 60, []
        for d in nach_key.values():
            label = cfg["geraete"].get(d["key"]) or cfg["geraete"].get(f"ip:{d['ip']}") or {}
            gesehen = max(stand["gesehen"].get(k, 0) for k in (d["key"], f"ip:{d['ip']}"))
            liste.append({**d, "name": label.get("name") or d["name"], "bezeichnet": bool(label.get("name")), "bekannt": bool(label.get("bekannt")), "wichtig": bool(label.get("wichtig")),
                          "neu": cfg["baseline"] and not label.get("bekannt"), "gesehen": gesehen or None, "online": bool(gesehen) and jetzt - gesehen <= fenster})
        return sorted(liste, key=lambda d: (not d["online"], ip_sortierung(d["ip"]), d["name"]))

    def uebersicht(self):
        with self.lock:
            cfg, stand, jetzt = self.config(), self.stand, time.time()
            quellen, herkuenfte = [], []
            for herkunft, snap in stand["snapshots"].items():
                herkuenfte.append({"name": herkunft, "t": snap.get("t"), "scan": snap.get("ts"), "fehler_scan": snap.get("fehler_scan", []), "netze": snap.get("netze", []), "tailscale": snap.get("tailscale")})
                quellen += [{**q, "herkunft": herkunft, "stand": snap.get("tq")} for q in snap.get("quellen", [])]
            geraete = self.geraete_liste(cfg, stand, jetzt)
            volumes = [v for q in quellen if q["ok"] for v in q.get("volumes", [])]
            wan = next((q["wan"] for q in quellen if q["ok"] and q.get("wan")), None)
            return {"modus": cfg["modus"], "stand": max((h["t"] or 0 for h in herkuenfte), default=None), "herkuenfte": herkuenfte, "quellen": quellen, "geraete": geraete,
                    "alarme": [{"key": k, "text": t} for k, t in stand["alarme"].items()], "prognosen": self.prognosen, "agent": {**stand["agent"], "aktiv": bool(self.agent_origin())},
                    "zusammenfassung": {"geraete_online": sum(d["online"] for d in geraete), "geraete_gesamt": len(geraete), "neue": sum(d["neu"] and d["online"] for d in geraete),
                                        "quellen_ok": sum(q["ok"] for q in quellen), "quellen_gesamt": len(quellen), "speicher_gesamt": sum(v["gesamt"] for v in volumes),
                                        "speicher_belegt": sum(v["belegt"] for v in volumes), "wan": wan, "strom_w": round(sum(q["strom"]["leistung_w"] for q in quellen if q.get("strom")), 1)}}

    # --- Alarme -----------------------------------------------------------------------
    def alarme_berechnen(self, ue, cfg):
        a, jetzt, ergebnis = cfg["alarme"], time.time(), {}
        for q in ue["quellen"]:
            schluessel, name = f"{q['herkunft']}/{q['id']}", q["name"]
            if not q["ok"]:
                if self.ausfaelle.get((q["herkunft"], q["id"]), 0) >= a["ausfall_versuche"]:
                    ergebnis[f"quelle:{schluessel}"] = f"{name} antwortet nicht: {q.get('fehler', '')}"
                continue
            ergebnis.update({f"voll:{schluessel}:{v['name']}": f"{name}: {v['name']} zu {v.get('prozent', 0)} % voll" for v in q.get("volumes", []) if v.get("prozent", 0) >= a["voll_prozent"]})
            for p in q.get("platten", []):
                if p.get("status") == "fail":
                    ergebnis[f"platte:{schluessel}:{p['name']}"] = f"{name}: Platte {p['name']} meldet SMART-Fehler"
                elif p.get("temp", 0) >= a["temp_platte"]:
                    ergebnis[f"hitze:{schluessel}:{p['name']}"] = f"{name}: Platte {p['name']} ist {p['temp']} °C heiß"
            ergebnis.update({f"raid:{schluessel}:{r['name']}": f"{name}: RAID {r['name']} ist {r.get('detail', 'gestört')}" for r in q.get("raid", []) if r["status"] != "ok"})
            if q.get("wan") and q["wan"].get("verbunden") is False:
                ergebnis[f"wan:{schluessel}"] = f"{name}: Internetverbindung unterbrochen"
            if q.get("usv") and not q["usv"].get("netz", True):
                ergebnis[f"usv:{schluessel}"] = f"{name}: USV läuft auf Batterie ({q['usv'].get('ladung')} %)"
            if q.get("zertifikat") and q["zertifikat"]["tage"] <= a["zertifikat_tage"]:
                ergebnis[f"zert:{schluessel}"] = f"{name}: Zertifikat läuft in {q['zertifikat']['tage']} Tagen ab"
        for d in ue["geraete"]:
            if a["neues_geraet"] and d["neu"] and d["online"]:
                ergebnis[f"neu:{d['key']}"] = f"Neues Gerät im Netz: {d['name'] or d['ip']} ({d['hersteller'] or d['mac'] or 'unbekannt'})"
            if d["wichtig"] and d["gesehen"] and not d["online"] and jetzt - d["gesehen"] > a["offline_min"] * 60:
                ergebnis[f"aus:{d['key']}"] = f"{d['name'] or d['ip']} ist offline"
        if cfg["modus"] == "agent" and cfg["agent_hash"] and self.stand["agent"].get("zuletzt") and not self.agent_origin():
            ergebnis["agent"] = "Der Agent im Heimnetz meldet sich nicht mehr"
        return ergebnis

    def alarme_pruefen(self, ue):
        with self.lock:
            neu, alt = self.alarme_berechnen(ue, self.config()), self.stand["alarme"]
            meldungen = [f"Netzwerk: {t}" for k, t in neu.items() if k not in alt]
            meldungen += [f"Netzwerk: wieder in Ordnung – {t}" for k, t in alt.items() if k not in neu and k.split(":")[0] in MELDEN_ENDE]
            self.stand["alarme"] = neu
            write_json(self.stand_pfad, self.stand)
        for text in meldungen:  # ausserhalb des Locks: das Senden kann dauern
            try:
                self.notify(text)
            except OSError as e:
                print(f"netz: Meldung nicht gesendet ({text}): {e!r}", file=sys.stderr)

    # --- Verlauf und Prognose ---------------------------------------------------------
    def verlauf_schreiben(self, ue):
        jetzt = time.time()
        if jetzt - self.letzter_verlauf < VERLAUF_ABSTAND:
            return
        self.letzter_verlauf = jetzt
        zeile = {"t": int(jetzt), "g": ue["zusammenfassung"]["geraete_online"], "q": {}}
        for q in ue["quellen"]:
            if not q["ok"]:
                continue
            rate = q.get("wan") or q.get("traffic") or {}
            werte = {"cpu": q.get("cpu"), "ram": (q.get("ram") or {}).get("prozent"), "temp": q.get("temp"), "rx": rate.get("rx"), "tx": rate.get("tx"), "ms": q.get("ms"),
                     "vol": {v["name"][:HISTORIE_NAME]: [v["belegt"], v["gesamt"]] for v in q.get("volumes", [])[:HISTORIE_VOLUMES]}}
            zeile["q"][f"{q['herkunft']}/{q['id']}"] = {k: v for k, v in werte.items() if v not in (None, {})}
        with self.lock:
            with open(self.verlauf_pfad, "a", encoding="utf-8") as f:
                f.write(json.dumps(zeile) + "\n")
            self.verlauf_kuerzen()
        if jetzt - self.letzte_prognose >= PROGNOSE_ABSTAND:
            self.letzte_prognose = jetzt
            self.prognosen = self.prognose_berechnen(self.verlauf_lesen(PROGNOSE_TAGE, 10 ** 9))

    def verlauf_kuerzen(self):
        with open(self.verlauf_pfad, encoding="utf-8") as f:
            zeilen = f.readlines()
        if len(zeilen) > VERLAUF_MAX_ZEILEN:
            with open(self.verlauf_pfad, "w", encoding="utf-8") as f:
                f.writelines(zeilen[-VERLAUF_BEHALTEN:])

    def verlauf_lesen(self, tage, punkte=VERLAUF_PUNKTE):
        grenze = time.time() - tage * 86400
        try:
            with self.lock, open(self.verlauf_pfad, encoding="utf-8") as f:  # unter der Sperre: kuerzen schreibt die Datei neu
                zeilen = [z for z in (json.loads(x) for x in f if x.strip()) if z["t"] >= grenze]
        except FileNotFoundError:
            return []
        return zeilen[::max(1, len(zeilen) // punkte)]

    @staticmethod
    def prognose_berechnen(zeilen):
        """{"<quelle>|<laufwerk>": Tage bis voll oder None}. Aus dem Zuwachs der letzten Tage, mindestens 12 Messpunkte ueber 12 Stunden."""
        reihen = {}
        for z in zeilen:
            for qkey, werte in z["q"].items():
                for name, (belegt, gesamt) in (werte.get("vol") or {}).items():
                    reihen.setdefault(f"{qkey}|{name}", []).append((z["t"], belegt, gesamt))
        ergebnis = {}
        for key, punkte in reihen.items():
            if len(punkte) < PROGNOSE_MIN_PUNKTE or punkte[-1][0] - punkte[0][0] < PROGNOSE_MIN_SPANNE:
                continue
            pro_tag = trend([(t, b) for t, b, _ in punkte])
            ergebnis[key] = round((punkte[-1][2] - punkte[-1][1]) / pro_tag, 1) if pro_tag > 0 else None
        return ergebnis

    # --- Aktionen ---------------------------------------------------------------------
    def geraet_setzen(self, data):
        key = str(data.get("key", ""))
        if not GERAET_KEY.fullmatch(key):
            raise ValueError("ungültiges Gerät")
        with self.lock:
            cfg = self.config()
            if key not in cfg["geraete"] and len(cfg["geraete"]) >= MAX_GERAETE:
                raise ValueError("zu viele Geräte")
            alt = cfg["geraete"].get(key, {"name": "", "bekannt": False, "wichtig": False})
            neu = bereinige_geraet({**alt, **{k: v for k, v in data.items() if k in alt}})
            write_json(self.cfg_pfad, {**cfg, "geraete": {**cfg["geraete"], key: neu}})
        return {"ok": True}

    def befehl_an_agent(self, befehl):
        origin = self.agent_origin()
        if not origin:
            return False
        with self.lock:
            liste = self.befehle.setdefault(origin, [])
            liste.append(befehl)
            del liste[:-MAX_BEFEHLE]
        return True

    def wol_senden(self, mac):
        text = mac_text(mac)
        if not GERAET_KEY.fullmatch(text) or text.startswith("ip:"):
            raise ValueError("ungültige MAC-Adresse")
        if self.befehl_an_agent({"typ": "wol", "mac": text}):
            return {"weg": "agent"}
        cfg = self.config()
        if cfg["modus"] == "tailscale":
            raise ValueError("Im Tailscale-Modus braucht Wake-on-LAN einen Agenten im Heimnetz")
        wol(text, cfg["wol_broadcast"] or "255.255.255.255")
        return {"weg": "lokal"}

    def scan_anfordern(self):
        self.befehl_an_agent({"typ": "scan"})
        self.scan_jetzt.set()
        return {"ok": True}

    def testen(self, roh):
        alt = {q["id"]: q for q in self.config()["quellen"]}
        quelle = bereinige_quelle(roh, alt.get(roh.get("id") if isinstance(roh, dict) else None, {}))
        return normalisieren(abfragen(quelle, {}))

    def entdecken(self, text):
        cfg = self.config()
        netze = [bereinige_netz(text)] if text else cfg["netze"] or local_subnets()
        if not netze:
            raise ValueError("kein Netz angegeben (z. B. 192.168.1.0/24)")
        return {"geraete": [g for netz in netze for g in sweep(netz)], "netze": netze}

    # --- Anbindung an den Server ------------------------------------------------------
    def get_routes(self):
        return {"/api/netz": self.uebersicht, "/api/netz/config": self.config_ansicht}

    def query_routes(self):
        return {"/api/netz/verlauf": lambda q: self.verlauf_lesen(zahl(q.get("tage", ["1"])[0], 1, VERLAUF_MAX_TAGE, "Tage"))}

    def post(self, path, data, ip, token=None):
        """POST-Aufrufe dieses Moduls: (Status, Antwort) oder None. Aenderungen an Quellen und Agent-Token verlangen die Sicherheitsstufe."""
        if not path.startswith("/api/netz/"):
            return None
        aktion = path[len("/api/netz/"):]
        if aktion in STEPUP and not self.auth.is_elevated(token):
            raise PermissionError("stepup")
        if aktion == "config":
            ergebnis = self.speichern(data)
            return 200, {"ok": True, **ergebnis, "_audit": f"Netzwerk: Betriebsart {ergebnis['modus']}, {len(ergebnis['quellen'])} Quellen"}
        if aktion == "token":
            return 200, {"ok": True, **self.token_neu(), "_audit": "Agent-Token erneuert"}
        if aktion == "token/loeschen":
            return 200, {**self.token_loeschen(), "_audit": "Agent-Token gelöscht"}
        handler = {"test": lambda: {"ergebnis": self.testen(data.get("quelle"))}, "entdecken": lambda: self.entdecken(str(data.get("netz", ""))), "scan": self.scan_anfordern,
                   "geraet": lambda: self.geraet_setzen(data), "wol": lambda: self.wol_senden(str(data.get("mac", "")))}.get(aktion)
        return (200, {"ok": True, **handler(), "_audit": f"Netzwerk: {aktion}"}) if handler else None
