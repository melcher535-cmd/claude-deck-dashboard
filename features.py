"""Zusatzfunktionen des Dashboards: Push, Wecker, Feed, Gantt, Kosten nach Projekt, Notizen, Graph, Sicherung, Diagnose, Kurzbefehl-Schluessel.
Alle Abhaengigkeiten kommen von aussen (deps), damit das Modul nichts aus server.py importieren muss."""
import collections
import datetime
import glob
import json
import os
import sys
import threading
import time

from analytics import FEED_SIZE, activity_intervals, cost_breakdown, gantt, monitor_step
from notes import create_chat_note, graph_data, read_note, recent_notes, search_notes
from storage import read_json, write_json
from upkeep import create_backup, diagnose, list_backups
from control import Control

MONITOR_SECONDS = 3
MIN_QUERY = 2
STREAM_SECONDS = 600


class Features:
    def __init__(self, deps):
        self.d = deps
        self.feed = collections.deque(maxlen=FEED_SIZE)
        self.state, self.names, self.lock = {}, {}, threading.Lock()
        self.watch_path = f"{deps.here}/watch.json"
        self.control = Control(deps)

    # --- Wecker und Feed -------------------------------------------------------------
    def watched(self):
        return read_json(self.watch_path, {})

    def watch_set(self, session_id, on):
        watched = self.watched()
        if on:
            watched[session_id] = int(time.time())
        else:
            watched.pop(session_id, None)
        write_json(self.watch_path, watched)

    def monitor_once(self, now=None):
        """Ein Durchlauf: neue Aktionen in den Feed, beobachtete Sessions melden, wenn sie fertig sind."""
        live = self.d.live_data()["live"]
        with self.lock:
            watched = self.watched()
            events, finished = monitor_step(self.state, live, watched, now or time.time())
            self.names.update({r["sessionId"]: r.get("name") or r["sessionId"][:8] for r in live})
            self.feed.extend(events)
        for sid in finished:
            self.watch_set(sid, False)
            self.d.notify(f"{self.names.get(sid, sid[:8])} ist fertig")
        return events, finished

    def monitor_forever(self):
        while True:
            try:
                self.monitor_once()
            except (OSError, KeyError, ValueError) as e:
                print(f"monitor: {e!r}", file=sys.stderr)
            time.sleep(MONITOR_SECONDS)

    def stream_payload(self):
        with self.lock:
            return {"live": self.d.live_data(), "feed": list(self.feed)[::-1]}

    # --- Auswertungen ----------------------------------------------------------------
    def titles(self):
        titles = {}
        for path in glob.glob(self.d.sessions_glob):
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"ai-title"' in line:
                        try:
                            titles[os.path.basename(path)[:-6]] = json.loads(line).get("aiTitle") or ""
                        except ValueError:
                            continue
        return titles

    def gantt_data(self):
        sessions = []
        for path in glob.glob(self.d.sessions_glob):
            stamps, title = [], ""
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("type") == "ai-title":
                        title = row.get("aiTitle") or title
                    if row.get("timestamp"):
                        stamps.append(datetime.datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp())
            sessions.append((os.path.basename(path)[:-6], title, activity_intervals(stamps)))
        return gantt(sessions, datetime.datetime.now(datetime.timezone.utc).date())

    def costs_detail(self):
        return cost_breakdown(self.d.costs_log, self.titles())

    def session_info(self, session_id):
        path = self.d.find_session(session_id)
        if not path:
            return None
        s, chat = self.d.read_session(path), self.d.chat_data(session_id, 10 ** 9)
        return {"id": session_id, "title": s["title"] or session_id[:8], "cwd": s["cwd"], "first": s["first"], "last": s["last"], "messages": s["messages"],
                "input": s["input"], "output": s["output"], "cache_read": s["cache_read"], "cache_write": s["cache_write"],
                "tools": chat["tools"][:8], "files": chat["files"], "cost": chat["cost"]}

    # --- Notizen und Graph -----------------------------------------------------------
    def notes_search(self, query):
        q = query.get("q", [""])[0]
        return search_notes(self.d.vault, q) if len(q) >= MIN_QUERY else None

    def note(self, query):
        return read_note(self.d.vault, query.get("path", [""])[0])

    def note_from_chat(self, session_id):
        chat = self.d.chat_data(session_id, 0)
        if not chat:
            raise ValueError("Session unbekannt")
        first = next((b["text"] for m in chat["messages"] if m["role"] == "user" for b in m["blocks"] if b["type"] == "text" and not b["text"].lstrip().startswith("<")), "")
        info = {"title": chat["title"] or session_id[:8], "session_id": session_id, "cwd": chat["cwd"], "first": chat["first"], "last": chat["last"],
                "messages": chat["total"], "tools": chat["tools"], "files": chat["files"], "first_message": first}
        return create_chat_note(self.d.vault, info, datetime.date.today().isoformat())

    # --- Betrieb ---------------------------------------------------------------------
    def backup(self):
        return {"file": os.path.basename(create_backup(self.d.backup_dir, self.d.backup_sources)), "backups": list_backups(self.d.backup_dir)}

    def health(self):
        return {**diagnose(self.d.domain), "backups": list_backups(self.d.backup_dir)[:5]}

    # --- Kurzbefehl ------------------------------------------------------------------
    def shortcut_usage(self):
        u = self.d.usage_data()
        percent = {l["kind"]: l["percent"] for l in u["limits"]}
        session, week = percent.get("session"), percent.get("weekly_all")
        return {"session": session, "weekly": week, "text": f"Session {session} Prozent verbraucht, Woche {week} Prozent."}

    # --- Tabellen fuer den Server ----------------------------------------------------
    def get_routes(self):
        return {"/api/gantt": self.gantt_data, "/api/costs-detail": self.costs_detail, "/api/notes-recent": lambda: recent_notes(self.d.vault),
                "/api/graph": lambda: graph_data(self.d.graph), "/api/watch": self.watched, "/api/health": self.health, "/api/push/key": self.push_key,
                "/api/backup-list": lambda: list_backups(self.d.backup_dir), "/api/apikey": lambda: {"count": self.d.auth.api_key_count}, **self.control.get_routes()}

    def query_routes(self):
        return {"/api/notes-search": self.notes_search, "/api/note": self.note, **self.control.query_routes()}

    def prefix_routes(self):
        return {"/api/session-info/": lambda rest, query: self.session_info(rest)}

    def push_key(self):
        return {"key": self.d.push.public_key(), "devices": len(self.d.push.subscriptions())}

    def post(self, path, data, ip, token=None):
        """POST-Aufrufe dieses Moduls. Gibt (Status, Antwort) zurueck oder None, wenn der Pfad nicht hierher gehoert."""
        ok = (200, {"ok": True})
        if path == "/api/push/subscribe":
            self.d.push.subscribe(data)
            return ok
        if path == "/api/push/unsubscribe":
            self.d.push.unsubscribe(str(data.get("endpoint", "")))
            return ok
        if path == "/api/push/test":
            return 200, {"sent": self.d.push.broadcast("Claude Deck", "Testnachricht: Push funktioniert.")}
        if path.startswith("/api/watch/") and self.d.session_id_ok(path[len("/api/watch/"):]):
            self.watch_set(path[len("/api/watch/"):], bool(data.get("on")))
            return ok
        if path.startswith("/api/note-from-chat/") and self.d.session_id_ok(path[len("/api/note-from-chat/"):]):
            return 200, {"path": self.note_from_chat(path[len("/api/note-from-chat/"):])}
        if path == "/api/backup":
            return 200, self.backup()
        if path == "/api/apikey/create":
            return 200, {"key": self.d.auth.create_api_key()}
        if path == "/api/apikey/revoke":
            self.d.auth.revoke_api_keys()
            return ok
        handler = self.control.post(path, token)  # wirft PermissionError ohne Sicherheitsstufe
        return (200, handler(data)) if handler else None
