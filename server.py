"""Skill Review: a local web app for approving, denying and questioning the rules in your Claude Code skills.

Run:  py server.py            (then open http://127.0.0.1:8765)
      py server.py --open     (also opens the browser)
Each skill you open gets its own database in data/skills/<skill>/. The skill files change only when you deny or
restore a rule, or when an agent you asked makes an edit.
"""
import argparse
import http.server
import json
import mimetypes
import os
import re
import secrets
import socketserver
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser

FROZEN = getattr(sys, 'frozen', False)  # running as SkillReview.exe (the PyInstaller build)
# the source folder; in the exe, the temporary folder PyInstaller unpacks the bundled files into
APP_DIR = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

from agents import ROUTES, Runner  # noqa: E402
from provenance import Provenance  # noqa: E402
from registry import Registry, norm_dir  # noqa: E402
from skillparse import text_hash  # noqa: E402
from store import Store  # noqa: E402

# ./data next to the source. The exe keeps it in %LOCALAPPDATA%\SkillReview: its own folder may be Downloads or
# read-only, and APP_DIR is deleted when it exits.
DATA_DIR = (os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'SkillReview') if FROZEN
            else os.path.join(APP_DIR, 'data'))
STATIC = os.path.join(APP_DIR, 'static')
SKILLS_ROOT = os.path.expanduser(os.path.join('~', '.claude', 'skills'))
PROJECTS = os.path.expanduser(os.path.join('~', '.claude', 'projects'))


class App:
    def __init__(self, port):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.registry = Registry(DATA_DIR, SKILLS_ROOT)
        self.registry.first_run()
        self.stores = {}
        self.stores_lock = threading.RLock()
        self.prov = Provenance(DATA_DIR, PROJECTS, SKILLS_ROOT)
        self.runner = Runner(self, self.prov, PROJECTS)
        self.prov_busy = False
        self.stop = threading.Event()
        for s in self.registry.open_list():
            try:
                self.store(s['id']).sync()
            except Exception:
                traceback.print_exc()
        threading.Thread(target=self._background, daemon=True).start()
        threading.Thread(target=lambda: self.runner.check(self.registry.settings()), daemon=True).start()

    # ---- per-skill stores ----
    def store(self, sid):
        with self.stores_lock:
            if sid in self.stores:
                return self.stores[sid]
            rec = self.registry.get(sid)
            if not rec:
                raise ValueError(f'"{sid}" is not open. Add it with the + button.')
            path = self.registry.data_path(sid)
            os.makedirs(path, exist_ok=True)
            st = Store(path, sid, rec['dir'])
            self.stores[sid] = st
            return st

    def open_skill(self, d):
        sid = self.registry.open(d)
        self.store(sid).sync()
        threading.Thread(target=self._summaries, daemon=True).start()
        return sid

    def close_skill(self, sid, force=False):
        if not force and any(k[0] == sid for k in self.runner.live):
            raise ValueError('An agent is still working on this skill. Wait for it to finish first.')
        self.registry.close(sid)
        with self.stores_lock:
            st = self.stores.pop(sid, None)
        if st:
            st.close()

    def auto_sync(self, st):
        """Pick up file changes, unless an agent is editing that skills folder right now (its job syncs when done,
        so the change is credited to the agent rather than to 'outside the app')."""
        if st.exists() and not self.runner.is_editing(st.dir):
            st.sync()

    def after_agent_edit(self, root, changes, cause, open_new=True):
        """An agent changed files under root: open any skill it created, then re-sync every open skill it touched."""
        touched = {c['file'].split('/')[0] for c in changes if '/' in c['file']}
        if open_new:
            for c in changes:
                parts = c['file'].split('/')
                if c['kind'] == 'created' and len(parts) == 2 and parts[1] == 'SKILL.md':
                    try:
                        self.registry.open(os.path.join(root, parts[0]))
                    except ValueError:
                        pass
        for rec in self.registry.open_list():
            d = norm_dir(rec['dir'])
            if os.path.dirname(d) != norm_dir(root) or os.path.basename(rec['dir']) not in touched:
                continue
            created = any(c['kind'] == 'created' and c['file'] == f"{os.path.basename(rec['dir'])}/SKILL.md"
                          for c in changes)
            if not os.path.isfile(os.path.join(rec['dir'], 'SKILL.md')) and created:
                self.close_skill(rec['id'], force=True)  # a revert removed a skill the agent had made
            else:
                self.store(rec['id']).sync(cause=cause, force=True)

    # ---- background: keep the history index fresh and pre-compute "added"/"first written" per rule ----
    def _background(self):
        while not self.stop.is_set():
            try:
                self.prov_busy = True
                self.prov.refresh(min_interval=25)
                self._summaries()
            except Exception:
                traceback.print_exc()
            finally:
                self.prov_busy = False
            self.stop.wait(30)

    def _summaries(self):
        if self.prov.built_for < 0:
            return
        v = self.prov.built_for
        for rec in self.registry.open_list():
            try:
                st = self.store(rec['id'])
            except ValueError:
                continue
            changed = False
            for r in st.q('SELECT id, text, prov FROM rules WHERE present=1'):
                cur = json.loads(r['prov']) if r['prov'] else None
                h = text_hash(r['text'])
                if cur and cur.get('v') == v and cur.get('h') == h:
                    continue
                s = self.prov.summary(r['text'], st.dir)
                s['v'], s['h'] = v, h
                with st.lock:
                    st.db.execute('UPDATE rules SET prov=? WHERE id=?', (json.dumps(s), r['id']))
                changed = True
            if changed:
                st.version += 1

    # ---- views ----
    def open_view(self):
        out = []
        running = {}
        for k in self.runner.live:
            running[k[0]] = running.get(k[0], 0) + 1
        for rec in self.registry.open_list():
            item = dict(rec)
            try:
                st = self.store(rec['id'])
                item['missing'] = not st.exists()
                self.auto_sync(st)
                c = st.counts()
                item['counts'] = c
                item['todo'] = c.get('pending', 0) + c.get('changed', 0)
            except Exception as e:
                item['error'] = str(e)
                item['todo'] = 0
            item['running'] = running.get(rec['id'], 0)
            out.append(item)
        return out

    def boot(self):
        return {'open': self.open_view(), 'settings': self.registry.settings(), 'routes': ROUTES,
                'agent': self.runner.agent_status, 'skills_root': SKILLS_ROOT, 'data_dir': DATA_DIR}

    def version(self, sid):
        stv = 0
        if sid and self.registry.get(sid):
            st = self.store(sid)
            self.auto_sync(st)
            stv = st.version
        return {'v': f'{self.registry.version}.{stv}', 'open': self.open_view(), 'running': self.runner.running(),
                'agent': self.runner.agent_status,
                'history': {'busy': self.prov_busy, 'status': self.prov.status, 'ready': self.prov.built_for >= 0}}

    def _elsewhere(self, sid):
        """Rule hashes in the other open skills: present ones (where a removed rule went) and removed ones."""
        present, gone = {}, {}
        for rec in self.registry.open_list():
            if rec['id'] == sid:
                continue
            try:
                st = self.store(rec['id'])
            except ValueError:
                continue
            for r in st.q('SELECT hash, present FROM rules'):
                (present if r['present'] else gone).setdefault(r['hash'], []).append(rec['id'])
        return present, gone

    def state(self, sid):
        st = self.store(sid)
        rec = self.registry.get(sid)
        self.auto_sync(st)
        rules = st.rules()
        by_job = {}
        for e in st.q("SELECT e.rule_id, e.job_id, j.thread_id FROM events e JOIN jobs j ON j.id = e.job_id "
                      "WHERE e.type='removed' ORDER BY e.id"):
            by_job[e['rule_id']] = {'job_id': e['job_id'], 'thread_id': e['thread_id']}
        present_elsewhere, gone_elsewhere = self._elsewhere(sid)
        for r in rules:
            for k in ('removed_block', 'removed_file_sha', 'anchor_prev', 'anchor_next'):
                r.pop(k, None)
            if r['present']:
                if r['hash'] in gone_elsewhere:
                    r['moved_from'] = gone_elsewhere[r['hash']]
            else:
                if r['id'] in by_job:
                    r['removed_job'] = by_job[r['id']]
                if r['hash'] in present_elsewhere:
                    r['moved_to'] = present_elsewhere[r['hash']]
        base = st.q1('SELECT baseline_at FROM skills')
        return {'skill': sid, 'info': rec, 'missing': not st.exists(), 'files': st.skill_files(),
                'headings': st.headings(), 'rules': rules, 'baseline_at': base and base['baseline_at'],
                'version': f'{self.registry.version}.{st.version}', 'skill_dir': st.dir}

    def rule_detail(self, sid, rid):
        st = self.store(sid)
        r = st.rule(rid)
        if not r:
            raise ValueError('Unknown rule')
        rules = {x['id']: x for x in st.rules()}
        rule = rules.get(rid)
        if not rule['present'] and r['removed_block'] and r['removed_block'] != r['text']:
            rule['removed_block_preview'] = r['removed_block']
        events = st.q('SELECT * FROM events WHERE rule_id=? ORDER BY id DESC LIMIT 200', (rid,))
        for e in events:
            e['detail'] = json.loads(e['detail'] or '{}')
        return {'rule': rule, 'events': events, 'threads': self.threads(sid, rule_id=rid),
                'children': [x for x in rules.values() if x['parent_id'] == rid]}

    def history(self, sid, rid):
        st = self.store(sid)
        r = st.rule(rid)
        if not r:
            raise ValueError('Unknown rule')
        if self.prov.built_for < 0:
            return {'indexing': True, 'status': self.prov.status}
        self.prov.refresh(min_interval=20)
        tl = self.prov.match(r['text'])
        jobs = {}
        for rec in self.registry.open_list():
            try:
                for j in self.store(rec['id']).q('SELECT id, route, session_id, thread_id FROM jobs '
                                                 'WHERE session_id IS NOT NULL'):
                    jobs[j['session_id']] = dict(j, skill=rec['id'])
            except ValueError:
                pass
        sdn = st.dir.replace('\\', '/').lower() + '/'
        out = []
        for e in tl:
            lab = self.prov.label(e)
            item = {k: e[k] for k in ('id', 'ts', 'tool', 'file_path', 'kind', 'score', 'exact', 'session_id')}
            item.update(lab)
            item['in_skill'] = e['file_norm'].startswith(sdn)
            j = jobs.get(e['session_id'])
            if j:
                item['app_job'] = j
                item['session_title'] = f"Skill Review: {ROUTES.get(j['route'], {}).get('label', j['route'])}"
            out.append(item)
        return {'timeline': out, 'status': self.prov.status}

    def threads(self, sid, rule_id=None):
        st = self.store(sid)
        sql = 'SELECT * FROM threads' + (' WHERE rule_id=?' if rule_id else '') + ' ORDER BY updated_at DESC'
        rows = st.q(sql, (rule_id,) if rule_id else ())
        for t in rows:
            jobs = st.q('SELECT * FROM jobs WHERE thread_id=? ORDER BY id', (t['id'],))
            t['jobs'] = [self.runner.job_view(sid, j) for j in jobs]
            t['status'] = jobs[-1]['status'] if jobs else 'empty'
            rr = st.rule(t['rule_id'])
            t['rule'] = {'id': rr['id'], 'display': rr['display'], 'status': rr['status'], 'present': rr['present'],
                         'kind': rr['kind'], 'file': rr['file']} if rr else None
            t['changed_files'] = sum(len(j['changes']) for j in t['jobs'])
        return rows

    def events(self, sid, limit=1000):
        st = self.store(sid)
        rows = st.q('SELECT e.*, r.display rule_display, r.kind rule_kind, r.file rule_file FROM events e '
                    'LEFT JOIN rules r ON r.id = e.rule_id ORDER BY e.id DESC LIMIT ?', (limit,))
        for e in rows:
            e['detail'] = json.loads(e['detail'] or '{}')
            for k in ('old', 'new', 'text'):
                if isinstance(e['detail'].get(k), str):
                    e['detail'][k] = e['detail'][k][:600]
        return rows


class Handler(http.server.BaseHTTPRequestHandler):
    app: App = None
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        host = (self.headers.get('Host') or '').lower()
        return host in (f'127.0.0.1:{self.app.port}', f'localhost:{self.app.port}')

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n).decode('utf-8')) if n else {}

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def _dispatch(self, method):
        if not self._host_ok():
            return self._send(403, {'error': 'Forbidden host'})
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, dict(urllib.parse.parse_qsl(url.query))
        try:
            if path == '/api/ping':
                return self._send(200, {'app': 'skill-review'})
            if not path.startswith('/api/'):
                return self._static(path)
            if self.headers.get('X-Token') != self.app.token:
                return self._send(403, {'error': 'Bad token. Reload the page.'})
            if method == 'POST':
                origin = self.headers.get('Origin')
                if origin and origin not in (f'http://127.0.0.1:{self.app.port}', f'http://localhost:{self.app.port}'):
                    return self._send(403, {'error': 'Bad origin'})
            body = self._body() if method == 'POST' else {}
            m = re.match(r'^/api/s/([^/]+)(/.*)$', path)
            if m:
                return self._send(200, self._skill_api(method, urllib.parse.unquote(m.group(1)), m.group(2), qs, body))
            return self._send(200, self._api(method, path, qs, body))
        except ValueError as e:
            return self._send(400, {'error': str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {'error': f'{type(e).__name__}: {e}'})

    def _static(self, path):
        if path in ('/', '/index.html'):
            with open(os.path.join(STATIC, 'index.html'), encoding='utf-8') as fh:
                html = fh.read().replace('__TOKEN__', self.app.token)
            return self._send(200, html.encode('utf-8'), 'text/html; charset=utf-8')
        rel = os.path.normpath(path.lstrip('/')).replace('\\', '/')
        if rel.startswith('static/'):
            rel = rel[len('static/'):]
        full = os.path.join(STATIC, rel)
        if rel.startswith('..') or not os.path.isfile(full):
            return self._send(404, {'error': 'Not found'})
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        with open(full, 'rb') as fh:
            return self._send(200, fh.read(), ctype)

    def _skill_api(self, method, sid, path, qs, body):
        a = self.app
        st = a.store(sid)
        m = re.match(r'^/rule/(\d+)(?:/(\w+))?$', path)
        if m:
            rid, action = int(m.group(1)), m.group(2)
            if method == 'GET' and not action:
                return a.rule_detail(sid, rid)
            if method == 'GET' and action == 'history':
                return a.history(sid, rid)
            if method == 'POST' and action == 'approve':
                return st.approve(rid, cascade=body.get('cascade', True))
            if method == 'POST' and action == 'unapprove':
                return st.unapprove(rid)
            if method == 'POST' and action == 'flag':
                return st.flag(rid, note=body.get('note') or '')
            if method == 'POST' and action == 'deny':
                return st.deny(rid, note=(body.get('note') or '').strip())
            if method == 'POST' and action == 'restore':
                return st.restore(rid)
        m = re.match(r'^/thread/(\d+)$', path)
        if m:
            t = st.q1('SELECT * FROM threads WHERE id=?', (int(m.group(1)),))
            if not t:
                raise ValueError('Unknown conversation')
            return [x for x in a.threads(sid, t['rule_id']) if x['id'] == t['id']][0]
        m = re.match(r'^/job/(\d+)/(cancel|revert)$', path)
        if m and method == 'POST':
            jid = int(m.group(1))
            return {'ok': a.runner.cancel(sid, jid)} if m.group(2) == 'cancel' else a.runner.revert(sid, jid)
        if path == '/state':
            return a.state(sid)
        if path == '/threads':
            return a.threads(sid)
        if path == '/events':
            return a.events(sid)
        if path == '/section/approve' and method == 'POST':
            return st.approve_section(body['file'], body['path'])
        if path == '/undo' and method == 'POST':
            return st.undo(body['batch'])
        if path == '/ask' and method == 'POST':
            msg = (body.get('message') or '').strip()
            if not msg:
                raise ValueError('Type a message first.')
            return a.runner.submit(sid, int(body['rule_id']), msg, body.get('route') or 'auto',
                                   thread_id=body.get('thread_id'), edit_id=body.get('edit_id'))
        raise ValueError(f'No such endpoint: {method} {path}')

    def _api(self, method, path, qs, body):
        a = self.app
        m = re.match(r'^/api/edit/(\d+)/context$', path)
        if m:
            ctx = a.prov.context(int(m.group(1)))
            if not ctx:
                raise ValueError('Unknown edit')
            return ctx
        if path == '/api/boot':
            return a.boot()
        if path == '/api/version':
            return a.version(qs.get('skill'))
        if path == '/api/skills/available':
            return a.registry.discover()
        if path == '/api/skills/open' and method == 'POST':
            d = (body.get('dir') or '').strip().strip('"')
            if not d:
                raise ValueError('Give a folder that contains a SKILL.md.')
            return {'id': a.open_skill(os.path.expandvars(os.path.expanduser(d)))}
        if path == '/api/skills/close' and method == 'POST':
            a.close_skill(body['id'])
            return {'ok': True}
        if path == '/api/skills/reorder' and method == 'POST':
            a.registry.reorder(body['ids'])
            return {'ok': True}
        if path == '/api/settings':
            return a.registry.set_settings(body) if method == 'POST' else a.registry.settings()
        if path == '/api/agent/check' and method == 'POST':
            return a.runner.check(a.registry.settings())
        if path == '/api/history/reindex' and method == 'POST':
            a.prov.last_refresh = 0
            threading.Thread(target=lambda: (a.prov.refresh(min_interval=0), a._summaries()), daemon=True).start()
            return {'ok': True}
        if path == '/api/shutdown' and method == 'POST':
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return {'ok': True}
        raise ValueError(f'No such endpoint: {method} {path}')


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def already_running(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=1.5) as r:
            return json.loads(r.read()).get('app') == 'skill-review'
    except Exception:
        return False


def main():
    global SKILLS_ROOT, DATA_DIR
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--open', action='store_true', help='open the app in your browser (the exe always does)')
    ap.add_argument('--no-open', action='store_true', help="don't open the browser, even from the exe")
    ap.add_argument('--skills-root', help='your personal skills folder (default ~/.claude/skills)')
    ap.add_argument('--data', help='folder for the review databases and backups '
                                   r'(default ./data; the exe uses %%LOCALAPPDATA%%\SkillReview)')
    args = ap.parse_args()
    args.open = (args.open or FROZEN) and not args.no_open  # double-clicking the exe opens the page
    if args.skills_root:
        SKILLS_ROOT = os.path.abspath(args.skills_root)
    if args.data:
        DATA_DIR = os.path.abspath(args.data)
    url = f'http://127.0.0.1:{args.port}/'
    if already_running(args.port):
        print(f'Skill Review is already running at {url}')
        if args.open:
            webbrowser.open(url)
        return
    app = App(args.port)
    Handler.app = app
    httpd = Server(('127.0.0.1', args.port), Handler)
    print(f'Skill Review running at {url}  (Ctrl+C to stop)')
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
