"""Auswertungen fuer das Dashboard: Verbrauch, Fehler, Vault-Gesundheit und -Struktur, Pins, Zugriffsprotokoll."""
import collections
import datetime
import glob
import json
import os
import re

from analytics import error_patterns
from storage import read_json, write_json

WEEK_DAYS = 7
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
MAX_PINS = 1000
MAX_NOTE = 200
MAX_LISTED = 200


def usage_ranking(sessions, today, days=WEEK_DAYS, top=15):
    """Anteil jeder Session am Token-Verbrauch der letzten Tage. Das ist kein Prozentwert des Limits."""
    since = (today - datetime.timedelta(days=days)).isoformat()
    scored = []
    for s in sessions:
        tokens = sum(d["input"] + d["cache_write"] + d["output"] for date, d in s["daily"].items() if date >= since)
        if tokens:
            scored.append({"id": s["id"], "title": s["title"], "tokens": tokens})
    total = sum(r["tokens"] for r in scored) or 1
    scored.sort(key=lambda r: -r["tokens"])
    return {"days": days, "total": total, "sessions": [{**r, "share": round(100 * r["tokens"] / total, 1)} for r in scored[:top]]}


def error_list(paths, limit=100):
    """Fehlgeschlagene Werkzeug-Aufrufe aus allen Verlaeufen, neueste zuerst."""
    errors = []
    for path in paths:
        names, title, first = {}, "", len(errors)
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "ai-title":
                    title = d.get("aiTitle") or title
                content = d.get("message", {}).get("content") if d.get("type") in ("user", "assistant") else None
                for b in content if isinstance(content, list) else []:
                    if b.get("type") == "tool_use":
                        names[b["id"]] = b["name"]
                    elif b.get("type") == "tool_result" and b.get("is_error"):
                        text = b.get("content")
                        if isinstance(text, list):
                            text = "\n".join(x.get("text", "") for x in text if isinstance(x, dict))
                        errors.append({"id": os.path.basename(path)[:-6], "ts": d.get("timestamp"), "tool": names.get(b.get("tool_use_id"), "?"), "text": str(text)[:300]})
        for e in errors[first:]:
            e["title"] = title
    by_tool = collections.Counter(e["tool"] for e in errors)
    return {"total": len(errors), "by_tool": by_tool.most_common(), "patterns": error_patterns(errors),
            "errors": sorted(errors, key=lambda e: e["ts"] or "", reverse=True)[:limit]}


def vault_health(vault, timeline="Zeitleiste"):
    """Notizen, die niemand verlinkt, und Notizen, die nicht in der Zeitleiste stehen."""
    notes = [p for p in glob.glob(f"{vault}/**/*.md", recursive=True) if "graphify-out" not in p and "/.obsidian/" not in p]
    stems = {os.path.splitext(os.path.basename(p))[0]: os.path.relpath(p, vault) for p in notes}
    linked, in_timeline = set(), set()
    for p in notes:
        stem = os.path.splitext(os.path.basename(p))[0]
        with open(p, encoding="utf-8", errors="replace") as f:
            targets = {t.strip().rsplit("/", 1)[-1] for t in WIKILINK.findall(f.read())}
        linked |= targets - {stem}
        if stem == timeline:
            in_timeline = targets
    orphans = sorted(path for stem, path in stems.items() if stem not in linked)
    missing = sorted(path for stem, path in stems.items() if stem not in in_timeline and stem != timeline)
    return {"notes": len(notes), "orphans_total": len(orphans), "orphans": orphans[:MAX_LISTED],
            "not_in_timeline_total": len(missing), "not_in_timeline": missing[:MAX_LISTED]}


def vault_tree(path, depth=3):
    """Ordnerbaum mit Groessen; tiefere Ebenen werden nur aufsummiert."""
    node = {"name": os.path.basename(path) or path, "size": 0, "files": 0, "children": []}
    for entry in os.scandir(path):
        if entry.is_dir(follow_symlinks=False):
            child = vault_tree(entry.path, depth - 1)
            node["size"] += child["size"]
            node["files"] += child["files"]
            if depth > 0:
                node["children"].append(child)
        elif entry.is_file(follow_symlinks=False):
            node["size"] += entry.stat().st_size
            node["files"] += 1
    node["children"].sort(key=lambda c: -c["size"])
    return node


class Pins:
    """Markierte Sessions mit eigener Notiz."""

    def __init__(self, path):
        self.path = path

    def all(self):
        return read_json(self.path, {})

    def set(self, session_id, pinned, note):
        pins = self.all()
        if pinned or note:
            if session_id not in pins and len(pins) >= MAX_PINS:
                raise ValueError("zu viele Markierungen")
            pins[session_id] = {"pinned": bool(pinned), "note": str(note)[:MAX_NOTE]}
        else:
            pins.pop(session_id, None)
        write_json(self.path, pins)


def log_access(path, ip, event, now=None):
    row = {"t": (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="seconds"), "ip": ip, "event": event}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def log_audit(path, ip, action, status, detail=""):
    """Protokoll aller Aktionen, die etwas veraendern oder ausfuehren (ohne Passwoerter und Inhalte)."""
    row = {"t": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "ip": ip, "action": action, "status": status, "detail": str(detail)[:200]}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_access(path, limit=100):
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f.readlines()[-limit:]][::-1]
    except FileNotFoundError:
        return []
