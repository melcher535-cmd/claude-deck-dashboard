"""Claude Deck: lokales Dashboard fuer Claude-Code-Sessions und den Vault-Graphen.

Start: systemd-Dienst claude-deck (127.0.0.1:8770 hinter Caddy auf DECK_DOMAIN, Anmeldung per Formular)
"""
import collections
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import types
import zoneinfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen

from analytics import hours_until_full
from auth import MIN_PASSWORD_LENGTH, Auth
from docker_apps import DockerApps
from features import MONITOR_SECONDS, STREAM_SECONDS, Features
from dateien import Dateien
from firewall import Firewall
from ftp_browser import FtpBrowser
from netz import Netz
from notes import with_mobile_layout
from push import Push
from widgets import WidgetLayout
from insights import Pins, error_list, log_access, log_audit, read_access, usage_ranking, vault_health, vault_tree

SESSIONS = os.path.expanduser("~/.claude/projects/*/*.jsonl")
VAULT = "/root/Obsidian-sync"
GRAPH = f"{VAULT}/graphify-out/graph.json"
ZEITLEISTE = f"{VAULT}/Zeitleiste.md"
HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 8770


DAY_KEYS = ("input", "output", "cache_read", "cache_write", "messages")


def day_of(session, ts):
    return session["daily"].setdefault((ts or "")[:10], dict.fromkeys(DAY_KEYS, 0))


def is_human_message(content):
    """Werkzeug-Ergebnisse stehen im Verlauf als 'user', sind aber keine Nachricht des Nutzers."""
    return isinstance(content, str) or any(b.get("type") == "text" for b in content if isinstance(b, dict))


def read_session(path):
    s = {"id": os.path.basename(path)[:-6], "title": "", "first": None, "last": None, "messages": 0,
         "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "models": set(), "daily": {},
         "tools": collections.Counter(), "cwd": ""}
    seen = set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            s["cwd"] = s["cwd"] or d.get("cwd", "")
            ts = d.get("timestamp")
            if ts:
                s["first"] = s["first"] or ts
                s["last"] = ts
            if d.get("type") == "ai-title":
                s["title"] = d.get("aiTitle") or s["title"]
            elif d.get("type") == "user" and is_human_message(d.get("message", {}).get("content") or []):
                s["messages"] += 1
                day_of(s, ts)["messages"] += 1
            elif d.get("type") == "assistant":
                m = d.get("message", {})
                for b in m.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        s["tools"][b["name"]] += 1
                key = m.get("id") or d.get("uuid")
                if key in seen:
                    continue
                seen.add(key)
                u = m.get("usage") or {}
                counts = {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                          "cache_read": u.get("cache_read_input_tokens", 0),
                          "cache_write": u.get("cache_creation_input_tokens", 0)}
                day = day_of(s, ts)
                for k, n in counts.items():
                    s[k] += n
                    day[k] += n
                if m.get("model"):
                    s["models"].add(m["model"])
                    s.setdefault("by_model", collections.Counter())[m["model"]] += counts["output"]
    s["models"] = sorted(s["models"])
    return s


def sessions_data():
    rows = sorted((read_session(p) for p in glob.glob(SESSIONS)), key=lambda r: r["last"] or "", reverse=True)
    totals = {k: sum(r[k] for r in rows) for k in ("input", "output", "cache_read", "cache_write", "messages")}
    daily = collections.defaultdict(lambda: dict.fromkeys(DAY_KEYS + ("sessions",), 0))
    by_model, tools = collections.Counter(), collections.Counter()
    projects = collections.defaultdict(lambda: {"sessions": 0, "output": 0, "messages": 0})
    for r in rows:
        for date, counts in r.pop("daily").items():
            daily[date]["sessions"] += 1
            for k, n in counts.items():
                daily[date][k] += n
        by_model.update(r.pop("by_model", {}))
        tools.update(r.pop("tools"))
        p = projects[r["cwd"] or "(unbekannt)"]
        p["sessions"] += 1
        p["output"] += r["output"]
        p["messages"] += r["messages"]
    return {"totals": totals, "sessions": rows, "daily": sorted(daily.items())[-30:], "by_model": by_model.most_common(),
            "heat": {date: day["output"] for date, day in daily.items() if date},
            "tools": tools.most_common(15), "projects": sorted(projects.items(), key=lambda kv: -kv[1]["output"])}


SESSION_ID = re.compile(r"^[0-9a-f-]{36}$")
RESULT_MAX = 2000
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")


def find_session(sid):
    hits = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl")) if SESSION_ID.match(sid) else []
    return hits[0] if hits else None


COSTS_LOG = os.path.expanduser("~/.claude/metrics/costs.jsonl")
CONTEXT_TAIL_BYTES = 400_000


def session_cost(sid):
    """Geschaetzte Kosten einer Session aus dem Kostenlog (leer, wenn das Log sie nicht kennt)."""
    total = 0.0
    try:
        with open(COSTS_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                if sid in line:
                    d = json.loads(line)
                    if d.get("session_id") == sid:
                        total += d.get("estimated_cost_usd", 0)
    except (OSError, ValueError):
        return None
    return round(total, 2) if total else None


def tail_lines(sid):
    """Letzte Zeilen des Verlaufs einer Session, ohne die ganze Datei zu lesen."""
    path = find_session(sid)
    if not path:
        return []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - CONTEXT_TAIL_BYTES))
        return f.read().decode("utf-8", "replace").splitlines()


def parsed_reversed(lines):
    for line in reversed(lines):
        try:
            yield json.loads(line)
        except ValueError:
            continue


def context_tokens(lines):
    """Groesse des Kontexts laut letzter Antwort der Session (Eingabe + Cache)."""
    for d in parsed_reversed(lines):
        usage = d.get("message", {}).get("usage") if d.get("type") == "assistant" else None
        if usage:
            return usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
    return None


def last_action(lines):
    """Kurzbeschreibung der letzten Aktion, ohne Befehlstext: Werkzeugname und Dateiname oder Beschreibung."""
    for d in parsed_reversed(lines):
        if d.get("type") != "assistant":
            continue
        for b in reversed(d.get("message", {}).get("content") or []):
            if b.get("type") == "tool_use":
                i = b.get("input") or {}
                path = i.get("file_path") or i.get("path")
                return f"{b['name']} {os.path.basename(path) if path else str(i.get('description') or i.get('pattern') or '')[:50]}".strip()
            if b.get("type") == "text" and b["text"].strip():
                return "antwortet"
    return None


def chat_blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out = []
    for b in content:
        kind = b.get("type")
        if kind == "text":
            out.append({"type": "text", "text": b["text"]})
        elif kind == "thinking":
            out.append({"type": "thinking", "text": b.get("thinking", "")})
        elif kind == "tool_use" and b["name"] == "Edit" and "old_string" in (b.get("input") or {}):
            i = b["input"]
            out.append({"type": "diff", "file": i.get("file_path", ""), "old": i["old_string"][:RESULT_MAX], "new": i.get("new_string", "")[:RESULT_MAX]})
        elif kind == "tool_use":
            out.append({"type": "tool_use", "name": b["name"], "text": json.dumps(b.get("input"), ensure_ascii=False)[:RESULT_MAX]})
        elif kind == "tool_result":
            c = b.get("content")
            if isinstance(c, list):
                c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
            out.append({"type": "tool_result", "text": str(c)[:RESULT_MAX], "error": bool(b.get("is_error"))})
    return out


def chat_data(sid, after):
    path = find_session(sid)
    if not path:
        return None
    messages, tools, files = [], collections.Counter(), set()
    info = {"title": "", "cwd": "", "branch": "", "first": None, "last": None, "cost": session_cost(sid)}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            info["cwd"] = info["cwd"] or d.get("cwd", "")
            if d.get("timestamp"):
                info["first"] = info["first"] or d["timestamp"]
                info["last"] = d["timestamp"]
            info["branch"] = d.get("gitBranch") or info["branch"]
            if d.get("type") == "ai-title":
                info["title"] = d.get("aiTitle") or info["title"]
            if d.get("type") not in ("user", "assistant"):
                continue
            content = d.get("message", {}).get("content") or []
            blocks = chat_blocks(content)
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "tool_use":
                    tools[b["name"]] += 1
                    if b["name"] in EDIT_TOOLS and b.get("input", {}).get("file_path"):
                        files.add(b["input"]["file_path"])
            if blocks:
                messages.append({"role": d["type"], "ts": d.get("timestamp"), "blocks": blocks})
    return {**info, "tools": tools.most_common(), "files": sorted(files), "total": len(messages), "messages": messages[after:]}


def search_data(query):
    needle, hits = query.lower(), []
    for path in glob.glob(SESSIONS):
        title, snippet = "", ""
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "ai-title":
                    title = d.get("aiTitle") or title
                if snippet or d.get("type") not in ("user", "assistant"):
                    continue
                for b in chat_blocks(d.get("message", {}).get("content") or []):
                    pos = b["text"].lower().find(needle) if b["type"] == "text" else -1
                    if pos >= 0:
                        snippet = b["text"][max(0, pos - 60):pos + 100]
                        break
        if snippet:
            hits.append({"id": os.path.basename(path)[:-6], "title": title, "snippet": snippet, "mtime": os.path.getmtime(path)})
    return sorted(hits, key=lambda h: -h["mtime"])[:40]


def vault_data():
    with open(GRAPH, encoding="utf-8") as f:
        g = json.load(f)
    degree = collections.Counter()
    for e in g["links"]:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    labels = {n["id"]: n["label"] for n in g["nodes"]}
    communities = collections.Counter(n.get("community_name") or str(n.get("community")) for n in g["nodes"])
    with open(ZEITLEISTE, encoding="utf-8") as f:
        days = re.findall(r"^## (\d{4}-\d{2}-\d{2})\n((?:- .*\n?)+)", f.read(), re.M)
    recent = days[-5:]
    notes = [p for p in glob.glob(f"{VAULT}/**/*.md", recursive=True) if "graphify-out" not in p]
    graph_built = os.path.getmtime(GRAPH)
    folders = collections.Counter(os.path.relpath(p, VAULT).split(os.sep)[0] if os.sep in os.path.relpath(p, VAULT) else "(Wurzel)" for p in notes)
    return {
        "stale_notes": sum(1 for p in notes if os.path.getmtime(p) > graph_built),
        "today_notes": sum(1 for p in notes if datetime.date.fromtimestamp(os.path.getmtime(p)) == datetime.date.today()),
        "folders": folders.most_common(10),
        "types": collections.Counter(n["file_type"] for n in g["nodes"]).most_common(),
        "confidence": collections.Counter(e["confidence"] for e in g["links"]).most_common(),
        "per_day": [(d, len(re.findall(r"\[\[", b))) for d, b in days],
        "nodes": len(g["nodes"]), "edges": len(g["links"]),
        "notes": len(notes),
        "graph_built": graph_built,
        "communities": communities.most_common(12),
        "gods": [(labels[i], c) for i, c in degree.most_common(10) if i in labels],
        "timeline": [{"date": d, "notes": re.findall(r"\[\[(.+?)\]\]", b)} for d, b in reversed(recent)],
    }


def live_data():
    live, spare = [], 0
    for path in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except ValueError:
            continue
        if not os.path.exists(f"/proc/{d.get('pid')}"):
            continue
        if d.get("spare"):
            spare += 1
            continue
        lines = tail_lines(d["sessionId"])
        live.append({**{k: d.get(k) for k in ("pid", "sessionId", "name", "status", "kind", "cwd", "version", "startedAt", "updatedAt")},
                     "context": context_tokens(lines), "action": last_action(lines)})
    return {"live": sorted(live, key=lambda r: r["startedAt"] or 0), "spare": spare}


HARNESSES = ("claude", "codex", "copilot", "opencode", "pi", "tmux")
CLAUDE_DIR = os.path.expanduser("~/.claude")
SECRET_KEY = re.compile(r"token|key|secret|password|authorization|credential", re.I)


def masked(o):
    """Werte unter geheimnisartigen Schluesseln werden nie ausgeliefert."""
    if isinstance(o, dict):
        return {k: "***" if SECRET_KEY.search(k) and not isinstance(v, (dict, list)) else masked(v) for k, v in o.items()}
    if isinstance(o, list):
        return [masked(x) for x in o]
    return o


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def description_of(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for _, line in zip(range(25), f):
            if line.startswith("description:"):
                return line.split(":", 1)[1].strip().strip("\"'")[:200]
    return ""


def named_files(pattern, name_of):
    return sorted(({"name": name_of(p), "description": description_of(p)} for p in glob.glob(pattern)), key=lambda e: e["name"])


def config_data():
    installed = read_json(f"{CLAUDE_DIR}/plugins/installed_plugins.json", {}).get("plugins", {})
    hooks = {}
    for path in glob.glob(f"{CLAUDE_DIR}/plugins/cache/*/*/*/hooks/hooks.json"):
        for event, entries in read_json(path, {}).get("hooks", {}).items():
            counter = hooks.setdefault(path.split("/")[-4], collections.Counter())
            counter[event] += sum(len(e.get("hooks", [])) for e in entries)
    project = next(iter(read_json(os.path.expanduser("~/.claude.json"), {}).get("projects", {}).values()), {})
    return {
        "settings": masked(read_json(f"{CLAUDE_DIR}/settings.json", {})),
        "plugins": [{"name": n, "versions": [v.get("version") for v in vs if isinstance(v, dict)]} for n, vs in sorted(installed.items())],
        "hooks": {p: dict(c) for p, c in sorted(hooks.items())},
        "mcp": masked(project.get("mcpServers", {})),
        "agents": named_files(f"{CLAUDE_DIR}/agents/*.md", lambda p: os.path.basename(p)[:-3]),
        "skills": named_files(f"{CLAUDE_DIR}/skills/*/SKILL.md", lambda p: p.split("/")[-2]),
        "memory": named_files(f"{CLAUDE_DIR}/projects/*/memory/*.md", lambda p: os.path.basename(p)[:-3]),
        "rules": sorted(os.path.relpath(p, f"{CLAUDE_DIR}/rules") for p in glob.glob(f"{CLAUDE_DIR}/rules/**/*.md", recursive=True)),
    }


def costs_data():
    daily, by_model = collections.Counter(), collections.Counter()
    try:
        with open(os.path.expanduser("~/.claude/metrics/costs.jsonl"), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                daily[d["timestamp"][:10]] += d.get("estimated_cost_usd", 0)
                by_model[d.get("model", "?")] += d.get("estimated_cost_usd", 0)
    except OSError:
        pass
    return {"daily": [(k, round(v, 2)) for k, v in sorted(daily.items())[-30:]],
            "by_model": [(k, round(v, 2)) for k, v in by_model.most_common()], "total": round(sum(daily.values()), 2),
            "harnesses": [(h, shutil.which(h)) for h in HARNESSES]}


def expire_limits(limits, now):
    """Der Cache wird nur von Claude Code erneuert: Ist resets_at vorbei, gilt das Limit als zurueckgesetzt."""
    return [{**l, "percent": 0, "severity": "normal", "reset": True, "was_percent": l["percent"]}
            if datetime.datetime.fromisoformat(l["resets_at"]) <= now else l for l in limits]


def usage_data():
    """Limits aus dem Cache, den Claude Code fuer /usage in ~/.claude.json pflegt (kein Token noetig)."""
    with open(os.path.expanduser("~/.claude.json"), encoding="utf-8") as f:
        cache = json.load(f)["cachedUsageUtilization"]
    u = cache["utilization"]
    now = datetime.datetime.now(UTC)
    return {"fetched": cache["fetchedAtMs"], "limits": expire_limits(u["limits"], now), "extra_usage": u["extra_usage"], "spend": u["spend"],
            "per_model": {k: v for k, v in u.items() if k.startswith("seven_day_") and isinstance(v, dict)}}


HISTORY = f"{HERE}/usage-history.jsonl"
SAMPLE_SECONDS = 300
HISTORY_POINTS = 600
SEARCH_MIN = 2
MAX_BODY = 4096
FTP_BODY = 12 * 2**20  # Upload als Base64: 8 MB Datei plus Aufschlag
NETZ_BODY = 262144  # Konfiguration der Netzwerkseite
PUSH_BODY = 2 * 1024 * 1024  # Stand eines Agenten
CREDENTIALS = f"{HERE}/credentials.json"
ACCESS_LOG = f"{HERE}/access-log.jsonl"
AUDIT_LOG = f"{HERE}/audit.jsonl"
PINS = Pins(f"{HERE}/pins.json")
WIDGETS = WidgetLayout(f"{HERE}/widgets.json")
SUMMARY_HOUR = 20
SUMMARY_ZONE = "Europe/Berlin"
UTC = datetime.timezone.utc
COOKIE = "deck_session"
COOKIE_FLAGS = "Path=/; HttpOnly; Secure; SameSite=Strict"


def usage_history():
    try:
        with open(HISTORY, encoding="utf-8") as f:
            return [json.loads(line) for line in f.readlines()[-HISTORY_POINTS:]]
    except OSError:
        return []


NTFY_TOPIC = os.environ.get("DECK_NTFY_TOPIC", "")
ALERT_LEVELS = (80, 95)
RESET_ALERT_MAX_AGE = datetime.timedelta(hours=1)
LIMIT_NAMES = {"session": "Session-Limit (5 Std)", "weekly_all": "Wochen-Limit"}


def notify(text):
    """Meldung auf allen Wegen: ntfy.sh (wenn DECK_NTFY_TOPIC gesetzt ist) und Web-Push an alle angemeldeten Geraete."""
    if NTFY_TOPIC:
        urlopen(Request(f"https://ntfy.sh/{NTFY_TOPIC}", data=text.encode(), headers={"Title": "Claude Deck"}), timeout=10).close()
    PUSH.broadcast("Claude Deck", text)


def alert_on_limits(limits, alerted):
    for limit in limits:
        level = max((lv for lv in ALERT_LEVELS if limit["percent"] >= lv), default=None)
        key = (limit["kind"], level, limit["resets_at"])
        if level and key not in alerted:
            alerted.add(key)
            notify(f"{LIMIT_NAMES.get(limit['kind'], limit['kind'])} bei {limit['percent']} %")


def alert_on_resets(limits, alerted, now):
    for limit in limits:
        key = (limit["kind"], "reset", limit["resets_at"])
        age = now - datetime.datetime.fromisoformat(limit["resets_at"])
        if limit.get("reset") and key not in alerted and age < RESET_ALERT_MAX_AGE:
            alerted.add(key)
            notify(f"{LIMIT_NAMES.get(limit['kind'], limit['kind'])} zurückgesetzt (war {limit['was_percent']} %)")


def alert_on_forecast(limits, history, alerted, now=None):
    """Meldet einmal je Zuruecksetzung, wenn ein Limit bei dem aktuellen Tempo vor dem Reset voll waere."""
    now = now or datetime.datetime.now(UTC)
    for limit in limits:
        hours, key = hours_until_full(history, limit["kind"]), (limit["kind"], "prognose", limit["resets_at"])
        reset_hours = (datetime.datetime.fromisoformat(limit["resets_at"]) - now).total_seconds() / 3600
        if hours is not None and hours < min(FORECAST_ALERT_HOURS, reset_hours) and key not in alerted and limit["percent"] < 100:
            alerted.add(key)
            notify(f"{LIMIT_NAMES.get(limit['kind'], limit['kind'])}: bei diesem Tempo in etwa {hours:.0f} Std voll")


def sample_usage_forever():
    """Speichert die Auslastung, sobald Claude Code neue Werte in den Cache schreibt, und warnt bei hoher Auslastung."""
    history = usage_history()
    last, alerted = (history[-1]["t"] if history else None), set()
    while True:
        try:
            u = usage_data()
            if u["fetched"] != last:
                last = u["fetched"]
                row = {"t": u["fetched"], **{limit["kind"]: limit["percent"] for limit in u["limits"]}}
                with open(HISTORY, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                alert_on_limits(u["limits"], alerted)
                alert_on_forecast(u["limits"], usage_history(), alerted)
            alert_on_resets(u["limits"], alerted, datetime.datetime.now(UTC))
        except (OSError, KeyError, ValueError, URLError) as e:
            print(f"usage-sample: {e!r}", file=sys.stderr)
        time.sleep(SAMPLE_SECONDS)


SERVICES = ("caddy", "claude-deck", "ssh", "ufw", "obsidian-sync")
LOG_LINES = 5


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout


def services_data():
    services = [{"name": name, "state": run(["systemctl", "is-active", name]).strip(),
                 "since": run(["systemctl", "show", name, "--property=ActiveEnterTimestamp", "--value"]).strip(),
                 "log": [line[:200] for line in run(["journalctl", "-u", name, "-n", str(LOG_LINES), "--no-pager", "-o", "short"]).splitlines()]}
                for name in SERVICES]
    newest = max((os.path.getmtime(p) for p in glob.glob(f"{VAULT}/**/*.md", recursive=True)), default=0)
    return {"services": services, "vault_newest": newest}


def tree_size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(path) for f in files)


def backups_data():
    paths = {p for pattern in (f"{CLAUDE_DIR}/*backup*", "/root/*backup*", "/root/*.tar.gz") for p in glob.glob(pattern)}
    return sorted(({"path": p, "size": tree_size(p), "mtime": os.path.getmtime(p), "dir": os.path.isdir(p)} for p in paths), key=lambda i: -i["mtime"])


def system_data():
    with open("/proc/meminfo", encoding="utf-8") as f:
        mem = {k: int(v.split()[0]) for k, v in (line.split(":", 1) for line in f)}
    with open("/proc/uptime", encoding="utf-8") as f:
        uptime = float(f.read().split()[0])
    disk = shutil.disk_usage("/")
    top = subprocess.run(["ps", "-eo", "pid,pcpu,pmem,comm", "--sort=-pcpu"], capture_output=True, text=True, timeout=5, check=True)
    return {"load": os.getloadavg(), "cpus": os.cpu_count(), "uptime": uptime,
            "mem_total": mem["MemTotal"] * 1024, "mem_available": mem["MemAvailable"] * 1024,
            "disk_total": disk.total, "disk_used": disk.used,
            "top": [line.split(None, 3) for line in top.stdout.splitlines()[1:9]]}


def graph_search(query):
    with open(GRAPH, encoding="utf-8") as f:
        g = json.load(f)
    labels = {n["id"]: n["label"] for n in g["nodes"]}
    neighbors = collections.defaultdict(list)
    for e in g["links"]:
        neighbors[e["source"]].append((e["relation"], labels.get(e["target"], "?")))
        neighbors[e["target"]].append((e["relation"], labels.get(e["source"], "?")))
    needle = query.lower()
    return [{"label": n["label"], "community": n.get("community_name"), "file": n.get("source_file"), "neighbors": neighbors[n["id"]][:6]}
            for n in g["nodes"] if needle in n["label"].lower()][:30]


def ranking_data():
    rows = [read_session(p) for p in glob.glob(SESSIONS)]
    tools = collections.Counter()
    for r in rows:
        tools.update(r["tools"])
    return {**usage_ranking(rows, datetime.datetime.now(UTC).date()), "tools": tools.most_common(15)}


def account_data():
    return {"totp": auth.totp_enabled, "custom_password": "hash" in auth.creds, "min_password": MIN_PASSWORD_LENGTH}


def daily_summary_forever():
    """Schickt jeden Abend die Tageszahlen per ntfy."""
    zone = zoneinfo.ZoneInfo(SUMMARY_ZONE)
    while True:
        now = datetime.datetime.now(zone)
        target = now.replace(hour=SUMMARY_HOUR, minute=0, second=0, microsecond=0)
        time.sleep(((target if target > now else target + datetime.timedelta(days=1)) - now).total_seconds())
        try:
            day = dict(sessions_data()["daily"]).get(datetime.datetime.now(UTC).date().isoformat(), {})
            notify(f"Heute: {day.get('sessions', 0)} Sessions, {day.get('messages', 0)} Nachrichten, {day.get('output', 0) // 1000} k Output-Tokens")
        except (OSError, KeyError, ValueError, URLError) as e:
            print(f"tagesbericht: {e!r}", file=sys.stderr)


ROUTES = {"/api/sessions": sessions_data, "/api/vault": vault_data, "/api/live": live_data, "/api/usage": usage_data,
          "/api/costs": costs_data, "/api/config": config_data, "/api/system": system_data, "/api/usage-history": usage_history,
          "/api/services": services_data, "/api/backups": backups_data,
          "/api/ranking": ranking_data, "/api/errors": lambda: error_list(glob.glob(SESSIONS)), "/api/vault-health": lambda: vault_health(VAULT),
          "/api/vault-tree": lambda: vault_tree(VAULT), "/api/pins": PINS.all, "/api/widgets": WIDGETS.all, "/api/access": lambda: read_access(ACCESS_LOG), "/api/audit": lambda: read_access(AUDIT_LOG, 200), "/api/account": account_data}
PAGES = {"/": f"{HERE}/index.html", "/graph.html": f"{VAULT}/graphify-out/graph.html", "/login": f"{HERE}/login.html"}
SCRIPTS = {"/qrcode.js": f"{HERE}/qrcode.js", "/features.js": f"{HERE}/features.js", "/control.js": f"{HERE}/control.js", "/widgets.js": f"{HERE}/widgets.js", "/netz.js": f"{HERE}/netz.js", "/netz-einstellungen.js": f"{HERE}/netz-einstellungen.js", "/firewall.js": f"{HERE}/firewall.js", "/docker.js": f"{HERE}/docker.js", "/ftp.js": f"{HERE}/ftp.js", "/dateien.js": f"{HERE}/dateien.js"}  # qrcode-generator 1.4.4 (MIT) erzeugt den QR-Code im Browser
PUBLIC = {  # App-Symbol und Manifest brauchen keine Anmeldung: iOS holt sie beim "Zum Home-Bildschirm"
    "/manifest.webmanifest": (f"{HERE}/manifest.webmanifest", "application/manifest+json"),
    "/sw.js": (f"{HERE}/sw.js", "text/javascript"), "/i18n.js": (f"{HERE}/i18n.js", "text/javascript"), "/i18n-en.js": (f"{HERE}/i18n-en.js", "text/javascript"), "/icon-180.png": (f"{HERE}/icon-180.png", "image/png"),
    "/favicon-32.png": (f"{HERE}/favicon-32.png", "image/png"), "/favicon-64.png": (f"{HERE}/favicon-64.png", "image/png"), "/maskable-512.png": (f"{HERE}/maskable-512.png", "image/png"), "/icon-192.png": (f"{HERE}/icon-192.png", "image/png"), "/icon-512.png": (f"{HERE}/icon-512.png", "image/png"),
}
DAY_SECONDS = 86400
auth = Auth(os.environ.get("DECK_PASSWORD", ""), store=CREDENTIALS)
DOMAIN = os.environ.get("DECK_DOMAIN", "localhost")
BACKUP_DIR = "/root/deck-backups"
BACKUP_SOURCES = [HERE] + [f"{CLAUDE_DIR}/{name}" for name in ("CLAUDE.md", "settings.json", "agents", "skills", "rules")]
FORECAST_ALERT_HOURS = 6
PUSH = Push(f"{HERE}/push-keys.json", f"{HERE}/push-subs.json", f"https://{DOMAIN}")
features = Features(types.SimpleNamespace(
    here=HERE, vault=VAULT, graph=GRAPH, costs_log=COSTS_LOG, sessions_glob=SESSIONS, domain=DOMAIN, backup_dir=BACKUP_DIR, backup_sources=BACKUP_SOURCES, push=PUSH, auth=auth,
    claude_dir=CLAUDE_DIR, claude_bin="/root/.local/bin/claude", roots=["/root"], tmux_socket=None, home=os.path.expanduser("~"),
    live_data=live_data, usage_data=usage_data, notify=lambda text: notify(text), find_session=find_session, read_session=read_session, chat_data=chat_data,
    session_id_ok=lambda s: bool(SESSION_ID.match(s))))
netz = Netz(HERE, auth, lambda text: notify(text), DOMAIN)
firewall = Firewall(lambda text: notify(text), lambda text: log_audit(AUDIT_LOG, "-", "/api/firewall", 200, text))
dateien = Dateien()
ftpb = FtpBrowser(f"{HERE}/ftp-verbindungen.json")
docker = DockerApps(templates=read_json(f"{HERE}/docker-vorlagen.json", []), notify=lambda text: notify(text))
ROUTES.update(features.get_routes())
ROUTES.update(netz.get_routes())
ROUTES.update(firewall.get_routes())
ROUTES.update(docker.get_routes())
ROUTES.update(ftpb.get_routes())
QUERY_ROUTES, PREFIX_ROUTES = features.query_routes(), features.prefix_routes()
QUERY_ROUTES.update(netz.query_routes())
QUERY_ROUTES.update(docker.query_routes())
QUERY_ROUTES.update(ftpb.query_routes())
QUERY_ROUTES.update(dateien.query_routes())
UNGATED = ("/api/firewall/bestaetigen", "/api/firewall/zuruecknehmen")  # nehmen nur eine eigene, noch offene Aenderung an oder zurueck


class Handler(BaseHTTPRequestHandler):
    timeout = 60  # haengende Verbindungen (langsames Senden) nach 60 s kappen
    def client_ip(self):
        return self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()

    def token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return ""

    def reply(self, status, body=b"", ctype="text/html; charset=utf-8", headers=(), cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location, headers=()):
        self.reply(303, headers=[("Location", location), *headers])

    def do_GET(self):
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        if path in PUBLIC:
            file, ctype = PUBLIC[path]
            with open(file, "rb") as f:
                self.reply(200, f.read(), ctype, cache=f"public, max-age={DAY_SECONDS}")
            return
        if path == "/api/shortcut/usage":  # Kurzbefehl: Schluessel im Kopf statt Cookie, nur dieser eine lesende Aufruf
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer ") and auth.api_key_ok(self.client_ip(), header[7:]):
                self.reply(200, json.dumps(features.shortcut_usage()).encode(), "application/json")
            else:
                self.reply(401, b'{"error": "Schluessel"}', "application/json")
            return
        if path != "/login" and not auth.valid(self.token()):
            if path.startswith("/api/"):
                self.reply(401, b'{"error": "login"}', "application/json")
            else:
                self.redirect("/login")
            return
        if path == "/netz-agent.zip":
            self.reply(200, netz.agent_zip(), "application/zip", [("Content-Disposition", 'attachment; filename="netz-agent.zip"')])
            return
        after, search = query.get("after", ["0"])[0], query.get("q", [""])[0]
        if path == "/api/ftp/download":
            try:
                name, body = ftpb.download(query)
            except ValueError as e:
                self.reply(400, json.dumps({"error": str(e)}).encode(), "application/json")
                return
            self.reply(200, body, "application/octet-stream", [("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")])
            return
        if path == "/api/dateien/download":  # Inhalt der Serverplatte: nur mit erhoehter Sicherheitsstufe
            try:
                if not auth.is_elevated(self.token()):
                    raise PermissionError("stepup")
                name, body, real = dateien.download(query)
            except (PermissionError, ValueError) as e:
                self.reply(403 if isinstance(e, PermissionError) else 400, json.dumps({"error": str(e)}).encode(), "application/json")
                return
            log_audit(AUDIT_LOG, self.client_ip(), path, 200, f"Dateien: {real} geladen")
            self.reply(200, body, "application/octet-stream", [("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")])
            return
        if path == "/api/stream":
            self.stream()
            return
        if path == "/api/stepup/status":
            self.reply(200, json.dumps({"elevated": auth.is_elevated(self.token()), "totp": auth.totp_enabled}).encode(), "application/json")
            return
        prefix = next((p for p in PREFIX_ROUTES if path.startswith(p)), None)
        if path in ROUTES:
            result = ROUTES[path]()
        elif path in QUERY_ROUTES:
            try:
                result = QUERY_ROUTES[path](query)
            except ValueError as e:  # falsche Eingabe oder Fehler des FTP-Servers: als Meldung zurueck, nicht als Verbindungsabbruch
                result = {"error": str(e)}
        elif prefix:
            result = PREFIX_ROUTES[prefix](path[len(prefix):], query)
        elif path.startswith("/api/chat/") and after.isdigit():
            result = chat_data(path[len("/api/chat/"):], int(after))
        elif path == "/api/search" and len(search) >= SEARCH_MIN:
            result = {"sessions": search_data(search), "vault": graph_search(search)}
        else:
            result = None
        if result is not None:
            self.reply(200, json.dumps(result).encode(), "application/json")
        elif path in PAGES or path in SCRIPTS:
            with open(PAGES.get(path) or SCRIPTS[path], "rb") as f:
                body = f.read()
            if path == "/graph.html":
                body = with_mobile_layout(body.decode("utf-8")).encode("utf-8")
            self.reply(200, body, "text/javascript; charset=utf-8" if path in SCRIPTS else "text/html; charset=utf-8")
        else:
            self.send_error(404)

    def stream(self):
        """Server-Sent Events: alle MONITOR_SECONDS Sekunden die laufenden Sessions und der Aktivitaets-Feed."""
        self.send_response(200)
        for key, value in (("Content-Type", "text/event-stream"), ("Cache-Control", "no-store"), ("X-Accel-Buffering", "no")):
            self.send_header(key, value)
        self.end_headers()
        deadline = time.time() + STREAM_SECONDS
        try:
            while time.time() < deadline:
                self.wfile.write(b"data: " + json.dumps(features.stream_payload()).encode() + b"\n\n")
                self.wfile.flush()
                time.sleep(MONITOR_SECONDS)
        except (BrokenPipeError, ConnectionResetError):
            return

    def read_body(self, limit=MAX_BODY):
        length = self.headers.get("Content-Length", "")
        return self.rfile.read(int(length)) if length.isdigit() and int(length) <= limit else None

    def session_cookie(self, token):
        return ("Set-Cookie", f"{COOKIE}={token}; Max-Age={auth.remaining(token)}; {COOKIE_FLAGS}")

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/logout":
            auth.logout(self.token())
            self.redirect("/login", [("Set-Cookie", f"{COOKIE}=; Max-Age=0; {COOKIE_FLAGS}")])
        elif path == "/login":
            self.handle_login()
        elif path == "/api/netz/push":  # Agent im Heimnetz: eigener Token statt Sitzung
            self.handle_netz_push()
        elif path.startswith("/api/") and auth.valid(self.token()) and self.headers.get("X-Deck") == "1":
            self.handle_api_post(path)
        elif path.startswith("/api/"):
            self.reply(401, b'{"error": "login"}', "application/json")
        else:
            self.send_error(404)

    def handle_netz_push(self):
        header, ip = self.headers.get("Authorization", ""), self.client_ip()
        token = header[7:] if header.startswith("Bearer ") else ""
        try:
            netz.token_pruefen(ip, token)  # erst die Berechtigung, dann den (bis zu 2 MB grossen) Inhalt lesen
            raw = self.read_body(PUSH_BODY)
            if raw is None:
                self.send_error(413)
                return
            status, result = 200, netz.push(ip, token, raw)
        except PermissionError:
            print(f"Agent-Token abgelehnt von {ip}", file=sys.stderr)
            status, result = 401, {"error": "token"}
        except ValueError as e:
            status, result = 400, {"error": str(e)}
        self.reply(status, json.dumps(result).encode(), "application/json")

    def handle_login(self):
        raw = self.read_body()
        if raw is None:
            self.send_error(413)
            return
        form = parse_qs(raw.decode("utf-8", "replace"))
        ip = self.client_ip()
        token = auth.login(ip, form.get("password", [""])[0], form.get("code", [""])[0], stay=form.get("bleiben", [""])[0] == "1")
        if token:
            log_access(ACCESS_LOG, ip, "login")
            self.redirect("/", [self.session_cookie(token)])
            return
        locked = auth.locked(ip)
        log_access(ACCESS_LOG, ip, "sperre" if locked else "fehlschlag")
        print(f"Login fehlgeschlagen von {ip}", file=sys.stderr)
        if locked:
            self.notify_safely(f"Claude Deck: {ip} nach Fehlversuchen gesperrt")
        self.redirect("/login?fehler=" + ("gesperrt" if locked else "1"))

    def notify_safely(self, text):
        try:
            notify(text)
        except URLError as e:
            print(f"ntfy: {e!r}", file=sys.stderr)

    def handle_api_post(self, path):
        raw = self.read_body(NETZ_BODY if path.startswith("/api/netz/") else FTP_BODY if path in ("/api/ftp/hochladen", "/api/dateien/hochladen", "/api/dateien/speichern") else MAX_BODY)
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self.reply(400, b'{"error": "ungueltige Anfrage"}', "application/json")
            return
        ip, headers = self.client_ip(), []
        text = lambda key: str(data.get(key, ""))
        try:
            if auth.locked(ip):
                status, payload = 429, {"error": "Zu viele Fehlversuche. Bitte später erneut versuchen."}
            elif path == "/api/stepup":
                ok = auth.step_up(ip, self.token(), text("password"), text("code"))
                status, payload = (200, {"ok": True}) if ok else (403, {"error": "Passwort oder Code falsch."})
                log_audit(AUDIT_LOG, ip, path, status, "Sicherheitsstufe")
            elif path == "/api/account/password":
                token = auth.change_password(ip, text("current"), text("code"), text("new"))
                status, payload = (200, {"ok": True}) if token else (403, {"error": "Passwort oder Code falsch."})
                if token:
                    log_access(ACCESS_LOG, ip, "passwort")
                    headers = [self.session_cookie(token)]
            elif path == "/api/account/totp/begin":
                secret = auth.begin_totp()
                status, payload = 200, {"secret": secret, "uri": f"otpauth://totp/Claude%20Deck?secret={secret}&issuer=Claude%20Deck"}
            elif path == "/api/account/totp/enable":
                ok = auth.enable_totp(ip, text("code"))
                status, payload = (200, {"ok": True}) if ok else (403, {"error": "Code falsch."})
                if ok:
                    log_access(ACCESS_LOG, ip, "2fa-an")
            elif path == "/api/account/totp/disable":
                ok = auth.disable_totp(ip, text("password"), text("code"))
                status, payload = (200, {"ok": True}) if ok else (403, {"error": "Passwort oder Code falsch."})
                if ok:
                    log_access(ACCESS_LOG, ip, "2fa-aus")
            elif path.startswith("/api/pins/") and SESSION_ID.match(path[len("/api/pins/"):]):
                PINS.set(path[len("/api/pins/"):], data.get("pinned"), text("note"))
                status, payload = 200, {"ok": True}
            elif path == "/api/widgets":
                status, payload = 200, WIDGETS.save(data)
            elif path.startswith(("/api/firewall/", "/api/docker/", "/api/ftp/", "/api/dateien/")):
                if path not in UNGATED and not auth.is_elevated(self.token()):
                    raise PermissionError("stepup")
                status, payload = firewall.post(path, data) or docker.post(path, data) or ftpb.post(path, data) or dateien.post(path, data) or (404, {"error": "unbekannt"})
            else:
                handled = netz.post(path, data, ip, self.token()) or features.post(path, data, ip, self.token())
                status, payload = handled or (404, {"error": "unbekannt"})
        except PermissionError:
            status, payload = 403, {"error": "stepup"}
        except ValueError as e:
            status, payload = 400, {"error": str(e)}
        if isinstance(payload, dict) and path != "/api/stepup":
            log_audit(AUDIT_LOG, ip, path, status, payload.pop("_audit", ""))
        self.reply(status, json.dumps(payload).encode(), "application/json", headers)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if not os.environ.get("DECK_PASSWORD") and "hash" not in auth.creds:
        raise SystemExit("DECK_PASSWORD fehlt")
    threading.Thread(target=sample_usage_forever, daemon=True).start()
    threading.Thread(target=features.monitor_forever, daemon=True).start()
    threading.Thread(target=netz.schleife, daemon=True).start()
    if NTFY_TOPIC:
        threading.Thread(target=daily_summary_forever, daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
