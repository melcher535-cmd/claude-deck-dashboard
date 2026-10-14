import base64
import ftplib
import os
import tempfile

from ftp_browser import FtpBrowser, MAX_UPLOAD, clean_path, list_dir


class FakeFtp:
    """Spielt einen FTP-Server im Speicher nach."""
    def __init__(self, mlsd=True):
        self.files, self.dirs, self.use_mlsd, self.quit_called = {"/a.txt": b"hallo", "/sub/b.bin": b"12"}, {"/", "/sub"}, mlsd, False

    def mlsd(self, path, facts):
        if not self.use_mlsd:
            raise ftplib.error_perm("500 MLSD nicht bekannt")
        prefix = path.rstrip("/") + "/"
        yield ".", {"type": "cdir"}
        yield "..", {"type": "pdir"}
        for d in sorted(self.dirs - {"/"}):
            if d.startswith(prefix) and "/" not in d[len(prefix):]:
                yield d[len(prefix):], {"type": "dir"}
        for f, data in self.files.items():
            if f.startswith(prefix) and "/" not in f[len(prefix):]:
                yield f[len(prefix):], {"type": "file", "size": str(len(data))}

    def retrlines(self, cmd, callback):
        callback("drwxr-xr-x   2 ftp ftp      4096 Oct 14 12:00 sub")
        callback("-rw-r--r--   1 ftp ftp         5 Oct 14 12:00 mit leerzeichen.txt")
        callback("total 8")

    def retrbinary(self, cmd, callback):
        path = cmd[len("RETR "):]
        if path not in self.files:
            raise ftplib.error_perm("550 Datei nicht gefunden")
        callback(self.files[path])

    def storbinary(self, cmd, fp):
        self.files[cmd[len("STOR "):]] = fp.read()

    def mkd(self, path):
        self.dirs.add(path)

    def rmd(self, path):
        self.dirs.remove(path)

    def delete(self, path):
        del self.files[path]

    def quit(self):
        self.quit_called = True

    def close(self):
        pass


def make(accept="geheim"):
    server, calls = FakeFtp(), []

    def connect(profile, password):
        calls.append((profile, password))
        if password != accept:
            raise ftplib.error_perm("530 Login incorrect.")
        return server
    return FtpBrowser(os.path.join(tempfile.mkdtemp(), "ftp.json"), connect), server, calls


def verbinden(browser, name="web", password="geheim"):
    return browser.post("/api/ftp/verbinden", {"name": name, "host": "ftp.example.org", "port": "21", "user": "demo", "password": password, "modus": "tls"})


def expect_error(fn, *args):
    try:
        fn(*args)
    except ValueError as e:
        return str(e)
    raise AssertionError("ValueError erwartet")


def test_connect_saves_profile_without_the_password():
    browser, _, _ = make()
    assert verbinden(browser)[0] == 200
    assert "geheim" not in open(browser.path, encoding="utf-8").read()
    assert browser.view()["verbindungen"] == [{"name": "web", "host": "ftp.example.org", "port": 21, "user": "demo", "modus": "tls", "verbunden": True}]
    assert oct(os.stat(browser.path).st_mode & 0o777) == "0o600"


def test_wrong_password_saves_nothing():
    browser, _, _ = make()
    assert "530" in expect_error(verbinden, browser, "web", "falsch")
    assert browser.view()["verbindungen"] == [] and browser.passwords == {}


def test_connect_rejects_bad_input():
    browser, _, calls = make()
    for feld, wert in (("host", "ftp.x/../y"), ("port", "0"), ("modus", "ssh"), ("name", ""), ("user", "")):
        data = {"name": "web", "host": "ftp.example.org", "port": "21", "user": "demo", "password": "geheim", "modus": "tls", feld: wert}
        expect_error(browser.post, "/api/ftp/verbinden", data)
    assert calls == []


def test_browsing_needs_a_saved_and_connected_profile():
    browser, _, _ = make()
    assert expect_error(browser.ls, {"name": ["web"]}) == "Verbindung unbekannt"
    verbinden(browser)
    browser.post("/api/ftp/trennen", {"name": "web"})
    assert "Nicht verbunden" in expect_error(browser.ls, {"name": ["web"]})


def test_ls_lists_folders_first_and_skips_dot_entries():
    browser, _, _ = make()
    verbinden(browser)
    r = browser.ls({"name": ["web"], "pfad": ["/"]})
    assert r["pfad"] == "/" and [(e["name"], e["art"]) for e in r["eintraege"]] == [("sub", "ordner"), ("a.txt", "datei")]
    assert r["eintraege"][1]["groesse"] == 5


def test_ls_falls_back_to_unix_list_output():
    items = list_dir(FakeFtp(mlsd=False), "/")
    assert [(i["name"], i["art"], i["groesse"]) for i in items] == [("sub", "ordner", 4096), ("mit leerzeichen.txt", "datei", 5)]


def test_path_traversal_is_normalized_to_root():
    assert clean_path("/a/../../etc") == "/etc" and clean_path("../../x") == "/x" and clean_path("") == "/" and clean_path("//a") == "/a"


def test_download_returns_name_and_bytes_and_closes_the_connection():
    browser, server, _ = make()
    verbinden(browser)
    assert browser.download({"name": ["web"], "pfad": ["/a.txt"]}) == ("a.txt", b"hallo") and server.quit_called


def test_missing_file_becomes_a_message_not_a_traceback():
    browser, _, _ = make()
    verbinden(browser)
    assert "550" in expect_error(browser.download, {"name": ["web"], "pfad": ["/weg.txt"]})


def test_upload_writes_file_and_checks_name_and_size():
    browser, server, _ = make()
    verbinden(browser)
    daten = base64.b64encode(b"neu").decode()
    assert browser.post("/api/ftp/hochladen", {"name": "web", "pfad": "/sub", "datei": "n.txt", "daten": daten})[0] == 200
    assert server.files["/sub/n.txt"] == b"neu"
    for datei in ("../x", "a/b", "..", "", "a\r\nDELE b"):
        expect_error(browser.post, "/api/ftp/hochladen", {"name": "web", "pfad": "/", "datei": datei, "daten": daten})
    expect_error(browser.post, "/api/ftp/hochladen", {"name": "web", "pfad": "/", "datei": "x", "daten": "kein base64!"})
    big = base64.b64encode(b"x" * (MAX_UPLOAD + 1)).decode()
    assert "groesser" in expect_error(browser.post, "/api/ftp/hochladen", {"name": "web", "pfad": "/", "datei": "x", "daten": big})


def test_mkdir_and_delete():
    browser, server, _ = make()
    verbinden(browser)
    browser.post("/api/ftp/ordner", {"name": "web", "pfad": "/", "neu": "neu"})
    assert "/neu" in server.dirs
    browser.post("/api/ftp/loeschen", {"name": "web", "pfad": "/neu", "art": "ordner"})
    browser.post("/api/ftp/loeschen", {"name": "web", "pfad": "/a.txt", "art": "datei"})
    assert "/neu" not in server.dirs and "/a.txt" not in server.files
    expect_error(browser.post, "/api/ftp/loeschen", {"name": "web", "pfad": "/", "art": "ordner"})


def test_audit_text_never_contains_the_password():
    browser, _, _ = make()
    _, payload = verbinden(browser)
    assert "geheim" not in payload["_audit"]


def test_remove_forgets_profile_and_password():
    browser, _, _ = make()
    verbinden(browser)
    browser.post("/api/ftp/entfernen", {"name": "web"})
    assert browser.view()["verbindungen"] == [] and browser.passwords == {}
    expect_error(browser.post, "/api/ftp/entfernen", {"name": "web"})


def test_post_routes_unknown_paths_to_none():
    browser, _, _ = make()
    assert browser.post("/api/docker/neu", {}) is None and browser.post("/api/ftp/unbekannt", {}) is None
