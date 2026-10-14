"""Firewall des Servers (ufw): Regeln lesen, hinzufuegen, loeschen, Firewall an und aus, dazu die lauschenden Ports.
Jede Aenderung kann mit Ruecknahme-Timer laufen: kommt keine Bestaetigung, stellt der Server den alten Stand selbst wieder her.
Es laeuft nie ein Shell-Text, alle Eingaben werden gegen Muster geprueft. SSH, 80 und 443 sind gegen Aussperren geschuetzt."""
import ipaddress
import os
import re
import subprocess
import threading
import time
import uuid

RULE = re.compile(r"^\[\s*(\d+)\]\s+(.+?)\s{2,}(ALLOW|DENY|REJECT|LIMIT)(?: (IN|OUT|FWD))?\s{2,}(.+?)\s*$")
TARGET = re.compile(r"^(\d{1,5})(?::(\d{1,5}))?(?:/(tcp|udp))?(?: \(v6\))?$")
DEFAULTS = re.compile(r"(\w+) \((incoming|outgoing|routed)\)")
COMMENT = re.compile(r"^[\w .,:+@()-]{1,40}$")
ACTIONS = ("allow", "deny", "reject", "limit")
ALWAYS_OPEN = (80, 443)
ROLLBACK_SECONDS = 60
SNAPSHOT_FILES = ("user.rules", "user6.rules", "ufw.conf")
CMD_TIMEOUT = 20


def run_command(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=CMD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 1, f"{argv[0]}: Zeitüberschreitung"
    return r.returncode, (r.stdout + r.stderr).strip()


def parse_rules(text):
    rules = []
    for line in text.splitlines():
        m = RULE.match(line)
        if not m:
            continue
        nr, target, action, direction, source = m.groups()
        source, _, comment = source.partition(" # ")
        t = TARGET.match(target)
        rules.append({"nr": int(nr), "nach": target, "aktion": action, "richtung": direction or "IN", "von": source.strip(), "kommentar": comment.strip(),
                      "v6": "(v6)" in target, "port": f"{t.group(1)}:{t.group(2)}" if t and t.group(2) else (t.group(1) if t else ""), "proto": (t.group(3) or "") if t else ""})
    return rules


def parse_listening(text):
    """Zeilen von `ss -H -tulnp` zu {proto, adresse, port, prozess, lokal}. IPv4 und IPv6 desselben Ports werden zusammengefasst."""
    seen, ports = set(), []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 6 or f[0] not in ("tcp", "udp"):
            continue
        addr, _, port = f[4].rpartition(":")
        proc = re.search(r'\(\("([^"]+)"', " ".join(f[6:]))
        local = addr.startswith("127.") or addr in ("[::1]", "::1")
        key = (f[0], port, local)
        if key in seen or not port.isdigit():
            continue
        seen.add(key)
        ports.append({"proto": f[0], "adresse": addr, "port": int(port), "prozess": proc.group(1) if proc else "", "lokal": local})
    return sorted(ports, key=lambda p: (p["lokal"], p["port"]))


def covers(rule, port, proto):
    low, _, high = rule["port"].partition(":")
    if not low or not int(low) <= port <= int(high or low):
        return False
    return not rule["proto"] or rule["proto"] == proto


def exposure(entry, rules):
    """Zustand eines lauschenden Ports: lokal, docker (ufw wirkungslos), offen, eingeschraenkt oder blockiert."""
    if entry["lokal"]:
        return "lokal"
    if entry["prozess"] == "docker-proxy":
        return "docker"
    hits = [r for r in rules if r["aktion"] in ("ALLOW", "LIMIT") and r["richtung"] == "IN" and covers(r, entry["port"], entry["proto"])]
    if not hits:
        return "blockiert"
    return "offen" if any(r["von"].startswith("Anywhere") for r in hits) else "eingeschraenkt"


class Firewall:
    def __init__(self, notify=lambda text: None, audit=lambda text: None, run=run_command, ufw_dir="/etc/ufw", clock=time.time, seconds=ROLLBACK_SECONDS):
        self.notify, self.audit, self.run, self.ufw_dir, self.clock, self.seconds = notify, audit, run, ufw_dir, clock, seconds
        self.lock, self.pending, self.timer = threading.Lock(), None, None

    # --- Lesen -------------------------------------------------------------------------
    def installed(self):
        try:
            return self.run(["ufw", "version"])[0] == 0
        except FileNotFoundError:
            return False

    def rules(self):
        rc, out = self.run(["ufw", "status", "numbered"])
        if rc:
            raise ValueError(f"ufw: {out[:200]}")
        return out.startswith("Status: active"), parse_rules(out)

    def defaults(self):
        _, out = self.run(["ufw", "status", "verbose"])
        names = {"incoming": "eingehend", "outgoing": "ausgehend", "routed": "weitergeleitet"}
        return {names[kind]: policy for policy, kind in DEFAULTS.findall(out)}

    def listening(self):
        return parse_listening(self.run(["ss", "-H", "-tulnp"])[1])

    def protected_ports(self):
        return sorted({*ALWAYS_OPEN, *(p["port"] for p in self.listening() if p["prozess"] == "sshd")})

    def view(self):
        if not self.installed():
            return {"verfuegbar": False}
        active, rules = self.rules()
        return {"verfuegbar": True, "aktiv": active, "standard": self.defaults(), "regeln": rules, "geschuetzt": self.protected_ports(), "ausstehend": self.pending_view(),
                "ports": [{**p, "zustand": exposure(p, rules)} for p in self.listening()]}

    def pending_view(self):
        p = self.pending
        return {"id": p["id"], "text": p["text"], "sekunden": max(0, int(p["ablauf"] - self.clock()))} if p else None

    # --- Schutz und Ruecknahme --------------------------------------------------------------
    def _snapshot(self):
        snap = {}
        for name in SNAPSHOT_FILES:
            with open(os.path.join(self.ufw_dir, name), encoding="utf-8") as f:
                snap[name] = f.read()
        return snap

    def _restore(self, snap, was_active):
        for name, text in snap.items():
            with open(os.path.join(self.ufw_dir, name), "w", encoding="utf-8") as f:
                f.write(text)
        self.run(["ufw", "--force", "enable"] if was_active else ["ufw", "disable"])

    def _arm(self, snap, was_active, text):
        ident = uuid.uuid4().hex[:8]
        self.pending = {"id": ident, "text": text, "ablauf": self.clock() + self.seconds, "snap": snap, "aktiv": was_active}
        self.timer = threading.Timer(self.seconds, self._expire, args=(ident,))
        self.timer.daemon = True
        self.timer.start()
        return ident

    def _expire(self, ident):
        with self.lock:
            p = self.pending
            if not p or p["id"] != ident:
                return
            self._restore(p["snap"], p["aktiv"])
            self.pending = None
        self.audit(f"Firewall: Aenderung automatisch zurueckgenommen ({p['text']})")
        self.notify(f"Firewall-Änderung nicht bestätigt und zurückgenommen: {p['text']}")

    def _close(self, ident, restore):
        with self.lock:
            p = self.pending
            if not p or p["id"] != ident:
                raise ValueError("keine offene Änderung mit dieser Kennung")
            self.timer.cancel()
            if restore:
                self._restore(p["snap"], p["aktiv"])
            self.pending = None
        return p["text"]

    def confirm(self, ident):
        return {"ok": True, "_audit": f"Firewall bestaetigt: {self._close(ident, False)}"}

    def rollback_now(self, ident):
        return {"ok": True, "_audit": f"Firewall zurueckgenommen: {self._close(ident, True)}"}

    def _change(self, argv, text, rollback):
        """Fuehrt einen ufw-Befehl aus, mit Ruecknahme-Timer, wenn gewuenscht. Es wartet nie mehr als eine Aenderung zugleich."""
        with self.lock:
            if self.pending:
                raise ValueError("Es wartet noch eine Änderung auf Bestätigung. Erst bestätigen oder zurücknehmen.")
            active, _ = self.rules()
            try:
                snap = self._snapshot()
            except OSError as e:
                raise ValueError(f"Regeldatei nicht lesbar: {e.strerror}") from e
            rc, out = self.run(argv)
            if rc:
                raise ValueError(f"ufw: {out[:200]}")
            ident = self._arm(snap, active, text) if rollback else None
        return {"ok": True, "ruecknahme": ident, "sekunden": self.seconds if ident else 0, "_audit": f"Firewall: {text}"}

    # --- Aendern --------------------------------------------------------------------------
    def add_rule(self, data):
        action, port, proto = str(data.get("aktion", "")), str(data.get("port", "")), str(data.get("proto", "tcp"))
        source, comment = str(data.get("quelle", "") or "any").strip(), str(data.get("kommentar", "")).strip()
        if action not in ACTIONS or proto not in ("tcp", "udp", "any"):
            raise ValueError("Aktion oder Protokoll ungültig")
        m = re.fullmatch(r"(\d{1,5})(?::(\d{1,5}))?", port)
        low, high = (int(m.group(1)), int(m.group(2) or m.group(1))) if m else (0, 0)
        if not m or not 1 <= low <= high <= 65535:
            raise ValueError("Port: Zahl von 1 bis 65535 oder Bereich wie 8000:8100")
        if high > low and proto == "any":
            raise ValueError("Ein Portbereich braucht tcp oder udp")
        if source != "any":
            try:
                source = str(ipaddress.ip_network(source, strict=False))
            except ValueError:
                raise ValueError("Quelle: IP-Adresse oder Netz wie 203.0.113.0/24") from None
        if comment and not COMMENT.fullmatch(comment):
            raise ValueError("Kommentar: bis 40 Zeichen, nur Buchstaben, Ziffern und .,:+@()-")
        if action != "allow" and source == "any" and any(low <= p <= high for p in self.protected_ports()) and not data.get("bestaetigt"):
            raise ValueError("geschützt: Das würde SSH, 80 oder 443 für alle sperren. Nur mit ausdrücklicher Bestätigung.")
        argv = ["ufw", action, "from", source, "to", "any", "port", port, *(["proto", proto] if proto != "any" else []), *(["comment", comment] if comment else [])]
        return self._change(argv, f"{action} {port}/{proto} von {source}", bool(data.get("ruecknahme")))

    def delete_rule(self, data):
        """Loescht Regel Nr. N, aber nur, wenn sie noch so aussieht, wie der Browser sie gezeigt hat (Nummern verschieben sich)."""
        nr, expected = data.get("nr"), (str(data.get("nach", "")), str(data.get("aktion", "")), str(data.get("von", "")))
        rule = next((r for r in self.rules()[1] if r["nr"] == nr), None)
        if not rule or (rule["nach"], rule["aktion"], rule["von"]) != expected:
            raise ValueError("Die Regel hat sich geändert. Bitte die Liste neu laden.")
        protects = rule["aktion"] in ("ALLOW", "LIMIT") and any(covers(rule, p, rule["proto"] or "tcp") for p in self.protected_ports())
        if protects and not data.get("bestaetigt"):
            raise ValueError("geschützt: Das ist die Freigabe für SSH, 80 oder 443. Nur mit ausdrücklicher Bestätigung löschen.")
        return self._change(["ufw", "--force", "delete", str(nr)], f"Regel {nr} gelöscht ({rule['aktion']} {rule['nach']} von {rule['von']})", bool(data.get("ruecknahme")))

    def set_enabled(self, data):
        on = bool(data.get("an"))
        return self._change(["ufw", "--force", "enable"] if on else ["ufw", "disable"], "Firewall an" if on else "Firewall aus", bool(data.get("ruecknahme")))

    # --- Anbindung an den Server ----------------------------------------------------------
    def get_routes(self):
        return {"/api/firewall": self.view}

    def post(self, path, data):
        """(Status, Antwort) oder None, wenn der Pfad nicht hierher gehoert. Die Sicherheitsstufe prueft der Aufrufer."""
        action = path[len("/api/firewall/"):] if path.startswith("/api/firewall/") else None
        handler = {"regel": self.add_rule, "loeschen": self.delete_rule, "schalten": self.set_enabled,
                   "bestaetigen": lambda d: self.confirm(str(d.get("id", ""))), "zuruecknehmen": lambda d: self.rollback_now(str(d.get("id", "")))}.get(action)
        return (200, handler(data)) if handler else None
