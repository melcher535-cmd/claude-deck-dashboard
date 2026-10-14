"""Dateibrowser fuer die Festplatte des Servers: ansehen, laden, hochladen, Ordner anlegen, umbenennen, loeschen.
Das Dashboard laeuft mit den Rechten seines Benutzers, der Browser sieht also genau das, was dieser Benutzer sieht.
Sichere Voreinstellungen: Loeschen nur fuer Dateien und leere Ordner (nie rekursiv), Hochladen und Umbenennen ueberschreiben nichts, Ordner direkt unter / bleiben unberuehrt."""
import base64
import itertools
import os
import re

LEAF = re.compile(r"^[^/\0]{1,255}$")
MAX_ENTRIES, MAX_DOWNLOAD, MAX_UPLOAD = 2000, 50 * 2**20, 8 * 2**20


def clean_path(path):
    return os.path.normpath("/" + str(path).lstrip("/"))  # normpath laesst genau zwei fuehrende Schraegstriche stehen


def leaf(value):
    if not LEAF.match(value) or value in (".", ".."):
        raise ValueError("Name ungueltig")
    return value


def resolve(path):
    """Der echte Pfad mit aufgeloesten Symlinks, damit Anzeige und Zugriff dasselbe meinen."""
    return os.path.realpath(clean_path(path))


def child(folder, name):
    """Ziel direkt in einem Ordner. Der Name selbst wird nicht aufgeloest, ein Symlink wird also als Link behandelt."""
    return os.path.join(resolve(folder), leaf(name))


def entry(path):
    """Ziel eines vorhandenen Eintrags aus einem Pfad. Ordner direkt unter / sind tabu."""
    clean = clean_path(path)
    target = child(os.path.dirname(clean), os.path.basename(clean))
    if os.path.dirname(target) == "/":
        raise ValueError("Ordner direkt unter / sind geschuetzt")
    return target


def os_error(e):
    return ValueError(e.strerror or "Zugriff fehlgeschlagen")


class Dateien:
    def query_routes(self):
        return {"/api/dateien/ls": self.ls}

    def ls(self, query):
        path = resolve(query.get("pfad", ["/"])[0])
        try:
            with os.scandir(path) as it:
                entries = list(itertools.islice(it, MAX_ENTRIES + 1))
        except NotADirectoryError:
            raise ValueError("Kein Ordner") from None
        except OSError as e:
            raise os_error(e) from e
        items = []
        for e in entries[:MAX_ENTRIES]:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            art = "link" if e.is_symlink() else "ordner" if e.is_dir(follow_symlinks=False) else "datei"
            items.append({"name": e.name, "art": art, "groesse": st.st_size, "geaendert": int(st.st_mtime)})
        items.sort(key=lambda i: (i["art"] != "ordner", i["name"].lower()))
        return {"pfad": path, "eintraege": items, "gekuerzt": len(entries) > MAX_ENTRIES}

    def download(self, query):
        """(Dateiname, Inhalt, echter Pfad fuer das Audit)."""
        path = resolve(query.get("pfad", [""])[0])
        try:
            if os.path.getsize(path) > MAX_DOWNLOAD:
                raise ValueError(f"Datei groesser als {MAX_DOWNLOAD // 2**20} MB")
            with open(path, "rb") as f:
                return os.path.basename(path) or "datei", f.read(), path
        except OSError as e:
            raise os_error(e) from e

    # --- Aendern -----------------------------------------------------------------------
    def post(self, path, data):
        """(Status, Antwort) oder None. Die Sicherheitsstufe prueft der Aufrufer."""
        action = path[len("/api/dateien/"):] if path.startswith("/api/dateien/") else None
        handler = {"freigabe": self.grant, "ordner": self.mkdir, "loeschen": self.delete, "umbenennen": self.rename, "hochladen": self.upload}.get(action)
        return (200, handler(data)) if handler else None

    def grant(self, data):
        """Hebt nur die Sicherheitsstufe an, damit der folgende Download erlaubt ist."""
        return {"ok": True, "_audit": f"Dateien: Download freigegeben fuer {clean_path(data.get('pfad', ''))}"}

    def mkdir(self, data):
        target = child(data.get("pfad", "/"), str(data.get("neu", "")))
        try:
            os.mkdir(target)
        except OSError as e:
            raise os_error(e) from e
        return {"ok": True, "_audit": f"Dateien: Ordner {target} angelegt"}

    def delete(self, data):
        target = entry(data.get("pfad", ""))
        try:
            (os.rmdir if os.path.isdir(target) and not os.path.islink(target) else os.remove)(target)
        except OSError as e:
            raise os_error(e) from e
        return {"ok": True, "_audit": f"Dateien: {target} geloescht"}

    def rename(self, data):
        source = entry(data.get("pfad", ""))
        target = child(os.path.dirname(source), str(data.get("neu", "")))
        if os.path.lexists(target):
            raise ValueError("Name existiert schon")
        try:
            os.rename(source, target)
        except OSError as e:
            raise os_error(e) from e
        return {"ok": True, "_audit": f"Dateien: {source} umbenannt in {target}"}

    def upload(self, data):
        target = child(data.get("pfad", "/"), str(data.get("datei", "")))
        content = base64.b64decode(str(data.get("daten", "")), validate=True)
        if len(content) > MAX_UPLOAD:
            raise ValueError(f"Datei groesser als {MAX_UPLOAD // 2**20} MB")
        try:
            with open(target, "xb") as f:  # x: bricht ab, statt eine vorhandene Datei zu ueberschreiben
                f.write(content)
        except OSError as e:
            raise os_error(e) from e
        return {"ok": True, "_audit": f"Dateien: {target} hochgeladen ({len(content)} Bytes)"}
