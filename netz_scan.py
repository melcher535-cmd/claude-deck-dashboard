"""Geraete im Netz finden: Ping-Sweep, ARP, Namen, Hersteller, offene Ports und Typ-Erkennung. Dazu Wake-on-LAN und Tailscale-Status."""
import concurrent.futures
import ipaddress
import json
import re
import shutil
import socket
import subprocess

from netz_http import NetzFehler, anfrage

MAX_HOSTS = 1024
WORKERS = 64
PORT_TIMEOUT = 0.5
NAME_TIMEOUT = 1.5
PING_PORTS = (443, 80, 22, 445)
PROBE_PORTS = (22, 53, 80, 139, 443, 445, 548, 1900, 2049, 2283, 3000, 3493, 5000, 5001, 8006, 8080, 8096, 8123, 8443, 9000, 32400, 49000)
HTTP_PORTS = (80, 443, 5000, 5001, 8006, 8080, 8096, 8123, 8443, 3000, 2283, 9000, 32400)
TLS_PORTS = (443, 5001, 8006, 8443)
SKIP_INTERFACES = ("lo", "docker", "br-", "veth", "virbr", "tailscale")
WOL_PORT = 9

# Hersteller anhand der ersten drei MAC-Bytes. Bewusst klein: nur Eintraege, bei denen die Zuordnung sicher ist.
ESP = "Espressif (Shelly, Tasmota, ESP)"
OUI = {
    "001132": "Synology", "245EBE": "QNAP", "B827EB": "Raspberry Pi", "DCA632": "Raspberry Pi", "E45F01": "Raspberry Pi", "D83ADD": "Raspberry Pi",
    "245A4C": "Ubiquiti", "74ACB9": "Ubiquiti", "F09FC2": "Ubiquiti", "FCECDA": "Ubiquiti", "788A20": "Ubiquiti",
    "3CA62F": "AVM (Fritz!Box)", "7CFF4D": "AVM (Fritz!Box)", "C80E14": "AVM (Fritz!Box)", "2C91AB": "AVM (Fritz!Box)",
    "4C5E0C": "MikroTik", "E48D8C": "MikroTik", "6C3B6B": "MikroTik", "B869F4": "MikroTik",
    "246F28": ESP, "30AEA4": ESP, "3C71BF": ESP, "84CCA8": ESP, "A4CF12": ESP, "ECFABC": ESP,
}

# (Suchtext in der Seite, Adapter-Typ, Anzeigename). Reihenfolge: speziellere zuerst.
SIGNATUREN = (
    ("pi-hole", "pihole", "Pi-hole"), ("adguard home", "adguard", "AdGuard Home"), ("home assistant", "homeassistant", "Home Assistant"),
    ("immich", "immich", "Immich"), ("opnsense", "opnsense", "OPNsense"), ("routeros", "mikrotik", "MikroTik RouterOS"), ("mikrotik", "mikrotik", "MikroTik"),
    ("unifi", "unifi", "UniFi"), ("openwrt", "openwrt", "OpenWrt"), ("luci", "openwrt", "OpenWrt (LuCI)"), ("truenas", "truenas", "TrueNAS"),
    ("freenas", "truenas", "TrueNAS"), ("synology", "synology", "Synology DiskStation"), ("diskstation", "synology", "Synology DiskStation"),
    ("qnap", "snmp", "QNAP"), ("unraid", "ssh", "Unraid"), ("openmediavault", "ssh", "OpenMediaVault"), ("ugreen", "ssh", "UGREEN NAS"),
    ("ugos", "ssh", "UGREEN NAS"), ("proxmox", "ssh", "Proxmox"), ("fritz!box", "fritzbox", "Fritz!Box"), ("jellyfin", "http", "Jellyfin"),
    ("plex", "http", "Plex"), ("portainer", "http", "Portainer"), ("tasmota", "tasmota", "Tasmota"), ("shelly", "shelly", "Shelly"),
)


def local_subnets():
    """Netze der eigenen Schnittstellen (ohne Docker, Loopback und Tailscale)."""
    if not shutil.which("ip"):
        return []
    out = subprocess.run(["ip", "-o", "-4", "addr", "show", "scope", "global"], capture_output=True, text=True, timeout=5).stdout
    netze = []
    for zeile in out.splitlines():
        teile = zeile.split()
        if len(teile) > 3 and not teile[1].startswith(SKIP_INTERFACES) and ipaddress.ip_interface(teile[3]).ip.is_private and not ipaddress.ip_interface(teile[3]).ip.is_link_local:
            netze.append(str(ipaddress.ip_interface(teile[3]).network))
    return netze


def arp_table():
    """{ip: mac} der Nachbarn, die der Kernel kennt."""
    try:
        with open("/proc/net/arp", encoding="utf-8") as f:
            zeilen = [z.split() for z in f.read().splitlines()[1:]]
    except OSError:
        return {}
    return {z[0]: z[3].upper() for z in zeilen if len(z) >= 4 and z[3] != "00:00:00:00:00:00" and z[2] != "0x0"}


def tcp_offen(ip, port, timeout=PORT_TIMEOUT):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((ip, port)) == 0


def ping(ip, timeout=1):
    """True, wenn das Geraet auf Ping oder auf einen der ueblichen TCP-Ports antwortet (manche Geraete blocken Ping)."""
    if shutil.which("ping") and subprocess.run(["ping", "-c", "1", "-W", str(timeout), ip], capture_output=True, timeout=timeout + 3).returncode == 0:
        return True
    return any(tcp_offen(ip, p, 0.3) for p in PING_PORTS)


def offene_ports(ip):
    return [p for p in PROBE_PORTS if tcp_offen(ip, p)]


def hostname(ip):
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        try:
            return pool.submit(socket.gethostbyaddr, ip).result(NAME_TIMEOUT)[0]
        except (OSError, concurrent.futures.TimeoutError):
            return ""


def normal_mac(mac):
    return re.sub(r"[^0-9A-F]", "", str(mac).upper())


def mac_text(mac):
    roh = normal_mac(mac)
    return ":".join(roh[i:i + 2] for i in range(0, 12, 2)) if len(roh) == 12 else str(mac)


def zufaellige_mac(mac):
    """Handys wechseln die MAC pro WLAN ('privates WLAN'). Das zweite Bit des ersten Bytes zeigt das an."""
    roh = normal_mac(mac)
    return len(roh) == 12 and bool(int(roh[:2], 16) & 2)


def hersteller(mac):
    roh = normal_mac(mac)
    return "Zufällige MAC (privates WLAN)" if zufaellige_mac(roh) else OUI.get(roh[:6], "")


def erkennen(ip, ports):
    """Rate den Geraetetyp: (adapter_typ, anzeigename) oder ('', '')."""
    if 49000 in ports:
        try:
            status, body = anfrage(f"http://{ip}:49000/tr64desc.xml", timeout=2)
            if status == 200 and b"fritz" in body.lower():
                return "fritzbox", "Fritz!Box"
        except NetzFehler:
            pass
    if 3493 in ports:
        return "nut", "USV (NUT)"
    for port in (p for p in HTTP_PORTS if p in ports):
        try:
            _, body = anfrage(f"{'https' if port in TLS_PORTS else 'http'}://{ip}:{port}/", timeout=2)
        except NetzFehler:
            continue
        text = body[:65536].decode("utf-8", "replace").lower()
        for needle, typ, name in SIGNATUREN:
            if needle in text:
                return typ, name
    return ("ssh", "") if 22 in ports else ("", "")


def pruefen(ip, arp):
    """Ein Host: None wenn nicht erreichbar, sonst die Geraetezeile."""
    if not ping(ip):
        return None
    mac = arp.get(ip) or arp_table().get(ip, "")
    ports = offene_ports(ip)
    typ, name = erkennen(ip, ports)
    return {"ip": ip, "mac": mac, "name": hostname(ip), "hersteller": hersteller(mac) if mac else "", "ports": ports, "typ": typ, "typname": name}


def sweep(netz):
    """Alle erreichbaren Hosts eines Netzes (CIDR, hoechstens MAX_HOSTS Adressen)."""
    hosts = list(ipaddress.ip_network(netz, strict=False).hosts())
    if len(hosts) > MAX_HOSTS:
        raise NetzFehler(f"{netz} ist zu groß (höchstens {MAX_HOSTS} Adressen)")
    arp = arp_table()
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        gefunden = [g for g in pool.map(lambda h: pruefen(str(h), arp), hosts) if g]
    return sorted(gefunden, key=lambda g: ipaddress.ip_address(g["ip"]))


def wol(mac, broadcast="255.255.255.255"):
    """Magic Packet: sechsmal FF, dann sechzehnmal die MAC. Wirkt nur im selben Netz wie der Sender."""
    roh = normal_mac(mac)
    if len(roh) != 12:
        raise NetzFehler(f"ungültige MAC: {mac}")
    paket = b"\xff" * 6 + bytes.fromhex(roh) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(paket, (broadcast, WOL_PORT))


def tailscale_geraete():
    """(Geraete im Tailnet, Lage) laut `tailscale status --json`. Ohne Tailscale ist die Liste leer."""
    if not shutil.which("tailscale"):
        return [], {"installiert": False}
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=8)
        daten = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return [], {"installiert": True, "angemeldet": False}
    selbst = daten.get("Self") or {}
    geraete = [{"name": p.get("HostName", ""), "ip": (p.get("TailscaleIPs") or [""])[0], "os": p.get("OS", ""), "online": bool(p.get("Online")),
                "routen": p.get("PrimaryRoutes") or [], "exit": bool(p.get("ExitNodeOption"))} for p in (daten.get("Peer") or {}).values()]
    return geraete, {"installiert": True, "angemeldet": daten.get("BackendState") == "Running", "name": selbst.get("HostName", ""), "ip": (selbst.get("TailscaleIPs") or [""])[0]}
