import os
import subprocess
import tarfile
import tempfile
import time
import types

import auth
import control
import upkeep
from test_viewer import write


def make(d, **extra):
    fake = os.path.join(d, "fake-claude")
    write(fake, '#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$FAKE_OUT"\npwd >> "$FAKE_OUT"\nsleep 30\n')
    os.chmod(fake, 0o755)
    a = auth.Auth("pw", clock=lambda: 0)
    deps = types.SimpleNamespace(claude_dir=os.path.join(d, "claude"), claude_bin=fake, here=d, roots=[d], tmux_socket=f"deckctl{os.getpid()}", home=d,
                                 auth=a, backup_dir=os.path.join(d, "backups"), backup_sources=[os.path.join(d, "claude", "settings.json")], **extra)
    os.makedirs(os.path.join(d, "work"), exist_ok=True)
    return control.Control(deps), a


def elevate(a):
    token = a.login("1.1.1.1", "pw")
    assert a.step_up("1.1.1.1", token, "pw")
    return token


def finish(c):
    subprocess.run(["tmux", "-L", c.d.tmux_socket, "kill-server"], capture_output=True)


def wait_for(fn, seconds=4):
    end = time.time() + seconds
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.1)
    return fn()


# --- Sicherheitsstufe -------------------------------------------------------------------

def test_step_up_needs_valid_session_correct_credentials_and_expires():
    now = [0.0]
    a = auth.Auth("pw", clock=lambda: now[0])
    token = a.login("1.1.1.1", "pw")
    assert not a.is_elevated(token) and not a.step_up("1.1.1.1", token, "falsch") and not a.step_up("1.1.1.1", "erfunden", "pw")
    assert a.step_up("1.1.1.1", token, "pw") and a.is_elevated(token)
    now[0] += auth.STEPUP_SECONDS + 1
    assert not a.is_elevated(token)
    assert a.step_up("1.1.1.1", token, "pw")
    a.logout(token)
    assert not a.is_elevated(token)


def test_failed_step_up_attempts_lock_the_ip_and_password_change_ends_elevation():
    a = auth.Auth("pw", clock=lambda: 0)
    token = a.login("1.1.1.1", "pw")
    for _ in range(auth.MAX_FAILURES):
        a.step_up("2.2.2.2", token, "falsch")
    assert a.locked("2.2.2.2") and not a.step_up("2.2.2.2", token, "pw")
    other = a.login("3.3.3.3", "pw")
    a.step_up("3.3.3.3", other, "pw")
    new = a.change_password("3.3.3.3", "pw", "", "ein-neues-langes-passwort")
    assert not a.is_elevated(other) and a.valid(new) and not a.is_elevated(new)


def test_every_critical_path_is_refused_without_step_up_and_safe_paths_are_not():
    with tempfile.TemporaryDirectory() as d:
        c, a = make(d)
        token = a.login("1.1.1.1", "pw")
        for path in control.CRITICAL:
            try:
                c.post(path, token)
                raise AssertionError(path)
            except PermissionError:
                pass
        assert c.post("/api/mail/send", token) is not None and c.post("/api/unbekannt", token) is None
        elevated = elevate(a)
        for path in control.CRITICAL:
            assert callable(c.post(path, elevated)), path


# --- tmux bedienen --------------------------------------------------------------------------

def test_send_types_text_literally_and_sends_only_allowed_keys():
    with tempfile.TemporaryDirectory() as d:
        c, _ = make(d)
        try:
            subprocess.run(["tmux", "-L", c.d.tmux_socket, "new-session", "-d", "-s", "t1", "sh", "-c", "read x; echo GOT:$x; sleep 20"], check=True)
            time.sleep(0.4)
            c.send({"name": "t1", "text": "-x C-c $(echo boese) ; rm"})   # Minuszeichen, Tastennamen und Shell-Zeichen bleiben Text
            c.send({"name": "t1", "key": "Enter"})
            assert "GOT:-x C-c $(echo boese) ; rm" in wait_for(lambda: c.pane({"name": ["t1"]})["text"] if "GOT:" in c.pane({"name": ["t1"]})["text"] else "")
            for bad in ({"name": "t1", "key": "C-z"}, {"name": "t 1", "text": "x"}, {"name": "t1", "text": ""}, {"name": "t1", "text": "x" * (control.MAX_SEND + 1)}, {"name": "t1", "text": "a\x00b"}):
                try:
                    c.send(bad)
                    raise AssertionError(bad)
                except ValueError:
                    pass
        finally:
            finish(c)


def test_start_runs_claude_without_a_shell_in_an_allowed_folder_and_rejects_bad_input():
    with tempfile.TemporaryDirectory() as d:
        c, _ = make(d)
        out = os.path.join(d, "argv.txt")
        os.environ["FAKE_OUT"] = out
        write(os.path.join(d, "claude", "agents", "helfer.md"), "")
        try:
            r = c.start({"name": "probe", "cwd": os.path.join(d, "work"), "agent": "helfer", "prompt": 'Mach "x"; touch /tmp/pwned-deck $(id)'})
            assert r["name"] == "deck-probe"
            lines = wait_for(lambda: open(out).read().splitlines() if os.path.exists(out) and len(open(out).read().splitlines()) >= 4 else None)
            assert lines[:3] == ["--agent", "helfer", 'Mach "x"; touch /tmp/pwned-deck $(id)'] and lines[3] == os.path.realpath(os.path.join(d, "work"))
            assert not os.path.exists("/tmp/pwned-deck") and [s["name"] for s in c.sessions()] == ["deck-probe"]
            for bad in ({"name": "a b"}, {"name": ""}, {"name": "x", "cwd": "/etc"}, {"name": "x", "agent": "gibtsnicht"}, {"name": "x", "prompt": "--dangerously-skip-permissions"}, {"name": "x", "prompt": "p" * (control.MAX_PROMPT + 1)}):
                try:
                    c.start(bad)
                    raise AssertionError(bad)
                except ValueError:
                    pass
        finally:
            finish(c)


def test_kill_only_ends_sessions_started_by_the_dashboard():
    with tempfile.TemporaryDirectory() as d:
        c, _ = make(d)
        try:
            for name in ("fremd", "deck-eigen"):
                subprocess.run(["tmux", "-L", c.d.tmux_socket, "new-session", "-d", "-s", name, "sleep", "20"], check=True)
            try:
                c.kill({"name": "fremd"})
                raise AssertionError("fremde Sitzung beendet")
            except ValueError:
                pass
            c.kill({"name": "deck-eigen"})
            assert [s["name"] for s in c.sessions()] == ["fremd"]
        finally:
            finish(c)


def test_team_start_launches_leader_with_prompt_and_members_without():
    with tempfile.TemporaryDirectory() as d:
        c, _ = make(d)
        write(os.path.join(d, "claude", "agents", "a1.md"), "")
        write(os.path.join(d, "claude", "agents", "a2.md"), "")
        os.environ["FAKE_OUT"] = os.path.join(d, "argv.txt")
        try:
            c.team_save({"name": "tt", "leader": "a1", "members": ["a1", "a2"], "cwd": os.path.join(d, "work"), "prompt": "Auftrag eins"})
            assert c.team_start({"name": "tt"})["started"] == ["deck-team-tt-a1", "deck-team-tt-a2"]
            assert sorted(s["name"] for s in c.sessions()) == ["deck-team-tt-a1", "deck-team-tt-a2"]
        finally:
            finish(c)


def test_mail_deliver_types_message_and_marks_it_delivered():
    with tempfile.TemporaryDirectory() as d:
        c, _ = make(d)
        try:
            subprocess.run(["tmux", "-L", c.d.tmux_socket, "new-session", "-d", "-s", "empf", "sh", "-c", "read x; echo ERHALTEN:$x; sleep 20"], check=True)
            time.sleep(0.4)
            mid = c.mail_send({"to": "empf", "body": "Bitte pruefen"})["id"]
            c.mail_deliver({"id": mid})
            assert "ERHALTEN:Bitte pruefen" in wait_for(lambda: c.pane({"name": ["empf"]})["text"] if "ERHALTEN" in c.pane({"name": ["empf"]})["text"] else "")
            assert c.mail()[0]["status"] == "zugestellt"
        finally:
            finish(c)


# --- Einstellungen, MCP, Sicherung -------------------------------------------------------------

def test_settings_write_validates_backs_up_keeps_mode_and_prunes_old_backups():
    with tempfile.TemporaryDirectory() as d:
        c, _ = make(d)
        path = os.path.join(d, "claude", "settings.json")
        write(path, '{"theme": "dark"}')
        os.chmod(path, 0o640)
        for bad in ("kein json", "[1, 2]", ""):
            try:
                c.settings_write({"text": bad})
                raise AssertionError(bad)
            except ValueError:
                pass
        assert open(path, encoding="utf-8").read() == '{"theme": "dark"}'  # unveraendert nach Fehlern
        r = c.settings_write({"text": '{"theme":"light","model":"x"}'})
        assert '"theme": "light"' in open(path, encoding="utf-8").read() and oct(os.stat(path).st_mode & 0o777) == "0o640"
        assert open(os.path.join(d, "claude", "backups", r["backup"]), encoding="utf-8").read() == '{"theme": "dark"}'
        for i in range(control.SETTINGS_BACKUPS + 3):
            write(os.path.join(d, "claude", "backups", f"settings.json.2000-01-{i + 1:02d}_000000"), "{}")
        c.settings_write({"text": "{}"})
        assert len(os.listdir(os.path.join(d, "claude", "backups"))) == control.SETTINGS_BACKUPS


def test_mcp_add_and_remove_validate_input_before_running_the_claude_cli():
    with tempfile.TemporaryDirectory() as d:
        c, _ = make(d)
        for bad in ({"name": "a b", "config": {"command": "x"}}, {"name": "ok", "config": []}, {"name": "ok", "config": {}}, {"name": "ok", "config": {"url": "ftp://x"}}, {"name": "ok", "config": {"command": 5}}):
            try:
                c.mcp_add(bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        try:
            c.mcp_remove({"name": "../x"})
            raise AssertionError("Name")
        except ValueError:
            pass
        out = os.path.join(d, "argv.txt")
        os.environ["FAKE_OUT"] = out
        write(os.path.join(d, "ok-claude"), '#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$FAKE_OUT"\n')
        os.chmod(os.path.join(d, "ok-claude"), 0o755)
        c.d.claude_bin = os.path.join(d, "ok-claude")
        c.mcp_add({"name": "mein-server", "config": {"command": "npx", "args": ["x"]}})
        assert open(out).read().splitlines()[:3] == ["mcp", "add-json", "mein-server"] and open(out).read().splitlines()[-2:] == ["--scope", "user"]
        write(os.path.join(d, "fail-claude"), "#!/bin/sh\necho kaputt >&2\nexit 1\n")
        os.chmod(os.path.join(d, "fail-claude"), 0o755)
        c.d.claude_bin = os.path.join(d, "fail-claude")
        try:
            c.mcp_remove({"name": "x"})
            raise AssertionError("Fehler der CLI")
        except ValueError as e:
            assert "kaputt" in str(e)


def test_restore_brings_back_selected_parts_after_a_safety_backup_and_ignores_unsafe_names():
    with tempfile.TemporaryDirectory() as d:
        c, _ = make(d)
        claude = os.path.join(d, "claude")
        write(os.path.join(claude, "settings.json"), "neu")
        write(os.path.join(claude, "agents", "a.md"), "agent neu")
        c.d.backup_sources = [os.path.join(claude, "settings.json"), os.path.join(claude, "agents")]
        os.makedirs(c.d.backup_dir)
        archive = os.path.join(c.d.backup_dir, "deck-2026-01-01_100000.tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            for name, text in (("settings.json", "alt"), ("agents/a.md", "agent alt"), ("../draussen.txt", "x"), ("agents/../../draussen2.txt", "x")):
                info = tarfile.TarInfo(name)
                info.size = len(text)
                tar.addfile(info, __import__("io").BytesIO(text.encode()))
        r = c.restore({"name": os.path.basename(archive), "parts": ["settings.json", "nichtErlaubt"]})
        assert r["restored"] == ["settings.json"] and open(os.path.join(claude, "settings.json")).read() == "alt" and open(os.path.join(claude, "agents", "a.md")).read() == "agent neu"
        assert os.path.isfile(os.path.join(c.d.backup_dir, r["safety"])) and not os.path.exists(os.path.join(d, "draussen.txt")) and not os.path.exists(os.path.join(d, "draussen2.txt"))
        c.restore({"name": os.path.basename(archive), "parts": ["agents"]})
        assert open(os.path.join(claude, "agents", "a.md")).read() == "agent alt" and not os.path.exists(os.path.join(claude, "draussen2.txt"))
        for bad in ({"name": "../x.tar.gz", "parts": ["agents"]}, {"name": "deck-2026-01-01_100000.tar.gz", "parts": []}, {"name": "deck-1999-01-01_000000.tar.gz", "parts": ["agents"]}):
            try:
                c.restore(bad)
                raise AssertionError(bad)
            except ValueError:
                pass
