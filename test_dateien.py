import base64
import os
import tempfile

import dateien
from dateien import MAX_TEXT, MAX_UPLOAD, Dateien, clean_path


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


def test_ls_shows_permissions():
    d, root = make()
    assert d.ls({"pfad": [root]})["eintraege"][0]["rechte"].startswith("d")


def test_download_refuses_device_files_instead_of_reading_forever():
    d, _ = make()
    assert expect_error(d.download, {"pfad": ["/dev/zero"]}) == "Keine normale Datei"


def test_copy_file_into_folder_and_name_clash_gets_a_suffix():
    d, root = make()
    post(d, "kopieren", pfad=f"{root}/b.txt", ziel=f"{root}/ordner")
    post(d, "kopieren", pfad=f"{root}/b.txt", ziel=f"{root}/ordner")
    r = post(d, "kopieren", pfad=f"{root}/b.txt", ziel=f"{root}/ordner")[1]
    assert open(f"{root}/ordner/b.txt").read() == "zwei" and os.path.exists(f"{root}/ordner/b Kopie.txt") and r["name"] == "b Kopie 2.txt"
    assert open(f"{root}/b.txt").read() == "zwei"


def test_copy_folder_recursively_keeps_symlinks_as_links():
    d, root = make()
    os.symlink("a.txt", f"{root}/ordner/rel")
    os.mkdir(f"{root}/ziel")
    post(d, "kopieren", pfad=f"{root}/ordner", ziel=f"{root}/ziel")
    assert open(f"{root}/ziel/ordner/a.txt").read() == "hallo" and os.path.islink(f"{root}/ziel/ordner/rel")


def test_copy_folder_into_itself_is_refused_and_leaves_nothing_behind():
    d, root = make()
    assert "sich selbst" in expect_error(d.post, "/api/dateien/kopieren", {"pfad": f"{root}/ordner", "ziel": f"{root}/ordner/leer"})
    assert os.listdir(f"{root}/ordner/leer") == []


def test_copy_refuses_missing_target_folder_and_special_files():
    d, root = make()
    assert "Zielordner" in expect_error(d.post, "/api/dateien/kopieren", {"pfad": f"{root}/b.txt", "ziel": f"{root}/nix"})
    assert "Sonder" in expect_error(d.post, "/api/dateien/kopieren", {"pfad": "/dev/zero", "ziel": root})


def test_copy_refuses_folders_containing_special_files():
    d, root = make()
    os.mkfifo(f"{root}/ordner/pipe")
    os.mkdir(f"{root}/zwei")
    assert "Sonder" in expect_error(d.post, "/api/dateien/kopieren", {"pfad": f"{root}/ordner", "ziel": f"{root}/zwei"})


def test_move_moves_file_and_folder_and_never_replaces():
    d, root = make()
    os.mkdir(f"{root}/ziel")
    post(d, "verschieben", pfad=f"{root}/b.txt", ziel=f"{root}/ziel")
    post(d, "verschieben", pfad=f"{root}/ordner", ziel=f"{root}/ziel")
    assert os.path.exists(f"{root}/ziel/b.txt") and os.path.exists(f"{root}/ziel/ordner/a.txt") and not os.path.exists(f"{root}/b.txt")
    open(f"{root}/b.txt", "w").write("neu")
    assert "schon" in expect_error(d.post, "/api/dateien/verschieben", {"pfad": f"{root}/b.txt", "ziel": f"{root}/ziel"}) and open(f"{root}/ziel/b.txt").read() == "zwei"


def test_move_folder_into_itself_and_top_level_are_refused():
    d, root = make()
    assert "sich selbst" in expect_error(d.post, "/api/dateien/verschieben", {"pfad": f"{root}/ordner", "ziel": f"{root}/ordner/leer"})
    assert "geschuetzt" in expect_error(d.post, "/api/dateien/verschieben", {"pfad": "/etc", "ziel": root})


def test_recursive_delete_removes_a_full_folder_but_not_through_symlinks():
    d, root = make()
    os.symlink(f"{root}/b.txt", f"{root}/ordner/zeigt-nach-aussen")
    assert expect_error(d.post, "/api/dateien/loeschen", {"pfad": f"{root}/ordner"}) and os.path.exists(f"{root}/ordner/a.txt")
    post(d, "loeschen", pfad=f"{root}/ordner", rekursiv=True)
    assert not os.path.exists(f"{root}/ordner") and open(f"{root}/b.txt").read() == "zwei"


def test_recursive_delete_refuses_the_folder_that_holds_the_dashboard():
    d, root = make()
    old, dateien.HERE = dateien.HERE, f"{root}/ordner"
    try:
        assert "Dashboard" in expect_error(d.post, "/api/dateien/loeschen", {"pfad": f"{root}/ordner", "rekursiv": True})
    finally:
        dateien.HERE = old
    assert os.path.exists(f"{root}/ordner/a.txt")


def test_save_replaces_text_keeps_mode_and_checks_input():
    d, root = make()
    os.chmod(f"{root}/b.txt", 0o640)
    post(d, "speichern", pfad=f"{root}/b.txt", text="neuer Text mit \u00fc")
    assert open(f"{root}/b.txt", encoding="utf-8").read() == "neuer Text mit \u00fc" and os.stat(f"{root}/b.txt").st_mode & 0o777 == 0o640
    post(d, "speichern", pfad=f"{root}/b.txt", text="kurz")
    assert open(f"{root}/b.txt").read() == "kurz"
    assert "existiert nicht" in expect_error(d.post, "/api/dateien/speichern", {"pfad": f"{root}/nix", "text": "x"})
    assert "existiert nicht" in expect_error(d.post, "/api/dateien/speichern", {"pfad": f"{root}/ordner", "text": "x"})
    assert expect_error(d.post, "/api/dateien/speichern", {"pfad": f"{root}/b.txt"}) == "Text fehlt"
    assert "groesser" in expect_error(d.post, "/api/dateien/speichern", {"pfad": f"{root}/b.txt", "text": "x" * (MAX_TEXT + 1)})
