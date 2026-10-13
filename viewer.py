"""Nur lesende Ansichten fuer Verwaltung und Betrieb: tmux-Sessions (Text der Terminals), lokale Git-Repositories, GitHub-Aufgaben,
Anbieter-Status, Harnesses, Slash-Befehle, Output-Styles und Statusline sowie Teams und Postfach als reine Notizen.
Nichts in diesem Modul startet Sessions, sendet Eingaben oder aendert Einstellungen."""
import glob
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

from storage import read_json, write_json

NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
NEW_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")
PANE_LINES, MAX_TEAM, MAX_MAIL, MAX_TEXT = 200, 8, 200, 4000
HARNESSES = ("claude", "codex", "copilot", "opencode", "pi", "tmux", "git", "gh")
VERSION_FLAG = {"tmux": "-V"}  # tmux kennt --version nicht
STATUS_URL = "https://status.claude.com/api/v2/summary.json"
STATUS_CACHE_SECONDS = 60
SKIP_DIRS = {".claude", "node_modules", ".cache", ".npm", ".local", ".git", "__pycache__"}
SECRET_URL = re.compile(r"//[^/@\s]+:[^/@\s]+@")


def frontmatter_description(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(25), f):
                if line.startswith("description:"):
                    return line.split(":", 1)[1].strip().strip("\"'")[:200]
    except OSError:
        pass
    return ""


class Viewer:
    def __init__(self, deps):
        self.d = deps
        self.status_cache = (0.0, None)

    # --- tmux (nur lesen) --------------------------------------------------------------
    def tmux(self, *args):
        cmd = ["tmux", *(["-L", self.d.tmux_socket] if self.d.tmux_socket else []), *args]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    def sessions(self):
        r = self.tmux("list-sessions", "-F", "#{session_name}|#{session_created}|#{session_attached}|#{session_windows}|#{pane_current_command}|#{pane_current_path}")
        rows = [line.split("|") for line in r.stdout.splitlines()] if r.returncode == 0 else []
        return [{"name": n, "created": int(c), "attached": int(a), "windows": int(w), "command": cmd, "path": p} for n, c, a, w, cmd, p in rows]

    def pane(self, query):
        name = query.get("name", [""])[0]
        if not NAME.fullmatch(name):
            raise ValueError("ungueltiger Sitzungsname")
        lines = min(1000, max(10, int(query.get("lines", [str(PANE_LINES)])[0])))
        r = self.tmux("capture-pane", "-p", "-J", "-t", f"={name}:", "-S", f"-{lines}")
        if r.returncode:
            raise ValueError("Sitzung nicht gefunden")
        return {"name": name, "text": r.stdout}

    # --- Teams und Postfach: gespeicherte Notizen, es wird nichts gestartet --------------
    def agent_names(self):
        return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(f"{self.d.claude_dir}/agents/*.md"))

    def allowed_cwd(self, cwd):
        real = os.path.realpath(cwd or self.d.roots[0])
        if not os.path.isdir(real) or not any(real == root or real.startswith(root + os.sep) for root in self.d.roots):
            raise ValueError("Ordner nicht erlaubt")
        return real

    def teams(self):
        return read_json(f"{self.d.here}/teams.json", {})

    def team_save(self, data):
        name = str(data.get("name", ""))
        if not NEW_NAME.fullmatch(name):
            raise ValueError("Teamname: nur Buchstaben, Ziffern, _ und -")
        members, leader, known = [str(m) for m in data.get("members", [])], str(data.get("leader", "")), self.agent_names()
        if not members or len(members) > MAX_TEAM or leader not in members or any(m not in known for m in members):
            raise ValueError(f"1 bis {MAX_TEAM} bekannte Agents, der Leiter muss dabei sein")
        teams = self.teams()
        teams[name] = {"leader": leader, "members": members, "cwd": self.allowed_cwd(data.get("cwd")), "prompt": str(data.get("prompt", ""))[:MAX_TEXT]}
        write_json(f"{self.d.here}/teams.json", teams)
        return {"ok": True, "_audit": f"Team gespeichert: {name}"}

    def team_delete(self, data):
        teams = self.teams()
        teams.pop(str(data.get("name", "")), None)
        write_json(f"{self.d.here}/teams.json", teams)
        return {"ok": True, "_audit": f"Team geloescht: {data.get('name')}"}

    def team_plan(self, query):
        name = query.get("name", [""])[0]
        team = self.teams().get(name)
        if not team:
            raise ValueError("Team unbekannt")
        ordered = [team["leader"]] + [m for m in team["members"] if m != team["leader"]]
        return {"team": name, "cwd": team["cwd"], "steps": [{"session": f"deck-team-{name}-{a}", "agent": a, "leader": i == 0,
                                                              "command": f"claude --agent {a}" + (' "<Auftrag>"' if i == 0 and team["prompt"] else "")} for i, a in enumerate(ordered)]}

    def mail(self):
        return read_json(f"{self.d.here}/mail.json", [])

    def _mail_save(self, rows):
        write_json(f"{self.d.here}/mail.json", rows[-MAX_MAIL:])

    def mail_send(self, data):
        rows = self.mail()
        row = {"id": (rows[-1]["id"] + 1) if rows else 1, "t": int(time.time()), "from": str(data.get("from", "Ich"))[:40], "to": str(data.get("to", ""))[:64],
               "subject": str(data.get("subject", ""))[:120], "body": str(data.get("body", ""))[:MAX_TEXT], "status": "neu"}
        if not row["body"]:
            raise ValueError("Nachricht ist leer")
        self._mail_save(rows + [row])
        return {"ok": True, "id": row["id"]}

    def mail_mark(self, data):
        rows = self.mail()
        for r in rows:
            if r["id"] == data.get("id") and r["status"] == "neu":
                r["status"] = "gelesen"
        self._mail_save(rows)
        return {"ok": True}

    def mail_delete(self, data):
        self._mail_save([r for r in self.mail() if r["id"] != data.get("id")])
        return {"ok": True}

    # --- Repositories und Aufgaben (nur lesen) -------------------------------------------
    def git(self, path, *args):
        return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=10).stdout.strip()

    def repo_paths(self):
        found = []
        for root in self.d.roots:
            base = root.rstrip("/").count("/")
            for current, dirs, _ in os.walk(root):
                if ".git" in dirs:
                    found.append(current)
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and current.count("/") - base < 3]
        return sorted(set(found))

    def repos(self):
        rows = []
        for path in self.repo_paths():
            status = self.git(path, "status", "--porcelain=v1", "-b").splitlines()
            rows.append({"path": path, "branch": status[0][3:].split("...")[0] if status else "?", "dirty": max(0, len(status) - 1),
                         "ahead_behind": status[0].split("[", 1)[1].rstrip("]") if status and "[" in status[0] else "",
                         "last": self.git(path, "log", "-1", "--format=%h|%s|%cr"), "remote": SECRET_URL.sub("//***@", self.git(path, "remote", "get-url", "origin"))})
        return rows

    def issues(self):
        if not shutil.which("gh") or subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=10).returncode:
            return {"available": False, "hint": "GitHub CLI (gh) ist nicht installiert oder nicht angemeldet. Mit `gh auth login` auf dem VPS anmelden."}
        out = []
        for repo in self.repos():
            m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", repo["remote"])
            if m:
                r = subprocess.run(["gh", "issue", "list", "--repo", m.group(1), "--limit", "30", "--json", "number,title,state,labels,assignees,updatedAt"], capture_output=True, text=True, timeout=30)
                out += [{**i, "repo": m.group(1)} for i in (json.loads(r.stdout) if r.returncode == 0 else [])]
        return {"available": True, "issues": out}

    # --- Anbieter, Harnesses, Befehle, Styles --------------------------------------------
    def provider(self):
        stamp, cached = self.status_cache
        if cached and time.time() - stamp < STATUS_CACHE_SECONDS:
            return cached
        try:
            with urllib.request.urlopen(urllib.request.Request(STATUS_URL, headers={"User-Agent": "claude-deck"}), timeout=8) as r:
                raw = json.load(r)
            result = {"ok": True, "indicator": raw["status"]["indicator"], "description": raw["status"]["description"],
                      "components": [{"name": c["name"], "status": c["status"]} for c in raw.get("components", [])],
                      "incidents": [{"name": i["name"], "status": i["status"]} for i in raw.get("incidents", [])]}
        except (OSError, ValueError, KeyError) as e:
            result = {"ok": False, "error": f"Statusseite nicht erreichbar ({type(e).__name__})"}
        self.status_cache = (time.time(), result)
        return result

    def harnesses(self):
        rows = []
        for name in HARNESSES:
            path = shutil.which(name) or (self.d.claude_bin if name == "claude" and os.path.exists(self.d.claude_bin) else None)
            version = ""
            if path:
                try:
                    version = subprocess.run([path, VERSION_FLAG.get(name, "--version")], capture_output=True, text=True, timeout=5).stdout.strip().splitlines()[0][:80]
                except (OSError, subprocess.TimeoutExpired, IndexError):
                    version = "?"
            rows.append({"name": name, "path": path, "version": version})
        return rows

    def settings_data(self):
        return read_json(f"{self.d.claude_dir}/settings.json", {})

    def _folders(self, own, plugin_subfolder):
        return [(f"{self.d.claude_dir}/{own}", "eigene")] + [(p, p.split("/")[-4]) for p in glob.glob(f"{self.d.claude_dir}/plugins/cache/*/*/*/{plugin_subfolder}")]

    def slash_commands(self):
        rows = []
        for folder, source in self._folders("commands", "commands"):
            for path in glob.glob(f"{folder}/**/*.md", recursive=True):
                rows.append({"name": "/" + os.path.splitext(os.path.relpath(path, folder))[0].replace(os.sep, ":"), "source": source, "description": frontmatter_description(path)})
        return sorted(rows, key=lambda r: (r["source"], r["name"]))

    def output_styles(self):
        rows = [{"name": os.path.splitext(os.path.basename(p))[0], "source": source, "description": frontmatter_description(p)}
                for folder, source in self._folders("output-styles", "output-styles") for p in glob.glob(f"{folder}/*.md")]
        settings = self.settings_data()
        status = settings.get("statusLine")
        command = status.get("command", "").split()[0] if isinstance(status, dict) and status.get("command") else ""
        script = ""
        if command and os.path.isfile(os.path.expanduser(command)):
            with open(os.path.expanduser(command), encoding="utf-8", errors="replace") as f:
                script = f.read(20000)
        return {"styles": sorted(rows, key=lambda r: (r["source"], r["name"])), "active": settings.get("outputStyle"), "statusline": status, "statusline_script": script}

    # --- Tabellen fuer Features ------------------------------------------------------------
    def get_routes(self):
        return {"/api/tmux": self.sessions, "/api/teams": self.teams, "/api/mail": self.mail, "/api/repos": self.repos, "/api/issues": self.issues,
                "/api/provider": self.provider, "/api/harnesses": self.harnesses, "/api/commands": self.slash_commands, "/api/styles": self.output_styles, "/api/agents-list": self.agent_names}

    def query_routes(self):
        return {"/api/tmux/pane": self.pane, "/api/team-plan": self.team_plan}

    def post(self, path):
        return {"/api/team/save": self.team_save, "/api/team/delete": self.team_delete, "/api/mail/send": self.mail_send, "/api/mail/read": self.mail_mark, "/api/mail/delete": self.mail_delete}.get(path)
