"""Eigene Widgets und Anordnung der Uebersichtsseite. Gespeichert auf dem Server, damit sie auf jedem Geraet gelten."""
import re

from storage import read_json, write_json

SOURCES = ("limit_session", "limit_woche", "limit_verlauf", "kosten", "tokens", "nachrichten", "sessions_aktiv", "sessions_tag", "fehler",
           "gesundheit", "zertifikat", "platte", "arbeitszeit", "notizen_heute", "notizen_neu", "repos", "postfach", "ram", "cpu", "dienste",
           "verbrauch", "werkzeuge", "sicherung", "netz_geraete", "netz_speicher", "netz_prognose", "netz_temp", "netz_wan", "netz_speedtest", "netz_alarme", "netz_dienste",
           "netz_strom", "netz_usv", "netz_dns", "netz_verlauf_wan", "netz_verlauf_geraete")
VIEWS = ("zahl", "ring", "linie", "saeulen", "liste")
RANGES = (1, 7, 30)
WIDTHS = ("halb", "voll")
MAX_CUSTOM = 24
MAX_IDS = 64
MAX_TITLE = 40
ID = re.compile(r"^[a-z0-9-]{1,32}$")


def clean_ids(value):
    """Gueltige, einmalige Kennungen in der gegebenen Reihenfolge."""
    if not isinstance(value, list) or len(value) > MAX_IDS:
        raise ValueError("ungueltige Liste")
    return list(dict.fromkeys(i for i in value if isinstance(i, str) and ID.match(i)))


def clean_widget(raw):
    if not isinstance(raw, dict):
        raise ValueError("ungueltiges Widget")
    title = str(raw.get("title", "")).strip()
    if not title or len(title) > MAX_TITLE:
        raise ValueError(f"Titel: 1 bis {MAX_TITLE} Zeichen")
    widget = {"id": raw.get("id"), "title": title, "source": raw.get("source"), "view": raw.get("view"), "range": raw.get("range"), "width": raw.get("width")}
    if not (isinstance(widget["id"], str) and ID.match(widget["id"])):
        raise ValueError("ungueltige Kennung")
    for key, allowed in (("source", SOURCES), ("view", VIEWS), ("range", RANGES), ("width", WIDTHS)):
        if widget[key] not in allowed:
            raise ValueError(f"ungueltiger Wert: {key}")
    return widget


class WidgetLayout:
    """Reihenfolge, ausgeblendete Bloecke und selbst angelegte Widgets."""

    def __init__(self, path):
        self.path = path

    def all(self):
        return read_json(self.path, {"order": [], "hidden": [], "custom": []})

    def save(self, data):
        custom = data.get("custom", [])
        if not isinstance(custom, list) or len(custom) > MAX_CUSTOM:
            raise ValueError("zu viele Widgets")
        custom = [clean_widget(w) for w in custom]
        if len({w["id"] for w in custom}) != len(custom):
            raise ValueError("doppelte Kennung")
        layout = {"order": clean_ids(data.get("order", [])), "hidden": clean_ids(data.get("hidden", [])), "custom": custom}
        write_json(self.path, layout)
        return layout
