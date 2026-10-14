"""Minimaler SNMP-Client (Version 1 und 2c, nur lesen) ohne Fremdpakete. Genug fuer GET und WALK auf NAS, Router und Switches."""
import os
import socket

GET, GETNEXT, RESPONSE = 0xA0, 0xA1, 0xA2
INTEGER, OCTETS, NULL, OID, SEQUENCE = 0x02, 0x04, 0x05, 0x06, 0x30
IPADDR, COUNTER32, GAUGE32, TIMETICKS, COUNTER64 = 0x40, 0x41, 0x42, 0x43, 0x46
NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB = 0x80, 0x81, 0x82
UNSIGNED = (COUNTER32, GAUGE32, TIMETICKS, COUNTER64)
MISSING = (NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB)
MAX_WALK = 2000
MAX_PACKET = 65535


class SnmpError(Exception):
    pass


class SnmpStatus(SnmpError):
    """Fehlerstatus aus der Antwort des Geraets (2 = noSuchName, bei SNMP v1 fuer jede unbekannte Kennung)."""

    def __init__(self, status):
        super().__init__(f"SNMP-Fehlerstatus {status}")
        self.status = status


def length(n):
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def tlv(tag, body):
    return bytes([tag]) + length(len(body)) + body


def encode_int(n):
    size = max(1, (n.bit_length() + 8) // 8)
    return tlv(INTEGER, n.to_bytes(size, "big", signed=True))


def encode_oid(oid):
    parts = [int(p) for p in oid.strip(".").split(".")]
    body = bytearray([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        body.extend(reversed(chunk))
    return tlv(OID, bytes(body))


def decode_oid(body):
    parts = [body[0] // 40, body[0] % 40]
    value = 0
    for b in body[1:]:
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(value)
            value = 0
    return ".".join(map(str, parts))


def read_tlv(data, pos):
    if pos + 2 > len(data):
        raise SnmpError("abgeschnittene Antwort")
    tag, first = data[pos], data[pos + 1]
    if first < 0x80:
        size, start = first, pos + 2
    else:
        count = first & 0x7F
        size, start = int.from_bytes(data[pos + 2:pos + 2 + count], "big"), pos + 2 + count
    if start + size > len(data):
        raise SnmpError("abgeschnittene Antwort")
    return tag, data[start:start + size], start + size


def decode_value(tag, body):
    if tag == INTEGER:
        return int.from_bytes(body, "big", signed=True)
    if tag in UNSIGNED:
        return int.from_bytes(body, "big")
    if tag == OID:
        return decode_oid(body)
    if tag == IPADDR:
        return ".".join(str(b) for b in body)
    if tag in MISSING or tag == NULL:
        return None
    return body


def build_request(kind, version, community, request_id, oids):
    binds = b"".join(tlv(SEQUENCE, encode_oid(o) + tlv(NULL, b"")) for o in oids)
    pdu = tlv(kind, encode_int(request_id) + encode_int(0) + encode_int(0) + tlv(SEQUENCE, binds))
    return tlv(SEQUENCE, encode_int(version) + tlv(OCTETS, community.encode()) + pdu)


def parse_response(data, request_id):
    tag, body, _ = read_tlv(data, 0)
    if tag != SEQUENCE:
        raise SnmpError("keine SNMP-Antwort")
    _, _, pos = read_tlv(body, 0)  # Version
    _, _, pos = read_tlv(body, pos)  # Community
    tag, pdu, _ = read_tlv(body, pos)
    if tag != RESPONSE:
        raise SnmpError("unerwarteter Antworttyp")
    _, rid, pos = read_tlv(pdu, 0)
    _, status, pos = read_tlv(pdu, pos)
    _, _, pos = read_tlv(pdu, pos)
    if int.from_bytes(rid, "big", signed=True) != request_id:
        raise SnmpError("falsche Antwortnummer")
    if int.from_bytes(status, "big"):
        raise SnmpStatus(int.from_bytes(status, "big"))
    _, binds, _ = read_tlv(pdu, pos)
    result, pos = [], 0
    while pos < len(binds):
        _, bind, pos = read_tlv(binds, pos)
        _, oid, inner = read_tlv(bind, 0)
        tag, value, _ = read_tlv(bind, inner)
        result.append((decode_oid(oid), tag, decode_value(tag, value)))
    return result


def ask(host, community, kind, oids, port=161, timeout=2.0, version=1):
    request_id = int.from_bytes(os.urandom(3), "big")
    packet = build_request(kind, version, community, request_id, oids)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (host, port))
        try:
            data, _ = sock.recvfrom(MAX_PACKET)
        except socket.timeout:
            raise SnmpError("keine Antwort (Zeitüberschreitung, Community oder Port prüfen)") from None
    return parse_response(data, request_id)


def get_stapel(host, community, oids, port, timeout, version):
    return {oid: value for oid, tag, value in ask(host, community, GET, oids, port, timeout, version) if tag not in MISSING}


def get(host, community, oids, port=161, timeout=2.0, version=1):
    """{oid: wert} fuer die gefragten Kennungen, fehlende fallen weg. version: 0 = v1, 1 = v2c.
    v1 bricht eine Abfrage mit mehreren Kennungen ab, sobald eine fehlt (noSuchName); deshalb dort einzeln fragen."""
    if version != 0:
        return get_stapel(host, community, oids, port, timeout, version)
    ergebnis = {}
    for oid in oids:
        try:
            ergebnis.update(get_stapel(host, community, [oid], port, timeout, version))
        except SnmpStatus as e:
            if e.status != 2:
                raise
    return ergebnis


def walk(host, community, root, port=161, timeout=2.0, version=1):
    """[(oid, wert)] aller Eintraege unterhalb von root."""
    rows, current = [], root
    while len(rows) < MAX_WALK:
        (oid, tag, value), = ask(host, community, GETNEXT, [current], port, timeout, version)
        if tag in MISSING or not oid.startswith(root + "."):
            break
        rows.append((oid, value))
        current = oid
    return rows


def text(value):
    return value.decode("utf-8", "replace").strip("\x00 ") if isinstance(value, bytes) else str(value)


def table(rows, root):
    """{zeilenindex: {spalte: wert}} aus einem Walk der Tabellen-Eintragskennung root (...Entry)."""
    result = {}
    for oid, value in rows:
        column, _, index = oid[len(root) + 1:].partition(".")
        result.setdefault(index, {})[column] = value
    return result
