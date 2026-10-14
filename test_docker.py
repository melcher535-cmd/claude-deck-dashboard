import json
import os
import tempfile
import time
import urllib.parse

import docker_apps
from docker_apps import DockerApps, build_spec, cpu_percent, demux_logs, split_image


class FakeEngine:
    """Spielt die Docker-Engine nach: Container, Images und Aufrufe im Speicher."""
    def __init__(self):
        self.containers = {"fremd": {"labels": {}, "state": "running"}}
        self.images, self.calls, self.fail_start, self.pull_error = {"nginx:alpine"}, [], False, ""
        self.log_bytes = b""

    def __call__(self, method, path, body=None, timeout=30):
        self.calls.append((method, path))
        url = urllib.parse.urlparse(path)
        parts, q = url.path.strip("/").split("/"), urllib.parse.parse_qs(url.query)
        out = lambda status, data=None: (status, json.dumps(data).encode() if data is not None else b"")
        if url.path == "/version":
            return out(200, {"Version": "29.1.3"})
        if url.path == "/containers/json":
            return out(200, [{"Id": "a" * 64, "Names": [f"/{n}"], "Image": "x", "ImageID": "sha256:" + "b" * 64, "State": c["state"], "Status": "Up", "Created": 1, "Ports": [], "Labels": c["labels"]}
                             for n, c in self.containers.items() if c["state"] == "running" or q.get("all")])
        if url.path == "/images/json":
            return out(200, [{"Id": "sha256:" + "b" * 64, "RepoTags": ["nginx:alpine"], "Size": 10, "Created": 1}])
        if url.path == "/images/create":
            if self.pull_error:
                return 200, json.dumps({"error": self.pull_error}).encode()
            self.images.add(q["fromImage"][0] + ":" + q.get("tag", ["latest"])[0])
            return 200, b'{"status":"Pulling"}\n'
        if parts[0] == "images" and parts[-1] == "json":
            return out(200 if urllib.parse.unquote("/".join(parts[1:-1])) in self.images else 404, {})
        if url.path == "/containers/create":
            self.containers[q["name"][0]] = {"labels": body["Labels"], "state": "created", "body": body}
            return out(201, {"Id": "c" * 64})
        if parts[0] == "containers" and len(parts) == 3 and parts[2] == "json":
            c = self.containers.get(parts[1])
            return out(200, {"Config": {"Labels": c["labels"]}}) if c else out(404, {"message": "No such container"})
        if parts[0] == "containers" and parts[2:] == ["start"]:
            if self.fail_start:
                return out(500, {"message": "port is already allocated"})
            self.containers[parts[1]]["state"] = "running"
            return out(204)
        if parts[0] == "containers" and parts[2:] in (["stop"], ["restart"]):
            self.containers[parts[1]]["state"] = "exited" if parts[2] == "stop" else "running"
            return out(204)
        if parts[0] == "containers" and parts[2:] == ["logs"]:
            return 200, self.log_bytes
        if parts[0] == "containers" and len(parts) == 2 and method == "DELETE":
            self.containers.pop(parts[1], None)
            return out(204)
        raise AssertionError(f"unerwarteter Aufruf {method} {path}")


def make(d):
    engine = FakeEngine()
    notes = []
    return DockerApps(engine, apps_dir=d, templates=[{"id": "t"}], notify=notes.append), engine, notes


def wait_done(apps, job_id):
    end = time.time() + 5
    while time.time() < end and not apps.jobs[job_id]["fertig"]:
        time.sleep(0.02)
    return apps.jobs[job_id]


def refused(fn, text=""):
    try:
        fn()
    except ValueError as e:
        assert text in str(e), str(e)
        return True
    return False


FORM = {"name": "web", "image": "nginx:alpine", "ports": [{"host": 8080, "container": 80}], "volumes": [{"sub": "html", "pfad": "/usr/share/nginx/html"}],
        "env": [{"k": "TZ", "v": "Europe/Berlin"}], "restart": "always", "mem_mb": 256, "cpus": 0.5}


def test_spec_binds_ports_to_loopback_by_default_and_marks_labels():
    name, image, body, folders, extern = build_spec(FORM, "/srv/x")
    assert body["HostConfig"]["PortBindings"] == {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]}
    assert body["Labels"] == {"deck.managed": "1", "deck.extern": "0"} and not extern
    assert body["HostConfig"]["Binds"] == ["/srv/x/web/html:/usr/share/nginx/html"] and folders == ["/srv/x/web/html"]
    assert body["Env"] == ["TZ=Europe/Berlin"] and body["HostConfig"]["Memory"] == 256 * 1024 * 1024 and body["HostConfig"]["NanoCpus"] == 5 * 10 ** 8
    assert body["HostConfig"]["Privileged"] is False and body["HostConfig"]["RestartPolicy"] == {"Name": "always"}


def test_spec_opens_a_port_to_the_world_only_when_asked():
    _, _, body, _, extern = build_spec({**FORM, "ports": [{"host": 8080, "container": 80, "extern": True}]}, "/srv/x")
    assert body["HostConfig"]["PortBindings"]["80/tcp"][0]["HostIp"] == "0.0.0.0" and extern and body["Labels"]["deck.extern"] == "1"


def test_spec_rejects_unsafe_or_malformed_input():
    bad = [{"name": "Web"}, {"name": "../x"}, {"name": "a b"}, {"image": "-v /:/h"}, {"image": "a b"}, {"image": ""},
           {"ports": [{"host": 0, "container": 80}]}, {"ports": [{"host": 80, "container": 70000}]}, {"ports": [{"host": 80, "container": 80, "proto": "icmp"}]},
           {"volumes": [{"sub": "..", "pfad": "/d"}]}, {"volumes": [{"sub": "a/b", "pfad": "/d"}]}, {"volumes": [{"sub": "a", "pfad": "/../etc"}]}, {"volumes": [{"sub": "a", "pfad": "rel"}]},
           {"env": [{"k": "A B", "v": "1"}]}, {"env": [{"k": "A", "v": "x\0y"}]}, {"restart": "kaputt"}, {"mem_mb": 1}, {"cpus": 0}, {"ports": "x"},
           {"env": [{"k": "A", "v": "1"}] * 41}]
    for patch in bad:
        assert refused(lambda: build_spec({**FORM, **patch}, "/srv/x")), patch


def test_create_makes_folders_labels_and_starts_container():
    with tempfile.TemporaryDirectory() as d:
        apps, engine, _ = make(d)
        r = apps.create(FORM)
        job = wait_done(apps, r["auftrag"])
        assert job["text"] == "Läuft" and not job["fehler"]
        assert engine.containers["web"]["state"] == "running" and engine.containers["web"]["labels"]["deck.managed"] == "1"
        assert os.path.isdir(os.path.join(d, "web", "html"))
        assert not any("images/create" in p for _, p in engine.calls)
        assert "TZ" not in r["_audit"] and "Europe/Berlin" not in r["_audit"]


def test_create_pulls_missing_image_first():
    with tempfile.TemporaryDirectory() as d:
        apps, engine, _ = make(d)
        job = wait_done(apps, apps.create({**FORM, "image": "ghcr.io/x/app:2"})["auftrag"])
        assert job["text"] == "Läuft" and "ghcr.io/x/app:2" in engine.images


def test_pull_error_is_reported_and_no_container_is_left():
    with tempfile.TemporaryDirectory() as d:
        apps, engine, notes = make(d)
        engine.pull_error = "pull access denied"
        job = wait_done(apps, apps.create({**FORM, "image": "nirgendwo/x"})["auftrag"])
        assert job["text"] == "Fehlgeschlagen" and "pull access denied" in job["fehler"] and "web" not in engine.containers and notes


def test_failed_start_removes_half_created_container():
    with tempfile.TemporaryDirectory() as d:
        apps, engine, notes = make(d)
        engine.fail_start = True
        job = wait_done(apps, apps.create(FORM)["auftrag"])
        assert "already allocated" in job["fehler"] and "web" not in engine.containers and notes


def test_create_refuses_taken_name():
    with tempfile.TemporaryDirectory() as d:
        apps, *_ = make(d)
        assert refused(lambda: apps.create({**FORM, "name": "fremd"}), "vergeben")


def test_foreign_containers_cannot_be_controlled():
    with tempfile.TemporaryDirectory() as d:
        apps, engine, _ = make(d)
        for action in ("start", "stop", "restart", "loeschen"):
            assert refused(lambda: apps.control(action, {"name": "fremd"}), "nicht vom Dashboard")
        assert "fremd" in engine.containers and refused(lambda: apps.control("stop", {"name": "gibtsnicht"}), "nicht gefunden")
        assert refused(lambda: apps.control("stop", {"name": "../etc"}), "ungültig")


def test_managed_container_start_stop_delete():
    with tempfile.TemporaryDirectory() as d:
        apps, engine, _ = make(d)
        wait_done(apps, apps.create(FORM)["auftrag"])
        apps.control("stop", {"name": "web"})
        assert engine.containers["web"]["state"] == "exited"
        apps.control("start", {"name": "web"})
        apps.control("restart", {"name": "web"})
        assert engine.containers["web"]["state"] == "running"
        assert "bleiben" in apps.control("loeschen", {"name": "web"})["_audit"] and "web" not in engine.containers


def test_view_lists_managed_flag_images_and_templates():
    with tempfile.TemporaryDirectory() as d:
        apps, *_ = make(d)
        wait_done(apps, apps.create(FORM)["auftrag"])
        v = apps.view()
        assert v["verfuegbar"] and v["version"] == "29.1.3" and v["vorlagen"] == [{"id": "t"}]
        assert {c["name"]: c["verwaltet"] for c in v["container"]} == {"fremd": False, "web": True} and v["images"][0]["benutzt"]


def test_view_reports_unavailable_when_engine_is_unreachable():
    def down(*a):
        raise FileNotFoundError("docker.sock")
    assert DockerApps(down).view() == {"verfuegbar": False}


def test_logs_demultiplex_frames_and_keep_plain_terminal_text():
    frame = lambda stream, text: bytes([stream, 0, 0, 0]) + len(text).to_bytes(4, "big") + text
    assert demux_logs(frame(1, b"hallo\n") + frame(2, b"fehler\n")) == b"hallo\nfehler\n"
    assert demux_logs(b"reiner text\n") == b"reiner text\n" and demux_logs(b"") == b""
    with tempfile.TemporaryDirectory() as d:
        apps, engine, _ = make(d)
        engine.log_bytes = frame(1, b"zeile\n")
        assert apps.logs({"name": ["fremd"], "n": ["50"]})["text"] == "zeile\n"
        assert refused(lambda: apps.logs({"name": ["fremd"], "n": ["99999"]}), "Zeilen")


def test_cpu_percent_uses_container_and_system_deltas():
    s = {"cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 2000, "online_cpus": 4}, "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000}}
    assert cpu_percent(s) == 80.0 and cpu_percent({}) == 0.0


def test_split_image_handles_tags_registries_and_digests():
    assert split_image("nginx") == ("nginx", "latest") and split_image("nginx:1.2") == ("nginx", "1.2")
    assert split_image("localhost:5000/app") == ("localhost:5000/app", "latest") and split_image("a/b@sha256:ab") == ("a/b@sha256:ab", None)


def test_post_routes_unknown_paths_to_none():
    apps, *_ = make("/tmp")
    assert apps.post("/api/netz/scan", {}) is None and apps.post("/api/docker/unbekannt", {}) is None
