"""App-wide state: which skills are open (each has its own database folder), settings, and skill discovery.

Layout under the data folder:
  app.db                    open skills + settings
  history.db                transcript index (shared: it describes your sessions, not one skill)
  skills/<skill id>/        review.db and backups/ for that one skill
"""
import glob
import json
import os
import re
import shutil
import sqlite3
import threading

import skillparse as sp
from store import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS open_skills(id TEXT PRIMARY KEY, name TEXT, dir TEXT, source TEXT, label TEXT,
  opened_at TEXT, ord INT);
CREATE TABLE IF NOT EXISTS known_skills(dir TEXT PRIMARY KEY, id TEXT, first_seen TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

DEFAULT_SETTINGS = {
    'models': {'router': 'haiku', 'historian': 'sonnet', 'origin': 'sonnet', 'editor': 'opus',
               'architect': 'opus', 'advisor': 'sonnet'},
    'max_budget_usd': 3.0,
    'claude_path': '',
    'read_dirs': [],  # extra folders the agents may read (Settings)
}

HOME = os.path.expanduser('~')
SOURCE_LABEL = {'user': 'Your skills', 'project': 'Project', 'plugin': 'Plugin', 'scheduled': 'Scheduled tasks',
                'folder': 'Folder'}
SCHEDULED_ROOT = os.path.join(HOME, '.claude', 'scheduled-tasks')


def norm_dir(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def frontmatter(skill_md):
    try:
        with open(skill_md, encoding='utf-8', errors='replace') as fh:
            text = fh.read(20000)
    except OSError:
        return {}
    out = {}
    for it in sp.parse(text)['items']:
        if it['kind'] == 'frontmatter':
            out[it['key']] = it['display'].strip().strip('"\'')
    return out


class Registry:
    def __init__(self, data_dir, user_root):
        self.data_dir = data_dir
        self.user_root = user_root
        os.makedirs(os.path.join(data_dir, 'skills'), exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(os.path.join(data_dir, 'app.db'), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.version = 1

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            self.db.execute(sql, args)
            self.version += 1

    # ---------- settings ----------
    def settings(self):
        out = json.loads(json.dumps(DEFAULT_SETTINGS))
        for r in self.q('SELECT key, value FROM settings'):
            try:
                val = json.loads(r['value'])
            except Exception:
                continue
            if isinstance(out.get(r['key']), dict) and isinstance(val, dict):
                out[r['key']].update(val)
            else:
                out[r['key']] = val
        return out

    def set_settings(self, patch):
        for k, v in patch.items():
            if k in DEFAULT_SETTINGS:
                self.x('INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)', (k, json.dumps(v)))
        return self.settings()

    # ---------- open skills ----------
    def open_list(self):
        return self.q('SELECT * FROM open_skills ORDER BY ord, opened_at')

    def get(self, sid):
        rows = self.q('SELECT * FROM open_skills WHERE id=?', (sid,))
        return rows[0] if rows else None

    def data_path(self, sid):
        return os.path.join(self.data_dir, 'skills', sid)

    def _source_for(self, d):
        nd = norm_dir(d)
        if os.path.dirname(nd) == norm_dir(self.user_root):
            return 'user', SOURCE_LABEL['user']
        if os.path.dirname(nd) == norm_dir(SCHEDULED_ROOT):
            return 'scheduled', SOURCE_LABEL['scheduled']
        parts = nd.replace('\\', '/').split('/')
        if '.claude' in parts and 'skills' in parts:
            i = parts.index('.claude')
            if parts[i + 1:i + 2] == ['plugins']:
                return 'plugin', self._plugin_name(d)
            if i > 0:
                return 'project', os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(d))))
        return 'folder', os.path.basename(os.path.dirname(d))

    @staticmethod
    def _plugin_name(d):
        p = d.replace('\\', '/').split('/')
        if 'skills' in p:
            i = len(p) - 1 - p[::-1].index('skills')
            for cand in p[i - 1::-1]:
                if not re.match(r'^[\d.]+$|^[0-9a-f]{7,}$', cand) and cand not in ('cache', 'marketplaces', 'plugins'):
                    return cand
        return 'plugin'

    def make_id(self, name, d, source, label):
        base = name if source == 'user' else f'{name}@{label}'
        base = re.sub(r'[^A-Za-z0-9._@-]+', '-', base).strip('-') or 'skill'
        sid, n = base, 2
        while True:
            row = self.get(sid)
            known = self.q('SELECT dir FROM known_skills WHERE id=?', (sid,))
            clash = (row and norm_dir(row['dir']) != norm_dir(d)) or (known and known[0]['dir'] != norm_dir(d))
            if not clash:
                return sid
            sid, n = f'{base}-{n}', n + 1

    def open(self, d):
        d = os.path.normpath(os.path.abspath(d))
        if os.path.basename(d).lower() == 'skill.md' or (os.path.isfile(d) and d.lower().endswith('.md')):
            d = os.path.dirname(d)  # a skill's SKILL.md, or another .md file beside it
        if not os.path.isfile(os.path.join(d, 'SKILL.md')):
            raise ValueError(f'No SKILL.md in {d}')
        for r in self.open_list():
            if norm_dir(r['dir']) == norm_dir(d):
                return r['id']
        known = self.q('SELECT id FROM known_skills WHERE dir=?', (norm_dir(d),))
        source, label = self._source_for(d)
        name = frontmatter(os.path.join(d, 'SKILL.md')).get('name') or os.path.basename(d)
        sid = known[0]['id'] if known else self.make_id(name, d, source, label)
        order = (self.q('SELECT MAX(ord) m FROM open_skills')[0]['m'] or 0) + 1
        self.x('INSERT OR REPLACE INTO open_skills VALUES (?,?,?,?,?,?,?)',
               (sid, name, d, source, label, now_iso(), order))
        self.x('INSERT OR IGNORE INTO known_skills VALUES (?,?,?)', (norm_dir(d), sid, now_iso()))
        return sid

    def close(self, sid):
        self.x('DELETE FROM open_skills WHERE id=?', (sid,))

    def reorder(self, ids):
        for i, sid in enumerate(ids):
            self.x('UPDATE open_skills SET ord=? WHERE id=?', (i, sid))

    # ---------- discovery ----------
    def project_dirs(self):
        dirs = set()
        try:
            with open(os.path.join(HOME, '.claude.json'), encoding='utf-8') as fh:
                dirs |= set((json.load(fh).get('projects') or {}).keys())
        except Exception:
            pass
        out = []
        for p in dirs:
            np_ = p.replace('/', os.sep)
            low = np_.lower()
            if os.sep + '.claude' + os.sep + 'worktrees' in low or os.sep + 'temp' + os.sep in low:
                continue
            if os.path.isdir(os.path.join(np_, '.claude', 'skills')):
                out.append(np_)
        return sorted(set(out))

    def discover(self):
        found = {}

        def add(d, source=None, label=None):
            md = os.path.join(d, 'SKILL.md')
            if not os.path.isfile(md):
                return
            key = norm_dir(d)
            if key in found:
                return
            fm = frontmatter(md)
            if source is None:
                source, label = self._source_for(d)
            found[key] = {'dir': os.path.normpath(d), 'name': fm.get('name') or os.path.basename(d),
                          'description': fm.get('description', ''), 'source': source,
                          'source_label': SOURCE_LABEL.get(source, source), 'label': label,
                          'mtime': os.path.getmtime(md)}

        for md in glob.glob(os.path.join(self.user_root, '*', 'SKILL.md')):
            add(os.path.dirname(md), 'user', SOURCE_LABEL['user'])
        for proj in self.project_dirs():
            for md in glob.glob(os.path.join(proj, '.claude', 'skills', '*', 'SKILL.md')):
                add(os.path.dirname(md), 'project', os.path.basename(proj))
        plugins = {}
        for md in glob.glob(os.path.join(HOME, '.claude', 'plugins', '**', 'skills', '*', 'SKILL.md'), recursive=True):
            d = os.path.dirname(md)
            k = (self._plugin_name(d), os.path.basename(d))
            if k not in plugins or os.path.getmtime(md) > os.path.getmtime(os.path.join(plugins[k], 'SKILL.md')):
                plugins[k] = d
        for (plugin, _), d in plugins.items():
            add(d, 'plugin', plugin)
        for md in glob.glob(os.path.join(SCHEDULED_ROOT, '*', 'SKILL.md')):
            add(os.path.dirname(md), 'scheduled', SOURCE_LABEL['scheduled'])
        for r in self.open_list():  # opened from a custom folder
            add(r['dir'])

        open_dirs = {norm_dir(r['dir']): r['id'] for r in self.open_list()}
        known = {r['dir']: r for r in self.q('SELECT * FROM known_skills')}
        out = []
        for key, s in found.items():
            s['open_id'] = open_dirs.get(key)
            k = known.get(key)
            db = os.path.join(self.data_path(k['id']), 'review.db') if k else None
            s['has_data'] = bool(db and os.path.exists(db))
            s['votes'] = 0
            if s['has_data'] and not s['open_id']:
                try:
                    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
                    s['votes'] = con.execute("SELECT COUNT(*) FROM events WHERE type IN "
                                             "('approved','denied','unapproved','restored')").fetchone()[0]
                    con.close()
                except sqlite3.Error:
                    pass
            s['seen_before'] = bool(k)
            out.append(s)
        order = {'user': 0, 'project': 1, 'folder': 2, 'scheduled': 3, 'plugin': 4}
        out.sort(key=lambda s: (order.get(s['source'], 9), (s['label'] or '').lower(), s['name'].lower()))
        return out

    # ---------- first run / migration from the single shared database ----------
    def first_run(self):
        """Move an old shared data/review.db into per-skill databases, then open your skills if none are open."""
        legacy = os.path.join(self.data_dir, 'review.db')
        migrated = []
        fresh = not self.q('SELECT dir FROM known_skills')
        if os.path.exists(legacy):
            src = sqlite3.connect(legacy)
            try:
                src.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                for (key, value) in src.execute('SELECT key, value FROM settings').fetchall():
                    if key in DEFAULT_SETTINGS:
                        self.x('INSERT OR IGNORE INTO settings VALUES (?, ?)', (key, value))
                skills = [r[0] for r in src.execute('SELECT DISTINCT skill FROM rules').fetchall()]
                for name in skills:
                    d = os.path.join(self.user_root, name)
                    sid = self.open(d) if os.path.isfile(os.path.join(d, 'SKILL.md')) else name
                    dest_dir = self.data_path(sid)
                    os.makedirs(dest_dir, exist_ok=True)
                    dest = sqlite3.connect(os.path.join(dest_dir, 'review.db'))
                    src.backup(dest)
                    for table in ('rules', 'events', 'files', 'skills', 'threads', 'jobs'):
                        dest.execute(f'DELETE FROM {table} WHERE skill IS NOT ?', (name,))
                        dest.execute(f'UPDATE {table} SET skill=?', (sid,))
                    dest.execute('DROP TABLE IF EXISTS settings')
                    dest.commit()
                    dest.execute('VACUUM')
                    dest.close()
                    old_backups = os.path.join(self.data_dir, 'backups', 'files', name)
                    if os.path.isdir(old_backups):
                        shutil.copytree(old_backups, os.path.join(dest_dir, 'backups', 'files'), dirs_exist_ok=True)
                    migrated.append(sid)
            finally:
                src.close()
            for suffix in ('', '-wal', '-shm'):
                if os.path.exists(legacy + suffix):
                    os.replace(legacy + suffix, os.path.join(self.data_dir, 'review.db.before-split' + suffix))
        if fresh:  # first run: open everything in your personal skills folder
            for md in sorted(glob.glob(os.path.join(self.user_root, '*', 'SKILL.md'))):
                self.open(os.path.dirname(md))
        return migrated
