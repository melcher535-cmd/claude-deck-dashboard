"""Betrieb: Sicherungen anlegen und auflisten, Selbstdiagnose (Zertifikat, Platte, Journal)."""
import datetime
import os
import re
import shutil
import socket
import ssl
import subprocess
import tarfile

KEEP_BACKUPS = 10
EXCLUDE = (".env", "credentials.json", "push-keys.json", "push-subs.json", "access-log.jsonl", "__pycache__", ".pyc")
CERT_WARN_DAYS = 14


def _filter(info):
    return None if any(part in info.name for part in EXCLUDE) else info


def create_backup(dest, sources, now=None):
    """tar.gz mit den Quellen (Name im Archiv = Ordnername). Geheimnisdateien bleiben draussen. Behaelt die letzten Sicherungen."""
    now = now or datetime.datetime.now()
    os.makedirs(dest, mode=0o700, exist_ok=True)
    path = os.path.join(dest, f"deck-{now:%Y-%m-%d_%H%M%S}.tar.gz")
    with tarfile.open(path, "w:gz") as tar:
        for source in sources:
            if os.path.exists(source):
                tar.add(source, arcname=os.path.basename(source.rstrip("/")), filter=_filter)
    os.chmod(path, 0o600)
    for old in sorted(f for f in os.listdir(dest) if f.startswith("deck-") and f.endswith(".tar.gz"))[:-KEEP_BACKUPS]:
        os.remove(os.path.join(dest, old))
    return path


def list_backups(dest):
    try:
        files = sorted((f for f in os.listdir(dest) if f.endswith(".tar.gz")), reverse=True)
    except FileNotFoundError:
        return []
    return [{"name": f, "size": os.path.getsize(os.path.join(dest, f)), "mtime": os.path.getmtime(os.path.join(dest, f))} for f in files]


def certificate_days(host, port=443, address="127.0.0.1"):
    """Verbleibende Tage des Zertifikats, das der Server fuer `host` ausliefert (None, wenn nicht lesbar)."""
    try:
        with socket.create_connection((address, port), timeout=5) as raw, ssl.create_default_context().wrap_socket(raw, server_hostname=host) as tls:
            not_after = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
    except (OSError, ssl.SSLError, KeyError):
        return None
    return int((not_after - datetime.datetime.now(datetime.timezone.utc).timestamp()) // 86400)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout


def diagnose(host, service="claude-deck"):
    disk = shutil.disk_usage("/")
    journal = re.search(r"take up ([\d.]+\w) ", run(["journalctl", "--disk-usage"]))
    errors = [line[:220] for line in run(["journalctl", "-u", service, "-n", "200", "--no-pager", "-o", "cat"]).splitlines() if re.search(r"Traceback|Error|usage-sample|tagesbericht|push:", line)][-8:]
    return {"cert_days": certificate_days(host), "cert_warn_days": CERT_WARN_DAYS, "disk_free": disk.free, "disk_total": disk.total,
            "journal": journal.group(1) if journal else None, "errors": errors,
            "service_since": run(["systemctl", "show", service, "--property=ActiveEnterTimestamp", "--value"]).strip()}
