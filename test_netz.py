import json
import math
import os
import socket
import stat
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import netz
import netz_agent
import netz_dienste
import netz_geraete
import netz_http
import netz_linux
import netz_scan
import netz_snmp as sn
from netz_http import NetzFehler
from netz_typen import GESCHUETZT, abfragen, katalog

PRIVAT = "127.0.0.1"


# --- Hilfen -----------------------------------------------------------------------------
class FakeHttp:
    """Kleiner HTTP-Server: routes = {(Methode, Pfad): (Status, Objekt)}. Gesehene Anfragen stehen in .log."""

    def __init__(self, routes):
        self.routes, self.log = routes, []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def handle_any(self):
                laenge = int(self.headers.get("Content-Length") or 0)
                fake.log.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, self.rfile.read(laenge)))
                status, objekt = fake.routes.get((self.command, self.path.split("?")[0]), (404, {}))
                body = json.dumps(objekt).encode()
                self.send_response(status)
                if 300 <= status < 400:
                    self.send_header("Location", "/ziel")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_DELETE = handle_any

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer((PRIVAT, 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def src(typ, **felder):
    return {"id": "t1", "typ": typ, "name": "Test", "host": PRIVAT, **felder}


def ok(ergebnis):
    assert ergebnis["ok"], ergebnis.get("fehler")
    return ergebnis


class FakeSnmp:
    """UDP-Responder mit fester Tabelle {oid: (tag, bytes)}; beantwortet GET und GETNEXT."""

    def __init__(self, tabelle):
        self.tabelle = tabelle
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((PRIVAT, 0))
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.loop, daemon=True).start()

    @staticmethod
    def key(oid):
        return tuple(int(x) for x in oid.split("."))

    def loop(self):
        while True:
            try:
                daten, addr = self.sock.recvfrom(65535)
            except OSError:
                return
            _, body, _ = sn.read_tlv(daten, 0)
            _, version, pos = sn.read_tlv(body, 0)
            _, community, pos = sn.read_tlv(body, pos)
            tag, pdu, _ = sn.read_tlv(body, pos)
            _, rid, p = sn.read_tlv(pdu, 0)
            _, _, p = sn.read_tlv(pdu, p)
            _, _, p = sn.read_tlv(pdu, p)
            _, binds, _ = sn.read_tlv(pdu, p)
            antworten, pos = b"", 0
            while pos < len(binds):
                _, bind, pos = sn.read_tlv(binds, pos)
                _, oid, _ = sn.read_tlv(bind, 0)
                antworten += self.antwort(tag, sn.decode_oid(oid))
            pdu_antwort = sn.tlv(sn.RESPONSE, sn.tlv(sn.INTEGER, rid) + sn.encode_int(0) + sn.encode_int(0) + sn.tlv(sn.SEQUENCE, antworten))
            self.sock.sendto(sn.tlv(sn.SEQUENCE, sn.tlv(sn.INTEGER, version) + sn.tlv(sn.OCTETS, community) + pdu_antwort), addr)

    def antwort(self, tag, wunsch):
        if tag == sn.GETNEXT:
            folgende = sorted((k for k in self.tabelle if self.key(k) > self.key(wunsch)), key=self.key)
            oid, (vtag, vbody) = (folgende[0], self.tabelle[folgende[0]]) if folgende else (wunsch, (sn.END_OF_MIB, b""))
        else:
            oid, (vtag, vbody) = (wunsch, self.tabelle[wunsch]) if wunsch in self.tabelle else (wunsch, (sn.NO_SUCH_INSTANCE, b""))
        return sn.tlv(sn.SEQUENCE, sn.encode_oid(oid) + sn.tlv(vtag, vbody))

    def close(self):
        self.sock.close()


def zahl(n, tag=sn.INTEGER):
    return tag, n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big", signed=tag == sn.INTEGER)


def text(s):
    return sn.OCTETS, s.encode()


def oid_wert(oid):
    return sn.OID, sn.encode_oid(oid)[2:]


# --- SNMP -------------------------------------------------------------------------------
def test_snmp_oid_roundtrips_including_large_arcs():
    for oid in ("1.3.6.1.2.1.1.1.0", "1.3.6.1.4.1.6574.2.1.1.6.0", "1.3.6.1.4.1.24681.1.2.17.1.4.128"):
        assert sn.decode_oid(sn.encode_oid(oid)[2:]) == oid


def test_snmp_get_walk_and_table_against_fake_agent():
    agent = FakeSnmp({"1.3.6.1.2.1.1.1.0": text("Linux nas"), "1.3.6.1.2.1.25.2.3.1.3.1": text("/volume1"), "1.3.6.1.2.1.25.2.3.1.3.2": text("/")})
    try:
        assert sn.text(sn.get(PRIVAT, "public", ["1.3.6.1.2.1.1.1.0"], agent.port)["1.3.6.1.2.1.1.1.0"]) == "Linux nas"
        assert sn.get(PRIVAT, "public", ["1.3.6.1.2.1.1.9.0"], agent.port) == {}
        zeilen = sn.walk(PRIVAT, "public", "1.3.6.1.2.1.25.2.3.1", agent.port)
        assert [sn.text(v) for _, v in zeilen] == ["/volume1", "/"]
        assert sn.table(zeilen, "1.3.6.1.2.1.25.2.3.1")["1"]["3"] == b"/volume1"
    finally:
        agent.close()


def test_snmp_times_out_with_a_clear_message():
    leer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    leer.bind((PRIVAT, 0))
    try:
        sn.get(PRIVAT, "public", ["1.3.6.1.2.1.1.1.0"], leer.getsockname()[1], timeout=0.2)
        raise AssertionError("kein Fehler")
    except sn.SnmpError as e:
        assert "Zeitüberschreitung" in str(e)
    finally:
        leer.close()


def synology_tabelle(rx):
    gb = 1 << 30
    return {
        "1.3.6.1.2.1.1.1.0": text("Linux DiskStation Synology"), "1.3.6.1.2.1.1.3.0": zahl(360000, sn.TIMETICKS), "1.3.6.1.2.1.1.5.0": text("nas"),
        "1.3.6.1.2.1.25.2.3.1.2.1": oid_wert(netz_geraete.HR_FIXED_DISK), "1.3.6.1.2.1.25.2.3.1.3.1": text("/volume1"), "1.3.6.1.2.1.25.2.3.1.4.1": zahl(4096, sn.GAUGE32),
        "1.3.6.1.2.1.25.2.3.1.5.1": zahl(1000 * gb // 4096, sn.GAUGE32), "1.3.6.1.2.1.25.2.3.1.6.1": zahl(250 * gb // 4096, sn.GAUGE32),
        "1.3.6.1.2.1.25.2.3.1.2.2": oid_wert(netz_geraete.HR_FIXED_DISK), "1.3.6.1.2.1.25.2.3.1.3.2": text("/"), "1.3.6.1.2.1.25.2.3.1.4.2": zahl(4096, sn.GAUGE32),
        "1.3.6.1.2.1.25.2.3.1.5.2": zahl(500, sn.GAUGE32), "1.3.6.1.2.1.25.2.3.1.6.2": zahl(100, sn.GAUGE32),
        "1.3.6.1.2.1.25.3.3.1.2.1": zahl(10, sn.INTEGER), "1.3.6.1.2.1.25.3.3.1.2.2": zahl(30, sn.INTEGER),
        "1.3.6.1.4.1.2021.4.5.0": zahl(8_000_000), "1.3.6.1.4.1.2021.4.6.0": zahl(6_000_000), "1.3.6.1.4.1.6574.1.2.0": zahl(41),
        "1.3.6.1.2.1.2.2.1.2.1": text("lo"), "1.3.6.1.2.1.2.2.1.2.2": text("eth0"), "1.3.6.1.2.1.2.2.1.8.1": zahl(1), "1.3.6.1.2.1.2.2.1.8.2": zahl(1),
        "1.3.6.1.2.1.31.1.1.1.6.2": zahl(rx, sn.COUNTER64), "1.3.6.1.2.1.31.1.1.1.10.2": zahl(rx // 2, sn.COUNTER64), "1.3.6.1.2.1.31.1.1.1.6.1": zahl(999, sn.COUNTER64),
        "1.3.6.1.4.1.6574.2.1.1.2.0": text("Disk 1"), "1.3.6.1.4.1.6574.2.1.1.3.0": text("WD40"), "1.3.6.1.4.1.6574.2.1.1.5.0": zahl(1), "1.3.6.1.4.1.6574.2.1.1.6.0": zahl(36),
        "1.3.6.1.4.1.6574.3.1.1.2.0": text("Volume 1"), "1.3.6.1.4.1.6574.3.1.1.3.0": zahl(1),
    }


def test_snmp_adapter_reads_synology_storage_cpu_ram_disks_and_traffic_rate():
    agent = FakeSnmp(synology_tabelle(1_000_000))
    zustand = {}
    try:
        erst = ok(abfragen(src("snmp", port=agent.port), zustand))
        assert [v["name"] for v in erst["volumes"]] == ["/volume1"]
        assert erst["volumes"][0]["prozent"] == 25.0
        assert erst["cpu"] == 20.0 and erst["temp"] == 41 and erst["uptime"] == 3600
        assert erst["ram"]["gesamt"] == 8_000_000 * 1024 and erst["ram"]["prozent"] == 25.0
        assert erst["platten"][0]["temp"] == 36 and erst["platten"][0]["status"] == "ok" and erst["raid"][0]["status"] == "ok"
        assert erst["traffic"] is None
        agent.tabelle.update({k: v for k, v in synology_tabelle(3_000_000).items() if k.startswith("1.3.6.1.2.1.31.1.1.1")})
        time.sleep(1.1)
        zweit = ok(abfragen(src("snmp", port=agent.port), zustand))
        assert 1_000_000 < zweit["traffic"]["rx"] < 2_100_000
    finally:
        agent.close()


# --- Zielpruefung -----------------------------------------------------------------------
def test_only_home_loopback_and_tailscale_addresses_are_allowed():
    for host in ("192.168.1.5", "10.0.0.1", "172.16.4.4", "100.64.1.2", "127.0.0.1"):
        assert netz_http.ziel_erlaubt(host), host
    for host in ("8.8.8.8", "169.254.169.254", "1.1.1.1"):
        assert not netz_http.ziel_erlaubt(host), host


def test_public_target_is_refused_before_any_request():
    ergebnis = abfragen(src("tcp", host="8.8.8.8", port=53), {})
    assert not ergebnis["ok"] and "Heimnetz" in ergebnis["fehler"]


def test_ssh_refuses_option_like_host_and_missing_key_file():
    assert "ungültige Zeichen" in abfragen(src("ssh", host="-oProxyCommand=x", benutzer="root"), {})["fehler"]
    assert "nicht gefunden" in abfragen(src("ssh", benutzer="root", schluessel="/gibt/es/nicht"), {})["fehler"]


# --- Linux ------------------------------------------------------------------------------
LINUX_AUSGABE = """##os
Linux 6.1.0
PRETTY_NAME="UGOS Pro"
##meminfo
MemTotal:        8000000 kB
MemFree:          500000 kB
MemAvailable:    6000000 kB
##loadavg
0.50 0.40 0.30 1/200 999
##uptime
7200.55 14000.1
##cpustat
cpu  100 0 100 800 0 0 0 0 0 0
4
##mounts
/dev/md0 /volume1 ext4 rw 0 0
/dev/md0 /volume1/docker btrfs rw 0 0
tmpfs /run tmpfs rw 0 0
/dev/sdc1 /boot ext4 rw 0 0
##df
Filesystem     1024-blocks      Used Available Capacity Mounted on
/dev/md0        3906250000 976562500 2929687500      25% /volume1
tmpfs                 1000         0      1000       0% /run
/dev/sdc1           500000    100000    400000      20% /boot
##temp
/sys/class/thermal/thermal_zone0/temp 45000
/sys/class/hwmon/hwmon1/temp1_input 52000
##mdstat
Personalities : [raid1]
md0 : active raid1 sda1[0] sdb1[1]
      976630336 blocks super 1.2 [2/1] [U_]
      [==>..................]  recovery = 12.5% (122/976) finish=10min
##netdev
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 5000 10 0 0 0 0 0 0 5000 10 0 0 0 0 0 0
  eth0: 1000 10 0 0 0 0 0 0 2000 10 0 0 0 0 0 0
##zpool
tank\t10.9T\t5.4T\tDEGRADED
##smart
@@/dev/sda
Device Model:     WDC WD40EFRX
User Capacity:    4,000,787,030,016 bytes [4.00 TB]
SMART overall-health self-assessment test result: PASSED
194 Temperature_Celsius     0x0022   115   100   000    Old_age   Always       -       35 (Min/Max 20/45)
@@/dev/nvme0n1
Model Number:                       Samsung 980
SMART overall-health self-assessment test result: FAILED!
Temperature:                        41 Celsius
##end
"""


def test_linux_parser_reads_volumes_ram_temperature_raid_zfs_and_smart():
    r = netz_linux.parse(LINUX_AUSGABE, {}, now=1000)
    assert r["modell"] == "UGOS Pro" and r["uptime"] == 7200 and r["kerne"] == 4
    assert [v["name"] for v in r["volumes"]] == ["/volume1", "ZFS tank"]
    assert r["volumes"][0]["gesamt"] == 3906250000 * 1024 and r["volumes"][1]["gesamt"] == int(10.9 * (1 << 40))
    assert r["ram"] == {"gesamt": 8000000 * 1024, "belegt": 2000000 * 1024}
    assert r["temp"] == 52.0
    md = r["raid"][0]
    assert md["status"] == "warn" and "[U_]" in md["detail"] and "12.5 %" in md["detail"]
    assert r["raid"][1] == {"name": "ZFS tank", "status": "warn", "detail": "DEGRADED"}
    sda, nvme = r["platten"]
    assert (sda["modell"], sda["status"], sda["temp"], sda["kapazitaet"]) == ("WDC WD40EFRX", "ok", 35, 4000787030016)
    assert (nvme["status"], nvme["temp"]) == ("fail", 41)


def test_linux_cpu_and_traffic_use_the_difference_between_two_samples():
    zustand = {}
    erst = netz_linux.parse(LINUX_AUSGABE, zustand, now=1000)
    assert erst["traffic"] is None and erst["cpu"] == 12.5
    zweit = LINUX_AUSGABE.replace("cpu  100 0 100 800", "cpu  200 0 200 900").replace("eth0: 1000 10 0 0 0 0 0 0 2000", "eth0: 6000 10 0 0 0 0 0 0 4000")
    r = netz_linux.parse(zweit, zustand, now=1010)
    assert r["traffic"] == {"rx": 500, "tx": 200}
    assert r["cpu"] == 66.7


def test_local_collector_runs_on_this_machine():
    r = ok(abfragen({"id": "l", "typ": "lokal", "name": "hier"}, {}))
    assert r["ram"]["gesamt"] > 0 and r["volumes"] and r["uptime"] > 0


# --- Adapter gegen Fake-Server ----------------------------------------------------------
def test_pihole_v6_logs_in_reads_summary_and_logs_out():
    fake = FakeHttp({("POST", "/api/auth"): (200, {"session": {"sid": "abc", "valid": True}}), ("GET", "/api/stats/summary"): (200, {"queries": {"total": 100, "blocked": 25, "percent_blocked": 25.0},
                     "gravity": {"domains_being_blocked": 1000}, "clients": {"active": 4}}), ("GET", "/api/dns/blocking"): (200, {"blocking": "enabled"}), ("DELETE", "/api/auth"): (200, {})})
    try:
        r = ok(abfragen(src("pihole", port=fake.port, passwort="pw"), {}))
        assert r["dns"] == {"anfragen": 100, "blockiert": 25, "prozent": 25.0, "domains": 1000, "aktiv": True, "clients": 4}
        assert any(m == "GET" and h.get("sid") == "abc" for m, _, h, _ in fake.log) and fake.log[-1][0] == "DELETE"
    finally:
        fake.close()


def test_pihole_wrong_password_is_reported():
    fake = FakeHttp({("POST", "/api/auth"): (401, {})})
    try:
        assert "Passwort falsch" in abfragen(src("pihole", port=fake.port), {})["fehler"]
    finally:
        fake.close()


def test_adguard_uses_basic_auth_and_computes_block_rate():
    fake = FakeHttp({("GET", "/control/status"): (200, {"version": "v0.107", "protection_enabled": True}), ("GET", "/control/stats"): (200, {"num_dns_queries": 200, "num_blocked_filtering": 50})})
    try:
        r = ok(abfragen(src("adguard", port=fake.port, benutzer="u", passwort="p"), {}))
        assert r["dns"]["prozent"] == 25.0 and r["dns"]["aktiv"] is True
        assert fake.log[0][2]["authorization"].startswith("Basic ")
    finally:
        fake.close()


def test_truenas_reads_pools_and_open_alerts_with_bearer_token():
    fake = FakeHttp({("GET", "/api/v2.0/system/info"): (200, {"hostname": "nas", "version": "TrueNAS-13", "uptime_seconds": 1000, "loadavg": [0.1, 0.2, 0.3], "cores": 4}),
                     ("GET", "/api/v2.0/pool"): (200, [{"name": "tank", "status": "ONLINE", "healthy": True, "size": 1000, "allocated": 400}]),
                     ("GET", "/api/v2.0/alert/list"): (200, [{"level": "WARNING", "formatted": "Platte sda ist heiß", "dismissed": False}, {"level": "INFO", "formatted": "egal"}])})
    try:
        r = ok(abfragen(src("truenas", port=fake.port, api_key="KEY"), {}))
        assert r["volumes"] == [{"name": "tank", "gesamt": 1000, "belegt": 400, "prozent": 40.0}]
        assert r["raid"][0]["status"] == "ok" and r["hinweise"] == ["Platte sda ist heiß"]
        assert fake.log[0][2]["authorization"] == "Bearer KEY"
    finally:
        fake.close()


def test_mikrotik_reads_resources_dhcp_clients_and_wan_rate():
    fake = FakeHttp({("GET", "/rest/system/resource"): (200, {"uptime": "1d2h3m4s", "cpu-load": "7", "free-memory": "300", "total-memory": "1000", "version": "7.15", "board-name": "hAP"}),
                     ("GET", "/rest/interface"): (200, [{"name": "ether1", "running": "true", "rx-byte": "1000", "tx-byte": "500"}]),
                     ("GET", "/rest/ip/dhcp-server/lease"): (200, [{"address": "192.168.88.10", "mac-address": "AA:BB:CC:00:00:01", "host-name": "laptop", "status": "bound"}, {"address": "192.168.88.11", "status": "waiting"}])})
    try:
        r = ok(abfragen(src("mikrotik", port=fake.port, benutzer="a", passwort="b"), {}))
        assert r["uptime"] == 86400 + 7200 + 180 + 4 and r["cpu"] == 7.0 and r["ram"]["belegt"] == 700
        assert [c["name"] for c in r["clients"]] == ["laptop"] and r["wan"]["verbunden"] is True
    finally:
        fake.close()


def test_immich_reports_storage_counts_and_queue():
    fake = FakeHttp({("GET", "/api/server/statistics"): (200, {"photos": 10, "videos": 2, "usage": 5_000_000_000}), ("GET", "/api/server/storage"): (200, {"diskSizeRaw": 100, "diskUseRaw": 40}),
                     ("GET", "/api/server/version"): (200, {"major": 1, "minor": 2, "patch": 3}), ("GET", "/api/jobs"): (200, {"thumbnailGeneration": {"jobCounts": {"active": 1, "waiting": 2}}})})
    try:
        r = ok(abfragen(src("immich", port=fake.port, api_key="K"), {}))
        assert r["version"] == "1.2.3" and r["volumes"][0]["prozent"] == 40.0 and ["Warteschlange", "3"] in r["werte"]
        assert fake.log[0][2]["x-api-key"] == "K"
    finally:
        fake.close()


def test_shelly_gen2_and_gen1_power():
    gen2 = FakeHttp({("GET", "/rpc/Shelly.GetStatus"): (200, {"switch:0": {"output": True, "apower": 12.5, "aenergy": {"total": 100.0}}, "sys": {"uptime": 50}})})
    gen1 = FakeHttp({("GET", "/status"): (200, {"relays": [{"ison": True}], "meters": [{"power": 8.0, "total": 600}], "uptime": 9})})
    try:
        assert ok(abfragen(src("shelly", port=gen2.port), {}))["strom"] == {"leistung_w": 12.5, "ein": True, "energie_wh": 100.0}
        assert ok(abfragen(src("shelly", port=gen1.port), {}))["strom"] == {"leistung_w": 8.0, "ein": True, "energie_wh": 10.0}
    finally:
        gen2.close()
        gen1.close()


def test_tasmota_reads_energy_block():
    fake = FakeHttp({("GET", "/cm"): (200, {"Status": {"FriendlyName": ["Waschmaschine"]}, "StatusSNS": {"ENERGY": {"Power": 20, "Total": 1.5}}, "StatusSTS": {"POWER": "ON"}})})
    try:
        r = ok(abfragen(src("tasmota", port=fake.port), {}))
        assert r["modell"] == "Waschmaschine" and r["strom"] == {"leistung_w": 20, "ein": True, "energie_wh": 1500.0}
    finally:
        fake.close()


def test_http_check_reports_status_and_missing_text():
    fake = FakeHttp({("GET", "/health"): (200, {"status": "ok"})})
    try:
        assert ok(abfragen({"id": "h", "typ": "http", "name": "Dienst", "url": f"http://{PRIVAT}:{fake.port}/health", "enthaelt": "ok"}, {}))["dienste"][0]["ok"]
        r = abfragen({"id": "h", "typ": "http", "name": "Dienst", "url": f"http://{PRIVAT}:{fake.port}/health", "enthaelt": "kaputt"}, {})
        assert not r["ok"] and r["fehler"] == "erwarteter Text fehlt"
        assert not abfragen({"id": "h", "typ": "http", "name": "D", "url": f"http://{PRIVAT}:{fake.port}/nichts"}, {})["ok"]
    finally:
        fake.close()


def test_nut_reads_ups_state_and_flags_battery_operation():
    antworten = {"ups": 'BEGIN LIST VAR ups\nVAR ups battery.charge "88"\nVAR ups battery.runtime "1200"\nVAR ups ups.status "OB DISCHRG"\nVAR ups ups.model "Eaton"\nEND LIST VAR ups\n'}
    server = socket.socket()
    server.bind((PRIVAT, 0))
    server.listen(1)

    def bedienen():
        verbindung, _ = server.accept()
        anfrage = verbindung.recv(100).decode().split()
        verbindung.sendall(antworten[anfrage[2]].encode())
        verbindung.close()

    threading.Thread(target=bedienen, daemon=True).start()
    try:
        r = abfragen(src("nut", port=server.getsockname()[1], ups="ups"), {})
        assert r["usv"]["ladung"] == 88.0 and r["usv"]["laufzeit"] == 1200 and r["usv"]["netz"] is False and r["ok"] is False
    finally:
        server.close()


def test_backup_check_flags_old_files(tmp=None):
    ordner = tempfile.mkdtemp()
    datei = os.path.join(ordner, "a.tar.gz")
    open(datei, "w").close()
    assert ok(abfragen({"id": "b", "typ": "backup", "name": "B", "pfad": f"{ordner}/*.tar.gz", "max_alter": 48}, {}))["backup"]["ok"]
    os.utime(datei, (time.time() - 100 * 3600,) * 2)
    r = abfragen({"id": "b", "typ": "backup", "name": "B", "pfad": f"{ordner}/*.tar.gz", "max_alter": 48}, {})
    assert not r["ok"] and "vor 100" in r["fehler"]
    assert "absolut" in abfragen({"id": "b", "typ": "backup", "name": "B", "pfad": "relativ/*"}, {})["fehler"]


def test_every_type_has_a_form_with_unique_field_keys_and_a_hint():
    typen = katalog()
    assert len(typen) >= 24
    for name, t in typen.items():
        schluessel = [f["key"] for f in t["felder"]]
        assert len(schluessel) == len(set(schluessel)), name
        assert t["hinweis"] and t["gruppe"], name


# --- Schema-Zuschnitt -------------------------------------------------------------------
def test_normalize_drops_bad_values_and_keeps_required_fields():
    q = netz.normalisieren({"id": "a", "typ": "snmp", "ok": True, "cpu": float("nan"), "temp": True, "volumes": [{"name": "/v", "gesamt": 100, "belegt": 50}, {"name": "x"}, "kaputt"],
                            "wan": "text", "extra": "unbekannt", "last": [1, "x", 2]})
    assert "cpu" not in q and "temp" not in q and "wan" not in q and "extra" not in q
    assert q["volumes"] == [{"name": "/v", "gesamt": 100, "belegt": 50, "prozent": 50.0}] and q["last"] == [1, 2]
    assert netz.normalisieren({"typ": "x"}) is None and not math.isnan(q.get("cpu", 0))


# --- Netz: Konfiguration, Push, Alarme --------------------------------------------------
def make_netz():
    ordner = tempfile.mkdtemp()
    meldungen = []
    auth = types.SimpleNamespace(is_elevated=lambda token: token == "stufe")
    return netz.Netz(ordner, auth, meldungen.append, "deck.example"), meldungen


def rejected(n, data):
    try:
        n.speichern(data)
    except ValueError as e:
        return str(e)
    raise AssertionError("nicht abgelehnt")


def quelle(**felder):
    return {"id": "nas", "typ": "snmp", "name": "NAS", "host": "192.168.1.20", **felder}


def test_config_rejects_public_networks_big_networks_unknown_types_and_duplicates():
    n, _ = make_netz()
    assert "private" in rejected(n, {"netze": ["8.8.8.0/24"]})
    assert "1024" in rejected(n, {"netze": ["10.0.0.0/8"]})
    assert "Betriebsart" in rejected(n, {"modus": "cloud"})
    assert "unbekannter Typ" in rejected(n, {"quellen": [quelle(typ="rootshell")]})
    assert "doppelt" in rejected(n, {"quellen": [quelle(), quelle()]})
    assert "Adresse fehlt" in rejected(n, {"quellen": [quelle(host="")]})
    assert "ungültige Geräteliste" in rejected(n, {"geraete": {"../x": {}}})


def test_config_masks_secrets_keeps_them_when_masked_value_comes_back_and_uses_private_file_rights():
    n, _ = make_netz()
    n.speichern({"modus": "tailscale", "netze": ["192.168.1.0/24"], "quellen": [quelle(community="geheim")]})
    ansicht = n.config_ansicht()
    assert ansicht["quellen"][0]["community"] == GESCHUETZT and "agent_hash" not in ansicht and ansicht["modus"] == "tailscale"
    n.speichern({"quellen": ansicht["quellen"]})
    assert n.config()["quellen"][0]["community"] == "geheim"
    assert stat.S_IMODE(os.stat(n.cfg_pfad).st_mode) == 0o600


def test_changing_sources_needs_the_step_up_but_labeling_a_device_does_not():
    n, _ = make_netz()
    for pfad in ("/api/netz/config", "/api/netz/token", "/api/netz/test", "/api/netz/entdecken"):
        try:
            n.post(pfad, {}, "1.1.1.1", "normal")
            raise AssertionError(pfad)
        except PermissionError:
            pass
    assert n.post("/api/netz/geraet", {"key": "AA:BB:CC:00:00:01", "name": "Drucker"}, "1.1.1.1", "normal")[0] == 200
    assert n.config()["geraete"]["AA:BB:CC:00:00:01"]["name"] == "Drucker"
    assert n.post("/api/ding", {}, "1.1.1.1") is None


def test_agent_push_needs_the_token_locks_after_failures_and_hands_out_commands():
    n, _ = make_netz()
    token = n.token_neu()["token"]
    nutzlast = json.dumps({"name": "heimnetz", "quellen": [{"id": "x", "typ": "snmp", "name": "NAS", "ok": True, "volumes": [{"name": "/v", "gesamt": 100, "belegt": 10}]}]}).encode()
    assert n.push("2.2.2.2", token, nutzlast) == {"ok": True, "befehle": []}
    assert n.uebersicht()["quellen"][0]["herkunft"] == "agent:heimnetz" and n.uebersicht()["agent"]["aktiv"]
    n.scan_anfordern()
    n.wol_senden("aa:bb:cc:00:00:01")
    assert n.push("2.2.2.2", token, nutzlast)["befehle"] == [{"typ": "scan"}, {"typ": "wol", "mac": "AA:BB:CC:00:00:01"}]
    for _ in range(netz.SPERRE_FEHLER):
        try:
            n.push("3.3.3.3", "falsch", nutzlast)
        except PermissionError:
            pass
    try:
        n.push("3.3.3.3", token, nutzlast)
        raise AssertionError("nicht gesperrt")
    except PermissionError as e:
        assert str(e) == "gesperrt"
    assert n.push("2.2.2.2", token, nutzlast)["ok"]


def test_agent_push_survives_hostile_data_and_rejects_garbage():
    n, _ = make_netz()
    token = n.token_neu()["token"]
    boese = {"name": "heimnetz", "quellen": [{"id": "x", "typ": "snmp", "ok": True, "volumes": "kaputt", "wan": "x", "platten": [1, 2], "ram": {"gesamt": "viel"}}, "nur-text", {"id": ["liste"]}],
             "geraete": [{"ip": 5}, {"ip": "192.168.1.9", "mac": "AA:BB:CC:00:00:09", "ports": "x"}]}
    n.push("2.2.2.2", token, json.dumps(boese).encode())
    ue = n.uebersicht()
    assert [q["id"] for q in ue["quellen"]] == ["x"] and [g["ip"] for g in ue["geraete"]] == ["192.168.1.9"]
    for roh in (b"kein json", b"[1]", json.dumps({"name": "../x"}).encode()):
        try:
            n.push("2.2.2.2", token, roh)
            raise AssertionError(roh)
        except ValueError:
            pass


def test_full_volume_alarm_fires_once_and_reports_recovery():
    n, meldungen = make_netz()
    q = lambda belegt: {"id": "nas", "typ": "snmp", "name": "NAS", "ok": True, "volumes": [{"name": "/volume1", "gesamt": 100, "belegt": belegt}]}
    n.einlesen("server", {"quellen": [q(95)]})
    n.einlesen("server", {"quellen": [q(96)]})
    assert meldungen == ["Netzwerk: NAS: /volume1 zu 95.0 % voll"]
    n.einlesen("server", {"quellen": [q(10)]})
    assert meldungen[-1] == "Netzwerk: wieder in Ordnung – NAS: /volume1 zu 96.0 % voll"
    assert n.uebersicht()["alarme"] == []


def test_source_failure_alarms_only_after_repeated_attempts():
    n, meldungen = make_netz()
    kaputt = {"id": "nas", "typ": "snmp", "name": "NAS", "ok": False, "fehler": "keine Antwort"}
    for _ in range(netz.STANDARD_ALARME["ausfall_versuche"] - 1):
        n.einlesen("server", {"quellen": [kaputt]})
    assert meldungen == []
    n.einlesen("server", {"quellen": [kaputt]})
    assert meldungen == ["Netzwerk: NAS antwortet nicht: keine Antwort"]


def test_first_scan_is_trusted_and_a_later_new_device_raises_one_alarm():
    n, meldungen = make_netz()
    alt = {"ip": "192.168.1.5", "mac": "AA:BB:CC:00:00:01", "name": "pc", "hersteller": "", "ports": [], "typ": "", "typname": ""}
    neu = {"ip": "192.168.1.77", "mac": "AA:BB:CC:00:00:77", "name": "", "hersteller": "Ubiquiti", "ports": [], "typ": "", "typname": ""}
    n.einlesen("server", {"geraete": [alt], "ts": time.time()})
    assert meldungen == [] and n.uebersicht()["zusammenfassung"]["neue"] == 0
    n.einlesen("server", {"geraete": [alt, neu], "ts": time.time()})
    n.einlesen("server", {"geraete": [alt, neu], "ts": time.time()})
    assert meldungen == ["Netzwerk: Neues Gerät im Netz: 192.168.1.77 (Ubiquiti)"]
    n.geraet_setzen({"key": "AA:BB:CC:00:00:77", "bekannt": True, "name": "Gast-Handy"})
    n.einlesen("server", {"geraete": [alt, neu], "ts": time.time()})
    ue = n.uebersicht()
    assert ue["alarme"] == [] and next(g for g in ue["geraete"] if g["ip"] == "192.168.1.77")["name"] == "Gast-Handy"


def test_important_device_going_offline_is_reported():
    n, meldungen = make_netz()
    g = {"ip": "192.168.1.5", "mac": "AA:BB:CC:00:00:01", "name": "nas", "hersteller": "", "ports": [], "typ": "", "typname": ""}
    n.einlesen("server", {"geraete": [g], "ts": time.time()})
    n.geraet_setzen({"key": "AA:BB:CC:00:00:01", "wichtig": True})
    n.stand["gesehen"]["AA:BB:CC:00:00:01"] = time.time() - 3600
    n.einlesen("server", {"geraete": [], "ts": time.time()})
    assert meldungen == ["Netzwerk: nas ist offline"]


def test_router_client_names_win_over_dns_names_and_ip_only_devices_get_their_mac():
    n, _ = make_netz()
    router = {"id": "r", "typ": "fritzbox", "name": "Fritz", "ok": True, "clients": [{"mac": "AA:BB:CC:00:00:05", "ip": "192.168.1.5", "name": "Wohnzimmer-TV", "verbindung": "wlan", "signal": -50}]}
    scan = {"ip": "192.168.1.5", "mac": "", "name": "unknown.lan", "hersteller": "", "ports": [8008], "typ": "", "typname": ""}
    n.einlesen("server", {"quellen": [router]})
    n.einlesen("server", {"geraete": [scan], "ts": time.time()})
    geraete = n.uebersicht()["geraete"]
    assert len(geraete) == 1 and geraete[0]["name"] == "Wohnzimmer-TV" and geraete[0]["mac"] == "AA:BB:CC:00:00:05" and geraete[0]["verbindung"] == "wlan" and geraete[0]["ports"] == [8008]


def test_storage_forecast_projects_days_until_full_from_growth():
    zeilen = [{"t": 1_000_000 + h * 3600, "q": {"server/nas": {"vol": {"/v": [100e9 + h * 10e9 / 24, 1000e9]}}}} for h in range(25)]
    prognose = netz.Netz.prognose_berechnen(zeilen)
    assert abs(prognose["server/nas|/v"] - 89) < 1
    assert netz.Netz.prognose_berechnen(zeilen[:5]) == {}
    flach = [{"t": z["t"], "q": {"server/nas": {"vol": {"/v": [100e9, 1000e9]}}}} for z in zeilen]
    assert netz.Netz.prognose_berechnen(flach) == {"server/nas|/v": None}


def test_history_is_written_at_most_every_five_minutes_and_trimmed():
    n, _ = make_netz()
    q = {"id": "nas", "typ": "snmp", "name": "NAS", "ok": True, "cpu": 5, "volumes": [{"name": "/v", "gesamt": 100, "belegt": 10}]}
    n.einlesen("server", {"quellen": [q]})
    n.einlesen("server", {"quellen": [q]})
    zeilen = n.verlauf_lesen(1)
    assert len(zeilen) == 1 and zeilen[0]["q"]["server/nas"]["vol"] == {"/v": [10, 100]}
    with open(n.verlauf_pfad, "w") as f:
        f.write("".join(json.dumps({"t": int(time.time()), "g": 0, "q": {}}) + "\n" for _ in range(netz.VERLAUF_MAX_ZEILEN + 5)))
    n.verlauf_kuerzen()
    assert len(open(n.verlauf_pfad).readlines()) == netz.VERLAUF_BEHALTEN


def test_wol_uses_the_agent_when_active_and_refuses_in_tailscale_mode_without_one():
    n, _ = make_netz()
    n.speichern({"modus": "tailscale"})
    try:
        n.wol_senden("AA:BB:CC:00:00:01")
        raise AssertionError("kein Fehler")
    except ValueError as e:
        assert "Agenten" in str(e)
    try:
        n.wol_senden("kaputt")
        raise AssertionError("kein Fehler")
    except ValueError:
        pass


def test_magic_packet_has_six_ff_and_sixteen_macs():
    empfaenger = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    empfaenger.bind((PRIVAT, 0))
    empfaenger.settimeout(2)
    alt = netz_scan.WOL_PORT
    netz_scan.WOL_PORT = empfaenger.getsockname()[1]
    try:
        netz_scan.wol("aa:bb:cc:00:00:01", PRIVAT)
        paket = empfaenger.recv(200)
    finally:
        netz_scan.WOL_PORT = alt
        empfaenger.close()
    assert paket == b"\xff" * 6 + bytes.fromhex("AABBCC000001") * 16


def test_sweep_finds_loopback_and_vendor_lookup_flags_random_macs():
    gefunden = netz_scan.sweep("127.0.0.0/30")
    assert "127.0.0.1" in [g["ip"] for g in gefunden]
    assert netz_scan.hersteller("00:11:32:AA:BB:CC") == "Synology" and "Zufällige" in netz_scan.hersteller("DA:A1:19:00:00:01")
    try:
        netz_scan.sweep("10.0.0.0/8")
        raise AssertionError("zu groß")
    except NetzFehler:
        pass


# --- Agent und Server -------------------------------------------------------------------
def test_agent_runs_wake_commands_and_reports_scan_requests():
    gesendet = []
    alt = netz_agent.wol
    netz_agent.wol = lambda mac, broadcast: gesendet.append(mac)
    try:
        assert netz_agent.befehle_ausfuehren([{"typ": "wol", "mac": "AA:BB:CC:00:00:01"}, {"typ": "scan"}]) is True
    finally:
        netz_agent.wol = alt
    assert gesendet == ["AA:BB:CC:00:00:01"]


def test_agent_package_contains_every_module_it_needs():
    n, _ = make_netz()
    n.ordner = os.path.dirname(os.path.abspath(netz.__file__))
    import io
    import zipfile
    namen = set(zipfile.ZipFile(io.BytesIO(n.agent_zip())).namelist())
    assert namen == set(netz.AGENT_DATEIEN)


def test_server_exposes_the_network_routes():
    import server
    assert "/api/netz" in server.ROUTES and "/api/netz/config" in server.ROUTES and "/api/netz/verlauf" in server.QUERY_ROUTES and "/netz.js" in server.SCRIPTS


# --- Haertung nach der Sicherheitspruefung -----------------------------------------------
def test_mapped_reserved_and_special_addresses_are_refused():
    for host in ("::ffff:169.254.169.254", "0.0.0.0", "192.0.2.1", "198.18.0.1", "240.0.0.1", "2002::1", "224.0.0.1", "fe80::1"):
        assert not netz_http.ziel_erlaubt(host), host
    for host in ("::1", "::ffff:192.168.1.5", "fd00::1"):
        assert netz_http.ziel_erlaubt(host), host
    assert netz_http.pruefe_ziel("::ffff:192.168.1.5") == "192.168.1.5"


def test_only_http_and_https_urls_are_requested():
    for url in ("file://localhost/etc/hostname", "ftp://127.0.0.1/x", "data:text/plain,x"):
        try:
            netz_http.anfrage(url)
            raise AssertionError(url)
        except NetzFehler as e:
            assert "nur http und https" in str(e)


def test_redirects_are_not_followed():
    fake = FakeHttp({("GET", "/start"): (302, {}), ("GET", "/ziel"): (200, {"geheim": 1})})
    try:
        status, _ = netz_http.anfrage(f"http://{PRIVAT}:{fake.port}/start", kopf={"X-Api-Key": "k"})
        assert status == 302 and [p for _, p, _, _ in fake.log] == ["/start"]
    finally:
        fake.close()


def test_connection_goes_to_the_checked_address_not_to_a_second_lookup():
    aufgeloest = []
    alt = netz_http.adressen
    netz_http.adressen = lambda host: aufgeloest.append(host) or [__import__("ipaddress").ip_address(PRIVAT)]
    fake = FakeHttp({("GET", "/x"): (200, {})})
    try:
        assert netz_http.anfrage(f"http://rebinding.example:{fake.port}/x")[0] == 200
    finally:
        netz_http.adressen = alt
        fake.close()
    assert aufgeloest and set(aufgeloest) == {"rebinding.example"}


def test_huge_numbers_from_a_device_do_not_break_the_round():
    q = netz.normalisieren({"id": "a", "typ": "snmp", "ok": True, "cpu": 10 ** 400, "temp": 5, "volumes": [{"name": "/v", "gesamt": 10 ** 400, "belegt": 1}]})
    assert "cpu" not in q and q["temp"] == 5 and q["volumes"] == []
    assert abfragen({"id": "x", "typ": "snmp", "name": "X", "host": PRIVAT, "port": 10 ** 9}, {})["ok"] is False


def test_snmp_text_values_are_not_multiplied_into_memory():
    assert netz_geraete.ganz(b"x" * 10) == 0 and netz_geraete.ganz(True) == 0 and netz_geraete.ganz(7) == 7
    volumes, ram = netz_geraete.snmp_storage([("1.3.6.1.2.1.25.2.3.1.2.1", netz_geraete.HR_FIXED_DISK), ("1.3.6.1.2.1.25.2.3.1.3.1", b"/v"), ("1.3.6.1.2.1.25.2.3.1.4.1", b"AAAAAAAAAA"),
                                              ("1.3.6.1.2.1.25.2.3.1.5.1", 100), ("1.3.6.1.2.1.25.2.3.1.6.1", 10)])
    assert volumes == [] and ram is None


def test_ports_must_be_valid_and_masked_secrets_do_not_follow_a_new_address():
    n, _ = make_netz()
    assert "Port" in rejected(n, {"quellen": [quelle(port=70000)]})
    n.speichern({"quellen": [quelle(community="geheim")]})
    ansicht = n.config_ansicht()["quellen"][0]
    ansicht["host"] = "192.168.1.99"
    assert "neu eingeben" in rejected(n, {"quellen": [ansicht]})
    ansicht["typ"], ansicht["host"] = "snmp", "192.168.1.20"
    n.speichern({"quellen": [ansicht]})
    assert n.config()["quellen"][0]["community"] == "geheim"


def test_networks_and_broadcast_must_be_private():
    n, _ = make_netz()
    for bad in ("169.254.0.0/24", "127.0.0.0/22", "8.8.8.0/24"):
        assert "private" in rejected(n, {"netze": [bad]})
    assert "private IPv4" in rejected(n, {"wol_broadcast": "8.8.8.8"})
    assert "private IPv4" in rejected(n, {"wol_broadcast": "::1"})
    n.speichern({"netze": ["192.168.188.0/24"], "wol_broadcast": "192.168.188.255"})


def test_ids_with_trailing_newline_are_rejected():
    n, _ = make_netz()
    assert "Kennung" in rejected(n, {"quellen": [quelle(id="nas\n")]})
    try:
        n.geraet_setzen({"key": "AA:BB:CC:00:00:01\n"})
        raise AssertionError("Schluessel mit Zeilenumbruch angenommen")
    except ValueError as e:
        assert "ungültiges Gerät" in str(e)


def test_agent_must_use_the_fixed_name_and_quellen_must_be_a_list():
    n, _ = make_netz()
    token = n.token_neu()["token"]
    for roh in (json.dumps({"name": "anderer", "quellen": []}), json.dumps({"name": "heimnetz", "quellen": {"a": 1}})):
        try:
            n.push("2.2.2.2", token, roh.encode())
            raise AssertionError(roh)
        except ValueError:
            pass


def test_state_is_pruned_old_devices_and_stale_failure_counters_disappear():
    n, _ = make_netz()
    n.stand["gesehen"]["AA:BB:CC:00:00:99"] = time.time() - 40 * 86400
    n.ausfaelle[("server", "weg")] = 5
    n.einlesen("server", {"quellen": [{"id": "da", "typ": "snmp", "name": "Da", "ok": True}]})
    assert "AA:BB:CC:00:00:99" not in n.stand["gesehen"] and ("server", "weg") not in n.ausfaelle


def test_pushed_ips_must_be_ipv4():
    n, _ = make_netz()
    n.einlesen("server", {"geraete": [{"ip": "192.168.1.5<img>", "mac": "", "name": "x"}, {"ip": "192.168.1.6", "mac": "", "name": "ok"}], "ts": time.time()})
    assert [g["ip"] for g in n.uebersicht()["geraete"]] == ["192.168.1.6"]


def test_backup_pattern_is_limited_to_one_directory():
    for muster in ("/tmp/**/x", "/tmp/../etc/*", "/tm?/x", "relativ/*"):
        assert "Pfad" in abfragen({"id": "b", "typ": "backup", "name": "B", "pfad": muster}, {})["fehler"], muster


def test_runaway_ssh_output_and_hanging_commands_are_cut_off():
    try:
        netz_linux.ausfuehren(["sh", "-c", "yes | head -c 5000000"], "")
        raise AssertionError("zu viel Ausgabe")
    except NetzFehler as e:
        assert "zu groß" in str(e)
    alt, netz_linux.TIMEOUT = netz_linux.TIMEOUT, 0.4
    try:
        netz_linux.ausfuehren(["sleep", "5"], "")
        raise AssertionError("kein Timeout")
    except __import__("subprocess").TimeoutExpired:
        pass
    finally:
        netz_linux.TIMEOUT = alt
    assert netz_linux.ausfuehren(["sh", "-c", "cat; echo fehler >&2"], "hallo") == ("hallo", "fehler\n")


class FakeFritz:
    """Fritz!Box-Attrappe: alle SOAP-Aufrufe verlangen Digest-Anmeldung (Benutzer u, Passwort p), wie neuere FRITZ!OS-Versionen."""

    def __init__(self, kaputt=()):
        import hashlib
        import re
        fake = self
        self.log, self.kaputt = [], kaputt

        antworten = {"GetStatusInfo": "<NewConnectionStatus>Connected</NewConnectionStatus>", "GetExternalIPAddress": "<NewExternalIPAddress>84.1.2.3</NewExternalIPAddress>",
                     "GetAddonInfos": "<NewByteSendRate>1000</NewByteSendRate><NewByteReceiveRate>5000</NewByteReceiveRate>",
                     "GetCommonLinkProperties": "<NewLayer1DownstreamMaxBitRate>250000000</NewLayer1DownstreamMaxBitRate><NewLayer1UpstreamMaxBitRate>40000000</NewLayer1UpstreamMaxBitRate>",
                     "GetInfo": "<NewModelName>FRITZ!Box 7590</NewModelName><NewSoftwareVersion>7.57</NewSoftwareVersion><NewUpTime>3600</NewUpTime>",
                     "GetHostNumberOfEntries": "<NewHostNumberOfEntries>0</NewHostNumberOfEntries>"}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                aktion = self.headers["SOAPACTION"].strip('"').split("#")[1]
                kopf = self.headers.get("Authorization", "")
                fake.log.append((aktion, bool(kopf), self.path))
                if self.path in fake.kaputt:
                    fehler = b"<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\"><s:Body><s:Fault><detail><UPnPError><errorCode>401</errorCode><errorDescription>Invalid Action</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>"
                    self.send_response(500)
                    self.send_header("Content-Length", str(len(fehler)))
                    self.end_headers()
                    self.wfile.write(fehler)
                    return
                if not self.digest_ok(kopf):
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Digest realm="F!Box", nonce="abc123", qop="auth", algorithm=MD5')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:{aktion}Response xmlns:u="urn:dslforum-org:service:x:1">{antworten[aktion]}</u:{aktion}Response></s:Body></s:Envelope>'.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def digest_ok(self, kopf):
                if not kopf.startswith("Digest "):
                    return False
                w = dict(re.findall(r'(\w+)="?([^",]+)"?', kopf[7:]))
                md5 = lambda t: hashlib.md5(t.encode()).hexdigest()
                ha1, ha2 = md5(f"u:{w.get('realm')}:p"), md5(f"POST:{w.get('uri')}")
                return w.get("username") == "u" and w.get("response") == md5(f"{ha1}:{w['nonce']}:{w['nc']}:{w['cnonce']}:{w['qop']}:{ha2}")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer((PRIVAT, 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_fritzbox_logs_in_with_digest_for_every_call_including_status_info():
    box = FakeFritz()
    try:
        r = ok(abfragen(src("fritzbox", port=box.port, benutzer="u", passwort="p"), {}))
        assert r["wan"]["verbunden"] and r["wan"]["ip"] == "84.1.2.3" and r["wan"]["rx"] == 5000 and r["modell"] == "FRITZ!Box 7590"
    finally:
        box.close()


def test_fritzbox_says_what_is_missing_when_the_login_is_absent_or_wrong():
    box = FakeFritz()
    try:
        assert "Anmeldung" in abfragen(src("fritzbox", port=box.port), {})["fehler"]
        assert "lehnt" in abfragen(src("fritzbox", port=box.port, benutzer="u", passwort="falsch"), {})["fehler"]
    finally:
        box.close()


def test_fritzbox_falls_back_to_the_upnp_interface_when_the_rate_call_fails_with_500():
    box = FakeFritz(kaputt=("/upnp/control/wancommonifconfig1",))
    try:
        r = ok(abfragen(src("fritzbox", port=box.port, benutzer="u", passwort="p"), {}))
        assert r["wan"]["rx"] == 5000 and r["hinweise"] == [] and any(p == "/igdupnp/control/WANCommonIFC1" for _, _, p in box.log)
    finally:
        box.close()


def test_fritzbox_still_reports_connection_when_no_rate_is_available_and_names_the_reason():
    box = FakeFritz(kaputt=("/upnp/control/wancommonifconfig1", "/igdupnp/control/WANCommonIFC1"))
    try:
        r = ok(abfragen(src("fritzbox", port=box.port, benutzer="u", passwort="p"), {}))
        assert r["wan"]["verbunden"] and "rx" not in r["wan"] and "Invalid Action" in r["hinweise"][0] and "HTTP 500" in r["hinweise"][0]
    finally:
        box.close()
