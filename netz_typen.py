"""Quellentypen des Netzwerk-Moduls: Registry, Feldbeschreibung fuer das Formular und die Abfrage einer Quelle mit einheitlichem Ergebnis.

Ergebnis einer Quelle (alle Schluessel ausser id, typ, name, ok, ms optional; Datenraten in Byte pro Sekunde):
  modell, version, uptime (s), cpu (%), ram {gesamt, belegt}, temp (Grad), last [1, 5, 15 Min], kerne,
  volumes [{name, gesamt, belegt, prozent}], platten [{name, modell, temp, status ok|warn|fail|unbekannt, kapazitaet}],
  raid [{name, status, detail}], traffic {rx, tx}, wan {verbunden, ip, rx, tx, down_max, up_max (Bit/s)},
  clients [{mac, ip, name, verbindung lan|wlan, signal}], dienste [{name, ok, detail}], strom {leistung_w, ein, energie_wh},
  usv {status, ladung, laufzeit, last, modell, ok}, dns {anfragen, blockiert, prozent, domains, aktiv}, zertifikat {tage, bis},
  speedtest {down, up, ping_ms, t}, backup {alter_h, datei, ok}, werte [[Name, Text]], hinweise [Text]."""
import subprocess
import sys
import time

from netz_http import NetzFehler
from netz_snmp import SnmpError

TYPEN = {}
GESCHUETZT = "********"  # so zeigt die Oberflaeche ein gespeichertes Geheimnis; zurueckgeschickt heisst es: unveraendert lassen
KEIN_ZIEL = ("lokal", "speedtest", "backup")  # Typen ohne Netzwerkadresse


def feld(key, label, art="text", standard="", hilfe="", geheim=False, optionen=None):
    """Ein Eingabefeld im Quellen-Formular. art: text, zahl, schalter, auswahl."""
    f = {"key": key, "label": label, "art": art, "standard": standard, "hilfe": hilfe, "geheim": geheim}
    if optionen:
        f["optionen"] = optionen
    return f


HOST = feld("host", "Adresse", hilfe="IP oder Name im Heimnetz, z. B. 192.168.1.20")
HTTPS = feld("https", "HTTPS", "schalter")


def port(standard):
    return feld("port", "Port", "zahl", standard)


def typ(schluessel, name, gruppe, felder, hinweis=""):
    """Registriert eine Abfragefunktion lade(src, state) -> dict."""
    def dekorieren(lade):
        TYPEN[schluessel] = {"name": name, "gruppe": gruppe, "felder": felder, "hinweis": hinweis, "lade": lade}
        return lade
    return dekorieren


def prozent(teil, gesamt):
    return round(100 * teil / gesamt, 1) if gesamt else 0.0


def ergaenzen(daten):
    """Prozentwerte der Laufwerke und des Arbeitsspeichers nachtragen."""
    for v in daten.get("volumes") or []:
        v["prozent"] = prozent(v["belegt"], v["gesamt"])
    ram = daten.get("ram")
    if ram and "prozent" not in ram:
        ram["prozent"] = prozent(ram["belegt"], ram["gesamt"])
    return daten


def abfragen(src, state):
    """Fragt eine Quelle ab. Fehler werden zur Meldung im Ergebnis (ok False), der Urtext geht auf stderr."""
    basis = {"id": src["id"], "typ": src["typ"], "name": src.get("name") or src["typ"]}
    start = time.time()
    try:
        daten = ergaenzen(TYPEN[src["typ"]]["lade"](src, state))
        return {**basis, "ok": daten.pop("ok", True), "ms": round(1000 * (time.time() - start)), **daten}
    except (NetzFehler, SnmpError) as e:
        fehler = str(e)
    except subprocess.TimeoutExpired:
        fehler = "Zeitüberschreitung"
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, ArithmeticError) as e:  # ArithmeticError: riesige Zahlen eines Geraets
        print(f"netz: {src['typ']} {src.get('host', '')}: {e!r}", file=sys.stderr)
        fehler = f"Antwort nicht lesbar ({type(e).__name__}: {e})"
    return {**basis, "ok": False, "fehler": fehler, "ms": round(1000 * (time.time() - start))}


def katalog():
    """Typen fuer die Oberflaeche (ohne Funktionen)."""
    return {k: {"name": v["name"], "gruppe": v["gruppe"], "felder": v["felder"], "hinweis": v["hinweis"]} for k, v in TYPEN.items()}
