import datetime
import auth
import server


def test_masked_hides_values_under_secret_looking_keys():
    data = {"env": {"API_TOKEN": "abc", "name": "x"}, "list": [{"password": "p", "ok": 1}], "keys": {"nested": "kept"}}
    assert server.masked(data) == {"env": {"API_TOKEN": "***", "name": "x"}, "list": [{"password": "***", "ok": 1}], "keys": {"nested": "kept"}}


def test_find_session_rejects_path_traversal():
    assert server.find_session("../../etc/passwd") is None
    assert server.find_session("x" * 36) is None


def make_auth():
    now = [0.0]
    return auth.Auth("geheim", clock=lambda: now[0]), now


def test_login_returns_valid_token_for_correct_password():
    a, _ = make_auth()
    token = a.login("1.1.1.1", "geheim")
    assert a.valid(token)
    a.logout(token)
    assert not a.valid(token)


def test_login_locks_ip_after_max_failures_even_with_correct_password():
    a, now = make_auth()
    for _ in range(auth.MAX_FAILURES):
        assert a.login("2.2.2.2", "falsch") is None
    assert a.locked("2.2.2.2")
    assert a.login("2.2.2.2", "geheim") is None
    now[0] += auth.LOCK_SECONDS + 1
    assert a.login("2.2.2.2", "geheim") is not None


def test_lock_is_per_ip():
    a, _ = make_auth()
    for _ in range(auth.MAX_FAILURES):
        a.login("3.3.3.3", "falsch")
    assert a.login("4.4.4.4", "geheim") is not None


def test_session_expires():
    a, now = make_auth()
    token = a.login("5.5.5.5", "geheim")
    now[0] += auth.SESSION_SECONDS + 1
    assert not a.valid(token)


def test_unknown_token_is_invalid():
    a, _ = make_auth()
    assert not a.valid("")
    assert not a.valid("erfunden")


def test_alert_sends_once_per_level_and_again_at_higher_level():
    sent = []
    original, server.notify = server.notify, sent.append
    try:
        alerted, limits = set(), [{"kind": "session", "percent": 85, "resets_at": "x"}, {"kind": "weekly_all", "percent": 22, "resets_at": "y"}]
        server.alert_on_limits(limits, alerted)
        server.alert_on_limits(limits, alerted)
        limits[0]["percent"] = 96
        server.alert_on_limits(limits, alerted)
    finally:
        server.notify = original
    assert sent == ["Session-Limit (5 Std) bei 85 %", "Session-Limit (5 Std) bei 96 %"]


def test_tool_results_are_not_human_messages():
    assert server.is_human_message("hallo")
    assert server.is_human_message([{"type": "text", "text": "hallo"}])
    assert not server.is_human_message([{"type": "tool_result", "content": "ok"}])


def test_edit_tool_call_becomes_diff_block():
    blocks = server.chat_blocks([{"type": "tool_use", "name": "Edit", "input": {"file_path": "/a", "old_string": "x", "new_string": "y"}}])
    assert blocks == [{"type": "diff", "file": "/a", "old": "x", "new": "y"}]


# --- Zweitfaktor, Passwortwechsel -------------------------------------------------

import base64
import datetime
import json
import os
import tempfile

import insights

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


def test_totp_matches_rfc6238_test_vector():
    assert auth.totp_code(RFC_SECRET, 59 // 30) == "287082"


def make_auth_with_totp(wall):
    a = auth.Auth("geheim", clock=lambda: 0, wall=lambda: wall[0])
    secret = a.begin_totp()
    assert a.enable_totp("1.1.1.1", auth.totp_code(secret, int(wall[0] // 30)))
    wall[0] += 31  # der beim Aktivieren benutzte Code darf nicht noch einmal gelten
    return a, secret


def test_login_needs_valid_code_when_totp_is_enabled():
    wall = [1_000_000.0]
    a, secret = make_auth_with_totp(wall)
    assert a.login("2.2.2.2", "geheim") is None
    assert a.login("2.2.2.2", "geheim", "000000") is None
    assert a.login("2.2.2.2", "falsch", auth.totp_code(secret, int(wall[0] // 30))) is None
    wall[0] += 31
    assert a.login("2.2.2.2", "geheim", auth.totp_code(secret, int(wall[0] // 30))) is not None


def test_totp_code_cannot_be_used_twice():
    wall = [1_000_000.0]
    a, secret = make_auth_with_totp(wall)
    code = auth.totp_code(secret, int(wall[0] // 30))
    assert a.login("3.3.3.3", "geheim", code) is not None
    assert a.login("3.3.3.3", "geheim", code) is None


def test_wrong_codes_lock_the_ip():
    wall = [1_000_000.0]
    a, _ = make_auth_with_totp(wall)
    for _ in range(auth.MAX_FAILURES):
        a.login("4.4.4.4", "geheim", "000000")
    assert a.locked("4.4.4.4")


def test_change_password_replaces_old_password_and_ends_all_sessions():
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "credentials.json")
        a = auth.Auth("geheim", store=store)
        old_token = a.login("5.5.5.5", "geheim")
        new_token = a.change_password("5.5.5.5", "geheim", "", "ein-neues-langes-passwort")
        assert not a.valid(old_token) and a.valid(new_token)
        assert a.login("5.5.5.5", "geheim") is None
        assert auth.Auth("geheim", store=store).login("5.5.5.5", "ein-neues-langes-passwort") is not None
        assert oct(os.stat(store).st_mode & 0o777) == "0o600"


def test_change_password_rejects_short_password_and_wrong_current_password():
    a = auth.Auth("geheim")
    try:
        a.change_password("6.6.6.6", "geheim", "", "kurz")
        raise AssertionError("kurzes Passwort wurde akzeptiert")
    except ValueError:
        pass
    assert a.change_password("6.6.6.6", "falsch", "", "ein-neues-langes-passwort") is None
    assert a.login("6.6.6.6", "geheim") is not None


def test_disable_totp_needs_password_and_code():
    wall = [1_000_000.0]
    a, secret = make_auth_with_totp(wall)
    assert not a.disable_totp("7.7.7.7", "falsch", "000000")
    assert a.totp_enabled
    assert a.disable_totp("7.7.7.7", "geheim", auth.totp_code(secret, int(wall[0] // 30)))
    assert not a.totp_enabled


# --- Auswertungen -----------------------------------------------------------------

def test_usage_ranking_counts_only_recent_days_and_sums_shares():
    day = {"input": 10, "cache_write": 0, "output": 90}
    sessions = [{"id": "a", "title": "A", "daily": {"2026-10-12": day, "2026-09-01": day}}, {"id": "b", "title": "B", "daily": {"2026-10-11": {**day, "output": 290}}}]
    r = insights.usage_ranking(sessions, datetime.date(2026, 10, 12))
    assert [s["id"] for s in r["sessions"]] == ["b", "a"] and r["total"] == 400 and sum(s["share"] for s in r["sessions"]) == 100


def test_vault_health_finds_orphans_and_notes_missing_from_timeline():
    with tempfile.TemporaryDirectory() as vault:
        for name, text in {"Zeitleiste.md": "[[A]]", "A.md": "[[B]] [[A]]", "B.md": "", "C.md": "[[A]]"}.items():
            with open(os.path.join(vault, name), "w", encoding="utf-8") as f:
                f.write(text)
        h = insights.vault_health(vault)
        assert h["orphans"] == ["C.md", "Zeitleiste.md"]
        assert h["not_in_timeline"] == ["B.md", "C.md"]


def test_vault_tree_sums_sizes_of_nested_folders():
    with tempfile.TemporaryDirectory() as vault:
        os.makedirs(os.path.join(vault, "x", "y"))
        for rel, size in {"a.md": 1, "x/b.md": 2, "x/y/c.md": 4}.items():
            with open(os.path.join(vault, rel), "w", encoding="utf-8") as f:
                f.write("z" * size)
        tree = insights.vault_tree(vault)
        assert (tree["size"], tree["files"]) == (7, 3)
        assert [(c["name"], c["size"]) for c in tree["children"]] == [("x", 6)]


def test_pins_set_and_remove_and_limit_note_length():
    with tempfile.TemporaryDirectory() as tmp:
        pins = insights.Pins(os.path.join(tmp, "pins.json"))
        pins.set("s1", True, "n" * 500)
        assert pins.all()["s1"] == {"pinned": True, "note": "n" * insights.MAX_NOTE}
        pins.set("s1", False, "")
        assert pins.all() == {}


def test_error_list_names_failed_tool_and_ignores_successful_results():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "s.jsonl")
        rows = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash"}, {"type": "tool_use", "id": "t2", "name": "Read"}]}, "timestamp": "2026-10-12T10:00:00Z"},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "boom"}, {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]}, "timestamp": "2026-10-12T10:00:01Z"}]
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        r = insights.error_list([path])
        assert r["total"] == 1 and r["errors"][0]["tool"] == "Bash" and r["by_tool"] == [("Bash", 1)]


def test_last_action_describes_tool_without_command_text():
    def line(block):
        return json.dumps({"type": "assistant", "message": {"content": [block]}})
    edit = line({"type": "tool_use", "name": "Edit", "input": {"file_path": "/a/b/server.py", "old_string": "x"}})
    bash = line({"type": "tool_use", "name": "Bash", "input": {"command": "echo geheim", "description": "Dienst neu starten"}})
    assert server.last_action([edit]) == "Edit server.py"
    assert server.last_action([bash]) == "Bash Dienst neu starten"
    assert server.last_action([edit, line({"type": "text", "text": "fertig"})]) == "antwortet"
    assert server.last_action([]) is None


def test_long_session_lives_30_days_and_normal_session_12_hours():
    a = auth.Auth("geheim", clock=lambda: 0)
    short, long_ = a.login("8.8.8.8", "geheim"), a.login("8.8.8.8", "geheim", stay=True)
    assert a.remaining(short) == auth.SESSION_SECONDS and a.remaining(long_) == auth.LONG_SESSION_SECONDS


def test_expired_limit_shows_zero_and_notifies_once_after_reset():
    now = datetime.datetime(2026, 10, 12, 20, 0, tzinfo=datetime.timezone.utc)
    limits = [{"kind": "session", "percent": 96, "severity": "critical", "resets_at": "2026-10-12T19:50:00+00:00"},
              {"kind": "weekly_all", "percent": 32, "severity": "normal", "resets_at": "2026-10-16T07:00:00+00:00"}]
    shown = server.expire_limits(limits, now)
    assert [l["percent"] for l in shown] == [0, 32] and shown[0]["severity"] == "normal" and shown[0]["was_percent"] == 96
    assert limits[0]["percent"] == 96  # Eingabe bleibt unveraendert
    sent, original, alerted = [], server.notify, set()
    server.notify = sent.append
    try:
        server.alert_on_resets(shown, alerted, now)
        server.alert_on_resets(shown, alerted, now)
        old = server.expire_limits(limits, now + datetime.timedelta(hours=3))
        server.alert_on_resets(old, set(), now + datetime.timedelta(hours=3))  # zu lange her (z. B. nach Neustart)
    finally:
        server.notify = original
    assert sent == ["Session-Limit (5 Std) zurückgesetzt (war 96 %)"]
