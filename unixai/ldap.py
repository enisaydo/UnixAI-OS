"""Minimal LDAPv3 istemcisi (bağımlılıksız)

- ldap:// (opsiyonel StartTLS) ve ldaps://
- simple bind, search (equality / presence / & | ! filtreleri), unbind
- Yalnızca kimlik doğrulama için gereken kısım; BER kodlaması RFC 4511'e göre
"""
from __future__ import annotations

import socket
import ssl
from urllib.parse import urlsplit


# ------------------------------------------------------------------
#  BER
# ------------------------------------------------------------------
def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _len(len(value)) + value


def seq(tag: int, items) -> bytes:
    return tlv(tag, b"".join(items))


def ostr(s, tag: int = 0x04) -> bytes:
    return tlv(tag, s if isinstance(s, bytes) else str(s).encode("utf-8"))


def bint(n: int, tag: int = 0x02) -> bytes:
    b = n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big")
    if b[0] & 0x80:
        b = b"\x00" + b
    return tlv(tag, b)


def bbool(v: bool) -> bytes:
    return tlv(0x01, b"\xff" if v else b"\x00")


def read_tlv(buf: bytes, off: int = 0):
    """Tek bir TLV okur: (tag, value, next) veya tamamlanmamışsa None"""
    if len(buf) < off + 2:
        return None
    tag, ln, p = buf[off], buf[off + 1], off + 2
    if ln & 0x80:
        n = ln & 0x7F
        if n == 0 or n > 4:
            raise ValueError("Desteklenmeyen BER uzunluğu")
        if len(buf) < p + n:
            return None
        ln = int.from_bytes(buf[p:p + n], "big")
        p += n
    if len(buf) < p + ln:
        return None
    return tag, buf[p:p + ln], p + ln


def children(buf: bytes) -> list:
    out, off = [], 0
    while off < len(buf):
        t = read_tlv(buf, off)
        if not t:
            raise ValueError("Bozuk BER verisi")
        out.append(t)
        off = t[2]
    return out


def to_int(b: bytes) -> int:
    return int.from_bytes(b, "big") if b else 0


# ------------------------------------------------------------------
#  Filtre: "(&(objectClass=user)(sAMAccountName=ali))"
# ------------------------------------------------------------------
def escape_filter(v: str) -> str:
    """RFC 4515 değer kaçışı (kullanıcı girdisi filtreye konmadan önce)"""
    return "".join(f"\\{ord(c):02x}" if c in "\\*()\0" else c for c in str(v))


def _unescape(s: str) -> bytes:
    out, i = bytearray(), 0
    while i < len(s):
        if s[i] == "\\" and len(s) >= i + 3 and all(c in "0123456789abcdefABCDEF" for c in s[i + 1:i + 3]):
            out.append(int(s[i + 1:i + 3], 16))
            i += 3
        else:
            out += s[i].encode("utf-8")
            i += 1
    return bytes(out)


def parse_filter(text: str) -> bytes:
    s, pos = text.strip(), [0]

    def parse():
        i = pos[0]
        if i >= len(s) or s[i] != "(":
            raise ValueError(f"Filtre hatası (konum {i}): '(' bekleniyordu")
        i += 1
        op = s[i] if i < len(s) else ""
        if op in "&|" and op:
            pos[0] = i + 1
            items = []
            while pos[0] < len(s) and s[pos[0]] == "(":
                items.append(parse())
            out = seq(0xA0 if op == "&" else 0xA1, items)
        elif op == "!":
            pos[0] = i + 1
            out = tlv(0xA2, parse())
        else:
            end = s.find(")", i)
            if end == -1:
                raise ValueError("Filtre hatası: ')' eksik")
            item = s[i:end]
            eq = item.find("=")
            if eq < 1:
                raise ValueError(f"Filtre hatası: '{item}'")
            attr, value = item[:eq], item[eq + 1:]
            if attr[-1] in "~<>:":
                raise ValueError(f"Desteklenmeyen filtre operatörü: '{item}'")
            if value == "*":
                out = tlv(0x87, attr.encode("utf-8"))  # present
            elif "*" in value:
                raise ValueError(f"Joker karakterli (substring) filtre desteklenmiyor: '{item}'")
            else:
                out = seq(0xA3, [ostr(attr), ostr(_unescape(value))])  # equalityMatch
            pos[0] = end
        if pos[0] >= len(s) or s[pos[0]] != ")":
            raise ValueError(f"Filtre hatası (konum {pos[0]}): ')' bekleniyordu")
        pos[0] += 1
        return out

    f = parse()
    if pos[0] != len(s):
        raise ValueError("Filtre hatası: fazladan karakter")
    return f


# ------------------------------------------------------------------
#  Bağlantı
# ------------------------------------------------------------------
RESULT_TEXT = {0: "success", 1: "operationsError", 2: "protocolError", 8: "strongerAuthRequired", 10: "referral", 32: "noSuchObject",
               34: "invalidDNSyntax", 48: "inappropriateAuthentication", 49: "invalidCredentials", 50: "insufficientAccessRights",
               51: "busy", 52: "unavailable", 53: "unwillingToPerform"}


class LdapError(Exception):
    def __init__(self, code: int, diag: str, op: str):
        super().__init__(f"LDAP {op}: {RESULT_TEXT.get(code, 'hata')} ({code})" + (f" — {diag}" if diag else ""))
        self.code = code
        self.diag = diag


def _result(value: bytes):
    ch = children(value)
    return to_int(ch[0][1]), ch[2][1].decode("utf-8", "replace") if len(ch) > 2 else ""


class LdapClient:
    def __init__(self, url: str, timeout_ms: int = 10000, insecure_tls: bool = False, start_tls: bool = False):
        self.url, self.timeout = url, timeout_ms / 1000
        self.insecure_tls, self.start_tls_flag = insecure_tls, start_tls
        self.msg_id, self.sock, self.buf = 0, None, b""

    def _ctx(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if self.insecure_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def connect(self):
        u = urlsplit(self.url)
        if u.scheme not in ("ldap", "ldaps"):
            raise ValueError(f"LDAP adresi ldap:// veya ldaps:// ile başlamalı: {self.url}")
        secure = u.scheme == "ldaps"
        self.host, port = u.hostname, u.port or (636 if secure else 389)
        try:
            sock = socket.create_connection((self.host, port), timeout=self.timeout)
            self.sock = self._ctx().wrap_socket(sock, server_hostname=self.host) if secure else sock
        except socket.gaierror:
            raise RuntimeError(f"LDAP sunucusu '{self.host}' çözülemedi (DNS). Tam adı kullanın.")
        except ConnectionRefusedError:
            raise RuntimeError(f"LDAP sunucusu bağlantıyı reddetti: {self.host}. Port doğru mu (389 / 636)?")
        except ssl.SSLCertVerificationError as e:
            raise RuntimeError(f"LDAP TLS sertifikası doğrulanamadı ({e.verify_message}). LDAP_TLS_INSECURE=true ile kapatabilirsiniz.")
        except (socket.timeout, TimeoutError):
            raise RuntimeError(f"LDAP bağlantı zaman aşımı: {self.host}:{port}")
        if not secure and self.start_tls_flag:
            self._start_tls()
        return self

    def _recv(self):
        """Bir LDAPMessage okur: (msgId, (tag, value))"""
        while True:
            t = read_tlv(self.buf)
            if t:
                self.buf = self.buf[t[2]:]
                ch = children(t[1])
                return to_int(ch[0][1]), ch[1]
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("LDAP bağlantısı kapandı")
            self.buf += chunk

    def _send(self, op: bytes) -> int:
        self.msg_id += 1
        self.sock.sendall(seq(0x30, [bint(self.msg_id), op]))
        return self.msg_id

    def _wait(self, mid: int):
        while True:
            rid, op = self._recv()
            if rid == mid:
                return op

    def _start_tls(self):
        op = self._wait(self._send(seq(0x77, [ostr("1.3.6.1.4.1.1466.20037", 0x80)])))
        if op[0] != 0x78:
            raise RuntimeError("StartTLS: beklenmeyen yanıt")
        code, diag = _result(op[1])
        if code:
            raise LdapError(code, diag, "StartTLS")
        try:
            self.sock = self._ctx().wrap_socket(self.sock, server_hostname=self.host)
        except ssl.SSLCertVerificationError as e:
            raise RuntimeError(f"LDAP TLS sertifikası doğrulanamadı ({e.verify_message}). LDAP_TLS_INSECURE=true ile kapatabilirsiniz.")

    def bind(self, dn: str, password: str) -> bool:
        op = self._wait(self._send(seq(0x60, [bint(3), ostr(dn), ostr(password, 0x80)])))
        if op[0] != 0x61:
            raise RuntimeError("Bind: beklenmeyen yanıt")
        code, diag = _result(op[1])
        if code:
            raise LdapError(code, diag, "bind")
        return True

    def search(self, base: str, flt: str, attributes=(), size_limit: int = 2) -> list:
        """[{dn, attrs: {ad(küçük harf): [değerler]}}]"""
        req = seq(0x63, [ostr(base), bint(2, 0x0A), bint(0, 0x0A), bint(size_limit), bint(max(1, int(self.timeout))), bbool(False),
                         parse_filter(flt), seq(0x30, [ostr(a) for a in attributes])])
        mid = self._send(req)
        entries = []
        while True:
            rid, (tag, value) = self._recv()
            if rid != mid:
                continue
            if tag == 0x64:
                name, attrs = children(value)[:2]
                out = {}
                for a in children(attrs[1]):
                    typ, vals = children(a[1])[:2]
                    out[typ[1].decode("utf-8", "replace").lower()] = [v[1].decode("utf-8", "replace") for v in children(vals[1])]
                entries.append({"dn": name[1].decode("utf-8", "replace"), "attrs": out})
            elif tag == 0x73:  # SearchResultReference (AD referansları) yok sayılır
                continue
            elif tag == 0x65:
                code, diag = _result(value)
                if code and not (code == 4 and entries):  # 4 = sizeLimitExceeded
                    raise LdapError(code, diag, "search")
                return entries
            else:
                raise RuntimeError(f"Search: beklenmeyen yanıt (0x{tag:x})")

    def close(self):
        try:
            if self.sock:
                self._send(b"\x42\x00")  # unbind
                self.sock.close()
        except OSError:
            pass
