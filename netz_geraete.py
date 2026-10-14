"""Abfragen fuer NAS und Router: SNMP (fast jedes Geraet), Synology DSM, TrueNAS, Fritz!Box, UPnP, UniFi, OpenWrt, MikroTik, OPNsense.
Jede Funktion liefert ein Ergebnis im Schema aus netz_typen.py. Herstellerspezifisches ist nur dort drin, wo das Geraet es selbst anbietet."""
import base64
import http.cookiejar
import json
import re
import socket
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import netz_snmp as snmp_client
from netz_http import NetzFehler, anfrage, basis, json_von, pruefe_ziel, sicherer_opener
from netz_typen import HOST, HTTPS, feld, port, typ

USER = feld("benutzer", "Benutzer")
PASSWORT = feld("passwort", "Passwort", "text", "", geheim=True)
ZERTIFIKAT = feld("ssl_pruefen", "Zertifikat prüfen", "schalter", False, "Aus lassen bei selbst signierten Zertifikaten (fast alle NAS und Router)")
SKIP_IF = ("lo", "docker", "veth", "br-", "virbr", "tailscale", "wg", "tun", "tap", "dummy", "sit")
SKIP_VOLUMES = ("/proc", "/sys", "/dev", "/run", "/tmp", "/boot", "/config", "/opt", "/lib", "/var/run", "/etc", "/rom", "/overlay")
UPTIME_TEILE = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
BAD_LEVELS = ("WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY")
MAX_ALERTS = 5
MAX_FRITZ_HOSTS = 120


def counter_rate(state, key, jetzt, rx, tx, wrap=0):
    """Byte pro Sekunde aus zwei Zaehlerstaenden. wrap: Zaehlerbreite, falls der Zaehler ueberlaeuft (32 Bit bei alten Routern)."""
    alt = state.get(key)
    state[key] = (jetzt, rx, tx)
    if not alt or jetzt <= alt[0]:
        return None
    drx, dtx = rx - alt[1], tx - alt[2]
    if wrap:
        drx, dtx = drx % wrap, dtx % wrap
    elif drx < 0 or dtx < 0:
        return None
    return {"rx": round(drx / (jetzt - alt[0])), "tx": round(dtx / (jetzt - alt[0]))}


def basic(user, passwort):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{passwort}".encode()).decode()}


def ampel(code, ok=(), warn=()):
    return "ok" if code in ok else "warn" if code in warn else "fail"


# --- SNMP -------------------------------------------------------------------------------
SYS_DESCR, SYS_UPTIME, SYS_NAME = "1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.3.0", "1.3.6.1.2.1.1.5.0"
HR_STORAGE, HR_FIXED_DISK, HR_RAM = "1.3.6.1.2.1.25.2.3.1", "1.3.6.1.2.1.25.2.1.4", "1.3.6.1.2.1.25.2.1.2"
HR_PROCESSOR_LOAD = "1.3.6.1.2.1.25.3.3.1.2"
HR_UPTIME = "1.3.6.1.2.1.25.1.1.0"
UCD_MEM_TOTAL, UCD_MEM_FREE, UCD_MEM_BUFFER, UCD_MEM_CACHED = "1.3.6.1.4.1.2021.4.5.0", "1.3.6.1.4.1.2021.4.6.0", "1.3.6.1.4.1.2021.4.14.0", "1.3.6.1.4.1.2021.4.15.0"
UCD_CPU_IDLE, UCD_CPU = "1.3.6.1.4.1.2021.11.11.0", "1.3.6.1.4.1.2021.11"
UCD_CPU_RAW_IDLE, UCD_CPU_RAW_BUSY = (53, 54), (50, 51, 52, 56, 61, 64)  # Spalten der Rohzaehler: Leerlauf (inkl. Warten) und Arbeit
IF_DESCR, IF_STATUS, IF_HC_IN, IF_HC_OUT, IF_IN, IF_OUT = ("1.3.6.1.2.1.2.2.1.2", "1.3.6.1.2.1.2.2.1.8", "1.3.6.1.2.1.31.1.1.1.6", "1.3.6.1.2.1.31.1.1.1.10",
                                                         "1.3.6.1.2.1.2.2.1.10", "1.3.6.1.2.1.2.2.1.16")
SYNO_SYSTEM_TEMP, SYNO_DISK, SYNO_RAID = "1.3.6.1.4.1.6574.1.2.0", "1.3.6.1.4.1.6574.2.1.1", "1.3.6.1.4.1.6574.3.1.1"
QNAP_SYSTEM_TEMP, QNAP_HDD = "1.3.6.1.4.1.24681.1.2.6.0", "1.3.6.1.4.1.24681.1.2.11.1"
SYNO_DISK_STATUS, SYNO_RAID_STATUS = {1: "ok", 2: "ok", 3: "warn"}, {1: "ok", 11: "warn", 12: "fail"}
QNAP_DISK_STATUS = {0: "ok", -4: "unbekannt", -5: None, -6: "warn", -9: "fail"}  # None = Schacht leer


def ganz(wert):
    """SNMP-Werte kommen vom Geraet; nur echte Ganzzahlen werden gerechnet (sonst 0), damit Texte nicht vervielfacht werden."""
    return wert if isinstance(wert, int) and not isinstance(wert, bool) else 0


def snmp_storage(rows):
    volumes, ram = [], None
    for eintrag in snmp_client.table(rows, HR_STORAGE).values():
        einheit, gesamt, belegt = (ganz(eintrag.get(k)) for k in ("4", "5", "6"))
        gesamt, belegt = gesamt * einheit, belegt * einheit
        name = snmp_client.text(eintrag.get("3", b""))
        if eintrag.get("2") == HR_FIXED_DISK and gesamt > 0 and not name.startswith(SKIP_VOLUMES):
            volumes.append({"name": name, "gesamt": gesamt, "belegt": belegt})
        elif eintrag.get("2") == HR_RAM and gesamt > 0:
            ram = {"gesamt": gesamt, "belegt": belegt}
    if len(volumes) > 1:
        volumes = [v for v in volumes if v["name"] != "/"] or volumes
    return volumes, ram


def ucd_cpu(abfrage, idle_prozent, state):
    """CPU aus den Rohzaehlern von net-snmp (neuere Versionen liefern keinen Prozentwert mehr), als Unterschied zweier Messungen."""
    spalten = {int(o.rsplit(".", 2)[1]): v for o, v in abfrage(UCD_CPU) if o.endswith(".0")}
    idle = sum(spalten.get(c, 0) for c in UCD_CPU_RAW_IDLE)
    gesamt = idle + sum(spalten.get(c, 0) for c in UCD_CPU_RAW_BUSY)
    alt = state.get("ucdcpu")
    state["ucdcpu"] = (gesamt, idle)
    if alt and gesamt > alt[0]:
        return round(100 * (1 - (idle - alt[1]) / (gesamt - alt[0])), 1)
    return None if idle_prozent is None else 100 - idle_prozent


def snmp_column(abfrage, oid):
    """{zeilenindex: wert} einer Tabellenspalte."""
    return {o.rsplit(".", 1)[1]: v for o, v in abfrage(oid)}


def snmp_interfaces(abfrage, wahl):
    """Summe der Zaehler der gewaehlten (oder aller aktiven echten) Schnittstellen: (rx, tx, wrap)."""
    namen = {i: snmp_client.text(v) for i, v in snmp_column(abfrage, IF_DESCR).items()}
    oper = snmp_column(abfrage, IF_STATUS)
    gewaehlt = [n.strip() for n in wahl.split(",") if n.strip()]
    nutzbar = [i for i, n in namen.items() if (n in gewaehlt if gewaehlt else oper.get(i) == 1 and not n.startswith(SKIP_IF))]
    ein = snmp_column(abfrage, IF_HC_IN)
    if ein:
        aus, wrap = snmp_column(abfrage, IF_HC_OUT), 0
    else:
        ein, aus, wrap = snmp_column(abfrage, IF_IN), snmp_column(abfrage, IF_OUT), 1 << 32
    return sum(ein.get(i, 0) for i in nutzbar), sum(aus.get(i, 0) for i in nutzbar), wrap


def synology_snmp(abfrage):
    platten = [{"name": snmp_client.text(e.get("2", b"")), "modell": snmp_client.text(e.get("3", b"")), "temp": e.get("6"), "status": SYNO_DISK_STATUS.get(e.get("5"), "fail"), "kapazitaet": 0}
               for e in snmp_client.table(abfrage(SYNO_DISK), SYNO_DISK).values()]
    raid = [{"name": snmp_client.text(e.get("2", b"")), "status": SYNO_RAID_STATUS.get(e.get("3"), "warn"), "detail": f"Status {e.get('3')}"}
            for e in snmp_client.table(abfrage(SYNO_RAID), SYNO_RAID).values()]
    return platten, raid


def erste_zahl(text):
    m = re.search(r"-?\d+", snmp_client.text(text))
    return int(m.group(0)) if m else None


def qnap_snmp(abfrage):
    platten = []
    for e in snmp_client.table(abfrage(QNAP_HDD), QNAP_HDD).values():
        stand = QNAP_DISK_STATUS.get(e.get("4"), "warn")
        if stand is not None:
            platten.append({"name": snmp_client.text(e.get("2", b"")), "modell": snmp_client.text(e.get("5", b"")), "temp": erste_zahl(e.get("3", b"")), "status": stand, "kapazitaet": 0})
    return platten


@typ("snmp", "SNMP (fast jedes NAS und jeder Router)", "NAS und Router", [HOST, port(161), feld("community", "Community", "text", "public", "Nur Lesen reicht", True),
     feld("version", "Version", "auswahl", "2c", "", False, ["2c", "1"]), feld("schnittstelle", "Schnittstellen für den Datenverkehr", "text", "", "Namen mit Komma, leer = alle aktiven (z. B. eth0)")],
     "SNMP v1 und v2c. Am NAS oder Router aktivieren (Synology: Systemsteuerung → Terminal & SNMP, QNAP: Systemsteuerung → Netzwerk → SNMP). Synology und QNAP liefern zusätzlich Plattentemperaturen und RAID-Status.")
def snmp(src, state):
    host, comm, p = src["host"], src.get("community") or "public", int(src.get("port") or 161)
    ver = 0 if str(src.get("version")) == "1" else 1
    host = pruefe_ziel(host)
    abfrage = lambda oid: snmp_client.walk(host, comm, oid, p, version=ver)
    kopf = snmp_client.get(host, comm, [SYS_DESCR, SYS_UPTIME, SYS_NAME, HR_UPTIME], p, version=ver)
    if not kopf:
        raise NetzFehler("SNMP antwortet nicht")
    volumes, ram = snmp_storage(abfrage(HR_STORAGE))
    ucd = snmp_client.get(host, comm, [UCD_MEM_TOTAL, UCD_MEM_FREE, UCD_MEM_BUFFER, UCD_MEM_CACHED, UCD_CPU_IDLE, SYNO_SYSTEM_TEMP, QNAP_SYSTEM_TEMP], p, version=ver)
    ucd = {k: v for k, v in ucd.items() if isinstance(v, int) or k == QNAP_SYSTEM_TEMP}
    if UCD_MEM_TOTAL in ucd and UCD_MEM_FREE in ucd:  # frei ohne Puffer und Cache, die das System bei Bedarf hergibt
        belegt = ucd[UCD_MEM_TOTAL] - ucd[UCD_MEM_FREE] - ucd.get(UCD_MEM_BUFFER, 0) - ucd.get(UCD_MEM_CACHED, 0)
        ram = {"gesamt": ucd[UCD_MEM_TOTAL] * 1024, "belegt": max(0, belegt) * 1024}
    lasten = [v for _, v in abfrage(HR_PROCESSOR_LOAD)]
    cpu = round(sum(lasten) / len(lasten), 1) if lasten else ucd_cpu(abfrage, ucd.get(UCD_CPU_IDLE), state)
    rx, tx, wrap = snmp_interfaces(abfrage, str(src.get("schnittstelle", "")))
    temp = ucd.get(SYNO_SYSTEM_TEMP, erste_zahl(ucd[QNAP_SYSTEM_TEMP]) if QNAP_SYSTEM_TEMP in ucd else None)
    beschreibung = snmp_client.text(kopf.get(SYS_DESCR, b""))
    platten, raid = [], []
    if "synology" in beschreibung.lower() or SYNO_SYSTEM_TEMP in ucd:
        platten, raid = synology_snmp(abfrage)
    elif "qnap" in beschreibung.lower() or QNAP_SYSTEM_TEMP in ucd:
        platten = qnap_snmp(abfrage)
    return {"modell": beschreibung[:80], "uptime": ganz(kopf.get(HR_UPTIME, kopf.get(SYS_UPTIME, 0))) // 100, "cpu": cpu, "ram": ram, "temp": temp, "volumes": volumes, "platten": platten, "raid": raid,
            "traffic": counter_rate(state, "if", time.time(), rx, tx, wrap)}


# --- Synology DSM -----------------------------------------------------------------------
@typ("synology", "Synology DiskStation (DSM)", "NAS und Router", [HOST, port(""), HTTPS, USER, PASSWORT, ZERTIFIKAT],
     "Eigenen Benutzer in der Gruppe administrators anlegen, ohne Zwei-Faktor. Port 5000 (HTTP) oder 5001 (HTTPS), leer = Standard. Ohne Admin-Rechte hilft SNMP.")
def synology(src, state):
    b = basis({**src, "port": src.get("port") or (5001 if src.get("https") else 5000)}, 5000)
    pruefen = bool(src.get("ssl_pruefen"))

    def api(pfad, **felder):
        _, body = anfrage(f"{b}/webapi/{pfad}", "POST", {"Content-Type": "application/x-www-form-urlencoded"}, urllib.parse.urlencode(felder), pruefen=pruefen)
        antwort = json.loads(body)
        if not antwort.get("success"):
            raise NetzFehler(f"DSM-Fehler {antwort.get('error', {}).get('code')} bei {felder.get('api')}")
        return antwort.get("data") or {}

    sid = api("auth.cgi", api="SYNO.API.Auth", version=6, method="login", account=src.get("benutzer", ""), passwd=src.get("passwort", ""), session="Core", format="sid")["sid"]
    try:
        speicher = api("entry.cgi", api="SYNO.Storage.CGI.Storage", version=1, method="load_info", _sid=sid)
        system = api("entry.cgi", api="SYNO.Core.System", version=1, method="info", _sid=sid)
        last = api("entry.cgi", api="SYNO.Core.System.Utilization", version=1, method="get", _sid=sid)
    finally:
        api("auth.cgi", api="SYNO.API.Auth", version=6, method="logout", session="Core", _sid=sid)
    volumes = [{"name": v.get("vol_path") or v.get("id", "?"), "gesamt": int(v["size"]["total"]), "belegt": int(v["size"]["used"])} for v in speicher.get("volumes", []) if v.get("size")]
    platten = [{"name": d.get("name", ""), "modell": d.get("model", ""), "temp": d.get("temp"), "kapazitaet": int(d.get("size_total") or 0),
                "status": "ok" if d.get("status") == "normal" and d.get("smart_status") in (None, "normal") else "warn" if d.get("status") == "normal" else "fail"} for d in speicher.get("disks", [])]
    raid = [{"name": p.get("id", "?"), "status": ampel(p.get("status"), ok=("normal",), warn=("background", "scrubbing", "degrade")), "detail": str(p.get("status"))} for p in speicher.get("storagePools", [])]
    cpu, mem = last.get("cpu") or {}, last.get("memory") or {}
    netz = next((n for n in last.get("network", []) if n.get("device") == "total"), None)
    ram = {"gesamt": int(mem["total_real"]) * 1024, "belegt": int(mem["total_real"] - mem.get("avail_real", 0)) * 1024} if mem.get("total_real") else None
    return {"modell": system.get("model", ""), "version": system.get("firmware_ver", ""), "temp": system.get("sys_temp"), "volumes": volumes, "platten": platten, "raid": raid, "ram": ram,
            "cpu": (cpu.get("user_load", 0) + cpu.get("system_load", 0) + cpu.get("other_load", 0)) if cpu else None, "traffic": {"rx": netz["rx"], "tx": netz["tx"]} if netz else None,
            "werte": [["Betriebszeit", str(system["up_time"])]] if system.get("up_time") else []}


# --- TrueNAS ----------------------------------------------------------------------------
@typ("truenas", "TrueNAS (CORE und SCALE)", "NAS und Router", [HOST, port(""), feld("https", "HTTPS", "schalter", True), feld("api_key", "API-Schlüssel", "text", "", "Oben rechts → API-Schlüssel", True), ZERTIFIKAT],
     "REST-Schnittstelle v2.0 mit API-Schlüssel (Bearer). Neuere TrueNAS-Versionen stellen REST auf WebSocket um; dann SNMP oder SSH nutzen.")
def truenas(src, state):
    b = basis({**src, "port": src.get("port") or (443 if src.get("https") else 80)}, 443)
    kopf, pruefen = {"Authorization": f"Bearer {src.get('api_key', '')}"}, bool(src.get("ssl_pruefen"))
    get = lambda pfad: json_von(f"{b}/api/v2.0/{pfad}", kopf=kopf, pruefen=pruefen)
    info, pools, alerts = get("system/info"), get("pool"), get("alert/list")
    volumes = [{"name": p["name"], "gesamt": p.get("size") or 0, "belegt": p.get("allocated") or 0} for p in pools]
    raid = [{"name": p["name"], "status": "ok" if p.get("healthy") else "warn", "detail": str(p.get("status"))} for p in pools]
    offen = [a.get("formatted", "")[:140] for a in alerts if a.get("level") in BAD_LEVELS and not a.get("dismissed")]
    return {"modell": info.get("system_product") or info.get("hostname", ""), "version": info.get("version", ""), "uptime": int(info.get("uptime_seconds") or 0), "last": info.get("loadavg"),
            "kerne": info.get("cores"), "volumes": volumes, "raid": raid, "hinweise": offen[:MAX_ALERTS]}


# --- Fritz!Box (TR-064) und UPnP --------------------------------------------------------
TR064_VERBINDUNG = (("/upnp/control/wanipconnection1", "urn:dslforum-org:service:WANIPConnection:1"), ("/upnp/control/wanpppconn1", "urn:dslforum-org:service:WANPPPConnection:1"))
FRITZ_IF = ("/upnp/control/wancommonifconfig1", "urn:dslforum-org:service:WANCommonInterfaceConfig:1")
FRITZ_HOSTS = ("/upnp/control/hosts", "urn:dslforum-org:service:Hosts:1")
FRITZ_INFO = ("/upnp/control/deviceinfo", "urn:dslforum-org:service:DeviceInfo:1")


def soap(b, pfad, dienst, aktion, argumente="", opener=None):
    umschlag = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                f'<s:Body><u:{aktion} xmlns:u="{dienst}">{argumente}</u:{aktion}></s:Body></s:Envelope>')
    status, body = anfrage(f"{b}{pfad}", "POST", {"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{dienst}#{aktion}"'}, umschlag, opener=opener)
    if status >= 400:
        raise NetzFehler(f"{aktion}: HTTP {status}")
    return {el.tag.rsplit("}", 1)[-1]: (el.text or "") for el in ET.fromstring(body).iter() if el.tag.rsplit("}", 1)[-1].startswith("New")}


def digest_opener(user, passwort):
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, "http://", user, passwort)
    return sicherer_opener(urllib.request.HTTPDigestAuthHandler(manager))


def fritz_clients(b, opener):
    anzahl = int(soap(b, *FRITZ_HOSTS, "GetHostNumberOfEntries", opener=opener).get("NewHostNumberOfEntries") or 0)
    clients = []
    for i in range(min(anzahl, MAX_FRITZ_HOSTS)):
        h = soap(b, *FRITZ_HOSTS, "GetGenericHostEntry", f"<NewIndex>{i}</NewIndex>", opener=opener)
        if h.get("NewActive") == "1":
            clients.append({"mac": h.get("NewMACAddress", ""), "ip": h.get("NewIPAddress", ""), "name": h.get("NewHostName", ""), "verbindung": "wlan" if "802.11" in h.get("NewInterfaceType", "") else "lan"})
    return clients


@typ("fritzbox", "Fritz!Box (TR-064)", "NAS und Router", [feld("host", "Adresse", "text", "fritz.box"), port(49000), USER, PASSWORT],
     "In der Fritz!Box: Heimnetz → Netzwerk → Netzwerkeinstellungen → „Zugriff für Anwendungen zulassen“ (TR-064) einschalten und einen Benutzer mit Rechten anlegen. Ohne Anmeldung gibt es Datenrate und öffentliche IP, mit Anmeldung auch Modell und Geräteliste.")
def fritzbox(src, state):
    b = f"http://{src['host']}:{int(src.get('port') or 49000)}"
    opener = digest_opener(src["benutzer"], src.get("passwort", "")) if src.get("benutzer") else None
    verbindung, fehler = None, None
    for pfad, dienst in TR064_VERBINDUNG:
        try:
            verbindung = (soap(b, pfad, dienst, "GetStatusInfo"), soap(b, pfad, dienst, "GetExternalIPAddress"))
            break
        except NetzFehler as e:
            fehler = e
    if verbindung is None:
        raise fehler
    zaehler, link = soap(b, *FRITZ_IF, "GetAddonInfos"), soap(b, *FRITZ_IF, "GetCommonLinkProperties")
    wan = {"verbunden": verbindung[0].get("NewConnectionStatus") == "Connected", "ip": verbindung[1].get("NewExternalIPAddress", ""),
           "rx": int(zaehler.get("NewByteReceiveRate") or 0), "tx": int(zaehler.get("NewByteSendRate") or 0),
           "down_max": int(link.get("NewLayer1DownstreamMaxBitRate") or 0), "up_max": int(link.get("NewLayer1UpstreamMaxBitRate") or 0)}
    daten = {"wan": wan, "hinweise": []}
    if opener:
        try:
            info = soap(b, *FRITZ_INFO, "GetInfo", opener=opener)
            daten.update({"modell": info.get("NewModelName", ""), "version": info.get("NewSoftwareVersion", ""), "uptime": int(info.get("NewUpTime") or 0), "clients": fritz_clients(b, opener)})
        except NetzFehler as e:
            daten["hinweise"].append(f"Geräteliste nicht abrufbar: {e}")
    return daten


def ssdp_location(host, timeout=2.0):
    """Fragt ein Geraet direkt per SSDP nach seiner UPnP-Beschreibung (LOCATION)."""
    suche = b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 1\r\nST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n'
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(suche, (host, 1900))
        try:
            antwort = s.recv(4096).decode("utf-8", "replace")
        except socket.timeout:
            raise NetzFehler("UPnP antwortet nicht (UPnP am Router aktivieren)") from None
    m = re.search(r"^location:\s*(\S+)", antwort, re.I | re.M)
    if not m:
        raise NetzFehler("UPnP: keine Beschreibung gefunden")
    return m.group(1)


def upnp_dienste(beschreibung_url):
    """{serviceType: steuer_url} aus der UPnP-Beschreibung."""
    _, body = anfrage(beschreibung_url)
    dienste = {}
    for el in ET.fromstring(body).iter():
        if el.tag.rsplit("}", 1)[-1] == "service":
            felder = {c.tag.rsplit("}", 1)[-1]: (c.text or "").strip() for c in el}
            dienste[felder.get("serviceType", "")] = urllib.parse.urljoin(beschreibung_url, felder.get("controlURL", ""))
    return dienste


@typ("upnp", "Router per UPnP (jeder Router mit UPnP)", "NAS und Router", [HOST],
     "UPnP am Router einschalten. Liefert öffentliche IP, Verbindungsstatus, Datenrate und Leitungsgeschwindigkeit. Nur im selben Netz wie der Router erreichbar (Heimnetz oder Agent).")
def upnp(src, state):
    router = pruefe_ziel(src["host"])
    ort = ssdp_location(router)
    if urllib.parse.urlsplit(ort).hostname != router:
        raise NetzFehler("UPnP: Beschreibung liegt nicht auf dem Router selbst")
    dienste = {t: u for t, u in upnp_dienste(ort).items() if urllib.parse.urlsplit(u).hostname == router}
    common = next(((t, u) for t, u in dienste.items() if "WANCommonInterfaceConfig" in t), None)
    verb = next(((t, u) for t, u in dienste.items() if "WANIPConnection" in t or "WANPPPConnection" in t), None)
    if not common:
        raise NetzFehler("Router bietet keine Datenrate über UPnP an")

    def aufruf(dienst, aktion):
        teile = urllib.parse.urlsplit(dienst[1])
        return soap(f"{teile.scheme}://{teile.netloc}", teile.path, dienst[0], aktion)

    rx = int(aufruf(common, "GetTotalBytesReceived").get("NewTotalBytesReceived") or 0)
    tx = int(aufruf(common, "GetTotalBytesSent").get("NewTotalBytesSent") or 0)
    link = aufruf(common, "GetCommonLinkProperties")
    wan = {"down_max": int(link.get("NewLayer1DownstreamMaxBitRate") or 0), "up_max": int(link.get("NewLayer1UpstreamMaxBitRate") or 0),
           "verbunden": link.get("NewPhysicalLinkStatus") in ("Up", "Available"), **(counter_rate(state, "upnp", time.time(), rx, tx, 1 << 32) or {})}
    if verb:
        wan["ip"] = aufruf(verb, "GetExternalIPAddress").get("NewExternalIPAddress", "")
    return {"wan": wan}


# --- UniFi ------------------------------------------------------------------------------
@typ("unifi", "UniFi (Controller und UniFi OS)", "NAS und Router", [HOST, port(443), feld("https", "HTTPS", "schalter", True), USER, PASSWORT, feld("seite", "Site", "text", "default"),
     feld("variante", "Variante", "auswahl", "unifios", "", False, ["unifios", "klassisch"])],
     "UniFi OS (Dream Machine, Cloud Key Gen2+) oder klassischer Controller auf Port 8443. Einen lokalen Benutzer nur mit Leserechten anlegen.")
def unifi(src, state):
    b, site = basis({**src, "https": True}, 443), src.get("seite") or "default"
    klassisch = src.get("variante") == "klassisch"
    praefix = "" if klassisch else "/proxy/network"
    opener = sicherer_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    status, _ = anfrage(f"{b}{'/api/login' if klassisch else '/api/auth/login'}", "POST", daten={"username": src.get("benutzer", ""), "password": src.get("passwort", "")}, opener=opener)
    if status >= 400:
        raise NetzFehler(f"UniFi-Anmeldung abgelehnt (HTTP {status})")
    get = lambda pfad: json.loads(anfrage(f"{b}{praefix}/api/s/{site}/{pfad}", opener=opener)[1]).get("data", [])
    stationen, geraete, gesundheit = get("stat/sta"), get("stat/device"), get("stat/health")
    clients = [{"mac": c.get("mac", ""), "ip": c.get("ip", ""), "name": c.get("name") or c.get("hostname", ""), "verbindung": "lan" if c.get("is_wired") else "wlan", "signal": c.get("signal"), "netz": c.get("essid", "")} for c in stationen]
    w = next((h for h in gesundheit if h.get("subsystem") == "wan"), None)
    wan = {"verbunden": w.get("status") == "ok", "ip": w.get("wan_ip", ""), "rx": int(w.get("rx_bytes-r") or 0), "tx": int(w.get("tx_bytes-r") or 0)} if w else None
    dienste = [{"name": g.get("name") or g.get("mac", ""), "ok": g.get("state") == 1, "detail": f"{g.get('model', '')} · {g.get('num_sta', 0)} Clients · {g.get('version', '')}"} for g in geraete]
    return {"clients": clients, "wan": wan, "dienste": dienste}


# --- OpenWrt ----------------------------------------------------------------------------
@typ("openwrt", "OpenWrt (ubus)", "NAS und Router", [HOST, port(80), HTTPS, feld("benutzer", "Benutzer", "text", "root"), PASSWORT],
     "Auf dem Router: Pakete uhttpd-mod-ubus und rpcd (bei LuCI schon da). Für die Geräteliste zusätzlich luci-mod-rpc.")
def openwrt(src, state):
    b, zaehler = basis(src, 80), [0]

    def ubus(sitzung, objekt, methode, parameter=None):
        zaehler[0] += 1
        _, body = anfrage(f"{b}/ubus", "POST", daten={"jsonrpc": "2.0", "id": zaehler[0], "method": "call", "params": [sitzung, objekt, methode, parameter or {}]})
        antwort = json.loads(body)
        ergebnis = antwort.get("result")
        if not ergebnis or ergebnis[0] != 0:
            raise NetzFehler(f"ubus {objekt}.{methode}: Code {ergebnis[0] if ergebnis else antwort.get('error')}")
        return ergebnis[1] if len(ergebnis) > 1 else {}

    sid = ubus("0" * 32, "session", "login", {"username": src.get("benutzer") or "root", "password": src.get("passwort", "")})["ubus_rpc_session"]
    system, board = ubus(sid, "system", "info"), ubus(sid, "system", "board")
    m = system.get("memory", {})
    ram = {"gesamt": m["total"], "belegt": m["total"] - m.get("free", 0) - m.get("buffered", 0) - m.get("cached", 0)} if m.get("total") else None
    daten = {"modell": board.get("model", ""), "version": (board.get("release") or {}).get("description", ""), "uptime": system.get("uptime"), "ram": ram,
             "last": [round(x / 65536, 2) for x in system.get("load", [])], "hinweise": []}
    try:
        wan_if = next((i for i in ubus(sid, "network.interface", "dump").get("interface", []) if i.get("interface", "").startswith("wan")), None)
        if wan_if:
            statistik = ubus(sid, "network.device", "status", {"name": wan_if.get("l3_device") or wan_if.get("device")}).get("statistics", {})
            daten["wan"] = {"verbunden": bool(wan_if.get("up")), "ip": (wan_if.get("ipv4-address") or [{}])[0].get("address", ""),
                            **(counter_rate(state, "openwrt", time.time(), statistik.get("rx_bytes", 0), statistik.get("tx_bytes", 0)) or {})}
    except NetzFehler as e:
        daten["hinweise"].append(f"WAN nicht lesbar: {e}")
    try:
        leases = ubus(sid, "luci-rpc", "getDHCPLeases").get("dhcp_leases", [])
        daten["clients"] = [{"mac": x.get("macaddr", ""), "ip": x.get("ipaddr", ""), "name": x.get("hostname", ""), "verbindung": "lan"} for x in leases]
    except NetzFehler as e:
        daten["hinweise"].append(f"Geräteliste nicht lesbar: {e}")
    return daten


# --- MikroTik RouterOS 7 ----------------------------------------------------------------
def routeros_uptime(text):
    return sum(int(n) * UPTIME_TEILE[u] for n, u in re.findall(r"(\d+)([wdhms])", text))


@typ("mikrotik", "MikroTik RouterOS 7 (REST)", "NAS und Router", [HOST, port(""), feld("https", "HTTPS", "schalter", True), feld("benutzer", "Benutzer", "text", "admin"), PASSWORT,
     feld("schnittstelle", "WAN-Schnittstelle", "text", "ether1"), ZERTIFIKAT], "RouterOS ab Version 7.1: Dienst „www-ssl“ (oder „www“) einschalten. Ein Benutzer mit Gruppe „read“ genügt.")
def mikrotik(src, state):
    b = basis({**src, "port": src.get("port") or (443 if src.get("https") else 80)}, 443)
    kopf, pruefen = basic(src.get("benutzer", ""), src.get("passwort", "")), bool(src.get("ssl_pruefen"))
    get = lambda pfad: json_von(f"{b}/rest/{pfad}", kopf=kopf, pruefen=pruefen)
    res, schnittstellen, leases = get("system/resource"), get("interface"), get("ip/dhcp-server/lease")
    gesamt, frei = int(res.get("total-memory", 0)), int(res.get("free-memory", 0))
    wan = next((i for i in schnittstellen if i.get("name") == (src.get("schnittstelle") or "ether1")), None)
    daten = {"modell": res.get("board-name", ""), "version": res.get("version", ""), "uptime": routeros_uptime(res.get("uptime", "")), "cpu": float(res.get("cpu-load", 0)),
             "ram": {"gesamt": gesamt, "belegt": gesamt - frei} if gesamt else None,
             "clients": [{"mac": x.get("mac-address", ""), "ip": x.get("active-address") or x.get("address", ""), "name": x.get("host-name", ""), "verbindung": "lan"} for x in leases if x.get("status") == "bound"]}
    if wan:
        daten["wan"] = {"verbunden": wan.get("running") in (True, "true"), "ip": "", **(counter_rate(state, "mikrotik", time.time(), int(wan.get("rx-byte", 0)), int(wan.get("tx-byte", 0)), 1 << 64) or {})}
    return daten


# --- OPNsense ---------------------------------------------------------------------------
@typ("opnsense", "OPNsense (API)", "NAS und Router", [HOST, port(""), feld("https", "HTTPS", "schalter", True), feld("api_key", "API-Schlüssel", "text", "", "System → Zugang → Benutzer → API-Schlüssel", True),
     feld("api_secret", "API-Geheimnis", "text", "", "", True), ZERTIFIKAT], "pfSense hat keine offizielle API: dort SNMP oder SSH nutzen. OPNsense: Gerätetabelle (ARP) und Datenverkehr der Schnittstellen.")
def opnsense(src, state):
    b = basis({**src, "port": src.get("port") or (443 if src.get("https") else 80)}, 443)
    kopf, pruefen = basic(src.get("api_key", ""), src.get("api_secret", "")), bool(src.get("ssl_pruefen"))
    arp = json_von(f"{b}/api/diagnostics/interface/getArp", kopf=kopf, pruefen=pruefen)
    daten = {"clients": [{"mac": e.get("mac", ""), "ip": e.get("ip", ""), "name": e.get("hostname", ""), "verbindung": "lan"} for e in arp if isinstance(e, dict)], "hinweise": []}
    try:
        stand = json_von(f"{b}/api/diagnostics/traffic/interface", kopf=kopf, pruefen=pruefen).get("interfaces", {})
        echte = [i for n, i in stand.items() if not n.startswith(SKIP_IF)]
        daten["traffic"] = counter_rate(state, "opn", time.time(), sum(int(i.get("bytes received", 0)) for i in echte), sum(int(i.get("bytes transmitted", 0)) for i in echte))
    except NetzFehler as e:
        daten["hinweise"].append(f"Datenverkehr nicht lesbar: {e}")
    return daten
