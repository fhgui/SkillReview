"""Sharing the app with other PCs on your network (Settings > Other PCs on your network).

Off by default: the app then only listens on 127.0.0.1. While sharing, it listens on every IPv4 address of this PC
and other PCs must sign in with the access code shown in Settings on this PC. A signed-in PC gets a cookie (only
its SHA-256 is stored) and stays signed in across restarts until "New code" signs every other PC out. Requests
from this PC itself never need the code.
"""
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import threading
import time

from store import now_iso

COOKIE = 'sr_session'
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no 0/O or 1/I to misread
FAIL_LIMIT, FAIL_WINDOW = 5, 300  # wrong codes allowed per address in 5 minutes

SCHEMA = """
CREATE TABLE IF NOT EXISTS network(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, ip TEXT, agent TEXT, created TEXT);
"""


def new_code():
    """12 characters in 3 groups, about 60 bits: far beyond guessing at 5 tries per 5 minutes."""
    return '-'.join(''.join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(3))


def _digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def local_ipv4s():
    """This PC's IPv4 addresses other PCs could use: home-network ones (192.168.x.x) first, then other private
    ones (VPN tunnels often use 10.x), then the rest; never loopback or link-local."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:  # the address of the default route; connecting a UDP socket sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('192.0.2.1', 9))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    out = []
    for ip in found:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not (a.is_loopback or a.is_link_local or a.is_unspecified):
            out.append(ip)
    return sorted(out, key=lambda ip: (0 if ip.startswith('192.168.') else 1 if ipaddress.ip_address(ip).is_private
                                       else 2, ip))


def browser_name(agent):
    for name, pat in (('Edge', r'Edg/'), ('Opera', r'OPR/'), ('Chrome', r'Chrome/'), ('Firefox', r'Firefox/'),
                      ('Safari', r'Safari/')):
        if re.search(pat, agent or ''):
            break
    else:
        name = 'A browser'
    for os_name, pat in (('Windows', 'Windows'), ('Mac', 'Mac OS'), ('Android', 'Android'), ('iPhone', 'iPhone'),
                         ('iPad', 'iPad'), ('Linux', 'Linux')):
        if pat in (agent or ''):
            return f'{name} on {os_name}'
    return name


class Network:
    def __init__(self, registry):
        self.reg = registry
        with registry.lock:
            registry.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.sessions = {r['hash']: dict(r, last_seen=r['created']) for r in self._q('SELECT * FROM sessions')}
        self.fails = {}
        self._ips, self._ips_at = [], 0.0
        self.error = ''  # why the network listener could not open, if it could not

    # stored beside the registry's settings but in their own tables, so /api/settings never shows the code, and
    # without bumping the registry version (that would make every open page refresh)
    def _q(self, sql, args=()):
        with self.reg.lock:
            return [dict(r) for r in self.reg.db.execute(sql, args).fetchall()]

    def _x(self, sql, args=()):
        with self.reg.lock:
            self.reg.db.execute(sql, args)

    def get(self, key, default=None):
        rows = self._q('SELECT value FROM network WHERE key=?', (key,))
        return json.loads(rows[0]['value']) if rows else default

    def put(self, key, value):
        self._x('INSERT OR REPLACE INTO network(key, value) VALUES (?, ?)', (key, json.dumps(value)))

    @property
    def wanted(self):
        return bool(self.get('on', False))

    @property
    def code(self):
        return self.get('code') or ''

    def set_wanted(self, on):
        if on and not self.code:
            self.put('code', new_code())
        self.put('on', bool(on))

    def renew_code(self):
        """A new code, and every other PC is signed out."""
        self.put('code', new_code())
        with self.lock:
            self.sessions.clear()
        self._x('DELETE FROM sessions')

    # ---- addresses ----
    def ips(self, fresh=False):
        if fresh or time.time() - self._ips_at > 30:
            self._ips, self._ips_at = local_ipv4s(), time.time()
        return self._ips

    def host_ok(self, host, port, sharing):
        """The Host header must name this PC (a DNS-rebinding page names its own domain instead)."""
        host = (host or '').strip().lower()
        name, _, p = host.rpartition(':')
        if p != str(port):
            return False
        if name in ('127.0.0.1', 'localhost'):
            return True
        if not sharing:
            return False
        me = socket.gethostname().lower()
        if name in (me, me + '.local', me + '.lan', me + '.home'):
            return True
        return name in self.ips() or name in self.ips(fresh=True)

    def urls(self, port):
        return [f'http://{ip}:{port}/' for ip in self.ips(fresh=True)]

    # ---- signing in ----
    def login(self, ip, code, agent):
        """-> (cookie token, None) or (None, why not)."""
        now = time.time()
        with self.lock:
            recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
            self.fails[ip] = recent
            if len(recent) >= FAIL_LIMIT:
                wait = int(FAIL_WINDOW - (now - recent[0])) + 1
                return None, f'Too many wrong codes. Try again in {max(1, round(wait / 60))} min.'
        typed = re.sub(r'[^A-Z0-9]', '', (code or '').upper())
        real = re.sub(r'[^A-Z0-9]', '', self.code.upper())
        if not real or not hmac.compare_digest(typed, real):
            with self.lock:
                self.fails.setdefault(ip, []).append(now)
            time.sleep(0.4)
            return None, "That code doesn't match. Check it in Settings on the PC running Skill Review."
        token = secrets.token_urlsafe(32)
        rec = {'hash': _digest(token), 'ip': ip, 'agent': (agent or '')[:300], 'created': now_iso()}
        self._x('INSERT INTO sessions(hash, ip, agent, created) VALUES (?, ?, ?, ?)',
                (rec['hash'], rec['ip'], rec['agent'], rec['created']))
        with self.lock:
            self.fails.pop(ip, None)
            self.sessions[rec['hash']] = dict(rec, last_seen=rec['created'])
        return token, None

    @staticmethod
    def cookie_token(cookie_header):
        for part in (cookie_header or '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == COOKIE and v:
                return v
        return None

    def session(self, cookie_header, ip=None):
        token = self.cookie_token(cookie_header)
        if not token:
            return None
        with self.lock:
            s = self.sessions.get(_digest(token))
            if s:
                s['last_seen'] = now_iso()
                if ip:
                    s['ip'] = ip
            return s

    def logout(self, cookie_header):
        token = self.cookie_token(cookie_header)
        if token:
            h = _digest(token)
            with self.lock:
                self.sessions.pop(h, None)
            self._x('DELETE FROM sessions WHERE hash=?', (h,))

    def devices(self):
        with self.lock:
            rows = sorted(self.sessions.values(), key=lambda s: s['last_seen'], reverse=True)
        return [{'ip': s['ip'], 'browser': browser_name(s['agent']), 'signed_in': s['created'],
                 'last_seen': s['last_seen']} for s in rows]
