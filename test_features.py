import base64
import datetime
import json
import os
import tarfile
import tempfile
import urllib.error

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

import analytics
import auth
import notes
import push
import server
import upkeep


def tmp():
    return tempfile.TemporaryDirectory()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# --- Push ---------------------------------------------------------------------------

def test_push_encryption_matches_rfc8291_test_vector():
    as_private = ec.derive_private_key(int.from_bytes(push.b64d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    body = push.encrypt(b"When I grow up, I want to be a watermelon", push.b64d("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"),
                        push.b64d("BTBZMqHH6r4Tts7J_aSIgg"), push.b64d("DGv6ra1nlYgDCS1FRnbzlw"), as_private)
    assert push.b64u(body) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def make_push(d, clock=lambda: 1_000_000):
    return push.Push(os.path.join(d, "keys.json"), os.path.join(d, "subs.json"), "https://example.org", clock=clock)


def valid_subscription():
    key = ec.generate_private_key(ec.SECP256R1())
    return {"endpoint": "https://push.example/abc", "keys": {"p256dh": push.b64u(push.uncompressed(key.public_key())), "auth": push.b64u(os.urandom(16))}}


def test_vapid_token_is_signed_with_the_stored_key_and_names_the_push_service():
    with tmp() as d:
        p = make_push(d)
        header = p.vapid_authorization("https://push.example/abc")
        token, key = header[len("vapid t="):].split(", k=")
        h, c, s = token.split(".")
        assert json.loads(push.b64d(c))["aud"] == "https://push.example" and json.loads(push.b64d(c))["exp"] == 1_000_000 + push.JWT_LIFETIME
        raw = push.b64d(s)
        public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.b64d(key))
        public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))
        assert make_push(d).public_key() == key  # derselbe Schluessel nach dem Neuladen


def test_subscribe_rejects_bad_subscriptions_and_unsubscribe_removes():
    with tmp() as d:
        p = make_push(d)
        for bad in ({"endpoint": "http://x", "keys": {}}, {"endpoint": "https://x", "keys": {"p256dh": "AAAA", "auth": "AAAA"}}):
            try:
                p.subscribe(bad)
                raise AssertionError("ungueltiges Abo akzeptiert")
            except ValueError:
                pass
        sub = valid_subscription()
        p.subscribe(sub)
        assert list(p.subscriptions()) == [sub["endpoint"]]
        p.unsubscribe(sub["endpoint"])
        assert p.subscriptions() == {}


def test_send_removes_subscription_when_push_service_says_gone():
    with tmp() as d:
        p = make_push(d)
        sub = valid_subscription()
        p.subscribe(sub)
        original = push.urllib.request.urlopen
        def gone(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 410, "Gone", {}, None)
        push.urllib.request.urlopen = gone
        try:
            assert p.send(sub["endpoint"], p.subscriptions()[sub["endpoint"]], {"title": "x"}) == 410
        finally:
            push.urllib.request.urlopen = original
        assert p.subscriptions() == {}


# --- Auswertungen ---------------------------------------------------------------------

def test_cost_breakdown_groups_by_project_week_and_session_for_windows_and_linux_paths():
    with tmp() as d:
        path = os.path.join(d, "costs.jsonl")
        rows = [{"timestamp": "2026-10-12T10:00:00Z", "session_id": "s1", "transcript_path": "C:\\Users\\x\\.claude\\projects\\C--Users-x\\s1.jsonl", "estimated_cost_usd": 1.5},
                {"timestamp": "2026-10-12T11:00:00Z", "session_id": "s2", "transcript_path": "/root/.claude/projects/-root/s2.jsonl", "estimated_cost_usd": 0.5},
                {"timestamp": "2026-10-12T12:00:00Z", "session_id": "s2", "transcript_path": "/root/.claude/projects/-root/s2.jsonl", "estimated_cost_usd": 0.25}]
        write(path, "".join(json.dumps(r) + "\n" for r in rows) + "kaputt\n")
        r = analytics.cost_breakdown(path, {"s2": "Titel"})
        assert dict(r["projects"]) == {"C--Users-x": 1.5, "-root": 0.75}
        assert r["sessions"][0][:2] == ("s1", "s1") and r["sessions"][1][1:] == ("Titel", 0.75) and r["total"] == 2.25 and r["weeks"] == [("2026-W42", 2.25)]


def test_activity_intervals_split_on_long_pauses():
    assert analytics.activity_intervals([0, 60, 120, 5000, 5100]) == [[0, 120], [5000, 5100]]


def test_gantt_splits_intervals_at_midnight_utc():
    today = datetime.date(2026, 10, 12)
    midnight = datetime.datetime(2026, 10, 12, tzinfo=datetime.timezone.utc).timestamp()
    rows = dict(analytics.gantt([("s", "T", [[midnight - 3600, midnight + 3600]])], today, days=2))
    assert rows["2026-10-11"][0]["from"] == 23 and rows["2026-10-11"][0]["to"] == 24
    assert rows["2026-10-12"][0]["from"] == 0 and rows["2026-10-12"][0]["to"] == 1


def test_error_patterns_merge_errors_that_differ_only_in_numbers_and_paths():
    errors = [{"tool": "Bash", "text": "Permission denied: /root/a/1234", "ts": "2026-10-12T10:00:00Z"},
              {"tool": "Bash", "text": "Permission denied: /root/b/99", "ts": "2026-10-13T10:00:00Z"},
              {"tool": "Edit", "text": "String not found", "ts": None}]
    r = analytics.error_patterns(errors)
    assert r[0]["anzahl"] == 2 and r[0]["tool"] == "Bash" and r[0]["zuletzt"] == "2026-10-13T10:00:00Z" and len(r) == 2


def test_hours_until_full_uses_current_pace_and_ignores_flat_or_single_points():
    pts = [{"t": 0, "weekly_all": 10}, {"t": 3_600_000, "weekly_all": 20}]
    assert abs(analytics.hours_until_full(pts, "weekly_all") - 8) < 1e-9
    assert analytics.hours_until_full([{"t": 0, "x": 5}, {"t": 10, "x": 5}], "x") is None
    assert analytics.hours_until_full([{"t": 0, "x": 5}], "x") is None


def test_monitor_step_reports_new_actions_and_finished_watched_sessions():
    state, watched = {}, {"a": 1, "b": 1}
    live = [{"sessionId": "a", "name": "A", "status": "busy", "action": "Edit x.py"}, {"sessionId": "b", "name": "B", "status": "busy", "action": "Bash"}]
    events, finished = analytics.monitor_step(state, live, watched, 100)
    assert [e["action"] for e in events] == ["Edit x.py", "Bash"] and finished == []
    live = [{"sessionId": "a", "name": "A", "status": "idle", "action": "antwortet"}]  # a wartet, b ist beendet
    events, finished = analytics.monitor_step(state, live, watched, 200)
    assert sorted(finished) == ["a", "b"] and "b" not in state
    assert analytics.monitor_step(state, live, watched, 300) == ([], [])


def test_alert_on_forecast_sends_once_per_reset_and_only_when_full_before_reset():
    sent, original = [], server.notify
    server.notify = sent.append
    try:
        now = datetime.datetime(2026, 10, 12, tzinfo=datetime.timezone.utc)
        history = [{"t": 0, "weekly_all": 10}, {"t": 3_600_000, "weekly_all": 40}]  # 30 %/h -> 2 Std
        limit = {"kind": "weekly_all", "percent": 40, "resets_at": "2026-10-13T00:00:00+00:00"}
        alerted = set()
        server.alert_on_forecast([limit], history, alerted, now)
        server.alert_on_forecast([limit], history, alerted, now)
        assert sent == ["Wochen-Limit: bei diesem Tempo in etwa 2 Std voll"]
        soon = {**limit, "resets_at": "2026-10-12T01:00:00+00:00"}  # Reset kommt vor dem Limit
        server.alert_on_forecast([soon], history, set(), now)
        assert len(sent) == 1
    finally:
        server.notify = original


# --- Notizen ----------------------------------------------------------------------------

def test_read_note_rejects_traversal_non_markdown_and_hidden_folders():
    with tmp() as d:
        vault = os.path.join(d, "vault")
        write(os.path.join(vault, "A.md"), "hallo")
        write(os.path.join(vault, ".obsidian", "x.md"), "geheim")
        write(os.path.join(d, "ausserhalb.md"), "nein")
        write(os.path.join(vault, "b.txt"), "nein")
        os.symlink(os.path.join(d, "ausserhalb.md"), os.path.join(vault, "link.md"))
        assert notes.read_note(vault, "A.md")["text"] == "hallo"
        for bad in ("../ausserhalb.md", "b.txt", ".obsidian/x.md", "link.md", "/etc/passwd", "fehlt.md"):
            assert notes.read_note(vault, bad) is None, bad


def test_search_notes_puts_name_matches_first_and_returns_snippet():
    with tmp() as d:
        write(os.path.join(d, "Docker Anleitung.md"), "nichts")
        write(os.path.join(d, "Andere.md"), "Hier steht etwas über Docker im Text.")
        hits = notes.search_notes(d, "docker")
        assert [h["title"] for h in hits] == ["Docker Anleitung", "Andere"] and "Docker" in hits[1]["snippet"]


def test_create_chat_note_never_overwrites_sanitizes_title_and_updates_timeline():
    with tmp() as d:
        write(os.path.join(d, "Zeitleiste.md"), "# Zeitleiste\n\n## 2026-10-12\n- [[Alt]]\n\n## 2026-10-11\n- [[Aelter]]\n")
        info = {"title": 'Haupt/Titel: "x"', "session_id": "s", "cwd": "/root", "first": "a", "last": "b", "messages": 3, "tools": [("Bash", 2)], "files": ["/a.py"], "first_message": "Hallo"}
        first = notes.create_chat_note(d, info, "2026-10-12")
        second = notes.create_chat_note(d, info, "2026-10-12")
        assert first != second and os.path.isfile(os.path.join(d, first)) and "/" not in os.path.basename(first).replace(".md", "")
        timeline = open(os.path.join(d, "Zeitleiste.md"), encoding="utf-8").read()
        assert timeline.index("[[Alt]]") < timeline.index(f"[[{os.path.basename(first)[:-3]}]]") < timeline.index("## 2026-10-11")
        notes.add_to_timeline(os.path.join(d, "Zeitleiste.md"), "2026-10-13", "Neu")
        assert open(os.path.join(d, "Zeitleiste.md"), encoding="utf-8").read().rstrip().endswith("## 2026-10-13\n- [[Neu]]")


# --- Betrieb ------------------------------------------------------------------------------

def test_backup_skips_secret_files_and_keeps_only_the_latest_ones():
    with tmp() as d:
        src = os.path.join(d, "claude-deck")
        write(os.path.join(src, "server.py"), "x")
        write(os.path.join(src, ".env"), "DECK_PASSWORD=geheim")
        write(os.path.join(src, "credentials.json"), "{}")
        dest = os.path.join(d, "backups")
        for minute in range(upkeep.KEEP_BACKUPS + 2):
            path = upkeep.create_backup(dest, [src], datetime.datetime(2026, 10, 12, 10, minute))
        names = tarfile.open(path).getnames()
        assert "claude-deck/server.py" in names and not any(n.endswith((".env", "credentials.json")) for n in names)
        assert len(upkeep.list_backups(dest)) == upkeep.KEEP_BACKUPS and oct(os.stat(path).st_mode & 0o777) == "0o600"


# --- Kurzbefehl-Schluessel ----------------------------------------------------------------

def test_api_key_is_stored_hashed_checked_with_lockout_and_revocable():
    with tmp() as d:
        store = os.path.join(d, "c.json")
        a = auth.Auth("pw", store=store, clock=lambda: 0)
        key = a.create_api_key()
        assert key not in open(store, encoding="utf-8").read() and a.api_key_count == 1
        assert a.api_key_ok("1.1.1.1", key) and not a.api_key_ok("1.1.1.1", "falsch")
        for _ in range(auth.MAX_FAILURES):
            a.api_key_ok("2.2.2.2", "falsch")
        assert not a.api_key_ok("2.2.2.2", key)  # gesperrte IP
        a.revoke_api_keys()
        assert not a.api_key_ok("3.3.3.3", key) and a.api_key_count == 0


def test_graph_page_gets_a_phone_layout_without_touching_the_original():
    html = "<html><head><title>x</title></head><body><div id='sidebar'></div></body></html>"
    out = notes.with_mobile_layout(html)
    assert 'name="viewport"' in out and "max-width: 700px" in out and "#sidebar" in out and out.endswith("</body></html>") and out.count("</head>") == 1
