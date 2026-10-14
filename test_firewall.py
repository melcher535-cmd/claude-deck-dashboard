import os
import tempfile
import threading
import contextlib

import firewall

STATUS = """Status: active

     To                         Action      From
     --                         ------      ----
[ 1] 22/tcp                     ALLOW IN    Anywhere
[ 2] 80/tcp                     ALLOW IN    Anywhere
[ 3] 443/tcp                    ALLOW IN    Anywhere
[ 4] 22/tcp (v6)                ALLOW IN    Anywhere (v6)
[ 5] 8080/tcp                   ALLOW IN    203.0.113.0/24            # Kamera
"""
VERBOSE = "Status: active\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n"
SS = """tcp LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))
tcp LISTEN 0 4096 [::]:22 [::]:* users:(("sshd",pid=1,fd=4))
tcp LISTEN 0 4096 127.0.0.1:8770 0.0.0.0:* users:(("python3",pid=2,fd=4))
tcp LISTEN 0 4096 0.0.0.0:9000 0.0.0.0:* users:(("docker-proxy",pid=3,fd=4))
tcp LISTEN 0 4096 0.0.0.0:8080 0.0.0.0:* users:(("kamera",pid=4,fd=4))
tcp LISTEN 0 4096 0.0.0.0:5432 0.0.0.0:* users:(("postgres",pid=5,fd=4))
"""


class FakeUfw:
    """Spielt ufw nach: merkt sich die Befehle und liefert feste Ausgaben."""
    def __init__(self):
        self.calls, self.fail = [], False

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:3] == ["ufw", "status", "numbered"]:
            return 0, STATUS
        if argv[:3] == ["ufw", "status", "verbose"]:
            return 0, VERBOSE
        if argv[0] == "ss":
            return 0, SS
        if argv[:2] == ["ufw", "version"]:
            return 0, "ufw 0.36.2"
        return (1, "ERROR: Bad port") if self.fail else (0, "Rule added")

    def changes(self):
        return [c for c in self.calls if c[0] == "ufw" and c[1] not in ("status", "version")]


@contextlib.contextmanager
def make():
    with tempfile.TemporaryDirectory() as d:
        for name in firewall.SNAPSHOT_FILES:
            with open(os.path.join(d, name), "w") as f:
                f.write(f"alt {name}")
        fake, messages, audits = FakeUfw(), [], []
        fw = firewall.Firewall(notify=messages.append, audit=audits.append, run=fake, ufw_dir=d, seconds=0.2)
        yield fw, fake, d, messages, audits
        if fw.timer:
            fw.timer.cancel()


def refused(fn, text=""):
    try:
        fn()
    except ValueError as e:
        assert text in str(e), str(e)
        return True
    return False


def test_parses_rules_with_source_comment_and_v6_flag():
    rules = firewall.parse_rules(STATUS)
    assert [r["nr"] for r in rules] == [1, 2, 3, 4, 5]
    assert rules[3]["v6"] and rules[3]["von"] == "Anywhere (v6)"
    assert rules[4]["von"] == "203.0.113.0/24" and rules[4]["kommentar"] == "Kamera" and rules[4]["port"] == "8080"


def test_lists_listening_ports_once_per_protocol_and_scope():
    ports = firewall.parse_listening(SS)
    assert [(p["port"], p["prozess"], p["lokal"]) for p in ports if p["port"] == 22] == [(22, "sshd", False)]
    assert next(p for p in ports if p["port"] == 8770)["lokal"]


def test_view_marks_exposure_of_each_port():
    with make() as (fw, *_):
        zustand = {p["port"]: p["zustand"] for p in fw.view()["ports"]}
    assert zustand == {22: "offen", 5432: "blockiert", 8080: "eingeschraenkt", 9000: "docker", 8770: "lokal"}


def test_view_reports_defaults_and_protected_ports():
    with make() as (fw, *_):
        view = fw.view()
    assert view["standard"]["eingehend"] == "deny" and view["geschuetzt"] == [22, 80, 443]


def test_add_rule_builds_argv_without_shell():
    with make() as (fw, fake, *_):
        r = fw.add_rule({"aktion": "allow", "port": "9000", "proto": "tcp", "quelle": "203.0.113.7", "kommentar": "Test"})
        assert fake.changes() == [["ufw", "allow", "from", "203.0.113.7/32", "to", "any", "port", "9000", "proto", "tcp", "comment", "Test"]]
    assert r["ruecknahme"] is None


def test_rejects_invalid_rule_input_before_calling_ufw():
    bad = [{"aktion": "allow; reboot", "port": "80"}, {"aktion": "allow", "port": "0"}, {"aktion": "allow", "port": "70000"}, {"aktion": "allow", "port": "80 && ls"},
           {"aktion": "allow", "port": "9000:8000", "proto": "tcp"}, {"aktion": "allow", "port": "8000:8100", "proto": "any"},
           {"aktion": "allow", "port": "80", "quelle": "999.1.1.1"}, {"aktion": "allow", "port": "80", "quelle": "1.1.1.1; ls"},
           {"aktion": "allow", "port": "80", "kommentar": "a\nb"}, {"aktion": "allow", "port": "80", "proto": "icmp"}]
    with make() as (fw, fake, *_):
        for data in bad:
            assert refused(lambda: fw.add_rule(data)), data
        assert fake.changes() == []


def test_deny_on_protected_port_needs_confirmation():
    with make() as (fw, fake, *_):
        for port in ("22", "443", "20:100"):
            assert refused(lambda: fw.add_rule({"aktion": "deny", "port": port, "proto": "tcp"}), "geschützt")
        assert fake.changes() == []
        fw.add_rule({"aktion": "deny", "port": "22", "proto": "tcp", "bestaetigt": True})
        assert len(fake.changes()) == 1


def test_deny_from_one_address_on_protected_port_is_allowed():
    with make() as (fw, fake, *_):
        fw.add_rule({"aktion": "deny", "port": "22", "proto": "tcp", "quelle": "198.51.100.9"})
        assert len(fake.changes()) == 1


def test_delete_checks_rule_still_matches_what_the_browser_saw():
    with make() as (fw, fake, *_):
        assert refused(lambda: fw.delete_rule({"nr": 5, "nach": "9999/tcp", "aktion": "ALLOW", "von": "203.0.113.0/24"}), "geändert")
        assert fake.changes() == []
        fw.delete_rule({"nr": 5, "nach": "8080/tcp", "aktion": "ALLOW", "von": "203.0.113.0/24"})
        assert fake.changes() == [["ufw", "--force", "delete", "5"]]


def test_delete_of_ssh_or_web_allow_needs_confirmation():
    rule = {"nr": 3, "nach": "443/tcp", "aktion": "ALLOW", "von": "Anywhere"}
    with make() as (fw, fake, *_):
        assert refused(lambda: fw.delete_rule(rule), "geschützt")
        assert fake.changes() == []
        fw.delete_rule({**rule, "bestaetigt": True})
        assert fake.changes() == [["ufw", "--force", "delete", "3"]]


def test_ufw_error_becomes_value_error_and_arms_no_timer():
    with make() as (fw, fake, *_):
        fake.fail = True
        assert refused(lambda: fw.add_rule({"aktion": "allow", "port": "9000", "ruecknahme": True}), "ufw: ERROR")
        assert fw.pending is None


def test_unconfirmed_change_is_rolled_back_by_timer():
    with make() as (fw, fake, d, messages, audits):
        done = threading.Event()
        fw.notify = lambda text: (messages.append(text), done.set())
        fw.add_rule({"aktion": "allow", "port": "9000", "proto": "tcp", "ruecknahme": True})
        with open(os.path.join(d, "user.rules"), "w") as f:
            f.write("neu")
        assert done.wait(3)
        assert open(os.path.join(d, "user.rules")).read() == "alt user.rules"
        assert fake.calls[-1] == ["ufw", "--force", "enable"] and fw.pending is None
        assert "zurückgenommen" in messages[0] and "automatisch" in audits[0]


def test_confirmed_change_stays():
    with make() as (fw, fake, d, messages, _):
        ident = fw.add_rule({"aktion": "allow", "port": "9000", "ruecknahme": True})["ruecknahme"]
        with open(os.path.join(d, "user.rules"), "w") as f:
            f.write("neu")
        fw.confirm(ident)
        fw.timer.join(0.5)
        assert open(os.path.join(d, "user.rules")).read() == "neu" and fw.pending is None and messages == []


def test_manual_rollback_restores_snapshot_immediately():
    with make() as (fw, _, d, *_):
        ident = fw.add_rule({"aktion": "allow", "port": "9000", "ruecknahme": True})["ruecknahme"]
        with open(os.path.join(d, "user6.rules"), "w") as f:
            f.write("neu")
        fw.rollback_now(ident)
        assert open(os.path.join(d, "user6.rules")).read() == "alt user6.rules"


def test_second_change_waits_while_one_is_pending():
    with make() as (fw, fake, *_):
        fw.add_rule({"aktion": "allow", "port": "9000", "ruecknahme": True})
        assert refused(lambda: fw.add_rule({"aktion": "allow", "port": "9001"}), "wartet noch")
        assert len(fake.changes()) == 1


def test_confirm_with_wrong_id_is_rejected():
    with make() as (fw, *_):
        fw.add_rule({"aktion": "allow", "port": "9000", "ruecknahme": True})
        assert refused(lambda: fw.confirm("falsch"))


def test_toggle_uses_enable_and_disable():
    with make() as (fw, fake, *_):
        fw.set_enabled({"an": False})
        fw.set_enabled({"an": True})
        assert fake.changes() == [["ufw", "disable"], ["ufw", "--force", "enable"]]


def test_post_routes_unknown_paths_to_none():
    with make() as (fw, *_):
        assert fw.post("/api/netz/scan", {}) is None and fw.post("/api/firewall/unbekannt", {}) is None
