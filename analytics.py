"""Auswertungen ueber Sessions: Kosten nach Projekt, Arbeitszeitleiste, Fehler-Muster, Aktivitaets-Feed."""
import collections
import datetime
import json
import os
import re

ACTIVITY_GAP_SECONDS = 20 * 60
FEED_SIZE = 60
PATTERN_LENGTH = 70
NOISE = re.compile(r"/[\w./-]+|[0-9a-f]{8,}(?:-[0-9a-f]{4,})*|\d+")


def project_of(transcript_path):
    """Ordnername des Projekts aus dem Pfad des Verlaufs (Linux- und Windows-Pfade)."""
    parts = re.split(r"[\\/]", transcript_path or "")
    return parts[-2] if len(parts) >= 2 else "(unbekannt)"


def cost_breakdown(costs_path, titles, weeks=8, top=15):
    """Geschaetzte Kosten je Projekt, Session und Woche aus dem Kostenlog. titles: Session-ID -> Titel."""
    by_project, by_session, by_week = collections.Counter(), collections.Counter(), collections.Counter()
    try:
        with open(costs_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                usd = d.get("estimated_cost_usd", 0)
                by_project[project_of(d.get("transcript_path"))] += usd
                by_session[d.get("session_id", "?")] += usd
                try:
                    year, week, _ = datetime.date.fromisoformat(d["timestamp"][:10]).isocalendar()
                    by_week[f"{year}-W{week:02d}"] += usd
                except (KeyError, ValueError):
                    continue
    except OSError:
        pass
    rounded = lambda counter, n=None: [(k, round(v, 2)) for k, v in counter.most_common(n)]
    return {"projects": rounded(by_project), "weeks": [(k, round(v, 2)) for k, v in sorted(by_week.items())[-weeks:]],
            "sessions": [(sid, titles.get(sid) or sid[:8], usd) for sid, usd in rounded(by_session, top)], "total": round(sum(by_project.values()), 2)}


def activity_intervals(stamps, gap=ACTIVITY_GAP_SECONDS):
    """Zeitstempel (Sekunden) zu Arbeitsabschnitten: eine Pause ueber `gap` beendet einen Abschnitt."""
    intervals = []
    for t in sorted(stamps):
        if intervals and t - intervals[-1][1] <= gap:
            intervals[-1][1] = t
        else:
            intervals.append([t, t])
    return intervals


def gantt(sessions, today, days=7):
    """Arbeitsabschnitte der letzten Tage, an Tagesgrenzen (UTC) geteilt. sessions: [(id, titel, [[start, ende], ...])]."""
    rows = {}
    for offset in range(days):
        day = today - datetime.timedelta(days=offset)
        start = datetime.datetime.combine(day, datetime.time(), datetime.timezone.utc).timestamp()
        bars = []
        for sid, title, intervals in sessions:
            for a, b in intervals:
                lo, hi = max(a, start), min(b, start + 86400)
                if lo < hi or (a == b and start <= a < start + 86400):
                    bars.append({"id": sid, "title": title, "from": round((lo - start) / 3600, 3), "to": round(max(hi - start, lo - start + 60) / 3600, 3)})
        rows[day.isoformat()] = bars
    return sorted(rows.items())


def error_patterns(errors, top=10):
    """Fehler nach Ursache zusammenfassen: Zahlen, Pfade und Kennungen werden vereinheitlicht."""
    groups = {}
    for e in errors:
        key = (e["tool"], NOISE.sub("#", e["text"].lower()).strip()[:PATTERN_LENGTH])
        g = groups.setdefault(key, {"tool": e["tool"], "muster": key[1], "anzahl": 0, "zuletzt": "", "beispiel": e["text"][:160], "wochen": collections.Counter()})
        g["anzahl"] += 1
        g["zuletzt"] = max(g["zuletzt"], e["ts"] or "")
        if e["ts"]:
            year, week, _ = datetime.date.fromisoformat(e["ts"][:10]).isocalendar()
            g["wochen"][f"{year}-W{week:02d}"] += 1
    ranked = sorted(groups.values(), key=lambda g: -g["anzahl"])[:top]
    for g in ranked:
        g["wochen"] = sorted(g["wochen"].items())[-8:]
    return ranked


def hours_until_full(points, kind):
    """Stunden bis 100 % bei dem Tempo der letzten Messpunkte (None, wenn die Auslastung nicht steigt)."""
    pts = [p for p in points if p.get(kind) is not None][-8:]
    if len(pts) < 2 or pts[-1]["t"] <= pts[0]["t"] or pts[-1][kind] <= pts[0][kind]:
        return None
    rate = (pts[-1][kind] - pts[0][kind]) / (pts[-1]["t"] - pts[0]["t"])  # Prozent je Millisekunde
    return (100 - pts[-1][kind]) / rate / 3_600_000


def monitor_step(state, live, watched, now):
    """Ein Schritt der Ueberwachung. state: {session_id: (status, aktion)} vom letzten Schritt.
    Gibt (neue Feed-Eintraege, fertige beobachtete Sessions) zurueck und aktualisiert state."""
    events, finished, seen = [], [], set()
    for r in live:
        sid = r["sessionId"]
        seen.add(sid)
        before = state.get(sid)
        now_state = (r["status"], r.get("action"))
        if before != now_state and r.get("action"):
            events.append({"t": now, "id": sid, "name": r.get("name") or sid[:8], "action": r["action"], "status": r["status"]})
        if sid in watched and before and before[0] == "busy" and r["status"] != "busy":
            finished.append(sid)
        state[sid] = now_state
    for sid in [s for s in state if s not in seen]:  # Prozess beendet
        if sid in watched and state[sid][0] == "busy":
            finished.append(sid)
        del state[sid]
    return events, finished
