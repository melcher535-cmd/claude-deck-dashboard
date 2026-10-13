"""Ausfuehrende Funktionen: tmux bedienen, Sessions und Teams starten, Einstellungen und MCP-Server bearbeiten, Sicherung zurueckspielen,
Postfach zustellen. Jede dieser Aktionen verlangt die Sicherheitsstufe (Auth.step_up: Passwort und Code erneut, 5 Minuten) und
wird im Audit-Protokoll festgehalten. Alle Eingaben werden gegen Listen und Muster geprueft, es laeuft nie ein Shell-Text."""
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import tarfile

from upkeep import create_backup
from viewer import NAME, NEW_NAME, Viewer

BACKUP_NAME = re.compile(r"deck-\d{4}-\d{2}-\d{2}_\d{6}\.tar\.gz")
SESSION_PREFIX = "deck-"
ALLOWED_KEYS = {"Enter", "Escape", "C-c", "C-d", "Tab", "Up", "Down", "Left", "Right", "BSpace", "y", "n"}
MAX_SEND, MAX_PROMPT, SETTINGS_BACKUPS = 2000, 4000, 20
RESTORE_PARTS = ("CLAUDE.md", "settings.json", "agents", "skills", "rules")
SECRET_URL = re.compile(r"//[^/@\s]+:[^/@\s]+@")
CRITICAL = {"/api/tmux/send", "/api/tmux/start", "/api/tmux/kill", "/api/settings", "/api/mcp/add", "/api/mcp/remove", "/api/restore", "/api/team/start", "/api/mail/deliver"}


class Control(Viewer):
    # --- tmux bedienen ----------------------------------------------------------------
    def _tmux_ok(self, *args):
        r = self.tmux(*args)
        if r.returncode:
            raise ValueError((r.stderr or "tmux-Fehler").strip()[:200])

    def send(self, data):
        name = str(data.get("name", ""))
        if not NAME.fullmatch(name):
            raise ValueError("ungueltiger Sitzungsname")
        text, key = data.get("text"), data.get("key")
        if text is not None:
            text = str(text)
            if not text or len(text) > MAX_SEND or "\x00" in text:
                raise ValueError("Text leer oder zu lang")
            self._tmux_ok("send-keys", "-t", f"={name}:", "-l", "--", text)
        if key:
            if key not in ALLOWED_KEYS:
                raise ValueError("Taste nicht erlaubt")
            self._tmux_ok("send-keys", "-t", f"={name}:", key)
        return {"ok": True, "_audit": f"tmux {name}: " + (f"Text {len(text)} Zeichen " if text else "") + (key or "")}

    def start(self, data):
        short = str(data.get("name") or "")
        if not NEW_NAME.fullmatch(short):
            raise ValueError("Name: nur Buchstaben, Ziffern, _ und -, bis 40 Zeichen")
        name, cwd, agent, prompt = SESSION_PREFIX + short, self.allowed_cwd(data.get("cwd")), data.get("agent") or "", str(data.get("prompt") or "")
        if agent and agent not in self.agent_names():
            raise ValueError("Agent unbekannt")
        if len(prompt) > MAX_PROMPT or prompt.startswith("-"):
            raise ValueError("Auftrag zu lang oder beginnt mit -")
        argv = [self.d.claude_bin, *(["--agent", agent] if agent else []), *([prompt] if prompt else [])]
        self._tmux_ok("new-session", "-d", "-s", name, "-c", cwd, "-x", "180", "-y", "50", *argv)  # direkt ausgefuehrt, ohne Shell
        return {"ok": True, "name": name, "_audit": f"Start {name} in {cwd}" + (f" mit Agent {agent}" if agent else "")}

    def kill(self, data):
        name = str(data.get("name", ""))
        if not NAME.fullmatch(name) or not name.startswith(SESSION_PREFIX):
            raise ValueError("Nur Sitzungen, die das Dashboard gestartet hat (deck-…), lassen sich beenden")
        self._tmux_ok("kill-session", "-t", f"={name}")
        return {"ok": True, "_audit": f"Beendet {name}"}

    def team_start(self, data):
        name = str(data.get("name", ""))
        plan = self.team_plan({"name": [name]})
        team, started = self.teams()[name], []
        for step in plan["steps"]:
            self.start({"name": step["session"][len(SESSION_PREFIX):], "cwd": team["cwd"], "agent": step["agent"], "prompt": team["prompt"] if step["leader"] else ""})
            started.append(step["session"])
        return {"ok": True, "started": started, "_audit": f"Team gestartet: {name} ({len(started)} Sitzungen)"}

    def mail_deliver(self, data):
        """Nachricht als Eingabe in eine tmux-Sitzung tippen (Uebergabe an einen laufenden Agent)."""
        rows = self.mail()
        row = next((r for r in rows if r["id"] == data.get("id")), None)
        if not row or not row["to"]:
            raise ValueError("Nachricht oder Empfaenger fehlt")
        self.send({"name": row["to"], "text": row["body"]})
        self.send({"name": row["to"], "key": "Enter"})
        row["status"] = "zugestellt"
        self._mail_save(rows)
        return {"ok": True, "_audit": f"Postfach {row['id']} zugestellt an {row['to']}"}

    # --- Einstellungen, Hooks, Rechte ---------------------------------------------------
    def settings_raw(self):
        path = f"{self.d.claude_dir}/settings.json"
        with open(path, encoding="utf-8") as f:
            return {"text": f.read(), "mtime": os.path.getmtime(path)}

    def settings_write(self, data):
        """Neue settings.json speichern: muss ein JSON-Objekt sein, die alte Datei wird vorher gesichert."""
        parsed = json.loads(str(data.get("text", "")))
        if not isinstance(parsed, dict):
            raise ValueError("settings.json muss ein JSON-Objekt sein")
        path, backups = f"{self.d.claude_dir}/settings.json", f"{self.d.claude_dir}/backups"
        os.makedirs(backups, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        shutil.copy2(path, f"{backups}/settings.json.{stamp}")
        for old in sorted(glob.glob(f"{backups}/settings.json.*"))[:-SETTINGS_BACKUPS]:
            os.remove(old)
        mode, tmp = os.stat(path).st_mode & 0o777, f"{path}.deck-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(parsed, indent=2, ensure_ascii=False) + "\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        return {"ok": True, "backup": f"settings.json.{stamp}", "_audit": f"settings.json gespeichert, Schluessel: {', '.join(sorted(parsed))[:120]}"}

    # --- MCP-Server -------------------------------------------------------------------------
    def claude(self, *args, timeout=60):
        return subprocess.run([self.d.claude_bin, *args], capture_output=True, text=True, timeout=timeout, env={**os.environ, "HOME": self.d.home})

    def mcp_list(self):
        r = self.claude("mcp", "list")
        servers = []
        for line in r.stdout.splitlines():
            m = re.match(r"^(.*?): (.*) - (\S+)\s*(.*)$", line)
            if m:
                servers.append({"name": m.group(1), "target": SECRET_URL.sub("//***@", m.group(2)), "status": f"{m.group(3)} {m.group(4)}".strip()})
        return {"servers": servers}

    def mcp_add(self, data):
        name, config = str(data.get("name", "")), data.get("config")
        if not NEW_NAME.fullmatch(name):
            raise ValueError("Name: nur Buchstaben, Ziffern, _ und -")
        if not isinstance(config, dict) or not (isinstance(config.get("command"), str) or str(config.get("url", "")).startswith(("http://", "https://"))):
            raise ValueError('Konfiguration braucht "command" (stdio) oder "url" (http)')
        r = self.claude("mcp", "add-json", name, json.dumps(config), "--scope", "user")
        if r.returncode:
            raise ValueError((r.stderr or r.stdout).strip()[:300])
        return {"ok": True, "_audit": f"MCP hinzugefuegt: {name} ({'command' if 'command' in config else 'url'})"}

    def mcp_remove(self, data):
        name = str(data.get("name", ""))
        if not NEW_NAME.fullmatch(name):
            raise ValueError("ungueltiger Name")
        r = self.claude("mcp", "remove", name, "--scope", "user")
        if r.returncode:
            raise ValueError((r.stderr or r.stdout).strip()[:300])
        return {"ok": True, "_audit": f"MCP entfernt: {name}"}

    # --- Sicherung zurueckspielen -------------------------------------------------------------
    def restore(self, data):
        name, parts = str(data.get("name", "")), [p for p in data.get("parts", []) if p in RESTORE_PARTS]
        archive = os.path.join(self.d.backup_dir, name)
        if not BACKUP_NAME.fullmatch(name) or not parts or not os.path.isfile(archive):
            raise ValueError("Sicherung oder Teile ungueltig")
        safety = create_backup(self.d.backup_dir, self.d.backup_sources)  # Sicherung des jetzigen Stands, bevor etwas ueberschrieben wird
        with tarfile.open(archive) as tar:
            members = [m for m in tar.getmembers() if m.name.split("/")[0] in parts and not m.name.startswith("/") and ".." not in m.name.split("/")]
            tar.extractall(self.d.claude_dir, members=members, filter="data")
        restored = sorted({m.name.split("/")[0] for m in members})
        return {"ok": True, "restored": restored, "safety": os.path.basename(safety), "_audit": f"Zurueckgespielt aus {name}: {', '.join(restored)}"}

    # --- Tabellen fuer Features -----------------------------------------------------------------
    def get_routes(self):
        return {**super().get_routes(), "/api/settings-raw": self.settings_raw, "/api/mcp-list": self.mcp_list}

    def post(self, path, token=None):
        """Gibt die Funktion zum Pfad zurueck (oder None). Kritische Pfade nur mit Sicherheitsstufe."""
        routes = {"/api/tmux/send": self.send, "/api/tmux/start": self.start, "/api/tmux/kill": self.kill, "/api/settings": self.settings_write, "/api/mcp/add": self.mcp_add,
                  "/api/mcp/remove": self.mcp_remove, "/api/restore": self.restore, "/api/team/start": self.team_start, "/api/mail/deliver": self.mail_deliver}
        if path in routes:
            if not self.d.auth.is_elevated(token):
                raise PermissionError("stepup")
            return routes[path]
        return super().post(path)
