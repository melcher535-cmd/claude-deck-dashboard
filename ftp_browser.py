"""FTP-Dateibrowser: gespeicherte Verbindungen ansehen, Dateien laden und hochladen, Ordner anlegen, loeschen.
Jede Anfrage baut ihre eigene Verbindung auf (nur Standardbibliothek). Gespeichert werden nur Host, Port, Benutzer und Modus.
Das Kennwort bleibt im Speicher und ist nach einem Neustart des Dashboards weg. Verbunden wird nur mit gespeicherten Verbindungen."""
import base64
import contextlib
import ftplib
import io
import posixpath
import re
import ssl

from storage import read_json, write_json

NAME = re.compile(r"^[\w .-]{1,40}$")
HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
LEAF = re.compile(r"^[^/\r\n\0]{1,200}$")
UNIX_LIST = re.compile(r"^([d-])[rwxsStT-]{9}[+.@]?\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\w{3}\s+\d+\s+[\d:]+\s+(.+)$")
MODES = ("tls", "tls-selbst", "klartext")
TIMEOUT = 15
MAX_DOWNLOAD, MAX_UPLOAD, MAX_PASSWORD = 50 * 2**20, 8 * 2**20, 200


def open_ftp(profile, password):
    """Verbindet und meldet an. Standard ist explizites TLS mit geprueftem Zertifikat."""
    if profile["modus"] == "klartext":
        ftp = ftplib.FTP(timeout=TIMEOUT, encoding="utf-8")
    else:
        context = ssl.create_default_context()
        if profile["modus"] == "tls-selbst":
            context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
        ftp = ftplib.FTP_TLS(context=context, timeout=TIMEOUT, encoding="utf-8")
    try:
        ftp.connect(profile["host"], profile["port"])
        ftp.login(profile["user"], password)
        if profile["modus"] != "klartext":
            ftp.prot_p()
    except BaseException:
        ftp.close()
        raise
    return ftp


def clean_path(path):
    return posixpath.normpath("/" + str(path).lstrip("/"))  # normpath laesst genau zwei fuehrende Schraegstriche stehen


def leaf(value):
    if not LEAF.match(value) or value in (".", ".."):
        raise ValueError("Name ungueltig")
    return value


def list_dir(ftp, path):
    """Ordner zuerst, dann nach Name. MLSD, sonst die LIST-Ausgabe eines Unix-Servers."""
    try:
        rows = [(n, f.get("type"), int(f.get("size") or 0)) for n, f in ftp.mlsd(path, ["type", "size"])]
    except ftplib.error_perm:
        lines = []
        ftp.retrlines(f"LIST {path}", lines.append)
        rows = [(m[3], "dir" if m[1] == "d" else "file", int(m[2])) for m in map(UNIX_LIST.match, lines) if m]
    items = [{"name": n, "art": "ordner" if t == "dir" else "datei", "groesse": s} for n, t, s in rows if t in ("dir", "file") and n not in (".", "..")]
    return sorted(items, key=lambda i: (i["art"] != "ordner", i["name"].lower()))


class FtpBrowser:
    def __init__(self, path, connect=open_ftp):
        self.path, self.connect, self.passwords = path, connect, {}

    def profiles(self):
        return read_json(self.path, {})

    def view(self):
        return {"verbindungen": [{"name": n, **p, "verbunden": n in self.passwords} for n, p in sorted(self.profiles().items())]}

    @contextlib.contextmanager
    def session(self, name):
        profile = self.profiles().get(name)
        if not profile:
            raise ValueError("Verbindung unbekannt")
        if name not in self.passwords:
            raise ValueError("Nicht verbunden. Bitte neu verbinden.")
        try:
            ftp = self.connect(profile, self.passwords[name])
        except (OSError, ftplib.Error) as e:
            raise ValueError(f"FTP-Fehler: {e}") from e
        try:
            yield ftp
        except (OSError, ftplib.Error) as e:
            raise ValueError(f"FTP-Fehler: {e}") from e
        finally:
            try:
                ftp.quit()
            except (OSError, ftplib.Error):
                ftp.close()

    # --- Lesen -------------------------------------------------------------------------
    def get_routes(self):
        return {"/api/ftp": self.view}

    def query_routes(self):
        return {"/api/ftp/ls": self.ls}

    def ls(self, query):
        path = clean_path(query.get("pfad", ["/"])[0])
        with self.session(query.get("name", [""])[0]) as ftp:
            return {"pfad": path, "eintraege": list_dir(ftp, path)}

    def download(self, query):
        """(Dateiname, Inhalt) fuer den Download."""
        path = clean_path(query.get("pfad", [""])[0])
        chunks, size = [], 0

        def take(block):
            nonlocal size
            size += len(block)
            if size > MAX_DOWNLOAD:
                raise ValueError(f"Datei groesser als {MAX_DOWNLOAD // 2**20} MB")
            chunks.append(block)
        with self.session(query.get("name", [""])[0]) as ftp:
            ftp.retrbinary(f"RETR {path}", take)
        return posixpath.basename(path) or "datei", b"".join(chunks)

    # --- Aendern -----------------------------------------------------------------------
    def post(self, path, data):
        """(Status, Antwort) oder None. Die Sicherheitsstufe prueft der Aufrufer."""
        action = path[len("/api/ftp/"):] if path.startswith("/api/ftp/") else None
        handler = {"verbinden": self.connect_profile, "trennen": self.disconnect, "entfernen": self.remove,
                   "ordner": self.mkdir, "loeschen": self.delete, "hochladen": self.upload}.get(action)
        return (200, handler(data)) if handler else None

    def connect_profile(self, data):
        name, host, user, password = (str(data.get(k, "")).strip() for k in ("name", "host", "user", "password"))
        port, modus = str(data.get("port", "21")).strip(), data.get("modus")
        if not (NAME.match(name) and HOST.match(host) and 0 < len(user) <= 100 and 0 < len(password) <= MAX_PASSWORD and port.isdigit() and 0 < int(port) < 65536 and modus in MODES):
            raise ValueError("Angaben ungueltig")
        profile = {"host": host, "port": int(port), "user": user, "modus": modus}
        try:
            self.connect(profile, password).quit()
        except (OSError, ftplib.Error) as e:
            raise ValueError(f"FTP-Fehler: {e}") from e
        write_json(self.path, {**self.profiles(), name: profile})
        self.passwords[name] = password
        return {"ok": True, "_audit": f"FTP verbunden: {name} ({user}@{host}:{port}, {modus})"}

    def disconnect(self, data):
        self.passwords.pop(str(data.get("name", "")), None)
        return {"ok": True}

    def remove(self, data):
        name = str(data.get("name", ""))
        profiles = self.profiles()
        if profiles.pop(name, None) is None:
            raise ValueError("Verbindung unbekannt")
        write_json(self.path, profiles)
        self.passwords.pop(name, None)
        return {"ok": True, "_audit": f"FTP-Verbindung entfernt: {name}"}

    def mkdir(self, data):
        target = posixpath.join(clean_path(data.get("pfad", "/")), leaf(str(data.get("neu", ""))))
        with self.session(str(data.get("name", ""))) as ftp:
            ftp.mkd(target)
        return {"ok": True, "_audit": f"FTP {data.get('name')}: Ordner {target} angelegt"}

    def delete(self, data):
        target = clean_path(data.get("pfad", ""))
        if target == "/":
            raise ValueError("Wurzel nicht loeschbar")
        with self.session(str(data.get("name", ""))) as ftp:
            (ftp.rmd if data.get("art") == "ordner" else ftp.delete)(target)
        return {"ok": True, "_audit": f"FTP {data.get('name')}: {target} geloescht"}

    def upload(self, data):
        target = posixpath.join(clean_path(data.get("pfad", "/")), leaf(str(data.get("datei", ""))))
        content = base64.b64decode(str(data.get("daten", "")), validate=True)
        if len(content) > MAX_UPLOAD:
            raise ValueError(f"Datei groesser als {MAX_UPLOAD // 2**20} MB")
        with self.session(str(data.get("name", ""))) as ftp:
            ftp.storbinary(f"STOR {target}", io.BytesIO(content))
        return {"ok": True, "_audit": f"FTP {data.get('name')}: {target} hochgeladen ({len(content)} Bytes)"}
