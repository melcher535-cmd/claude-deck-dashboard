import os
import subprocess
import tempfile
import time
import types

import viewer


def deps(d, **extra):
    base = dict(claude_dir=os.path.join(d, "claude"), claude_bin="/nonexistent/claude", here=d, roots=[d], tmux_socket=None)
    base.update(extra)
    return types.SimpleNamespace(**base)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_tmux_view_lists_sessions_and_captures_text_without_sending_anything():
    with tempfile.TemporaryDirectory() as d:
        v = viewer.Viewer(deps(d, tmux_socket=f"deckprobe{os.getpid()}"))
        try:
            subprocess.run(["tmux", "-L", v.d.tmux_socket, "new-session", "-d", "-s", "demo", "sh", "-c", "echo hallo-welt; sleep 20"], check=True)
            time.sleep(0.5)
            assert [s["name"] for s in v.sessions()] == ["demo"]
            assert "hallo-welt" in v.pane({"name": ["demo"]})["text"]
            for bad in ("", "a b", "x;y", "../x"):
                try:
                    v.pane({"name": [bad]})
                    raise AssertionError(bad)
                except ValueError:
                    pass
            try:
                v.pane({"name": ["gibtsnicht"]})
                raise AssertionError("unbekannte Sitzung")
            except ValueError:
                pass
        finally:
            subprocess.run(["tmux", "-L", v.d.tmux_socket, "kill-server"], capture_output=True)
        assert v.sessions() == []


def test_viewer_has_no_method_that_executes_or_changes_sessions():
    names = {n for n in dir(viewer.Viewer) if not n.startswith("_")}
    assert not names & {"send", "start", "kill", "restore", "settings_write", "mcp_add", "mcp_remove", "team_start", "mail_deliver"}
    assert set(viewer.Viewer(deps("/tmp")).get_routes()) >= {"/api/tmux", "/api/repos"} and viewer.Viewer(deps("/tmp")).post("/api/tmux/send") is None


def test_team_roster_needs_known_agents_a_leader_among_members_and_an_allowed_folder():
    with tempfile.TemporaryDirectory() as d:
        write(os.path.join(d, "claude", "agents", "alpha.md"), "---\ndescription: x\n---\n")
        write(os.path.join(d, "claude", "agents", "beta.md"), "")
        os.makedirs(os.path.join(d, "work"))
        v = viewer.Viewer(deps(d))
        v.team_save({"name": "t1", "leader": "alpha", "members": ["alpha", "beta"], "cwd": os.path.join(d, "work"), "prompt": "los"})
        plan = v.team_plan({"name": ["t1"]})
        assert [s["agent"] for s in plan["steps"]] == ["alpha", "beta"] and plan["steps"][0]["leader"] and plan["steps"][0]["command"] == 'claude --agent alpha "<Auftrag>"'
        for bad in ({"name": "t2", "leader": "gamma", "members": ["gamma"]}, {"name": "t2", "leader": "alpha", "members": ["beta"]}, {"name": "t 2", "leader": "alpha", "members": ["alpha"]},
                    {"name": "t2", "leader": "alpha", "members": ["alpha"], "cwd": "/etc"}):
            try:
                v.team_save(bad)
                raise AssertionError(bad)
            except ValueError:
                pass
        v.team_delete({"name": "t1"})
        assert v.teams() == {}


def test_mail_keeps_messages_marks_read_and_deletes():
    with tempfile.TemporaryDirectory() as d:
        v = viewer.Viewer(deps(d))
        first = v.mail_send({"to": "agent-a", "subject": "Hallo", "body": "Bitte pruefen"})["id"]
        second = v.mail_send({"body": "zweite"})["id"]
        assert (first, second) == (1, 2) and v.mail()[0]["status"] == "neu"
        v.mail_mark({"id": first})
        assert v.mail()[0]["status"] == "gelesen"
        v.mail_delete({"id": first})
        assert [m["id"] for m in v.mail()] == [2]
        try:
            v.mail_send({"body": ""})
            raise AssertionError("leere Nachricht")
        except ValueError:
            pass


def test_repos_report_branch_dirty_files_and_hide_credentials_in_remote_urls():
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "proj")
        os.makedirs(repo)
        git = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        git("init", "-b", "main")
        write(os.path.join(repo, "a.txt"), "x")
        git("add", ".")
        git("commit", "-m", "erster")
        git("remote", "add", "origin", "https://user:geheim@github.com/me/proj.git")
        write(os.path.join(repo, "b.txt"), "y")
        os.makedirs(os.path.join(d, ".claude", "plugins", "x", ".git"))  # darf nicht auftauchen
        r = viewer.Viewer(deps(d)).repos()
        assert len(r) == 1 and r[0]["branch"] == "main" and r[0]["dirty"] == 1 and "erster" in r[0]["last"]
        assert "geheim" not in r[0]["remote"] and r[0]["remote"].startswith("https://***@github.com")


def test_slash_commands_and_output_styles_are_listed_from_own_and_plugin_folders():
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, "claude")
        write(os.path.join(c, "commands", "ein", "tool.md"), "---\ndescription: Eigenes Tool\n---\n")
        write(os.path.join(c, "plugins", "cache", "mp", "plug", "1.0", "commands", "los.md"), "---\ndescription: Aus Plugin\n---\n")
        write(os.path.join(c, "output-styles", "knapp.md"), "---\ndescription: Kurz\n---\n")
        write(os.path.join(c, "statusline.sh"), "#!/bin/sh\necho hi\n")
        write(os.path.join(c, "settings.json"), '{"outputStyle": "knapp", "statusLine": {"type": "command", "command": "%s/claude/statusline.sh"}}' % d)
        v = viewer.Viewer(deps(d))
        assert [(r["name"], r["source"]) for r in v.slash_commands()] == [("/ein:tool", "eigene"), ("/los", "mp")] or [r["name"] for r in v.slash_commands()] == ["/ein:tool", "/los"]
        s = v.output_styles()
        assert s["active"] == "knapp" and s["styles"][0]["name"] == "knapp" and "echo hi" in s["statusline_script"]


def test_harness_list_reports_installed_tools_with_versions():
    rows = {r["name"]: r for r in viewer.Viewer(deps("/tmp")).harnesses()}
    assert rows["tmux"]["path"] and rows["tmux"]["version"].startswith("tmux") and rows["codex"]["path"] is None
