"""Netzwerk-Agent fuer das Heimnetz (Betriebsart "agent"). Laeuft zu Hause (NAS, Raspberry Pi, Mini-PC), fragt Geraete ab und schickt den Stand per HTTPS an das Dashboard.
Nur Standardbibliothek. Das Dashboard muss den Agenten nicht erreichen: der Agent ruft nur nach draussen, es wird kein Port zu Hause geoeffnet.

  python3 netz_agent.py                 laeuft dauerhaft, liest netz-agent.json
  python3 netz_agent.py --einmal        ein Durchlauf, dann Ende
  python3 netz_agent.py --trocken       ein Durchlauf, Ergebnis ausgeben statt senden (zum Testen der Quellen)
  python3 netz_agent.py --entdecken     Geraete im Heimnetz suchen und den vermuteten Typ anzeigen
  python3 netz_agent.py --konfig PFAD   andere Konfigurationsdatei

Die Konfiguration (Server, Token, Quellen) erzeugt das Dashboard unter Netzwerk → Einstellungen → Agent."""
import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # die Module liegen neben diesem Skript, auch wenn Python isoliert startet
import netz_dienste  # noqa: E402  registriert die Quellentypen
from netz_scan import local_subnets, sweep, wol
from netz_typen import abfragen

STANDARD_KONFIG = "netz-agent.json"
WORKERS = 8
PUSH_TIMEOUT = 30
TICK_SECONDS = 2
MIN_INTERVALL = 30
SCAN_FEHLER_MAX = 10


def konfig_lesen(pfad):
    try:
        with open(pfad, encoding="utf-8") as f:
            konfig = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"{pfad} fehlt. Im Dashboard unter Netzwerk → Einstellungen → Agent die Konfiguration erzeugen und hier speichern.") from None
    if not (konfig.get("server") and konfig.get("token")):
        raise SystemExit(f"{pfad}: server und token müssen gesetzt sein")
    return konfig


def senden(konfig, daten):
    """Schickt den Stand ans Dashboard und gibt die Befehle zurueck, die dort fuer diesen Agenten warten."""
    req = urllib.request.Request(konfig["server"].rstrip("/") + "/api/netz/push", data=json.dumps(daten).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {konfig['token']}", "Content-Type": "application/json", "User-Agent": "claude-deck-agent"})
    with urllib.request.urlopen(req, timeout=PUSH_TIMEOUT) as r:
        return json.loads(r.read()).get("befehle", [])


def quellen_abfragen(konfig, states):
    aktive = [q for q in konfig.get("quellen", []) if q.get("aktiv", True)]
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(lambda q: abfragen(q, states.setdefault(q["id"], {})), aktive))


def geraete_scannen(konfig):
    netze, gefunden, fehler = konfig.get("netze") or local_subnets(), [], []
    for netz in netze:
        try:
            gefunden += sweep(netz)
        except (ValueError, OSError) as e:
            fehler.append(f"{netz}: {e}")
    return {"geraete": gefunden, "fehler_scan": fehler[:SCAN_FEHLER_MAX], "netze": netze}


def befehle_ausfuehren(befehle):
    """Fuehrt Befehle des Dashboards aus. Gibt True zurueck, wenn ein Scan angefordert wurde."""
    scan = False
    for b in befehle:
        if b.get("typ") == "wol":
            wol(b["mac"], os.environ.get("NETZ_WOL_BROADCAST", "255.255.255.255"))
            print(f"Aufwecken gesendet an {b['mac']}")
        elif b.get("typ") == "scan":
            scan = True
    return scan


def durchlauf(konfig, states, mit_scan, trocken):
    daten = {"name": konfig.get("name", "heimnetz"), "quellen": quellen_abfragen(konfig, states)}
    if mit_scan:
        daten.update(geraete_scannen(konfig))
    if trocken:
        print(json.dumps(daten, indent=2, ensure_ascii=False))
        return False
    return befehle_ausfuehren(senden(konfig, daten))


def entdecken(konfig):
    for netz in konfig.get("netze") or local_subnets():
        for g in sweep(netz):
            print(f"{g['ip']:<15} {g['mac'] or '-':<17} {g['name'] or '-':<28} {g['hersteller'] or '-':<24} Typ-Vorschlag: {g['typ'] or '-'} {g['typname']}")


def main():
    eltern = argparse.ArgumentParser(description="Netzwerk-Agent für Claude Deck")
    eltern.add_argument("--konfig", default=STANDARD_KONFIG)
    eltern.add_argument("--einmal", action="store_true")
    eltern.add_argument("--trocken", action="store_true")
    eltern.add_argument("--entdecken", action="store_true")
    args = eltern.parse_args()
    if args.entdecken:
        entdecken(konfig_lesen(args.konfig) if os.path.exists(args.konfig) else {})
        return
    konfig, states, letzte = konfig_lesen(args.konfig), {}, {"quellen": 0, "scan": 0}
    scan_jetzt = True
    while True:
        jetzt = time.time()
        intervall = max(MIN_INTERVALL, int(konfig.get("intervall", 60)))
        scan_abstand = max(MIN_INTERVALL, int(konfig.get("scan_intervall", 300)))
        if args.einmal or args.trocken or jetzt - letzte["quellen"] >= intervall:
            mit_scan = scan_jetzt or jetzt - letzte["scan"] >= scan_abstand
            letzte["quellen"], letzte["scan"] = jetzt, (jetzt if mit_scan else letzte["scan"])
            try:
                scan_jetzt = durchlauf(konfig, states, mit_scan, args.trocken)
            except (urllib.error.URLError, OSError, ValueError) as e:
                print(f"Senden fehlgeschlagen: {e}", file=sys.stderr)
            if args.einmal or args.trocken:
                return
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
