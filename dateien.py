"""Dateibrowser fuer die Festplatte des Servers: ansehen, laden, hochladen, bearbeiten, kopieren, verschieben, umbenennen, loeschen.
Das Dashboard laeuft mit den Rechten seines Benutzers, der Browser sieht also genau das, was dieser Benutzer sieht.
Sichere Voreinstellungen: Hochladen, Umbenennen, Verschieben und Kopieren ueberschreiben nichts, Ordner direkt unter / bleiben unberuehrt, ein Ordner mit dem Dashboard selbst wird nie geloescht,
Sondergeraete (Geraete, Pipes, Sockets) werden weder geladen noch kopiert."""
import base64
import errno
import itertools
import os
import re
import shutil
import stat

HERE = os.path.dirname(os.path.abspath(__file__))
LEAF = re.compile(r"^[^/\0]{1,255}$")
MAX_ENTRIES, MAX_DOWNLOAD, MAX_UPLOAD, MAX_TEXT, MAX_COPY = 2000, 50 * 2**20, 8 * 2**20, 2**20, 500 * 2**20


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


def source(path):
    """Ziel eines vorhandenen Eintrags aus einem Pfad. Ein Symlink bleibt ein Link."""
    clean = clean_path(path)
    if clean == "/":
        raise ValueError("Die Wurzel laesst sich nicht bearbeiten")
    return child(os.path.dirname(clean), os.path.basename(clean))


def entry(path):
    """Wie source, aber Ordner direkt unter / sind tabu (Loeschen, Verschieben, Umbenennen)."""
    target = source(path)
    if os.path.dirname(target) == "/":
        raise ValueError("Ordner direkt unter / sind geschuetzt")
    return target


def is_folder(path):
    return os.path.isdir(path) and not os.path.islink(path)


def inside(folder, path):
    return folder == path or folder.startswith(path.rstrip("/") + "/")


def free_name(folder, name):
    """name, sonst 'name Kopie', 'name Kopie 2' ... (vor der Endung)."""
    base, ext = os.path.splitext(name)
    candidate, n = name, 1
    while os.path.lexists(os.path.join(folder, candidate)):
        candidate, n = f"{base} Kopie{'' if n == 1 else f' {n}'}{ext}", n + 1
    return candidate


def copy_size(path):
    """Gesamtgroesse eines Eintrags. Bricht bei Sondergeraeten und bei mehr als MAX_COPY ab."""
    total = 0
    for base, dirs, files in os.walk(path):  # folgt keinen Symlinks
        for name in dirs + files:
            mode = os.lstat(os.path.join(base, name)).st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                raise ValueError("Enthaelt Sondergeraete, kann nicht kopiert werden")
            total += os.lstat(os.path.join(base, name)).st_size if stat.S_ISREG(mode) else 0
        if total > MAX_COPY:
            raise ValueError(f"Mehr als {MAX_COPY // 2**20} MB, zu gross zum Kopieren")
    return total


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
            items.append({"name": e.name, "art": art, "groesse": st.st_size, "geaendert": int(st.st_mtime), "rechte": stat.filemode(st.st_mode)})
        items.sort(key=lambda i: (i["art"] != "ordner", i["name"].lower()))
        return {"pfad": path, "eintraege": items, "gekuerzt": len(entries) > MAX_ENTRIES}

    def download(self, query):
        """(Dateiname, Inhalt, echter Pfad fuer das Audit). Nur normale Dateien: eine Geraetedatei meldet Groesse 0 und liefert endlos."""
        path = resolve(query.get("pfad", [""])[0])
        try:
            if not stat.S_ISREG(os.stat(path).st_mode):
                raise ValueError("Keine normale Datei")
            with open(path, "rb") as f:
                content = f.read(MAX_DOWNLOAD + 1)
        except OSError as e:
            raise os_error(e) from e
        if len(content) > MAX_DOWNLOAD:
            raise ValueError(f"Datei groesser als {MAX_DOWNLOAD // 2**20} MB")
        return os.path.basename(path) or "datei", content, path

    # --- Aendern -----------------------------------------------------------------------
    def post(self, path, data):
        """(Status, Antwort) oder None. Die Sicherheitsstufe prueft der Aufrufer."""
        action = path[len("/api/dateien/"):] if path.startswith("/api/dateien/") else None
        handler = {"freigabe": self.grant, "ordner": self.mkdir, "loeschen": self.delete, "umbenennen": self.rename, "hochladen": self.upload,
                   "kopieren": self.copy, "verschieben": self.move, "speichern": self.save}.get(action)
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
            if is_folder(target) and data.get("rekursiv"):
                if os.path.ismount(target):
                    raise ValueError("Eingehaengtes Laufwerk, nicht loeschbar")
                if inside(HERE, target):
                    raise ValueError("Enthaelt das Dashboard selbst, nicht loeschbar")
                shutil.rmtree(target)
            else:
                (os.rmdir if is_folder(target) else os.remove)(target)
        except OSError as e:
            raise os_error(e) from e
        return {"ok": True, "_audit": f"Dateien: {target} geloescht{' (samt Inhalt)' if data.get('rekursiv') else ''}"}

    def rename(self, data):
        origin = entry(data.get("pfad", ""))
        target = child(os.path.dirname(origin), str(data.get("neu", "")))
        if os.path.lexists(target):
            raise ValueError("Name existiert schon")
        try:
            os.rename(origin, target)
        except OSError as e:
            raise os_error(e) from e
        return {"ok": True, "_audit": f"Dateien: {origin} umbenannt in {target}"}

    def target_folder(self, data):
        folder = resolve(data.get("ziel", "/"))
        if not os.path.isdir(folder):
            raise ValueError("Zielordner existiert nicht")
        return folder

    def copy(self, data):
        origin, folder = source(data.get("pfad", "")), self.target_folder(data)
        real_folder = is_folder(origin)
        if real_folder and inside(folder, origin):
            raise ValueError("Ein Ordner laesst sich nicht in sich selbst kopieren")
        target = os.path.join(folder, free_name(folder, os.path.basename(origin)))
        try:
            if not (real_folder or stat.S_ISREG(os.lstat(origin).st_mode) or os.path.islink(origin)):
                raise ValueError("Sondergeraete lassen sich nicht kopieren")
            if (copy_size(origin) if real_folder else os.lstat(origin).st_size) > MAX_COPY:
                raise ValueError(f"Mehr als {MAX_COPY // 2**20} MB, zu gross zum Kopieren")
            shutil.copytree(origin, target, symlinks=True) if real_folder else shutil.copy2(origin, target, follow_symlinks=False)
        except OSError as e:
            if real_folder:
                shutil.rmtree(target, ignore_errors=True)  # keine halbe Kopie zuruecklassen
            raise os_error(e) from e
        return {"ok": True, "name": os.path.basename(target), "_audit": f"Dateien: {origin} kopiert nach {target}"}

    def move(self, data):
        origin, folder = entry(data.get("pfad", "")), self.target_folder(data)
        target = os.path.join(folder, os.path.basename(origin))
        if inside(folder, origin):
            raise ValueError("Ein Ordner laesst sich nicht in sich selbst verschieben")
        if os.path.lexists(target):
            raise ValueError("Im Zielordner gibt es den Namen schon")
        try:
            os.rename(origin, target)
        except OSError as e:
            if e.errno == errno.EXDEV:
                raise ValueError("Anderes Laufwerk: erst kopieren, dann loeschen") from e
            raise os_error(e) from e
        return {"ok": True, "_audit": f"Dateien: {origin} verschoben nach {target}"}

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

    def save(self, data):
        """Ersetzt den Inhalt einer vorhandenen Textdatei. Besitzer, Rechte und Hardlinks bleiben, weil dieselbe Datei beschrieben wird."""
        target, text = resolve(data.get("pfad", "")), data.get("text")
        if not isinstance(text, str):
            raise ValueError("Text fehlt")
        content = text.encode("utf-8")
        if len(content) > MAX_TEXT:
            raise ValueError(f"Text groesser als {MAX_TEXT // 2**20} MB")
        if not os.path.isfile(target):
            raise ValueError("Datei existiert nicht")
        try:
            with open(target, "r+b") as f:  # ponytail: nicht atomar, bei einem Absturz mitten im Schreiben bleibt eine halbe Datei (bis 1 MB). Temp-Datei und os.replace, falls das stoert.
                f.write(content)
                f.truncate()
        except OSError as e:
            raise os_error(e) from e
        return {"ok": True, "_audit": f"Dateien: {target} gespeichert ({len(content)} Bytes)"}
