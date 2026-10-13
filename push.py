"""Web-Push (RFC 8030, 8291, 8292) mit dem Paket `cryptography`: Schluessel, Abos, Verschluesselung, Versand."""
import base64
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from storage import read_json, write_json

MAX_SUBSCRIPTIONS = 20
RECORD_SIZE = 4096
JWT_LIFETIME = 12 * 3600
SEND_TIMEOUT = 10
GONE = (404, 410)


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def uncompressed(public_key):
    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def hkdf(length, salt, info, secret):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(secret)


def encrypt(payload, ua_public, auth_secret, salt, as_private=None):
    """Nutzlast fuer ein Abo verschluesseln (aes128gcm, RFC 8291). ua_public und auth_secret kommen aus dem Abo."""
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = uncompressed(as_private.public_key())
    shared = as_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = hkdf(32, auth_secret, b"WebPush: info\x00" + ua_public + as_public, shared)
    key = hkdf(16, salt, b"Content-Encoding: aes128gcm\x00", ikm)
    nonce = hkdf(12, salt, b"Content-Encoding: nonce\x00", ikm)
    sealed = AESGCM(key).encrypt(nonce, payload + b"\x02", None)  # 0x02 = letzter Datensatz
    return salt + struct.pack(">IB", RECORD_SIZE, len(as_public)) + as_public + sealed


class Push:
    def __init__(self, keys_path, subs_path, subject, clock=time.time):
        self.keys_path, self.subs_path, self.subject, self.clock = keys_path, subs_path, subject, clock

    def _private_key(self):
        stored = read_json(self.keys_path, None)
        if stored:
            return ec.derive_private_key(int.from_bytes(b64d(stored["private"]), "big"), ec.SECP256R1())
        key = ec.generate_private_key(ec.SECP256R1())
        write_json(self.keys_path, {"private": b64u(key.private_numbers().private_value.to_bytes(32, "big"))})
        return key

    def public_key(self):
        return b64u(uncompressed(self._private_key().public_key()))

    def subscriptions(self):
        return read_json(self.subs_path, {})

    def subscribe(self, sub):
        """Abo pruefen und speichern. Der Endpunkt muss https sein, die Schluessel muessen die richtige Laenge haben."""
        endpoint, keys = sub.get("endpoint", ""), sub.get("keys", {})
        if not endpoint.startswith("https://") or len(b64d(keys.get("p256dh", ""))) != 65 or len(b64d(keys.get("auth", ""))) != 16:
            raise ValueError("ungueltiges Abo")
        subs = self.subscriptions()
        if endpoint not in subs and len(subs) >= MAX_SUBSCRIPTIONS:
            raise ValueError("zu viele Geraete")
        subs[endpoint] = {"p256dh": keys["p256dh"], "auth": keys["auth"], "added": int(self.clock())}
        write_json(self.subs_path, subs)

    def unsubscribe(self, endpoint):
        subs = self.subscriptions()
        if subs.pop(endpoint, None) is not None:
            write_json(self.subs_path, subs)

    def vapid_authorization(self, endpoint):
        """JWT mit ES256 (RFC 8292). aud ist der Ursprung des Push-Dienstes."""
        parts = urlsplit(endpoint)
        header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        claims = b64u(json.dumps({"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(self.clock()) + JWT_LIFETIME, "sub": self.subject}).encode())
        key = self._private_key()
        r, s = decode_dss_signature(key.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
        return f"vapid t={header}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={self.public_key()}"

    def send(self, endpoint, sub, message, salt=None):
        """Ein Abo beliefern. Gibt den HTTP-Status zurueck; bei 404 oder 410 wird das Abo gestrichen."""
        body = encrypt(json.dumps(message).encode(), b64d(sub["p256dh"]), b64d(sub["auth"]), salt or os.urandom(16))
        request = urllib.request.Request(endpoint, data=body, method="POST", headers={
            "Authorization": self.vapid_authorization(endpoint), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": "86400", "Urgency": "normal"})
        try:
            with urllib.request.urlopen(request, timeout=SEND_TIMEOUT) as response:
                return response.status
        except urllib.error.HTTPError as e:
            if e.code in GONE:
                self.unsubscribe(endpoint)
            return e.code

    def broadcast(self, title, body, url="/"):
        """An alle Geraete senden. Gibt die Zahl der angenommenen Nachrichten zurueck."""
        sent = 0
        for endpoint, sub in list(self.subscriptions().items()):
            try:
                sent += self.send(endpoint, sub, {"title": title, "body": body, "url": url}) in (200, 201, 202)
            except (urllib.error.URLError, OSError) as e:
                print(f"push: {endpoint[:40]}… {e!r}", file=sys.stderr)
        return sent
