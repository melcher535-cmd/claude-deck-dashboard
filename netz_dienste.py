"""Abfragen fuer Dienste und Zubehoer im Heimnetz: Pi-hole, AdGuard, Home Assistant, Shelly, Tasmota, USV (NUT), Immich, Dienst- und Zertifikatspruefung,
Speedtest und Backup-Kontrolle. Dazu die Linux-Quellen lokal und ssh. Ergebnis jeweils im Schema aus netz_typen.py."""
import glob
import itertools
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import netz_linux
from netz_geraete import PASSWORT, ZERTIFIKAT, basic
from netz_http import NetzFehler, anfrage, basis, json_von, pruefe_ziel
from netz_scan import ping as ping_host
from netz_scan import tcp_offen
from netz_typen import HOST, HTTPS, feld, port, typ

NUT_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
NUT_BAD = ("OB", "LB", "RB", "OVER", "FSD", "ALARM")
MAX_BACKUP_FILES = 20000
MIN_SPEEDTEST_MINUTEN = 30
SPEED_DOWN, SPEED_UP = "https://speed.cloudflare.com/__down?bytes=25000000", "https://speed.cloudflare.com/__up"
SPEED_UP_BYTES = 8_000_000
SPEED_TIMEOUT = 40
CHUNK = 1 << 16
MAX_HA_WERTE = 12


# --- Linux ------------------------------------------------------------------------------
@typ("lokal", "Dieser Rechner (auch NAS mit Dashboard oder Agent)", "NAS und Router", [],
     "Liest Arbeitsspeicher, Prozessor, Laufwerke, RAID, ZFS, Temperaturen und (falls smartctl installiert ist) SMART-Werte vom Rechner, auf dem das Dashboard oder der Agent läuft.")
def lokal(src, state):
    return netz_linux.lokal(src, state)


@typ("ssh", "Linux-Gerät per SSH (UGREEN, Unraid, OMV, Proxmox, Raspberry Pi ...)", "NAS und Router",
     [HOST, port(22), feld("benutzer", "Benutzer", "text", "root"), feld("schluessel", "Schlüsseldatei", "text", "", "Pfad zum privaten Schlüssel auf diesem Rechner, z. B. /root/.ssh/id_ed25519")],
     "Funktioniert mit jedem Linux-NAS. Das Dashboard führt nur ein festes, lesendes Skript aus (/proc, df, smartctl, mdstat, zpool). Schlüssel statt Passwort; für SMART-Werte braucht der Benutzer Leserechte auf die Platten.")
def ssh(src, state):
    return netz_linux.ssh(src, state)


# --- DNS-Filter -------------------------------------------------------------------------
@typ("pihole", "Pi-hole", "Dienste", [HOST, port(80), HTTPS, feld("passwort", "Passwort (v6) oder API-Token (v5)", "text", "", "Version 6: Web- oder App-Passwort", True), ZERTIFIKAT],
     "Version 6 und 5. Bei Version 6 ein App-Passwort unter Einstellungen → Web-Interface/API anlegen.")
def pihole(src, state):
    b, pruefen = basis(src, 80), bool(src.get("ssl_pruefen"))
    geheim = src.get("passwort", "")
    status, body = anfrage(f"{b}/api/auth", "POST", daten={"password": geheim}, pruefen=pruefen)
    if status == 404:  # Version 5
        d = json_von(f"{b}/admin/api.php?summaryRaw&auth={urllib.parse.quote(geheim)}", pruefen=pruefen)
        return {"dns": {"anfragen": d["dns_queries_today"], "blockiert": d["ads_blocked_today"], "prozent": round(float(d["ads_percentage_today"]), 1), "domains": d["domains_being_blocked"], "aktiv": d.get("status") == "enabled"}}
    if status == 401:
        raise NetzFehler("Pi-hole: Passwort falsch")
    sid = (json.loads(body).get("session") or {}).get("sid")
    kopf = {"sid": sid} if sid else {}
    hinweise = []
    try:
        s = json_von(f"{b}/api/stats/summary", kopf=kopf, pruefen=pruefen)
        blockierung = json_von(f"{b}/api/dns/blocking", kopf=kopf, pruefen=pruefen)
    finally:
        if sid:
            try:
                anfrage(f"{b}/api/auth", "DELETE", kopf, pruefen=pruefen)
            except NetzFehler as e:
                hinweise.append(f"Abmelden fehlgeschlagen: {e}")
    q = s.get("queries", {})
    return {"dns": {"anfragen": q.get("total", 0), "blockiert": q.get("blocked", 0), "prozent": round(float(q.get("percent_blocked", 0)), 1), "domains": (s.get("gravity") or {}).get("domains_being_blocked", 0),
                    "aktiv": blockierung.get("blocking") == "enabled", "clients": (s.get("clients") or {}).get("active", 0)}, "hinweise": hinweise}


@typ("adguard", "AdGuard Home", "Dienste", [HOST, port(3000), HTTPS, feld("benutzer", "Benutzer"), PASSWORT, ZERTIFIKAT], "Benutzer und Passwort der Weboberfläche.")
def adguard(src, state):
    b, pruefen = basis(src, 3000), bool(src.get("ssl_pruefen"))
    kopf = basic(src.get("benutzer", ""), src.get("passwort", ""))
    status, stats = json_von(f"{b}/control/status", kopf=kopf, pruefen=pruefen), json_von(f"{b}/control/stats", kopf=kopf, pruefen=pruefen)
    gesamt, blockiert = stats.get("num_dns_queries", 0), stats.get("num_blocked_filtering", 0)
    return {"version": status.get("version", ""), "dns": {"anfragen": gesamt, "blockiert": blockiert, "prozent": round(100 * blockiert / gesamt, 1) if gesamt else 0.0, "aktiv": bool(status.get("protection_enabled"))}}


# --- Smart Home -------------------------------------------------------------------------
@typ("homeassistant", "Home Assistant", "Smart Home", [HOST, port(8123), HTTPS, feld("token", "Langlebiges Zugriffstoken", "text", "", "Profil → Sicherheit → Langlebige Zugriffstoken", True),
     feld("entitaeten", "Entitäten", "text", "", "Kommagetrennt, z. B. sensor.stromzaehler_leistung, light.wohnzimmer"), ZERTIFIKAT], "Zeigt die gewählten Entitäten als Werte.")
def homeassistant(src, state):
    b = basis(src, 8123)
    zustaende = json_von(f"{b}/api/states", kopf={"Authorization": f"Bearer {src.get('token', '')}"}, pruefen=bool(src.get("ssl_pruefen")))
    gewaehlt = [e.strip() for e in str(src.get("entitaeten", "")).split(",") if e.strip()]
    nach_id = {z["entity_id"]: z for z in zustaende}
    werte = []
    for eid in gewaehlt[:MAX_HA_WERTE]:
        z = nach_id.get(eid)
        werte.append([(z["attributes"].get("friendly_name") if z else eid), f"{z['state']} {z['attributes'].get('unit_of_measurement', '')}".strip() if z else "nicht gefunden"])
    return {"werte": [["Entitäten", str(len(zustaende))]] + werte}


def shelly_gen2(status):
    schalter = [v for k, v in status.items() if k.startswith(("switch:", "pm1:"))]
    return {"leistung_w": round(sum(s.get("apower", 0) for s in schalter), 1), "ein": any(s.get("output") for s in schalter), "energie_wh": round(sum((s.get("aenergy") or {}).get("total", 0) for s in schalter), 1)}


@typ("shelly", "Shelly (Steckdose, Relais, Strommessung)", "Smart Home", [HOST, port(80), feld("benutzer", "Benutzer"), PASSWORT],
     "Generation 1 und 2+. Bei Gen 2+ muss der Geräteschutz aus sein (das Dashboard kann dort kein Digest-Login).")
def shelly(src, state):
    b = basis(src, 80)
    kopf = basic(src["benutzer"], src.get("passwort", "")) if src.get("benutzer") else {}
    status, body = anfrage(f"{b}/rpc/Shelly.GetStatus", kopf=kopf)
    if status == 200:
        d = json.loads(body)
        return {"strom": shelly_gen2(d), "uptime": (d.get("sys") or {}).get("uptime")}
    if status == 401:
        raise NetzFehler("Shelly verlangt eine Anmeldung, die das Dashboard nicht kann (Geräteschutz ausschalten)")
    d = json_von(f"{b}/status", kopf=kopf)  # Gen 1
    zaehler = (d.get("meters") or [{}])[0]
    return {"strom": {"leistung_w": zaehler.get("power", 0), "ein": any(r.get("ison") for r in d.get("relays", [])), "energie_wh": round(zaehler.get("total", 0) / 60, 1)}, "uptime": d.get("uptime")}


@typ("tasmota", "Tasmota", "Smart Home", [HOST, port(80), feld("benutzer", "Benutzer"), PASSWORT], "Strommessung (ENERGY) und Schaltzustand.")
def tasmota(src, state):
    auth = f"&user={urllib.parse.quote(src['benutzer'])}&password={urllib.parse.quote(src.get('passwort', ''))}" if src.get("benutzer") else ""
    d = json_von(f"{basis(src, 80)}/cm?cmnd=Status%200{auth}")
    energie = (d.get("StatusSNS") or {}).get("ENERGY") or {}
    ein = str((d.get("StatusSTS") or {}).get("POWER", "")).upper() == "ON"
    return {"modell": ((d.get("Status") or {}).get("FriendlyName") or [""])[0], "strom": {"leistung_w": energie.get("Power", 0), "ein": ein, "energie_wh": round(float(energie.get("Total", 0)) * 1000, 1)}}


# --- USV (NUT) --------------------------------------------------------------------------
def nut_variablen(host, p, ups):
    with socket.create_connection((host, p), timeout=4) as s:
        s.settimeout(4)
        s.sendall(f"LIST VAR {ups}\n".encode())
        text = b""
        while b"END LIST VAR" not in text and not text.startswith(b"ERR"):
            teil = s.recv(4096)
            if not teil:
                break
            text += teil
    if text.startswith(b"ERR"):
        raise NetzFehler(f"NUT: {text.decode(errors='replace').strip()}")
    return {m.group(1): m.group(2) for m in re.finditer(r'^VAR \S+ (\S+) "(.*)"$', text.decode(errors="replace"), re.M)}


@typ("nut", "USV (Network UPS Tools)", "Smart Home", [HOST, port(3493), feld("ups", "Name der USV", "text", "ups")], "NUT-Server (z. B. auf Synology unter Systemsteuerung → Hardware & Energie → USV, oder upsd im Heimnetz).")
def nut(src, state):
    ups = str(src.get("ups") or "ups")
    if not NUT_NAME.fullmatch(ups):
        raise NetzFehler("USV-Name enthält ungültige Zeichen")
    v = nut_variablen(pruefe_ziel(src["host"]), int(src.get("port") or 3493), ups)
    teile = v.get("ups.status", "").split()
    return {"modell": v.get("ups.model", ""), "ok": not any(t in NUT_BAD for t in teile),
            "usv": {"status": " ".join(teile), "ladung": float(v["battery.charge"]) if "battery.charge" in v else None, "laufzeit": int(float(v["battery.runtime"])) if "battery.runtime" in v else None,
                    "last": float(v["ups.load"]) if "ups.load" in v else None, "netz": "OL" in teile}}


# --- Immich -----------------------------------------------------------------------------
@typ("immich", "Immich (Fotoserver)", "Dienste", [HOST, port(2283), HTTPS, feld("api_key", "API-Schlüssel", "text", "", "Benutzer-Einstellungen → API-Schlüssel (Administrator)", True), ZERTIFIKAT],
     "Zeigt Fotos, Videos, Speicherbelegung und die Warteschlange der Hintergrundaufgaben.")
def immich(src, state):
    b, kopf, pruefen = basis(src, 2283), {"x-api-key": src.get("api_key", "")}, bool(src.get("ssl_pruefen"))
    get = lambda pfad: json_von(f"{b}/api/{pfad}", kopf=kopf, pruefen=pruefen)
    statistik, speicher, version, jobs = get("server/statistics"), get("server/storage"), get("server/version"), get("jobs")
    warteschlange = sum((j.get("jobCounts") or {}).get(k, 0) for j in jobs.values() if isinstance(j, dict) for k in ("active", "waiting"))
    return {"version": f"{version.get('major')}.{version.get('minor')}.{version.get('patch')}",
            "volumes": [{"name": "Immich-Speicher", "gesamt": speicher.get("diskSizeRaw", 0), "belegt": speicher.get("diskUseRaw", 0)}],
            "werte": [["Fotos", str(statistik.get("photos", 0))], ["Videos", str(statistik.get("videos", 0))], ["Belegt", f"{round(statistik.get('usage', 0) / 1e9, 1)} GB"], ["Warteschlange", str(warteschlange)]]}


# --- Einfache Pruefungen ----------------------------------------------------------------
def pruefung(name, ok, detail, fehler):
    daten = {"ok": ok, "dienste": [{"name": name, "ok": ok, "detail": detail}]}
    if not ok:
        daten["fehler"] = fehler
    return daten


@typ("http", "Webdienst prüfen (HTTP)", "Dienste", [feld("url", "Adresse (URL)", "text", "", "z. B. http://192.168.1.5:8096/health"), feld("erwartet", "Erwarteter Status", "zahl", 200), feld("enthaelt", "Seite enthält (optional)")],
     "Prüft, ob ein Webdienst im Heimnetz antwortet, wie schnell, und optional ob ein Text auf der Seite steht.")
def http_pruefung(src, state):
    start = time.time()
    status, body = anfrage(src["url"])
    ms = round(1000 * (time.time() - start))
    erwartet, text = int(src.get("erwartet") or 200), str(src.get("enthaelt") or "")
    ok = status == erwartet and (not text or text.encode() in body)
    return pruefung(src.get("name") or src["url"], ok, f"HTTP {status} · {ms} ms", f"HTTP {status}" if status != erwartet else "erwarteter Text fehlt")


@typ("tcp", "Port prüfen (TCP)", "Dienste", [HOST, port(22)], "Prüft, ob ein Port offen ist, z. B. SMB (445), SSH (22), Plex (32400).")
def tcp(src, state):
    ziel = pruefe_ziel(src["host"])
    start = time.time()
    ok = tcp_offen(ziel, int(src.get("port") or 22), 3)
    return pruefung(f"{src['host']}:{src.get('port')}", ok, f"{round(1000 * (time.time() - start))} ms" if ok else "geschlossen", "Port geschlossen oder Gerät aus")


@typ("ping", "Gerät anpingen", "Dienste", [HOST], "Zeigt, ob ein Gerät erreichbar ist (Ping, ersatzweise typische Ports).")
def ping(src, state):
    ziel = pruefe_ziel(src["host"])
    start = time.time()
    ok = ping_host(ziel)
    return pruefung(src["host"], ok, f"{round(1000 * (time.time() - start))} ms" if ok else "keine Antwort", "keine Antwort")


@typ("zertifikat", "HTTPS-Zertifikat (Ablauf)", "Dienste", [feld("host", "Domain oder Adresse", "text", "", "Darf auch eine öffentliche Domain sein"), port(443)],
     "Warnt, bevor ein Zertifikat abläuft. Die Prüfung ist streng: ungültige oder selbst signierte Zertifikate gelten als Fehler.")
def zertifikat(src, state):
    host, p = src["host"], int(src.get("port") or 443)
    ziel = pruefe_ziel(host, oeffentlich=True)  # auch oeffentliche Domains, aber nie link-local, Multicast oder unspezifiziert
    try:
        with socket.create_connection((ziel, p), timeout=5) as s, ssl.create_default_context().wrap_socket(s, server_hostname=host) as t:
            cert = t.getpeercert()
    except ssl.SSLCertVerificationError as e:
        raise NetzFehler(f"Zertifikat ungültig: {e.verify_message}") from e
    except (OSError, ssl.SSLError) as e:
        raise NetzFehler(f"{host}: {e}") from e
    bis = ssl.cert_time_to_seconds(cert["notAfter"])
    return {"zertifikat": {"tage": int((bis - time.time()) // 86400), "bis": time.strftime("%Y-%m-%d", time.gmtime(bis))}}


# --- Speedtest und Backup ---------------------------------------------------------------
def lies_alles(antwort):
    gesamt = 0
    while True:
        teil = antwort.read(CHUNK)
        if not teil:
            return gesamt
        gesamt += len(teil)


def speed_messen():
    kopf = {"User-Agent": "claude-deck"}
    try:
        start = time.time()
        with urllib.request.urlopen(urllib.request.Request("https://speed.cloudflare.com/__down?bytes=0", headers=kopf), timeout=10) as r:
            r.read()
        ping_ms = round(1000 * (time.time() - start))
        start = time.time()
        with urllib.request.urlopen(urllib.request.Request(SPEED_DOWN, headers=kopf), timeout=SPEED_TIMEOUT) as r:
            down = lies_alles(r) / (time.time() - start)
        start = time.time()
        with urllib.request.urlopen(urllib.request.Request(SPEED_UP, data=bytes(SPEED_UP_BYTES), headers=kopf, method="POST"), timeout=SPEED_TIMEOUT) as r:
            r.read()
        up = SPEED_UP_BYTES / (time.time() - start)
    except (urllib.error.URLError, OSError) as e:
        raise NetzFehler(f"Speedtest: {getattr(e, 'reason', e)}") from e
    return {"down": round(down), "up": round(up), "ping_ms": ping_ms, "t": int(time.time())}


@typ("speedtest", "Internet-Geschwindigkeit (Speedtest)", "Dienste", [feld("intervall", "Abstand in Minuten", "zahl", 180, f"Mindestens {MIN_SPEEDTEST_MINUTEN}. Verbraucht je Messung etwa 33 MB Datenvolumen.")],
     "Misst von dem Rechner aus, auf dem das Dashboard oder der Agent läuft: im Tailscale-Modus also vom VPS, nicht von zu Hause. Für den Heimanschluss den Agenten nutzen.")
def speedtest(src, state):
    abstand = max(MIN_SPEEDTEST_MINUTEN, int(src.get("intervall") or 180)) * 60
    letzte = state.get("speedtest")
    if not letzte or time.time() - letzte["t"] >= abstand:
        letzte = state["speedtest"] = speed_messen()
    return {"speedtest": letzte}


@typ("backup", "Backup-Kontrolle (Dateialter)", "Dienste", [feld("pfad", "Dateien (Muster)", "text", "", "Absoluter Pfad mit Platzhaltern, z. B. /mnt/backup/*.tar.gz"), feld("max_alter", "Höchstalter in Stunden", "zahl", 48)],
     "Prüft auf dem Rechner mit Dashboard oder Agent, wie alt die neueste Datei ist. Warnt, wenn ein Backup ausbleibt.")
def backup(src, state):
    muster = str(src.get("pfad", ""))
    ordner = os.path.dirname(muster)
    if not muster.startswith("/") or "\0" in muster or ".." in muster.split("/") or any(c in ordner for c in "*?["):
        raise NetzFehler("Pfad muss absolut sein, ohne .. und ohne Platzhalter im Ordner (nur im Dateinamen)")
    dateien = list(itertools.islice(glob.iglob(muster), MAX_BACKUP_FILES))
    if not dateien:
        raise NetzFehler("keine Dateien gefunden")
    neueste = max(dateien, key=os.path.getmtime)
    alter = round((time.time() - os.path.getmtime(neueste)) / 3600, 1)
    ok = alter <= int(src.get("max_alter") or 48)
    daten = {"ok": ok, "backup": {"alter_h": alter, "datei": os.path.basename(neueste), "ok": ok}}
    if not ok:
        daten["fehler"] = f"Letztes Backup vor {alter} Stunden"
    return daten
