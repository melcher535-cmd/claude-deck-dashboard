"""HTTP-Helfer fuer das Netzwerk-Modul: Zielpruefung (nur Heimnetz-Adressen) und Anfragen mit Zeitlimit und Groessenlimit.
Der Name wird genau einmal aufgeloest und geprueft, die Verbindung geht an diese Adresse (kein DNS-Rebinding). Weiterleitungen werden nicht verfolgt."""
import http.client
import ipaddress
import json
import socket
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

TIMEOUT = 4
MAX_BYTES = 2_000_000
CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale und Mobilfunk-CGNAT
HEIMNETZE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8", "fc00::/7", "::1/128"))
USER_AGENT = "claude-deck"
SCHEMAS = ("http", "https")


class NetzFehler(Exception):
    """Fehler beim Abfragen eines Geraets, mit Text fuer die Oberflaeche."""


def adresse_erlaubt(adresse, oeffentlich=False):
    """Erlaubt: Heimnetz, Loopback, Tailscale. Nie link-local (Cloud-Metadaten), unspezifiziert oder Multicast. oeffentlich=True erlaubt zusaetzlich globale Adressen."""
    adresse = getattr(adresse, "ipv4_mapped", None) or adresse
    if adresse.is_link_local or adresse.is_unspecified or adresse.is_multicast:
        return False
    if any(adresse in netz for netz in HEIMNETZE):
        return True
    return oeffentlich and adresse.is_global


def adressen(host):
    try:
        return [ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]
    except (socket.gaierror, ValueError, UnicodeError) as e:
        raise NetzFehler(f"Name nicht auflösbar: {host}") from e


def pruefe_ziel(host, oeffentlich=False):
    """Loest host einmal auf, prueft ALLE Adressen und gibt die erste als Text zurueck. Mit dieser Adresse (nicht mit dem Namen) weiterverbinden."""
    if not host:
        raise NetzFehler("Adresse fehlt")
    gefunden = adressen(host)
    if not all(adresse_erlaubt(a, oeffentlich) for a in gefunden):
        raise NetzFehler(f"{host}: nur Adressen im Heimnetz, Loopback oder Tailscale erlaubt")
    erste = gefunden[0]
    return str(getattr(erste, "ipv4_mapped", None) or erste)


def ziel_erlaubt(host):
    try:
        pruefe_ziel(host)
    except NetzFehler:
        return False
    return True


def ssl_kontext(pruefen):
    ctx = ssl.create_default_context()
    if not pruefen:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # NAS und Router bringen fast immer selbst signierte Zertifikate mit
    return ctx


class _Http(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.create_connection((pruefe_ziel(self.host), self.port), self.timeout, self.source_address)


class _Https(http.client.HTTPSConnection):
    def connect(self):
        sock = socket.create_connection((pruefe_ziel(self.host), self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _HttpHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_Http, req)


class _HttpsHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_Https, req, context=self._context)


class _KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # ein Geraet darf uns nicht auf andere Adressen schicken (und Zugangsdaten mitnehmen)


def sicherer_opener(*handler, pruefen=False):
    """Opener nur fuer http und https, mit Adresspruefung, ohne Weiterleitungen, ohne file:, ftp: und Proxy. handler: z. B. Cookie- oder Digest-Handler."""
    opener = urllib.request.OpenerDirector()
    for h in (urllib.request.HTTPDefaultErrorHandler(), _KeineWeiterleitung(), urllib.request.HTTPErrorProcessor(), _HttpHandler(), _HttpsHandler(context=ssl_kontext(pruefen)), *handler):
        opener.add_handler(h)
    return opener


def anfrage(url, methode="GET", kopf=None, daten=None, pruefen=False, timeout=TIMEOUT, opener=None):
    """(status, body) einer Anfrage. daten: dict (JSON), bytes oder str. Auch 4xx/5xx kommen als Status zurueck."""
    teile = urlsplit(url)
    if teile.scheme not in SCHEMAS:
        raise NetzFehler(f"nur http und https erlaubt: {url}")
    pruefe_ziel(teile.hostname)
    headers = {"User-Agent": USER_AGENT, **(kopf or {})}
    body = daten
    if isinstance(daten, dict):
        body = json.dumps(daten).encode()
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(daten, str):
        body = daten.encode()
    req = urllib.request.Request(url, data=body, headers=headers, method=methode)
    try:
        with (opener or sicherer_opener(pruefen=pruefen)).open(req, timeout=timeout) as r:
            return r.status, r.read(MAX_BYTES)
    except urllib.error.HTTPError as e:
        return e.code, e.read(MAX_BYTES)
    except (urllib.error.URLError, OSError, ssl.SSLError) as e:
        raise NetzFehler(f"{teile.hostname}: {getattr(e, 'reason', e)}") from e


def json_von(url, **kw):
    status, body = anfrage(url, **kw)
    if status >= 400:
        raise NetzFehler(f"HTTP {status} von {urlsplit(url).path}")
    try:
        return json.loads(body)
    except ValueError as e:
        raise NetzFehler(f"keine JSON-Antwort von {urlsplit(url).path}") from e


def basis(src, standardport):
    """Grundadresse einer Quelle aus host, port und https der Konfiguration."""
    schema = "https" if src.get("https") else "http"
    return f"{schema}://{src['host']}:{int(src.get('port') or standardport)}"
