import os
import re
import stat
import tempfile

import widgets


def layout():
    return widgets.WidgetLayout(os.path.join(tempfile.mkdtemp(), "widgets.json"))


def widget(**changes):
    return {"id": "w1", "title": "Kosten", "source": "kosten", "view": "zahl", "range": 7, "width": "halb", **changes}


def rejected(store, data):
    try:
        store.save(data)
    except ValueError as e:
        return str(e)
    raise AssertionError("not rejected")


def test_returns_empty_layout_when_nothing_is_saved():
    assert layout().all() == {"order": [], "hidden": [], "custom": []}


def test_saves_and_returns_the_cleaned_layout_with_private_file_rights():
    store = layout()
    saved = store.save({"order": ["usage", "eigen-w1"], "hidden": ["live"], "custom": [widget(evil="x")]})
    assert saved == {"order": ["usage", "eigen-w1"], "hidden": ["live"], "custom": [widget()]}
    assert store.all() == saved
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600


def test_rejects_unknown_source_view_range_and_width():
    store = layout()
    for bad in ({"source": "root-shell"}, {"view": "iframe"}, {"range": 365}, {"width": "riesig"}):
        rejected(store, {"custom": [widget(**bad)]})


def test_rejects_bad_ids_duplicates_and_too_many_widgets():
    store = layout()
    rejected(store, {"custom": [widget(id="../x")]})
    rejected(store, {"custom": [widget(), widget()]})
    rejected(store, {"custom": [widget(id=f"w{i}") for i in range(widgets.MAX_CUSTOM + 1)]})


def test_rejects_empty_or_overlong_title():
    store = layout()
    rejected(store, {"custom": [widget(title="  ")]})
    rejected(store, {"custom": [widget(title="x" * (widgets.MAX_TITLE + 1))]})


def test_order_and_hidden_keep_only_valid_unique_ids():
    store = layout()
    saved = store.save({"order": ["usage", "usage", "../x", 5, "live"], "hidden": ["live", "live", "<b>"]})
    assert saved["order"] == ["usage", "live"] and saved["hidden"] == ["live"]


def test_rejects_wrong_types():
    store = layout()
    rejected(store, {"custom": "x"})
    rejected(store, {"custom": [5]})
    rejected(store, {"order": "usage"})


def test_every_source_has_a_renderer_in_the_front_end():
    ids = set(re.findall(r"^  (\w+): \{ name:", open(os.path.join(os.path.dirname(__file__), "widgets.js"), encoding="utf-8").read(), re.M))
    assert ids == set(widgets.SOURCES)
