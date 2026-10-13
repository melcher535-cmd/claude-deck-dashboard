"""Kleine JSON-Dateien: lesen mit Standardwert, atomar schreiben."""
import json
import os
import tempfile


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data):
    """Schreibt erst in eine Temp-Datei und ersetzt dann, damit nie eine halbe Datei liegt. Rechte 600."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
