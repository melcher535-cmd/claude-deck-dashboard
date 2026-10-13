"""Vault lesen und durchsuchen, Notiz aus einem Chat anlegen, Graph fuer die Handy-Ansicht aufbereiten."""
import collections
import datetime
import glob
import json
import os
import re

MAX_NOTE_BYTES = 200_000
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\r\n]+')
SKIP = ("graphify-out", ".obsidian", ".git")
CHAT_FOLDER = "Projekte/ChatNotizen"
MAX_TITLE = 80


def note_paths(vault):
    return [p for p in glob.glob(f"{vault}/**/*.md", recursive=True) if not any(f"/{s}/" in p for s in SKIP)]


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def search_notes(vault, query, limit=40):
    """Volltextsuche: Treffer im Namen zuerst, dann im Text, jeweils mit Ausschnitt."""
    needle, hits = query.lower(), []
    for p in note_paths(vault):
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read(MAX_NOTE_BYTES)
        pos = text.lower().find(needle)
        in_name = needle in stem(p).lower()
        if pos >= 0 or in_name:
            snippet = text[max(0, pos - 50):pos + 110].replace("\n", " ") if pos >= 0 else text[:140].replace("\n", " ")
            hits.append({"path": os.path.relpath(p, vault), "title": stem(p), "snippet": snippet, "mtime": os.path.getmtime(p), "name": in_name})
    return sorted(hits, key=lambda h: (not h["name"], -h["mtime"]))[:limit]


def recent_notes(vault, limit=30):
    notes = sorted(note_paths(vault), key=os.path.getmtime, reverse=True)[:limit]
    return [{"path": os.path.relpath(p, vault), "title": stem(p), "mtime": os.path.getmtime(p)} for p in notes]


def read_note(vault, relative):
    """Eine Notiz lesen. Der aufgeloeste Pfad muss im Vault liegen und auf .md enden, sonst None."""
    root = os.path.realpath(vault)
    path = os.path.realpath(os.path.join(root, relative))
    if not path.startswith(root + os.sep) or not path.endswith(".md") or any(f"/{s}/" in path for s in SKIP) or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return {"path": os.path.relpath(path, root), "title": stem(path), "text": f.read(MAX_NOTE_BYTES), "mtime": os.path.getmtime(path)}


def safe_title(title):
    return UNSAFE_NAME.sub(" ", title).strip()[:MAX_TITLE].strip() or "Chat"


def add_to_timeline(timeline_path, day, note_stem):
    """Eintrag unter der Ueberschrift des Tages ergaenzen (oder den Tag neu anlegen)."""
    with open(timeline_path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    entry, heading = f"- [[{note_stem}]]", f"## {day}"
    if heading in lines:
        i = lines.index(heading) + 1
        while i < len(lines) and not lines[i].startswith("## "):
            i += 1
        while i > 0 and lines[i - 1].strip() == "":
            i -= 1
        lines.insert(i, entry)
    else:
        lines += ["", heading, entry, ""]
    with open(timeline_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def create_chat_note(vault, info, day):
    """Notiz mit Kennzahlen eines Chats anlegen. Gibt den relativen Pfad zurueck; ueberschreibt nie."""
    folder = os.path.join(vault, CHAT_FOLDER)
    os.makedirs(folder, exist_ok=True)
    base, n = f"{day} {safe_title(info['title'])}", 1
    while os.path.exists(os.path.join(folder, f"{base}{'' if n == 1 else f' {n}'}.md")):
        n += 1
    name = f"{base}{'' if n == 1 else f' {n}'}"
    tools = ", ".join(f"{t} ({c})" for t, c in info["tools"][:8]) or "keine"
    files = "\n".join(f"- `{f}`" for f in info["files"][:30]) or "- keine"
    text = (f"---\nerstellt: {day}\ntags: [chat, claude-deck]\n---\n# {info['title']}\n\n"
            f"- Session: `{info['session_id']}`\n- Ordner: `{info['cwd']}`\n- Zeitraum: {info['first']} bis {info['last']}\n- Nachrichten: {info['messages']}\n"
            f"- Werkzeuge: {tools}\n\n## Erste Nachricht\n\n{info['first_message'][:600]}\n\n## Geänderte Dateien\n\n{files}\n")
    with open(os.path.join(folder, f"{name}.md"), "w", encoding="utf-8") as f:
        f.write(text)
    add_to_timeline(os.path.join(vault, "Zeitleiste.md"), day, name)
    return f"{CHAT_FOLDER}/{name}.md"


GRAPH_MOBILE = """<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  @media (max-width: 700px) {
    body { flex-direction: column; height: 100vh; height: 100dvh; }
    #graph { flex: 1 1 auto; min-height: 0; }
    #sidebar { width: 100%; max-height: 38%; border-left: 0; border-top: 1px solid #2a2a4e; }
    #info-panel { min-height: 0; padding: 10px 14px; }
    #legend-wrap { padding: 8px 12px; }
  }
</style>"""


def with_mobile_layout(html):
    """graphify legt eine feste 280-px-Seitenleiste neben den Graphen. Auf schmalen Bildschirmen steht sie darunter."""
    return html.replace("</head>", GRAPH_MOBILE + "</head>", 1)


def graph_data(graph_path):
    """Knoten und Kanten kompakt fuer die Zeichnung im Browser."""
    with open(graph_path, encoding="utf-8") as f:
        g = json.load(f)
    index = {n["id"]: i for i, n in enumerate(g["nodes"])}
    degree = collections.Counter()
    edges = [[index[e["source"]], index[e["target"]]] for e in g["links"] if e["source"] in index and e["target"] in index]
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
    communities = sorted({n.get("community_name") or str(n.get("community")) for n in g["nodes"]})
    cid = {name: i for i, name in enumerate(communities)}
    nodes = [{"l": n["label"], "c": cid[n.get("community_name") or str(n.get("community"))], "d": degree[i], "f": n.get("source_file")} for i, n in enumerate(g["nodes"])]
    return {"nodes": nodes, "edges": edges, "communities": communities}
