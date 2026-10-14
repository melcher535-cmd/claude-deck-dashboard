"""Docker-Container des Servers: ansehen, anlegen, starten, stoppen, loeschen, Logs und Auslastung.
Spricht ueber den Unix-Socket direkt mit der Docker-Engine (nur Standardbibliothek). Das Dashboard bedient nur Container mit dem Label deck.managed=1.
Sichere Voreinstellungen: Ports nur an 127.0.0.1, Daten nur unter /srv/deck-apps/<name>/, kein privileged, kein Host-Netz, kein Docker-Socket im Container."""
import concurrent.futures
import http.client
import json
import os
import re
import socket
import threading
import urllib.parse
import uuid

SOCKET = "/var/run/docker.sock"
APPS_DIR = "/srv/deck-apps"
NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")
IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}$")
SUBDIR = re.compile(r"^[A-Za-z0-9][\w.-]{0,39}$")
CONTAINER_PATH = re.compile(r"^/[\w./-]{0,200}$")
ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
POLICIES = ("no", "unless-stopped", "always", "on-failure")
MANAGED = "deck.managed"
MAX_ENV, MAX_PORTS, MAX_VOLUMES, MAX_LOG_BYTES, MAX_JOBS, LOG_LINES_MAX = 40, 20, 10, 200_000, 10, 1000
STATS_WORKERS, PULL_TIMEOUT, API_TIMEOUT, STATS_TIMEOUT = 8, 900, 30, 30


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def docker_request(method, path, body=None, timeout=API_TIMEOUT, sock=SOCKET):
    """Ein Aufruf der Docker-Engine: (Status, Antwort als bytes)."""
    conn = UnixConnection(sock, timeout)
    try:
        conn.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"} if body is not None else {})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def demux_logs(raw):
    """Docker liefert Logs ohne Terminal als Rahmen (8 Byte Kopf: Strom, 0, 0, 0, Laenge). Mit Terminal ist es reiner Text."""
    out, i = [], 0
    while i + 8 <= len(raw) and raw[i] in (0, 1, 2) and raw[i + 1:i + 4] == b"\0\0\0":
        size = int.from_bytes(raw[i + 4:i + 8], "big")
        out.append(raw[i + 8:i + 8 + size])
        i += 8 + size
    return b"".join(out) if out or not raw else raw


def split_image(image):
    if "@" in image:
        return image, None
    head, sep, tail = image.rpartition(":")
    return (head, tail) if sep and "/" not in tail else (image, "latest")


def cpu_percent(s):
    cpu, pre = s.get("cpu_stats", {}), s.get("precpu_stats", {})
    d_cpu = cpu.get("cpu_usage", {}).get("total_usage", 0) - pre.get("cpu_usage", {}).get("total_usage", 0)
    d_sys = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    return round(100 * d_cpu / d_sys * (cpu.get("online_cpus") or 1), 1) if d_sys > 0 and d_cpu >= 0 else 0.0


def number(value, low, high, label):
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: Zahl erwartet") from None
    if not low <= n <= high:
        raise ValueError(f"{label}: zwischen {low} und {high}")
    return n


def build_spec(data, apps_dir=APPS_DIR):
    """Prueft das Formular und baut daraus die Docker-Konfiguration. Wirft ValueError bei jeder Abweichung. Legt nichts an."""
    name, image = str(data.get("name", "")), str(data.get("image", "")).strip()
    if not NAME.fullmatch(name):
        raise ValueError("Name: Kleinbuchstaben, Ziffern, . _ -, bis 40 Zeichen")
    if not IMAGE.fullmatch(image):
        raise ValueError("Image ungültig (z. B. nginx:alpine)")
    policy = str(data.get("restart") or "unless-stopped")
    if policy not in POLICIES:
        raise ValueError("Neustart-Regel ungültig")
    ports, volumes, env = data.get("ports") or [], data.get("volumes") or [], data.get("env") or []
    if not all(isinstance(x, list) for x in (ports, volumes, env)) or len(ports) > MAX_PORTS or len(volumes) > MAX_VOLUMES or len(env) > MAX_ENV:
        raise ValueError("Zu viele Ports, Ordner oder Variablen")
    bindings, exposed, binds, env_list, extern = {}, {}, [], [], False
    for p in ports:
        host, inner, proto = int(number(p.get("host"), 1, 65535, "Host-Port")), int(number(p.get("container"), 1, 65535, "Container-Port")), str(p.get("proto") or "tcp")
        if proto not in ("tcp", "udp"):
            raise ValueError("Protokoll: tcp oder udp")
        public = bool(p.get("extern"))
        extern = extern or public
        bindings.setdefault(f"{inner}/{proto}", []).append({"HostIp": "0.0.0.0" if public else "127.0.0.1", "HostPort": str(host)})
        exposed[f"{inner}/{proto}"] = {}
    for v in volumes:
        sub, path = str(v.get("sub", "")), str(v.get("pfad", ""))
        if not SUBDIR.fullmatch(sub) or sub in (".", ".."):
            raise ValueError("Ordnername: Buchstaben, Ziffern, . _ -")
        if not CONTAINER_PATH.fullmatch(path) or ".." in path.split("/"):
            raise ValueError("Pfad im Container: absolut, ohne ..")
        binds.append(f"{apps_dir}/{name}/{sub}:{path}")
    for e in env:
        key, value = str(e.get("k", "")), str(e.get("v", ""))
        if not ENV_KEY.fullmatch(key) or "\0" in value or len(value) > 2000:
            raise ValueError(f"Variable {key[:30]!r} ungültig")
        env_list.append(f"{key}={value}")
    host_config = {"PortBindings": bindings, "Binds": binds, "RestartPolicy": {"Name": policy}, "Privileged": False,
                   "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}}}
    if data.get("mem_mb") not in (None, ""):
        host_config["Memory"] = int(number(data["mem_mb"], 16, 65536, "RAM-Grenze (MB)")) * 1024 * 1024
    if data.get("cpus") not in (None, ""):
        host_config["NanoCpus"] = int(number(data["cpus"], 0.1, 64, "CPU-Grenze") * 1e9)
    body = {"Image": image, "Env": env_list, "ExposedPorts": exposed, "HostConfig": host_config, "Labels": {MANAGED: "1", "deck.extern": "1" if extern else "0"}}
    return name, image, body, [f"{apps_dir}/{name}/{v['sub']}" for v in volumes], extern


class DockerApps:
    def __init__(self, request=docker_request, apps_dir=APPS_DIR, templates=(), notify=lambda text: None):
        self.request, self.apps_dir, self.templates, self.notify = request, apps_dir, list(templates), notify
        self.jobs, self.lock = {}, threading.Lock()

    # --- Docker-Aufrufe ---------------------------------------------------------------
    def send(self, method, path, body=None, timeout=API_TIMEOUT):
        try:
            return self.request(method, path, body, timeout)
        except OSError as e:
            raise ValueError(f"Docker nicht erreichbar: {e.strerror or e}") from e

    def call(self, method, path, body=None, timeout=API_TIMEOUT, ok=(200, 201, 204)):
        status, raw = self.send(method, path, body, timeout)
        if status not in ok:
            try:
                message = json.loads(raw).get("message", "")
            except ValueError:
                message = raw.decode("utf-8", "replace")
            raise ValueError(f"Docker: {message.strip()[:200] or status}")
        return json.loads(raw) if raw[:1] in (b"{", b"[") else raw

    def version(self):
        try:
            return self.call("GET", "/version").get("Version")
        except ValueError:
            return None

    def inspect(self, name):
        if not NAME.fullmatch(name):
            raise ValueError("ungültiger Containername")
        status, raw = self.send("GET", f"/containers/{name}/json")
        if status == 404:
            raise ValueError("Container nicht gefunden")
        return json.loads(raw)

    def managed(self, name):
        info = self.inspect(name)
        if info.get("Config", {}).get("Labels", {}).get(MANAGED) != "1":
            raise ValueError("Dieser Container wurde nicht vom Dashboard angelegt und bleibt unberührt.")
        return info

    # --- Lesen -----------------------------------------------------------------------
    def view(self):
        version = self.version()
        if not version:
            return {"verfuegbar": False}
        rows = self.call("GET", "/containers/json?all=1")
        containers = [{"id": c["Id"][:12], "name": c["Names"][0].lstrip("/"), "image": c["Image"], "zustand": c["State"], "status": c["Status"], "erstellt": c["Created"],
                       "ports": sorted({(p.get("IP", ""), p.get("PublicPort", 0), p["PrivatePort"], p["Type"]) for p in c.get("Ports", [])}),
                       "verwaltet": c.get("Labels", {}).get(MANAGED) == "1", "extern": c.get("Labels", {}).get("deck.extern") == "1"} for c in rows]
        used = {c["ImageID"] for c in rows}
        images = [{"id": i["Id"][7:19], "tags": i.get("RepoTags") or [], "groesse": i["Size"], "erstellt": i["Created"], "benutzt": i["Id"] in used} for i in self.call("GET", "/images/json")]
        with self.lock:
            jobs = list(self.jobs.values())
        return {"verfuegbar": True, "version": version, "container": containers, "images": images, "auftraege": jobs, "vorlagen": self.templates, "ordner": self.apps_dir}

    def stats(self):
        names = [c["Names"][0].lstrip("/") for c in self.call("GET", "/containers/json")]

        def one(name):
            try:
                s = self.call("GET", f"/containers/{name}/stats?stream=false", None, STATS_TIMEOUT)
                mem = s.get("memory_stats", {})
                return name, {"cpu": cpu_percent(s), "ram": mem.get("usage", 0) - mem.get("stats", {}).get("inactive_file", 0), "ram_max": mem.get("limit", 0)}
            except (OSError, ValueError):
                return name, None
        with concurrent.futures.ThreadPoolExecutor(STATS_WORKERS) as pool:
            return {n: s for n, s in pool.map(one, names) if s}

    def logs(self, query):
        name = query.get("name", [""])[0]
        lines = int(number(query.get("n", ["200"])[0], 1, LOG_LINES_MAX, "Zeilen"))
        self.inspect(name)
        raw = self.call("GET", f"/containers/{name}/logs?stdout=1&stderr=1&tail={lines}")
        return {"name": name, "text": demux_logs(raw if isinstance(raw, bytes) else b"")[-MAX_LOG_BYTES:].decode("utf-8", "replace")}

    # --- Aendern -----------------------------------------------------------------------
    def control(self, action, data):
        name = str(data.get("name", ""))
        self.managed(name)
        if action == "loeschen":
            self.call("DELETE", f"/containers/{name}?force=true")
            return {"ok": True, "_audit": f"Docker: {name} gelöscht (Daten unter {self.apps_dir}/{name} bleiben)"}
        path = {"start": f"/containers/{name}/start", "stop": f"/containers/{name}/stop?t=10", "restart": f"/containers/{name}/restart?t=10"}[action]
        self.call("POST", path, ok=(204, 304))
        return {"ok": True, "_audit": f"Docker: {name} {action}"}

    def create(self, data):
        name, image, body, folders, extern = build_spec(data, self.apps_dir)
        if self.send("GET", f"/containers/{name}/json")[0] == 200:
            raise ValueError("Name ist schon vergeben")
        job = {"id": uuid.uuid4().hex[:8], "name": name, "text": "Wartet", "fertig": False, "fehler": ""}
        with self.lock:
            self.jobs[job["id"]] = job
            for old in [k for k, j in self.jobs.items() if j["fertig"]][:-MAX_JOBS]:
                del self.jobs[old]
        threading.Thread(target=self._run_job, args=(job, name, image, body, folders), daemon=True).start()
        return {"ok": True, "auftrag": job["id"], "_audit": f"Docker: neu {name} aus {image}, Ports {sorted(body['ExposedPorts'])}, {len(body['Env'])} Variablen" + (", OFFEN nach außen" if extern else "")}

    def _run_job(self, job, name, image, body, folders):
        try:
            if self.send("GET", f"/images/{urllib.parse.quote(image, safe=':/@')}/json")[0] != 200:
                job["text"] = f"Lädt {image}"
                repo, tag = split_image(image)
                query = urllib.parse.urlencode({"fromImage": repo, **({"tag": tag} if tag else {})})
                status, raw = self.send("POST", f"/images/create?{query}", None, PULL_TIMEOUT)
                errors = [json.loads(line)["error"] for line in raw.decode("utf-8", "replace").splitlines() if line.startswith("{") and '"error"' in line]
                if status != 200 or errors:
                    raise ValueError(f"Docker: {(errors or [status])[0]}")
            job["text"] = "Legt an"
            for folder in folders:
                os.makedirs(folder, exist_ok=True)
            self.call("POST", f"/containers/create?name={name}", body)
            job["text"] = "Startet"
            try:
                self.call("POST", f"/containers/{name}/start", ok=(204, 304))
            except ValueError:
                self.send("DELETE", f"/containers/{name}?force=true")  # kein halb fertiger Container bleibt liegen
                raise
            job["text"] = "Läuft"
        except (OSError, ValueError) as e:  # OSError: Ordner anlegen
            job["fehler"] = str(e)[:300]
            job["text"] = "Fehlgeschlagen"
            self.notify(f"Docker: {name} konnte nicht angelegt werden: {job['fehler']}")
        job["fertig"] = True

    def prune_images(self, data):
        r = self.call("POST", "/images/prune?filters=" + urllib.parse.quote(json.dumps({"dangling": ["false"]})))
        return {"ok": True, "frei": r.get("SpaceReclaimed", 0), "_audit": f"Docker: ungenutzte Images entfernt, {r.get('SpaceReclaimed', 0)} Bytes frei"}

    # --- Anbindung an den Server ----------------------------------------------------------
    def get_routes(self):
        return {"/api/docker": self.view, "/api/docker/stats": self.stats}

    def query_routes(self):
        return {"/api/docker/logs": self.logs}

    def post(self, path, data):
        """(Status, Antwort) oder None. Die Sicherheitsstufe prueft der Aufrufer."""
        action = path[len("/api/docker/"):] if path.startswith("/api/docker/") else None
        handler = {"neu": self.create, "aufraeumen": self.prune_images, **{a: (lambda d, a=a: self.control(a, d)) for a in ("start", "stop", "restart", "loeschen")}}.get(action)
        return (200, handler(data)) if handler else None
