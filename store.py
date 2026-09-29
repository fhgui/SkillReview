"""One skill's review state in its own SQLite file, plus syncing it with that skill's files on disk.

Votes live only here; the skill files are touched only when the owner denies a rule (the block is cut out)
or restores one (the block is put back). Every rule keeps a stable id across edits: an unchanged rule is
matched by its text hash, an edited one by similarity, so an approval survives a file edit and a changed
approved rule comes back for review.
"""
import difflib
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

import skillparse as sp

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS rules(
  id INTEGER PRIMARY KEY,
  skill TEXT NOT NULL, file TEXT NOT NULL,
  kind TEXT, depth INT, parent_id INT, ord INT,
  text TEXT, display TEXT, hash TEXT,
  section TEXT, heading_line INT,
  line_start INT, line_end INT, block_end INT,
  extra TEXT,
  present INT DEFAULT 1,
  status TEXT DEFAULT 'pending',
  status_at TEXT,
  approved_text TEXT,
  prev_text TEXT, changed_at TEXT, changed_by TEXT,
  first_seen TEXT, last_seen TEXT, is_new INT DEFAULT 0, added_by TEXT,
  removed_at TEXT, removed_how TEXT, removed_note TEXT, removed_block TEXT, removed_line INT,
  removed_file_sha TEXT, anchor_prev TEXT, anchor_next TEXT, status_before_removal TEXT,
  reappeared_at TEXT,
  prov TEXT,
  flag_note TEXT, flagged_at TEXT
);
CREATE INDEX IF NOT EXISTS rules_present ON rules(present);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ts TEXT, skill TEXT, rule_id INT, type TEXT, detail TEXT, batch TEXT, job_id INT);
CREATE INDEX IF NOT EXISTS events_rule ON events(rule_id);
CREATE TABLE IF NOT EXISTS files(skill TEXT, file TEXT, mtime REAL, size INT, sha TEXT, PRIMARY KEY(skill, file));
CREATE TABLE IF NOT EXISTS skills(skill TEXT PRIMARY KEY, baseline_at TEXT);
CREATE TABLE IF NOT EXISTS threads(
  id INTEGER PRIMARY KEY, skill TEXT, rule_id INT, route TEXT, session_id TEXT, title TEXT,
  created_at TEXT, updated_at TEXT, edit_id INT);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, thread_id INT, skill TEXT, rule_id INT, route TEXT, route_reason TEXT, model TEXT,
  message TEXT, status TEXT, created_at TEXT, started_at TEXT, finished_at TEXT, session_id TEXT,
  result TEXT, error TEXT, cost REAL, log TEXT, changes TEXT, snapshot TEXT, reverted_at TEXT, cwd TEXT);
"""


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def iso_from_ts(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def sha(text):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def similarity(a, b):
    return difflib.SequenceMatcher(None, sp.norm(a), sp.norm(b), autojunk=False).ratio()


class Store:
    """Review state for ONE skill folder, kept in <data_dir>/review.db."""

    def __init__(self, data_dir, skill_id, skill_dir):
        self.data_dir = data_dir
        self.skill = skill_id
        self.dir = os.path.normpath(skill_dir)
        self.backup_dir = os.path.join(data_dir, 'backups')
        os.makedirs(self.backup_dir, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(os.path.join(data_dir, 'review.db'), check_same_thread=False,
                                  isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)
        cols = {row[1] for row in self.db.execute('PRAGMA table_info(rules)')}
        for col in ('flag_note', 'flagged_at'):  # added after the first release
            if col not in cols:
                self.db.execute(f'ALTER TABLE rules ADD COLUMN {col} TEXT')
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('skill_id', ?)", (skill_id,))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('dir', ?)", (self.dir,))
        self.version = 1
        self.restoring = set()

    def close(self):
        with self.lock:
            self.db.close()

    # ---------- small helpers ----------
    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def q1(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.version += 1
            return cur.lastrowid

    def tx(self):
        store = self

        class _Tx:
            def __enter__(self):
                store.lock.acquire()
                store.db.execute('BEGIN')

            def __exit__(self, et, ev, tb):
                try:
                    store.db.execute('ROLLBACK' if et else 'COMMIT')
                    store.version += 1
                finally:
                    store.lock.release()
        return _Tx()

    def event(self, rule_id, type_, detail=None, batch=None, job_id=None, ts=None):
        self.db.execute('INSERT INTO events(ts, skill, rule_id, type, detail, batch, job_id) VALUES (?,?,?,?,?,?,?)',
                        (ts or now_iso(), self.skill, rule_id, type_, json.dumps(detail or {}), batch, job_id))

    # ---------- files on disk ----------
    def exists(self):
        return os.path.isfile(os.path.join(self.dir, 'SKILL.md'))

    def skill_files(self):
        files = []
        for p in glob.glob(os.path.join(self.dir, '**', '*.md'), recursive=True):
            files.append(os.path.relpath(p, self.dir).replace('\\', '/'))
        files.sort(key=lambda f: (f != 'SKILL.md', f.count('/'), f.lower()))
        return files

    def file_path(self, rel):
        path = os.path.normpath(os.path.join(self.dir, rel.replace('/', os.sep)))
        if not path.startswith(self.dir + os.sep):
            raise ValueError('bad file path')
        return path

    def read_file(self, rel):
        with open(self.file_path(rel), 'r', encoding='utf-8', newline='') as fh:
            return fh.read()

    def write_file(self, rel, text, tag):
        path = self.file_path(rel)
        if os.path.exists(path):
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            dest = os.path.join(self.backup_dir, 'files', rel.replace('/', '__') + f'.{stamp}.{tag}.md')
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(path, dest)
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(text)

    def disk_signature(self):
        sig = []
        for rel in self.skill_files():
            try:
                st = os.stat(self.file_path(rel))
                sig.append((rel, st.st_mtime, st.st_size))
            except OSError:
                pass
        return sig

    def needs_sync(self):
        known = {r['file']: (r['mtime'], r['size']) for r in self.q('SELECT * FROM files')}
        sig = self.disk_signature()
        if len(sig) != len(known):
            return True
        return any(known.get(rel) != (mt, sz) for rel, mt, sz in sig)

    # ---------- sync ----------
    def sync(self, cause=None, force=False):
        """Match the files on disk to stored rules. cause: None (outside the app), {'job_id': n} or {'by': str}."""
        with self.lock:
            if not force and not self.needs_sync():
                return False
            parsed, texts, stats = {}, {}, {}
            for rel in self.skill_files():
                try:
                    texts[rel] = self.read_file(rel)
                    st = os.stat(self.file_path(rel))
                except OSError:
                    continue
                parsed[rel] = sp.parse(texts[rel])
                stats[rel] = (st.st_mtime, st.st_size)
            prev_sha = {r['file']: r['sha'] for r in self.q('SELECT * FROM files')}
            changed_files = {rel for rel in texts if prev_sha.get(rel) != sha(texts[rel])}
            changed_files |= set(prev_sha) - set(texts)
            if not changed_files and not force:
                with self.tx():
                    for rel, (mt, sz) in stats.items():
                        self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)',
                                        (self.skill, rel, mt, sz, sha(texts[rel])))
                return False
            baseline = self.q1('SELECT * FROM skills') is None
            with self.tx():
                self._sync_tx(parsed, stats, cause, baseline)
                for rel, (mt, sz) in stats.items():
                    self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)',
                                    (self.skill, rel, mt, sz, sha(texts[rel])))
                for rel in set(prev_sha) - set(texts):
                    self.db.execute('DELETE FROM files WHERE file=?', (rel,))
                if baseline:
                    self.db.execute('INSERT INTO skills VALUES (?, ?)', (self.skill, now_iso()))
            return True

    def _sync_tx(self, parsed, stats, cause, baseline):
        now = now_iso()
        job_id = (cause or {}).get('job_id')
        by = (cause or {}).get('by') or (f'job:{job_id}' if job_id else 'outside')
        cur = []  # (file, item)
        for rel, p in parsed.items():
            for it in p['items']:
                cur.append((rel, it))
        change_ts = {rel: iso_from_ts(mt) for rel, (mt, _) in stats.items()}
        old = [dict(r) for r in self.db.execute('SELECT * FROM rules WHERE present=1 ORDER BY ord')]
        match = [None] * len(cur)  # rule id for each current item
        used = set()

        # 1. same file, same text
        by_key = {}
        for r in old:
            by_key.setdefault((r['file'], r['hash']), []).append(r)
        for i, (rel, it) in enumerate(cur):
            for r in by_key.get((rel, it['hash']), []):
                if r['id'] not in used:
                    match[i] = r['id']
                    used.add(r['id'])
                    break
        # 2. same text, moved to another file of the skill
        by_hash = {}
        for r in old:
            if r['id'] not in used:
                by_hash.setdefault(r['hash'], []).append(r)
        for i, (rel, it) in enumerate(cur):
            if match[i] is None:
                for r in by_hash.get(it['hash'], []):
                    if r['id'] not in used:
                        match[i] = r['id']
                        used.add(r['id'])
                        break
        # 3. edited text: best similar unmatched rule in the same file
        edited = {}
        pairs = []
        rest_old = [r for r in old if r['id'] not in used]
        for i, (rel, it) in enumerate(cur):
            if match[i] is not None:
                continue
            for r in rest_old:
                if r['file'] != rel or (r['kind'] == 'frontmatter') != (it['kind'] == 'frontmatter'):
                    continue
                if it['kind'] == 'frontmatter':
                    if json.loads(r['extra'] or '{}').get('key') != it.get('key'):
                        continue
                    s = 1.0
                else:
                    s = similarity(r['text'], it['text'])
                if s >= 0.55:
                    pairs.append((s, i, r['id']))
        pairs.sort(reverse=True)
        for s, i, rid in pairs:
            if match[i] is None and rid not in used:
                match[i] = rid
                used.add(rid)
                edited[i] = s
        # 3b. the rule an agent was asked to rewrite: however different the new wording, the new item left where it
        # stood (same file and depth, within a few lines of its old start) is that rule
        focus = (cause or {}).get('focus') if (cause or {}).get('focus_skill') == self.skill else None
        focus_rule = next((r for r in old if r['id'] == focus and r['id'] not in used), None)
        if focus_rule:
            best, best_gap = None, 4
            for i, (rel, it) in enumerate(cur):
                if match[i] is None and rel == focus_rule['file'] and it['depth'] == focus_rule['depth'] \
                        and it['kind'] == focus_rule['kind']:
                    gap = abs(it['start'] - focus_rule['line_start'])
                    if gap < best_gap:
                        best, best_gap = i, gap
            if best is not None:
                match[best] = focus_rule['id']
                used.add(focus_rule['id'])
                edited[best] = 0.0
        # 4. text that was denied / removed before and is back
        revived = {}
        by_hash_gone = {}
        for r in self.db.execute('SELECT * FROM rules WHERE present=0 ORDER BY removed_at DESC'):
            by_hash_gone.setdefault(r['hash'], []).append(dict(r))
        for i, (rel, it) in enumerate(cur):
            if match[i] is None:
                for r in by_hash_gone.get(it['hash'], []):
                    if r['id'] not in used:
                        match[i] = r['id']
                        used.add(r['id'])
                        revived[i] = r
                        break

        old_by_id = {r['id']: r for r in old}
        ids = [None] * len(cur)
        for i, (rel, it) in enumerate(cur):
            extra = {k: it[k] for k in ('key', 'marker', 'checked', 'cells', 'table_header', 'table_line') if k in it}
            common = dict(file=rel, kind=it['kind'], depth=it['depth'], ord=i, text=it['text'],
                          display=it['display'], hash=it['hash'], section=json.dumps(it['section']),
                          heading_line=parsed[rel]['headings'][it['heading']]['line'] if it['heading'] is not None else None,
                          line_start=it['start'], line_end=it['end'], block_end=it['block_end'],
                          extra=json.dumps(extra), last_seen=now)
            rid = match[i]
            if rid is None:
                common.update(skill=self.skill, present=1, status='pending', status_at=now, first_seen=now,
                              is_new=0 if baseline else 1, added_by=None if baseline else by)
                cols = ','.join(common)
                rid = self.db.execute(f'INSERT INTO rules({cols}) VALUES ({",".join("?" * len(common))})',
                                      list(common.values())).lastrowid
                if not baseline:
                    self.event(rid, 'appeared', {'by': by, 'file': rel}, job_id=job_id,
                               ts=now if job_id else change_ts.get(rel))
            elif i in revived:
                r = revived[i]
                restoring = rid in self.restoring
                status = (r['status_before_removal'] or 'pending') if restoring else 'pending'
                common.update(present=1, status=status, status_at=now, removed_at=None, removed_how=None,
                              removed_note=None, removed_block=None, removed_line=None, removed_file_sha=None,
                              reappeared_at=None if restoring else now)
                self._update(rid, common)
                if not restoring:
                    self.event(rid, 'reappeared', {'by': by, 'was': r['removed_how'], 'removed_at': r['removed_at']},
                               job_id=job_id)
            else:
                r = old_by_id[rid]
                if i in edited or r['text'] != it['text']:
                    if sp.norm(r['text']) != sp.norm(it['text']):
                        common.update(prev_text=r['text'], changed_at=now if job_id else change_ts.get(rel, now),
                                      changed_by=by)
                        if r['status'] == 'approved':
                            common.update(status='changed', status_at=now)
                        elif r['status'] == 'changed' and sp.norm(r['approved_text'] or '') == sp.norm(it['text']):
                            common.update(status='approved', status_at=now)
                        self.event(rid, 'changed', {'by': by, 'old': r['text'], 'new': it['text']}, job_id=job_id)
                self._update(rid, common)
            ids[i] = rid
        file_offset = {}
        for i, (rel, _) in enumerate(cur):
            file_offset.setdefault(rel, i)
        for i, (rel, it) in enumerate(cur):
            parent = ids[file_offset[rel] + it['parent']] if it['parent'] is not None else None
            self.db.execute('UPDATE rules SET parent_id=? WHERE id=?', (parent, ids[i]))

        # rules that vanished: remember a neighbour so Restore can put them back in the right place
        seen = set(ids)
        hash_now = {ids[i]: it['hash'] for i, (_, it) in enumerate(cur)}
        for k, r in enumerate(old):
            if r['id'] in seen:
                continue
            anchor = None
            for o in reversed(old[:k]):
                if o['file'] == r['file'] and o['id'] in seen and o['parent_id'] == r['parent_id']:
                    anchor = 'after_block:' + hash_now[o['id']]
                    break
            if anchor is None and r['parent_id'] in seen:
                anchor = 'after_own:' + hash_now[r['parent_id']]
            self.db.execute("""UPDATE rules SET present=0, status_before_removal=status, status='gone',
                               status_at=?, removed_at=?, removed_how=?, removed_block=text, anchor_prev=?,
                               removed_line=NULL, removed_file_sha=NULL WHERE id=?""",
                            (now, now if job_id else change_ts.get(r['file'], now), 'job' if job_id else 'outside',
                             anchor, r['id']))
            self.event(r['id'], 'removed', {'by': by, 'text': r['text']}, job_id=job_id)
        if baseline:
            self.event(None, 'baseline', {'rules': len(cur)})

    def _update(self, rid, fields):
        sets = ','.join(f'{k}=?' for k in fields)
        self.db.execute(f'UPDATE rules SET {sets} WHERE id=?', list(fields.values()) + [rid])

    # ---------- reading ----------
    def rule(self, rid):
        return self.q1('SELECT * FROM rules WHERE id=?', (rid,))

    def descendants(self, rid, present_only=True):
        out, frontier = [], [rid]
        while frontier:
            marks = ','.join('?' * len(frontier))
            sql = f'SELECT * FROM rules WHERE parent_id IN ({marks})' + (' AND present=1' if present_only else '')
            kids = self.q(sql, frontier)
            out += kids
            frontier = [k['id'] for k in kids]
        return out

    def rules(self):
        rows = self.q('SELECT * FROM rules ORDER BY present DESC, file, ord')
        counts = {r['rule_id']: r['n'] for r in self.q('SELECT rule_id, COUNT(*) n FROM threads GROUP BY rule_id')}
        running = {r['rule_id'] for r in self.q("SELECT rule_id FROM jobs WHERE status IN ('queued','running')")}
        for r in rows:
            r['section'] = json.loads(r['section'] or '[]')
            r['extra'] = json.loads(r['extra'] or '{}')
            r['prov'] = json.loads(r['prov']) if r['prov'] else None
            r['threads'] = counts.get(r['id'], 0)
            r['busy'] = r['id'] in running
        return rows

    def counts(self):
        return {r['status']: r['n'] for r in self.q('SELECT status, COUNT(*) n FROM rules GROUP BY status')}

    def headings(self):
        out = []
        for rel in self.skill_files():
            try:
                p = sp.parse(self.read_file(rel))
            except OSError:
                continue
            for h in p['headings']:
                out.append({'file': rel, 'line': h['line'], 'level': h['level'], 'title': h['title'],
                            'path': h['path']})
        return out

    # ---------- votes ----------
    @staticmethod
    def _prev(t):
        """What undo needs to put a rule's vote back exactly."""
        return {'prev': t['status'], 'prev_approved': t['approved_text'], 'prev_note': t['flag_note'],
                'prev_flagged_at': t['flagged_at']}

    def approve(self, rid, cascade=True, batch=None):
        """Approve a rule and its waiting sub-rules. Approving a flagged rule means "done tuning": it clears the
        flag on it and on sub-rules flagged with it. Approving an unflagged parent leaves flagged sub-rules alone."""
        batch = batch or uuid.uuid4().hex[:12]
        r = self.rule(rid)
        if not r or not r['present']:
            raise ValueError('That rule is not in the file any more.')
        targets = [r] + (self.descendants(rid) if cascade else [])
        root_flagged = r['status'] == 'flagged'
        n = 0
        with self.tx():
            now = now_iso()
            for t in targets:
                if t['status'] == 'approved':
                    continue
                if t['status'] == 'flagged' and t['id'] != rid and not root_flagged:
                    continue
                self.db.execute("UPDATE rules SET status='approved', status_at=?, approved_text=?, is_new=0, "
                                "flag_note=NULL, flagged_at=NULL WHERE id=?", (now, t['text'], t['id']))
                self.event(t['id'], 'approved', dict(self._prev(t), cascade=t['id'] != rid), batch=batch)
                n += 1
        return {'batch': batch, 'count': n}

    def flag(self, rid, note='', batch=None):
        """Approve, but flag for later tuning (with an optional note). On a flagged rule it just updates the note."""
        batch = batch or uuid.uuid4().hex[:12]
        r = self.rule(rid)
        if not r or not r['present']:
            raise ValueError('That rule is not in the file any more.')
        note = (note or '').strip() or None
        with self.tx():
            now = now_iso()
            if r['status'] == 'flagged':
                self.db.execute('UPDATE rules SET flag_note=? WHERE id=?', (note, rid))
                self.event(rid, 'flag_note', {'note': note, 'prev_note': r['flag_note']}, batch=batch)
                return {'batch': batch, 'count': 1, 'updated': True}
            targets = [r] + [d for d in self.descendants(rid) if d['status'] in ('pending', 'changed')]
            for t in targets:
                self.db.execute("UPDATE rules SET status='flagged', status_at=?, flagged_at=?, flag_note=?, "
                                "approved_text=?, is_new=0 WHERE id=?",
                                (now, now, note if t['id'] == rid else None, t['text'], t['id']))
                self.event(t['id'], 'flagged', dict(self._prev(t), note=note, cascade=t['id'] != rid), batch=batch)
        return {'batch': batch, 'count': len(targets)}

    def approve_section(self, file, path):
        batch = uuid.uuid4().hex[:12]
        n = 0
        for r in self.rules():
            if r['present'] and r['file'] == file and r['section'][:len(path)] == path and \
                    r['status'] in ('pending', 'changed'):
                n += self.approve(r['id'], cascade=False, batch=batch)['count']
        return {'batch': batch, 'count': n}

    def unapprove(self, rid, batch=None):
        batch = batch or uuid.uuid4().hex[:12]
        r = self.rule(rid)
        with self.tx():
            self.db.execute("UPDATE rules SET status='pending', status_at=?, flag_note=NULL, flagged_at=NULL WHERE id=?",
                            (now_iso(), rid))
            self.event(rid, 'unapproved', self._prev(r), batch=batch)
        return {'batch': batch, 'count': 1}

    def deny(self, rid, note='', batch=None, how='denied', job_id=None):
        """Cut the rule (and its sub-rules) out of its file; keep everything in the removed list."""
        batch = batch or uuid.uuid4().hex[:12]
        with self.lock:
            r = self.rule(rid)
            if not r or not r['present']:
                raise ValueError('That rule is not in the file any more.')
            self.sync()
            r = self.rule(rid)
            if r['kind'] == 'frontmatter':
                raise ValueError("Frontmatter keys can't be removed; the skill needs them. Use Refine instead.")
            text = self.read_file(r['file'])
            nl, lines, _ = sp.split_text(text)
            own = '\n'.join(lines[r['line_start']:r['line_end']])
            if sp.text_hash(own) != r['hash']:
                self.sync(force=True)
                raise ValueError('The file changed under us; refreshed. Try again.')
            block = lines[r['line_start']:r['block_end']]
            kids = self.descendants(rid)
            items = sp.parse(text)['items']
            me = next(k for k, it in enumerate(items) if it['start'] == r['line_start'])
            prev_hash = next_hash = None
            sibs = [k for k in range(me) if items[k]['parent'] == items[me]['parent']
                    and items[k]['heading'] == items[me]['heading']]
            if sibs:
                prev_hash = 'after_block:' + items[sibs[-1]]['hash']
            elif items[me]['parent'] is not None:
                prev_hash = 'after_own:' + items[items[me]['parent']]['hash']
            for it in items[me + 1:]:
                if it['start'] >= r['block_end']:
                    next_hash = it['hash']
                    break
            new_text = sp.remove_lines(text, r['line_start'], r['block_end'])
            self.write_file(r['file'], new_text, 'deny')
            now = now_iso()
            with self.tx():
                for t in [r] + kids:
                    top = t['id'] == rid
                    self.db.execute("""UPDATE rules SET present=0, status_before_removal=status, status='denied',
                        status_at=?, removed_at=?, removed_how=?, removed_note=?, removed_block=?, removed_line=?,
                        removed_file_sha=?, anchor_prev=?, anchor_next=? WHERE id=?""",
                                    (now, now, how, note if top else '(removed with its parent rule)',
                                     '\n'.join(block) if top else t['text'], r['line_start'] if top else None,
                                     sha(new_text) if top else None, prev_hash if top else None,
                                     next_hash if top else None, t['id']))
                    self.event(t['id'], 'denied', {'note': note, 'prev': t['status'], 'with_parent': not top},
                               batch=batch, job_id=job_id)
            self.sync()
            return {'batch': batch, 'count': 1 + len(kids)}

    def restore(self, rid, batch=None):
        batch = batch or uuid.uuid4().hex[:12]
        with self.lock:
            r = self.rule(rid)
            if not r or r['present']:
                raise ValueError('That rule is already in the file.')
            if r['status'] == 'denied' and r['parent_id'] and r['removed_line'] is None:
                parent = self.rule(r['parent_id'])
                if parent and parent['status'] == 'denied' and parent['removed_at'] == r['removed_at']:
                    return self.restore(parent['id'], batch)
            self.sync()
            path = self.file_path(r['file'])
            text = self.read_file(r['file']) if os.path.exists(path) else ''
            block = sp.split_text(r['removed_block'] or r['text'])[1]
            parsed = sp.parse(text)
            at = None
            if r['removed_file_sha'] and r['removed_file_sha'] == sha(text) and r['removed_line'] is not None:
                at = r['removed_line']
            if at is None and r['anchor_prev']:
                mode, _, h = r['anchor_prev'].partition(':')
                hit = [it for it in parsed['items'] if it['hash'] == h]
                if hit:
                    at = hit[0]['block_end'] if mode == 'after_block' else hit[0]['end']
            if at is None and r['anchor_next']:
                hit = [it for it in parsed['items'] if it['hash'] == r['anchor_next']]
                if hit:
                    at = hit[0]['start']
            if at is None:
                end = sp.section_end(parsed, json.loads(r['section'] or '[]'))
                if end is not None:
                    _, lines, _ = sp.split_text(text)
                    while end > 0 and (not lines[end - 1].strip() or sp.HRULE.match(lines[end - 1])):
                        end -= 1
                    at = end
            if at is None:
                at = parsed['line_count']
            spaced = r['kind'] in ('para', 'code')
            new_text = sp.insert_lines(text, at, block, spaced=spaced)
            group = [r]
            if r['status'] == 'denied':
                group += [d for d in self.descendants(rid, present_only=False)
                          if d['status'] == 'denied' and d['removed_at'] == r['removed_at']]
            self.restoring = {g['id'] for g in group}
            try:
                self.write_file(r['file'], new_text, 'restore')
                with self.tx():
                    for g in group:
                        self.event(g['id'], 'restored', {'was': g['removed_how']}, batch=batch)
                self.sync(force=True)
            finally:
                self.restoring = set()
            back = self.rule(rid)
            if not back['present']:
                raise ValueError('Put the text back, but could not match it again. Check the file.')
            return {'batch': batch, 'count': len(group)}

    def undo(self, batch):
        evs = self.q('SELECT * FROM events WHERE batch=? ORDER BY id DESC', (batch,))
        if not evs:
            raise ValueError('Nothing to undo.')
        done = 0
        restored = set()
        for e in evs:
            d = json.loads(e['detail'] or '{}')
            r = self.rule(e['rule_id'])
            if not r:
                continue
            if e['type'] in ('approved', 'flagged', 'unapproved'):
                prev = d.get('prev') or ('approved' if e['type'] == 'unapproved' else 'pending')
                with self.tx():
                    self.db.execute('UPDATE rules SET status=?, status_at=?, approved_text=?, flag_note=?, flagged_at=? '
                                    'WHERE id=?', (prev, now_iso(), d.get('prev_approved', r['approved_text']),
                                                   d.get('prev_note'), d.get('prev_flagged_at'), r['id']))
                    self.event(r['id'], 'undo', {'undid': e['type']})
                done += 1
            elif e['type'] == 'flag_note':
                with self.tx():
                    self.db.execute('UPDATE rules SET flag_note=? WHERE id=?', (d.get('prev_note'), r['id']))
                    self.event(r['id'], 'undo', {'undid': 'flag_note'})
                done += 1
            elif e['type'] == 'denied' and not d.get('with_parent') and not r['present'] and r['id'] not in restored:
                self.restore(r['id'])
                restored.add(r['id'])
                done += 1
        return {'count': done}


# ---------- snapshots for agent jobs (a whole skills folder, since an agent may touch several skills) ----------
def _hash_file(p):
    with open(p, 'rb') as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def _read(path):
    with open(path, 'r', encoding='utf-8', errors='replace', newline='') as fh:
        return fh.read()


def snapshot(root, dest):
    """Copy every .md under root so an agent's changes can be diffed and reverted."""
    manifest = {}
    for p in glob.glob(os.path.join(root, '**', '*.md'), recursive=True):
        rel = os.path.relpath(p, root)
        out = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(p, out)
        manifest[rel] = _hash_file(p)
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, '_manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump({'root': root, 'files': manifest}, fh)
    return dest


def snapshot_diff(dest):
    with open(os.path.join(dest, '_manifest.json'), encoding='utf-8') as fh:
        m = json.load(fh)
    root, before = m['root'], m['files']
    after = {os.path.relpath(p, root): _hash_file(p)
             for p in glob.glob(os.path.join(root, '**', '*.md'), recursive=True)}
    changes = []
    for rel in sorted(set(before) | set(after)):
        if before.get(rel) == after.get(rel):
            continue
        old = _read(os.path.join(dest, rel)) if rel in before else ''
        new = _read(os.path.join(root, rel)) if rel in after else ''
        name = rel.replace('\\', '/')
        diff = ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), 'before/' + name,
                                            'after/' + name, n=2))
        changes.append({'file': name, 'kind': 'created' if rel not in before else 'deleted' if rel not in after
                        else 'modified', 'diff': diff[:60000], 'after_sha': after.get(rel)})
    return root, changes


def revert_snapshot(dest, changes):
    with open(os.path.join(dest, '_manifest.json'), encoding='utf-8') as fh:
        root = json.load(fh)['root']
    problems = []
    for c in changes:
        live = os.path.join(root, c['file'].replace('/', os.sep))
        cur = _hash_file(live) if os.path.exists(live) else None
        if cur != c.get('after_sha'):
            problems.append(c['file'])
    if problems:
        raise ValueError('These files changed again after the agent finished, so they were left alone: '
                         + ', '.join(problems))
    for c in changes:
        rel = c['file'].replace('/', os.sep)
        live = os.path.join(root, rel)
        if c['kind'] == 'created':
            os.remove(live)
            d = os.path.dirname(live)
            while os.path.normpath(d) != os.path.normpath(root) and os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                d = os.path.dirname(d)
        else:
            os.makedirs(os.path.dirname(live), exist_ok=True)
            shutil.copy2(os.path.join(dest, rel), live)
    return root
