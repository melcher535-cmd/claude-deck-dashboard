"""Kennzahlen von jedem Linux-Geraet: lokal oder per SSH. Ein festes Shell-Skript liest /proc, df, smartctl, mdstat und zpool, ein Parser macht das Ergebnis einheitlich.
Deckt UGREEN, Unraid, OpenMediaVault, TrueNAS SCALE, Proxmox, Raspberry Pi, OpenWrt und jedes andere Linux-NAS ab, auch wenn es keine eigene Schnittstelle hat."""
import os
import re
import selectors
import subprocess
import time

from netz_http import NetzFehler, pruefe_ziel

TIMEOUT = 25
MAX_AUSGABE = 1_000_000  # ein fremdes Geraet darf uns nicht den Speicher fuellen
SSH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")
SKIP_MOUNTS = ("/boot", "/snap", "/run", "/sys", "/proc", "/dev", "/var/lib/docker", "/var/lib/containers", "/var/lib/kubelet")
REAL_FS = ("ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "ntfs", "ntfs3", "exfat", "vfat", "fuseblk", "jfs", "reiserfs", "bcachefs")
SKIP_NET = ("lo", "docker", "veth", "br-", "virbr", "tailscale", "wg", "tun", "tap")
TEMP_MIN, TEMP_MAX = 5, 120
UNITS = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40, "P": 1 << 50}
HEALTH_PASSED = re.compile(r"overall-health self-assessment test result:\s*PASSED|SMART Health Status:\s*OK")
HEALTH_FAILED = re.compile(r"overall-health self-assessment test result:\s*FAILED|SMART Health Status:\s*(?!OK)")
SMART_TEMP = (re.compile(r"^\s*19[04]\s+\S*Temperature\S*\s+(?:\S+\s+){7}(\d+)"), re.compile(r"^Temperature:\s+(\d+)\s+Celsius"), re.compile(r"^Current Drive Temperature:\s+(\d+)"))
MD_PROGRESS = re.compile(r"(resync|recovery|reshape|check)\s*=\s*([\d.]+)%")

SCRIPT = r"""
echo '##os'; uname -sr; grep -E '^(PRETTY_NAME|NAME)=' /etc/os-release 2>/dev/null | head -1
echo '##meminfo'; grep -E '^(MemTotal|MemAvailable|MemFree|Buffers|Cached):' /proc/meminfo
echo '##loadavg'; cat /proc/loadavg
echo '##uptime'; cat /proc/uptime
echo '##cpustat'; head -1 /proc/stat; grep -c '^processor' /proc/cpuinfo
echo '##mounts'; cat /proc/mounts
echo '##df'; df -kP 2>/dev/null || df -k
echo '##temp'; for f in /sys/class/thermal/thermal_zone*/temp /sys/class/hwmon/hwmon*/temp*_input; do [ -r "$f" ] && echo "$f $(cat "$f" 2>/dev/null)"; done
echo '##mdstat'; cat /proc/mdstat 2>/dev/null
echo '##netdev'; cat /proc/net/dev
echo '##zpool'; command -v zpool >/dev/null 2>&1 && zpool list -H -o name,size,alloc,health 2>/dev/null
echo '##smart'; if command -v smartctl >/dev/null 2>&1; then for d in /dev/sd? /dev/nvme?n1; do [ -e "$d" ] && { echo "@@$d"; smartctl -iHA "$d" 2>/dev/null; }; done; fi
echo '##end'
"""


def ausfuehren(cmd, eingabe):
    """Startet cmd, gibt (stdout, stderr) als Text zurueck. Bricht ab (Prozess wird beendet), wenn es laenger als TIMEOUT dauert oder mehr als MAX_AUSGABE Byte kommen."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    daten, ende, auswahl = {proc.stdout: bytearray(), proc.stderr: bytearray()}, time.time() + TIMEOUT, selectors.DefaultSelector()
    try:
        try:
            proc.stdin.write(eingabe.encode())
            proc.stdin.close()
        except BrokenPipeError:
            pass  # das Ziel hat sofort aufgelegt; der Grund steht in stderr und wird unten gemeldet
        for strom in daten:
            auswahl.register(strom, selectors.EVENT_READ)
        while auswahl.get_map():
            rest = ende - time.time()
            if rest <= 0:
                raise subprocess.TimeoutExpired(cmd, TIMEOUT)
            for schluessel, _ in auswahl.select(rest):
                teil = os.read(schluessel.fd, 65536)
                if not teil:
                    auswahl.unregister(schluessel.fileobj)
                    continue
                daten[schluessel.fileobj] += teil
                if len(daten[schluessel.fileobj]) > MAX_AUSGABE:
                    raise NetzFehler("Ausgabe des Geräts ist zu groß")
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        auswahl.close()
        proc.stdout.close()
        proc.stderr.close()
    return daten[proc.stdout].decode("utf-8", "replace"), daten[proc.stderr].decode("utf-8", "replace")


def run_local():
    return ausfuehren(["sh", "-s"], SCRIPT)[0]


def run_ssh(src):
    host, user, port = str(src.get("host", "")), str(src.get("benutzer", "")), int(src.get("port") or 22)
    if not SSH_NAME.fullmatch(host) or not SSH_NAME.fullmatch(user):
        raise NetzFehler("SSH: Host oder Benutzer enthält ungültige Zeichen")
    ziel = pruefe_ziel(host)  # einmal aufgeloest und geprueft; verbunden wird mit dieser Adresse, der Host-Schluessel gehoert zum Namen
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new", "-o", f"HostKeyAlias={host}", "-p", str(port)]
    key = str(src.get("schluessel", "")).strip()
    if key:
        if not os.path.isfile(key):
            raise NetzFehler(f"SSH-Schlüsseldatei nicht gefunden: {key}")
        cmd += ["-i", key]
    stdout, stderr = ausfuehren(cmd + ["--", f"{user}@{ziel}", "sh -s"], SCRIPT)
    if "##end" not in stdout:
        raise NetzFehler("SSH: " + (stderr.strip().splitlines() or ["keine Antwort"])[-1][:160])
    return stdout


def sections(text):
    result, name = {}, None
    for line in text.splitlines():
        if line.startswith("##"):
            name = line[2:]
            result[name] = []
        elif name:
            result[name].append(line)
    return result


def to_bytes(text):
    """'10.9T', '512G' oder '1024' (ZFS-Schreibweise) in Bytes."""
    m = re.match(r"^([\d.]+)([KMGTP]?)", text.strip())
    return int(float(m.group(1)) * UNITS.get(m.group(2), 1)) if m else 0


def memory(lines):
    kb = {m.group(1): int(m.group(2)) for m in (re.match(r"(\w+):\s+(\d+)", z) for z in lines) if m}
    total = kb.get("MemTotal", 0) * 1024
    free = kb["MemAvailable"] if "MemAvailable" in kb else kb.get("MemFree", 0) + kb.get("Buffers", 0) + kb.get("Cached", 0)
    return {"gesamt": total, "belegt": max(0, total - free * 1024)} if total else None


def cpu_percent(stat_line, cores, load1, state):
    """CPU-Auslastung aus dem Unterschied zweier Messungen. Beim ersten Mal: Last pro Kern als Naeherung."""
    felder = [int(x) for x in stat_line.split()[1:] if x.isdigit()]
    total, idle = sum(felder), felder[3] + (felder[4] if len(felder) > 4 else 0)
    alt = state.get("cpu")
    state["cpu"] = (total, idle)
    if alt and total > alt[0]:
        return round(100 * (1 - (idle - alt[1]) / (total - alt[0])), 1)
    return round(min(100, 100 * load1 / max(cores, 1)), 1)


def volumes(df_lines, mount_lines):
    fstypes = {z.split()[1]: z.split()[2] for z in mount_lines if len(z.split()) >= 3}
    seen, result = set(), []
    for z in df_lines[1:]:
        f = z.split()
        if len(f) < 6 or not f[1].isdigit():
            continue
        device, total, used, mount = f[0], int(f[1]) * 1024, int(f[2]) * 1024, " ".join(f[5:])
        if mount.startswith(SKIP_MOUNTS) or fstypes.get(mount, "") not in REAL_FS or device in seen or total <= 0:
            continue
        seen.add(device)
        result.append({"name": mount, "gesamt": total, "belegt": used})
    return result


def temperature(lines):
    werte = []
    for z in lines:
        teile = z.split()
        if len(teile) == 2 and teile[1].lstrip("-").isdigit():
            grad = int(teile[1]) / 1000 if int(teile[1]) > 1000 else int(teile[1])
            if TEMP_MIN <= grad <= TEMP_MAX:
                werte.append(grad)
    return round(max(werte), 1) if werte else None


def raid_md(lines):
    result = []
    for z in lines:
        m = re.match(r"^(md\d+)\s*:\s*(\S+)\s+(\S+)", z)
        if m:
            result.append({"name": f"{m.group(1)} ({m.group(3)})", "status": "ok" if m.group(2) == "active" else "fail", "detail": m.group(2)})
        elif result and re.search(r"\[[U_]+\]", z):
            result[-1]["detail"] = re.search(r"\[[U_]+\]", z).group(0)
            if "_" in result[-1]["detail"]:
                result[-1]["status"] = "warn"
        elif result and MD_PROGRESS.search(z):
            m2 = MD_PROGRESS.search(z)
            result[-1]["detail"] += f" · {m2.group(1)} {m2.group(2)} %"
    return result


def zfs(lines):
    vols, raids = [], []
    for z in lines:
        f = z.split()
        if len(f) == 4:
            vols.append({"name": f"ZFS {f[0]}", "gesamt": to_bytes(f[1]), "belegt": to_bytes(f[2])})
            raids.append({"name": f"ZFS {f[0]}", "status": "ok" if f[3] == "ONLINE" else "warn" if f[3] == "DEGRADED" else "fail", "detail": f[3]})
    return vols, raids


def smart_disks(lines):
    disks, current = [], None
    for z in lines:
        if z.startswith("@@"):
            current = {"name": z[2:].replace("/dev/", ""), "modell": "", "temp": None, "status": "unbekannt", "kapazitaet": 0}
            disks.append(current)
            continue
        if current is None:
            continue
        m = re.match(r"^(Device Model|Model Number|Product):\s+(.+)", z)
        if m and not current["modell"]:
            current["modell"] = m.group(2).strip()
        if HEALTH_PASSED.search(z):
            current["status"] = "ok"
        elif HEALTH_FAILED.search(z):
            current["status"] = "fail"
        m = re.match(r"^(User Capacity|Total NVM Capacity):\s+([\d,.]+)\s+bytes", z)
        if m:
            current["kapazitaet"] = int(re.sub(r"\D", "", m.group(2)))
        m = next((r.match(z) for r in SMART_TEMP if r.match(z)), None)
        if m:
            current["temp"] = int(m.group(1))
    return disks


def traffic(lines, state, now):
    rx = tx = 0
    for z in lines[2:]:
        name, _, rest = z.partition(":")
        felder = rest.split()
        if len(felder) >= 9 and not name.strip().startswith(SKIP_NET):
            rx, tx = rx + int(felder[0]), tx + int(felder[8])
    alt = state.get("net")
    state["net"] = (now, rx, tx)
    if not alt or now <= alt[0] or rx < alt[1] or tx < alt[2]:
        return None
    dt = now - alt[0]
    return {"rx": round((rx - alt[1]) / dt), "tx": round((tx - alt[2]) / dt)}


def parse(text, state, now=None):
    s = sections(text)
    now = now or time.time()
    load = (s.get("loadavg") or ["0 0 0"])[0].split()
    cpuzeilen = s.get("cpustat") or []
    cores = int(cpuzeilen[1]) if len(cpuzeilen) > 1 and cpuzeilen[1].isdigit() else 1
    zfs_volumes, zfs_raids = zfs(s.get("zpool") or [])
    os_zeilen = s.get("os") or []
    result = {
        "modell": re.sub(r'^(PRETTY_NAME|NAME)="?|"$', "", os_zeilen[1]) if len(os_zeilen) > 1 else "",
        "kernel": os_zeilen[0] if os_zeilen else "",
        "uptime": int(float((s.get("uptime") or ["0"])[0].split()[0])), "last": [float(x) for x in load[:3]], "kerne": cores,
        "ram": memory(s.get("meminfo") or []), "temp": temperature(s.get("temp") or []),
        "volumes": volumes(s.get("df") or [], s.get("mounts") or []) + zfs_volumes,
        "platten": smart_disks(s.get("smart") or []), "raid": raid_md(s.get("mdstat") or []) + zfs_raids,
        "traffic": traffic(s.get("netdev") or [], state, now),
    }
    if cpuzeilen:
        result["cpu"] = cpu_percent(cpuzeilen[0], cores, result["last"][0], state)
    return result


def lokal(src, state):
    return parse(run_local(), state)


def ssh(src, state):
    return parse(run_ssh(src), state)
