import base64
import os
import tempfile

from dateien import MAX_UPLOAD, Dateien, clean_path


def expect_error(fn, *args):
    try:
        fn(*args)
    except ValueError as e:
        return str(e)
    raise AssertionError("ValueError erwartet")


def make():
    root = os.path.realpath(tempfile.mkdtemp())
    os.makedirs(f"{root}/ordner/leer")
    open(f"{root}/ordner/a.txt", "w").write("hallo")
    open(f"{root}/b.txt", "w").write("zwei")
    os.symlink(f"{root}/b.txt", f"{root}/link")
    return Dateien(), root


def post(d, action, **data):
    return d.post(f"/api/dateien/{action}", data)


def test_ls_lists_folders_first_with_kind_and_size():
    d, root = make()
    r = d.ls({"pfad": [root]})
    assert [(e["name"], e["art"]) for e in r["eintraege"]] == [("ordner", "ordner"), ("b.txt", "datei"), ("link", "link")]
    assert r["eintraege"][1]["groesse"] == 4 and r["pfad"] == root and not r["gekuerzt"]


def test_ls_reports_missing_and_non_folder_as_messages():
    d, root = make()
    assert expect_error(d.ls, {"pfad": [f"{root}/nix"]})
    assert expect_error(d.ls, {"pfad": [f"{root}/b.txt"]}) == "Kein Ordner"


def test_path_parts_with_dotdot_and_double_slash_are_normalized():
    assert clean_path("/a/../../etc") == "/etc" and clean_path("//a") == "/a" and clean_path("") == "/"


def test_download_returns_name_bytes_and_real_path():
    d, root = make()
    assert d.download({"pfad": [f"{root}/ordner/a.txt"]}) == ("a.txt", b"hallo", f"{root}/ordner/a.txt")
    assert d.download({"pfad": [f"{root}/link"]})[1:] == (b"zwei", f"{root}/b.txt")
    assert expect_error(d.download, {"pfad": [f"{root}/ordner"]})


def test_upload_creates_file_but_never_overwrites():
    d, root = make()
    daten = base64.b64encode(b"neu").decode()
    assert post(d, "hochladen", pfad=root, datei="n.txt", daten=daten)[0] == 200 and open(f"{root}/n.txt", "rb").read() == b"neu"
    assert expect_error(d.post, "/api/dateien/hochladen", {"pfad": root, "datei": "b.txt", "daten": daten}) and open(f"{root}/b.txt").read() == "zwei"
    for datei in ("../x", "a/b", "..", ""):
        expect_error(d.post, "/api/dateien/hochladen", {"pfad": root, "datei": datei, "daten": daten})
    big = base64.b64encode(b"x" * (MAX_UPLOAD + 1)).decode()
    assert "groesser" in expect_error(d.post, "/api/dateien/hochladen", {"pfad": root, "datei": "big", "daten": big})


def test_delete_removes_files_and_empty_folders_only():
    d, root = make()
    post(d, "loeschen", pfad=f"{root}/ordner/leer")
    post(d, "loeschen", pfad=f"{root}/b.txt")
    assert not os.path.exists(f"{root}/ordner/leer") and not os.path.exists(f"{root}/b.txt")
    assert expect_error(d.post, "/api/dateien/loeschen", {"pfad": f"{root}/ordner"}) and os.path.exists(f"{root}/ordner/a.txt")


def test_delete_of_a_symlink_removes_the_link_not_the_target():
    d, root = make()
    post(d, "loeschen", pfad=f"{root}/link")
    assert not os.path.lexists(f"{root}/link") and os.path.exists(f"{root}/b.txt")


def test_top_level_folders_are_protected():
    d, _ = make()
    for action, data in (("loeschen", {"pfad": "/etc"}), ("umbenennen", {"pfad": "/etc", "neu": "x"})):
        assert "geschuetzt" in expect_error(d.post, f"/api/dateien/{action}", data)
    assert expect_error(d.post, "/api/dateien/loeschen", {"pfad": "/"})


def test_rename_refuses_to_replace_and_rejects_paths_in_the_new_name():
    d, root = make()
    post(d, "umbenennen", pfad=f"{root}/b.txt", neu="c.txt")
    assert os.path.exists(f"{root}/c.txt")
    open(f"{root}/d.txt", "w").write("x")
    assert "existiert" in expect_error(d.post, "/api/dateien/umbenennen", {"pfad": f"{root}/c.txt", "neu": "d.txt"})
    assert expect_error(d.post, "/api/dateien/umbenennen", {"pfad": f"{root}/c.txt", "neu": "../c"}) and os.path.exists(f"{root}/c.txt")


def test_mkdir_creates_folder_and_refuses_duplicates():
    d, root = make()
    post(d, "ordner", pfad=root, neu="neu")
    assert os.path.isdir(f"{root}/neu") and expect_error(d.post, "/api/dateien/ordner", {"pfad": root, "neu": "neu"})


def test_post_routes_unknown_paths_to_none():
    assert Dateien().post("/api/ftp/ordner", {}) is None and Dateien().post("/api/dateien/unbekannt", {}) is None
