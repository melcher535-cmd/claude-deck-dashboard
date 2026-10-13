"""Anmeldung: Sitzungs-Cookie, Sperre nach Fehlversuchen, eigenes Passwort und TOTP-Zweitfaktor."""
import base64
import hashlib
import hmac
import secrets
import struct
import time

from storage import read_json, write_json

SESSION_SECONDS = 12 * 3600
LONG_SESSION_SECONDS = 30 * 24 * 3600
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
MAX_TRACKED_IPS = 10_000
API_KEYS_MAX = 5
STEPUP_SECONDS = 300
MIN_PASSWORD_LENGTH = 12
TOTP_STEP = 30
TOTP_DIGITS = 6
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT).hex()


def totp_code(secret, counter):
    """TOTP nach RFC 6238 (HMAC-SHA1) fuer einen Zeitschritt."""
    mac = hmac.new(base64.b32decode(secret), struct.pack(">Q", counter), "sha1").digest()
    offset = mac[-1] & 15
    number = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** TOTP_DIGITS).zfill(TOTP_DIGITS)


class Auth:
    def __init__(self, password, store=None, clock=time.monotonic, wall=time.time):
        self.password = password  # Start-Passwort aus der Umgebung, gilt bis ein eigenes gesetzt ist
        self.store = store  # Pfad zur credentials.json; None haelt alles nur im Speicher
        self.clock = clock
        self.wall = wall
        self.creds = read_json(store, {}) if store else {}
        self.sessions = {}  # Token -> Ablaufzeit
        self.elevated = {}  # Token -> Ende der Sicherheitsstufe
        self.failures = {}  # IP -> (Fehlversuche, gesperrt bis)

    @property
    def totp_enabled(self):
        return bool(self.creds.get("totp"))

    def _save(self):
        if self.store:
            write_json(self.store, self.creds)

    def password_ok(self, password):
        if "hash" in self.creds:
            return hmac.compare_digest(hash_password(password, bytes.fromhex(self.creds["salt"])), self.creds["hash"])
        return hmac.compare_digest(password.encode(), self.password.encode())

    def _matching_counter(self, secret, code):
        """Zeitschritt, zu dem der Code passt (eine Stelle Toleranz), und der noch nicht benutzt wurde."""
        now = int(self.wall() // TOTP_STEP)
        for counter in (now - 1, now, now + 1):
            if counter > self.creds.get("last_counter", -1) and hmac.compare_digest(totp_code(secret, counter), code):
                return counter
        return None

    def code_ok(self, code):
        secret = self.creds.get("totp")
        if not secret:
            return True
        counter = self._matching_counter(secret, code)
        if counter is None:
            return False
        self.creds["last_counter"] = counter
        self._save()
        return True

    def locked(self, ip):
        return self.failures.get(ip, (0, 0))[1] > self.clock()

    def _guard(self, ip, check):
        """Fuehrt eine Pruefung unter Fehlversuch-Zaehlung aus. Gesperrte IPs bestehen sie nie."""
        if self.locked(ip):
            return False
        count, until = self.failures.get(ip, (0, 0))
        if until:  # Sperre ist abgelaufen: Zaehler neu beginnen
            count = 0
        if not check():
            if len(self.failures) >= MAX_TRACKED_IPS:
                self.failures.clear()
            count += 1
            self.failures[ip] = (count, self.clock() + LOCK_SECONDS if count >= MAX_FAILURES else 0)
            return False
        self.failures.pop(ip, None)
        return True

    def _new_session(self, seconds=SESSION_SECONDS):
        token = secrets.token_urlsafe(32)
        self.sessions[token] = self.clock() + seconds
        return token

    def remaining(self, token):
        """Restlaufzeit einer Sitzung in Sekunden (fuer das Cookie)."""
        return int(self.sessions[token] - self.clock())

    def login(self, ip, password, code="", stay=False):
        """Gibt ein Sitzungs-Token zurueck oder None bei falschen Angaben und bei Sperre. stay: 30 Tage statt 12 Stunden."""
        if not self._guard(ip, lambda: self.password_ok(password) & self.code_ok(code)):
            return None
        return self._new_session(LONG_SESSION_SECONDS if stay else SESSION_SECONDS)

    def change_password(self, ip, current, code, new):
        """Setzt ein eigenes Passwort, beendet alle Sitzungen und gibt ein frisches Token zurueck (None bei falschen Angaben)."""
        if len(new) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"mindestens {MIN_PASSWORD_LENGTH} Zeichen")
        if not self._guard(ip, lambda: self.password_ok(current) & self.code_ok(code)):
            return None
        salt = secrets.token_bytes(16)
        self.creds.update(salt=salt.hex(), hash=hash_password(new, salt))
        self._save()
        self.sessions.clear()
        self.elevated.clear()
        return self._new_session()

    def begin_totp(self):
        """Erzeugt ein neues Geheimnis; aktiv wird es erst nach enable_totp."""
        self.creds["totp_pending"] = base64.b32encode(secrets.token_bytes(20)).decode()
        self._save()
        return self.creds["totp_pending"]

    def enable_totp(self, ip, code):
        pending = self.creds.get("totp_pending")
        if not pending:
            raise ValueError("kein Geheimnis angelegt")
        counter = self._matching_counter(pending, code)
        if not self._guard(ip, lambda: counter is not None):
            return False
        self.creds.update(totp=pending, last_counter=counter)
        del self.creds["totp_pending"]
        self._save()
        return True

    def disable_totp(self, ip, password, code):
        if not self._guard(ip, lambda: self.password_ok(password) & self.code_ok(code)):
            return False
        self.creds.pop("totp", None)
        self.creds.pop("last_counter", None)
        self._save()
        return True

    @property
    def api_key_count(self):
        return len(self.creds.get("api_keys", []))

    def create_api_key(self):
        """Schluessel fuer den Kurzbefehl (nur lesend). Er wird nur als Pruefsumme gespeichert und hier einmal herausgegeben."""
        token = secrets.token_urlsafe(32)
        keys = self.creds.get("api_keys", []) + [{"hash": hashlib.sha256(token.encode()).hexdigest(), "created": int(self.wall())}]
        self.creds["api_keys"] = keys[-API_KEYS_MAX:]
        self._save()
        return token

    def api_key_ok(self, ip, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        return self._guard(ip, lambda: any(hmac.compare_digest(digest, k["hash"]) for k in self.creds.get("api_keys", [])))

    def revoke_api_keys(self):
        self.creds.pop("api_keys", None)
        self._save()

    def step_up(self, ip, token, password, code=""):
        """Sicherheitsstufe fuer kritische Aktionen: Passwort und Code erneut pruefen, gilt dann STEPUP_SECONDS."""
        if not self.valid(token) or not self._guard(ip, lambda: self.password_ok(password) & self.code_ok(code)):
            return False
        self.elevated[token] = self.clock() + STEPUP_SECONDS
        return True

    def is_elevated(self, token):
        return self.valid(token) and self.elevated.get(token, 0) > self.clock()

    def valid(self, token):
        expires = self.sessions.get(token)
        if expires is None:
            return False
        if expires < self.clock():
            del self.sessions[token]
            return False
        return True

    def logout(self, token):
        self.sessions.pop(token, None)
        self.elevated.pop(token, None)
