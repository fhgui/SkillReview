"""Trace rules back to the Claude Code sessions that wrote them.

Claude Code keeps every session as JSONL under ~/.claude/projects/<project>/<session>.jsonl (subagents under
<session>/subagents/). This module indexes every Edit / Write / MultiEdit of a .md file found there, then
matches a rule's text against the text those edits wrote. The earliest match is where the rule first
appeared; later ones show where it was copied or reworded. For any edit it can pull the conversation that led
up to it: the owner's messages and the agent's replies just before the edit.
"""
import difflib
import glob
import json
import math
import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict

import skillparse as sp

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INT, mtime REAL, offset INT, lineno INT);
CREATE TABLE IF NOT EXISTS edits(
  id INTEGER PRIMARY KEY, tool_use_id TEXT UNIQUE, path TEXT, lineno INT, offset INT, session_id TEXT,
  agent_id TEXT, ts TEXT, cwd TEXT, file_path TEXT, file_norm TEXT, tool TEXT, old_text TEXT, new_text TEXT,
  failed INT DEFAULT 0);
CREATE INDEX IF NOT EXISTS edits_file ON edits(file_norm, ts);
CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY, title TEXT, first_prompt TEXT, project TEXT,
  cwd TEXT, path TEXT, first_ts TEXT);
CREATE TABLE IF NOT EXISTS agents(path TEXT PRIMARY KEY, session_id TEXT, agent_id TEXT, description TEXT,
  agent_type TEXT, prompt TEXT, tool_use_id TEXT, workflow TEXT);
"""

NOISE_PREFIXES = ('<command-', '<local-command', '<task-notification', '<system-reminder', '[SYSTEM NOTIFICATION',
                  'Caveat:', '<bash-', '<user-prompt-submit-hook', '<ide_', '[Request interrupted')
TAG_BLOCK = re.compile(r'<(system-reminder|ide_selection|ide_opened_file)>.*?</\1>', re.S)
WORD = sp.WORD


def norm_path(p):
    return (p or '').replace('\\', '/').lower()


def toks(n, min_len=3):
    return {w for w in WORD.findall(n) if len(w) >= min_len and w not in sp.STOP}


def owner_text(d):
    """The owner's own words in a user record, or None for tool results, reminders and other noise."""
    if d.get('type') != 'user' or d.get('isMeta') or d.get('isCompactSummary'):
        return None
    c = (d.get('message') or {}).get('content')
    if isinstance(c, str):
        parts = [c]
    elif isinstance(c, list):
        parts = [p.get('text', '') for p in c if isinstance(p, dict) and p.get('type') == 'text']
    else:
        return None
    t = TAG_BLOCK.sub('', '\n'.join(parts)).strip()
    if not t or t.startswith(NOISE_PREFIXES):
        return None
    return t


def tool_summary(c):
    inp = c.get('input') or {}
    name = c.get('name', '?')
    if 'file_path' in inp:
        return f"{name} {os.path.basename(inp['file_path'])}"
    if name in ('Bash', 'PowerShell'):
        return f"{name}: {inp.get('description') or (inp.get('command') or '')[:90]}"
    if name == 'Agent':
        return f"Agent: {inp.get('description', '')}"
    if name in ('Grep', 'Glob'):
        return f"{name} {inp.get('pattern', '')[:60]}"
    return name


def reverse_lines(path, end_offset, max_bytes=48 << 20):
    """Yield complete lines ending before end_offset, newest first."""
    with open(path, 'rb') as fh:
        pos, buf, done = end_offset, b'', 0
        while pos > 0 and done < max_bytes:
            size = min(1 << 20, pos)
            pos -= size
            fh.seek(pos)
            buf = fh.read(size) + buf
            done += size
            parts = buf.split(b'\n')
            buf = parts[0]
            for line in reversed(parts[1:]):
                if line.strip():
                    yield line
        if pos == 0 and buf.strip():
            yield buf


def find_bytes(path, needle):
    with open(path, 'rb') as fh:
        base, tail = 0, b''
        while True:
            chunk = fh.read(8 << 20)
            if not chunk:
                return None
            data = tail + chunk
            i = data.find(needle)
            if i >= 0:
                return base - len(tail) + i
            tail = data[-len(needle):]
            base += len(chunk)


class Provenance:
    def __init__(self, data_dir, projects_dir, skills_root):
        self.projects_dir = projects_dir
        self.skills_root = skills_root
        self.db = sqlite3.connect(os.path.join(data_dir, 'history.db'), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.index_lock = threading.Lock()
        self.chunk_text, self.chunk_edits, self.inv = [], [], {}
        self.edit_meta = {}
        self.built_for = -1
        self.edit_count = 0
        self.last_refresh = 0
        self.status = 'not indexed yet'

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    # ---------- indexing ----------
    def refresh(self, min_interval=20):
        with self.index_lock:
            if time.time() - self.last_refresh < min_interval and self.built_for >= 0:
                return False
            t0 = time.time()
            known = {r['path']: r for r in self.q('SELECT * FROM files')}
            added = 0
            for path in glob.glob(os.path.join(self.projects_dir, '**', '*.jsonl'), recursive=True):
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                rec = known.get(path)
                if rec and rec['size'] == st.st_size and rec['mtime'] == st.st_mtime:
                    continue
                if rec and st.st_size >= rec['size']:
                    offset, lineno = rec['offset'], rec['lineno']
                else:
                    offset, lineno = 0, 0
                    with self.lock:
                        self.db.execute('DELETE FROM edits WHERE path=?', (path,))
                try:
                    offset, lineno, n = self._scan(path, offset, lineno)
                except OSError:
                    continue
                added += n
                with self.lock:
                    self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)',
                                    (path, st.st_size, st.st_mtime, offset, lineno))
            self.last_refresh = time.time()
            total = self.q('SELECT COUNT(*) n FROM edits')[0]['n']
            if total != self.built_for:
                self._build()
            self.status = f'{total} markdown edits indexed ({round(time.time() - t0, 1)} s, +{added})'
            return added > 0

    def _scan(self, path, offset, lineno):
        rel = os.path.relpath(path, self.projects_dir)
        project = rel.split(os.sep)[0]
        is_agent = os.sep + 'subagents' + os.sep in path
        found = 0
        edits, failed, titles = [], [], {}
        session_seen = set(r['session_id'] for r in self.q('SELECT session_id FROM sessions WHERE path=?', (path,)))
        need_prompt = not is_agent and not session_seen
        agent_row = None
        if is_agent and offset == 0:
            agent_row = {'path': path, 'session_id': None, 'agent_id': None, 'description': None,
                         'agent_type': None, 'prompt': None, 'tool_use_id': None, 'workflow': None}
            meta = path[:-6] + '.meta.json'
            if os.path.exists(meta):
                try:
                    with open(meta, encoding='utf-8') as fh:
                        m = json.load(fh)
                    agent_row.update(description=m.get('description'), agent_type=m.get('agentType'),
                                     tool_use_id=m.get('toolUseId'))
                except Exception:
                    pass
            wf = re.search(r'[\\/]workflows[\\/](wf_[^\\/]+)[\\/]', path)
            if wf:
                agent_row['workflow'] = wf.group(1)
        with open(path, 'rb') as fh:
            fh.seek(offset)
            for line in fh:
                if not line.endswith(b'\n'):
                    break
                cur_off, cur_line = offset, lineno
                offset += len(line)
                lineno += 1
                want_edit = b'"tool_use"' in line and b'.md' in line and (
                    b'"Edit"' in line or b'"Write"' in line or b'"MultiEdit"' in line)
                want_err = b'"is_error":true' in line and b'"tool_result"' in line
                want_title = b'"custom-title"' in line
                want_first = (need_prompt and b'"type":"user"' in line) or (agent_row and not agent_row['prompt']
                                                                           and b'"type":"user"' in line)
                if not (want_edit or want_err or want_title or want_first):
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if want_title and d.get('type') == 'custom-title':
                    titles[d.get('sessionId')] = d.get('customTitle')
                    continue
                if want_first:
                    txt = owner_text(d) if not agent_row else None
                    if agent_row and d.get('type') == 'user':
                        c = (d.get('message') or {}).get('content')
                        txt = c if isinstance(c, str) else ' '.join(
                            p.get('text', '') for p in (c or []) if isinstance(p, dict) and p.get('type') == 'text')
                        agent_row.update(prompt=(txt or '')[:6000], session_id=d.get('sessionId'),
                                         agent_id=d.get('agentId'))
                    elif txt:
                        need_prompt = False
                        with self.lock:
                            self.db.execute('INSERT INTO sessions(session_id, first_prompt, project, cwd, path, '
                                            'first_ts) VALUES (?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET '
                                            'first_prompt=COALESCE(first_prompt, excluded.first_prompt), '
                                            'cwd=COALESCE(cwd, excluded.cwd), first_ts=COALESCE(first_ts, '
                                            'excluded.first_ts)',
                                            (d.get('sessionId'), txt[:2000], project, d.get('cwd'), path,
                                             d.get('timestamp')))
                if want_err and d.get('type') == 'user':
                    for c in (d.get('message') or {}).get('content') or []:
                        if isinstance(c, dict) and c.get('type') == 'tool_result' and c.get('is_error'):
                            failed.append(c.get('tool_use_id'))
                if want_edit and d.get('type') == 'assistant':
                    for c in (d.get('message') or {}).get('content') or []:
                        if not (isinstance(c, dict) and c.get('type') == 'tool_use'
                                and c.get('name') in ('Edit', 'Write', 'MultiEdit')):
                            continue
                        inp = c.get('input') or {}
                        fp = inp.get('file_path') or ''
                        fpn = norm_path(fp)
                        if not fpn.endswith('.md') or '/appdata/local/temp/' in fpn:
                            continue
                        if c['name'] == 'Write':
                            old, new = '', inp.get('content') or ''
                        elif c['name'] == 'Edit':
                            old, new = inp.get('old_string') or '', inp.get('new_string') or ''
                        else:
                            es = inp.get('edits') or []
                            old = '\n\n'.join(e.get('old_string') or '' for e in es)
                            new = '\n\n'.join(e.get('new_string') or '' for e in es)
                        edits.append((c.get('id'), path, cur_line, cur_off, d.get('sessionId'),
                                      d.get('agentId') if d.get('isSidechain') else None, d.get('timestamp'),
                                      d.get('cwd'), fp, fpn, c['name'], old, new))
        with self.lock:
            if edits:
                self.db.executemany('INSERT OR IGNORE INTO edits(tool_use_id, path, lineno, offset, session_id, '
                                    'agent_id, ts, cwd, file_path, file_norm, tool, old_text, new_text) '
                                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', edits)
                found = len(edits)
            for tid in failed:
                self.db.execute('UPDATE edits SET failed=1 WHERE tool_use_id=?', (tid,))
            for sid, title in titles.items():
                self.db.execute('INSERT OR IGNORE INTO sessions(session_id, project, path) VALUES (?,?,?)',
                                (sid, project, path))
                self.db.execute('UPDATE sessions SET title=? WHERE session_id=?', (title, sid))
            if agent_row:
                self.db.execute('INSERT OR REPLACE INTO agents VALUES (:path, :session_id, :agent_id, :description, '
                                ':agent_type, :prompt, :tool_use_id, :workflow)', agent_row)
        return offset, lineno, found

    def _build(self):
        rows = self.q('SELECT id, new_text FROM edits WHERE failed=0')
        text_idx = {}
        chunk_text, chunk_edits = [], []
        inv = defaultdict(list)
        for r in rows:
            for c in sp.chunks(r['new_text'] or ''):
                n = sp.norm(c).lower()
                if len(n) < 12:
                    continue
                ci = text_idx.get(n)
                if ci is None:
                    ci = text_idx[n] = len(chunk_text)
                    chunk_text.append(n)
                    chunk_edits.append([])
                    for t in toks(n, 2):
                        inv[t].append(ci)
                chunk_edits[ci].append(r['id'])
        self.chunk_text, self.chunk_edits, self.inv = chunk_text, chunk_edits, dict(inv)
        self.edit_meta = {r['id']: r for r in self.q(
            'SELECT id, tool_use_id, path, lineno, session_id, agent_id, ts, cwd, file_path, file_norm, tool '
            'FROM edits WHERE failed=0')}
        self.built_for = len(rows) + len(self.q('SELECT id FROM edits WHERE failed=1'))

    # ---------- matching ----------
    @staticmethod
    def _score(rn, cn):
        sm = difflib.SequenceMatcher(None, rn, cn, autojunk=False)
        ratio = sm.ratio()
        if len(cn) > len(rn) * 1.3:
            cov = sum(b.size for b in sm.get_matching_blocks() if b.size >= 4) / max(1, len(rn))
            return max(ratio, cov * 0.95)
        return ratio

    def _best_in(self, rn, text):
        best = 0.0
        for c in sp.chunks(text or ''):
            n = sp.norm(c).lower()
            if len(n) >= 12:
                best = max(best, self._score(rn, n))
        return best

    def match(self, text, threshold=0.55):
        """All indexed edits that wrote (a version of) this text, oldest first, each labelled."""
        if self.built_for < 0:
            self.refresh(min_interval=0)
        rn = sp.norm(text).lower()
        if len(rn) < 24 or re.match(r'^name: [\w-]+$', rn):
            return []
        rt = toks(rn)
        if len(rt) < 3:
            rt = toks(rn, 2)
        hits = Counter()
        for t in rt:
            lst = self.inv.get(t)
            if lst and len(lst) < 40000:
                hits.update(lst)
        need = max(1, math.ceil(0.5 * len(rt)))
        per_edit = {}
        for ci, h in hits.most_common(300):
            if h < need:
                break
            s = self._score(rn, self.chunk_text[ci])
            if s < threshold:
                continue
            for eid in self.chunk_edits[ci]:
                if s > per_edit.get(eid, 0):
                    per_edit[eid] = s
        if not per_edit:
            return []
        metas = sorted((self.edit_meta[e] for e in per_edit if e in self.edit_meta), key=lambda m: m['ts'] or '')
        olds = {r['id']: r['old_text'] for r in self.q(
            'SELECT id, old_text FROM edits WHERE id IN (%s)' % ','.join('?' * len(metas)), [m['id'] for m in metas])}
        out = []
        seen_files = {}
        first_added = None
        for m in metas:
            s = per_edit[m['id']]
            if m['tool'] == 'Write':
                before = seen_files.get(m['file_norm'], 0.0)
            else:
                before = self._best_in(rn, olds.get(m['id']))
            if before < threshold:
                kind = 'added'
            elif s > before + 0.02:
                kind = 'revised'
            else:
                kind = 'kept'
            seen_files[m['file_norm']] = max(seen_files.get(m['file_norm'], 0.0), s)
            if kind == 'added':
                if first_added is None:
                    first_added = m['id']
                    kind = 'origin'
                else:
                    kind = 'copied' if s >= 0.8 else 'added'
            out.append(dict(m, score=round(s, 3), before=round(before, 3), kind=kind, exact=s >= 0.97))
        return out

    def summary(self, text, skill_dir):
        tl = self.match(text)
        if not tl:
            return {'n': 0}
        sdn = norm_path(skill_dir).rstrip('/') + '/'
        origin = next((e for e in tl if e['kind'] == 'origin'), tl[0])
        added = next((e for e in tl if e['file_norm'].startswith(sdn) and e['kind'] != 'kept'), None)
        changed = [e for e in tl if e['kind'] == 'revised']

        def brief(e):
            return {'edit': e['id'], 'ts': e['ts'], 'file': os.path.basename(e['file_path']),
                    'session': e['session_id'], 'agent': e['agent_id']} if e else None
        return {'n': len(tl), 'origin': brief(origin), 'added': brief(added),
                'last_revised': brief(changed[-1]) if changed else None}

    # ---------- labels & context ----------
    def session(self, sid):
        rows = self.q('SELECT * FROM sessions WHERE session_id=?', (sid,))
        return rows[0] if rows else {'session_id': sid}

    def agent(self, path):
        rows = self.q('SELECT * FROM agents WHERE path=?', (path,))
        return rows[0] if rows else None

    def label(self, e):
        s = self.session(e['session_id'])
        title = s.get('title') or (s.get('first_prompt') or '')[:90] or e['session_id']
        out = {'session_title': title, 'session_id': e['session_id'], 'project': s.get('project'),
               'cwd': e.get('cwd'), 'transcript': e['path'], 'line': e['lineno'] + 1}
        if e.get('agent_id') or os.sep + 'subagents' + os.sep in e['path']:
            a = self.agent(e['path']) or {}
            out['agent'] = {'id': e.get('agent_id') or a.get('agent_id'), 'description': a.get('description'),
                            'type': a.get('agent_type'), 'workflow': a.get('workflow')}
        return out

    def edit(self, edit_id):
        rows = self.q('SELECT * FROM edits WHERE id=?', (edit_id,))
        return rows[0] if rows else None

    def context(self, edit_id, max_prompts=3, max_tools=10):
        e = self.edit(edit_id)
        if not e:
            return None
        is_agent = os.sep + 'subagents' + os.sep in e['path']
        msgs = self._before(e['path'], e['offset'], max_prompts, max_tools, is_agent)
        out = {'edit': {k: e[k] for k in ('id', 'ts', 'tool', 'file_path', 'old_text', 'new_text')},
               'label': self.label(e), 'messages': msgs}
        if is_agent:
            a = self.agent(e['path']) or {}
            out['agent_prompt'] = (a.get('prompt') or '')[:4000]
            parent = os.path.join(self.projects_dir, os.path.relpath(e['path'], self.projects_dir).split(os.sep)[0],
                                  e['session_id'] + '.jsonl')
            needle = (a.get('tool_use_id') or a.get('workflow') or '').encode()
            if needle and os.path.exists(parent):
                pos = find_bytes(parent, b'"' + needle + b'"') if a.get('tool_use_id') else find_bytes(parent, needle)
                if pos:
                    out['parent_messages'] = self._before(parent, pos, 2, 0, False)
        return out

    def _before(self, path, offset, max_prompts, max_tools, is_agent):
        msgs, prompts, tools = [], 0, 0
        for line in reverse_lines(path, offset):
            if b'"type":"user"' not in line and b'"type":"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get('type') == 'user':
                t = owner_text(d)
                if t:
                    msgs.append({'role': 'lead agent' if is_agent else 'owner', 'text': t[:3000], 'ts': d.get('timestamp')})
                    prompts += 1
                    if prompts >= max_prompts:
                        break
            else:
                for c in reversed((d.get('message') or {}).get('content') or []):
                    if not isinstance(c, dict):
                        continue
                    if c.get('type') == 'text' and c.get('text', '').strip():
                        msgs.append({'role': 'agent', 'text': c['text'][:1500], 'ts': d.get('timestamp')})
                    elif c.get('type') == 'tool_use' and tools < max_tools and prompts == 0:
                        msgs.append({'role': 'tool', 'text': tool_summary(c), 'ts': d.get('timestamp')})
                        tools += 1
        msgs.reverse()
        # keep the owner's words, the agent's first reply to each, and its last few messages before the edit
        agent_idx = [k for k, m in enumerate(msgs) if m['role'] == 'agent']
        keep = set(agent_idx[-4:])
        for k, m in enumerate(msgs):
            if m['role'] in ('owner', 'lead agent'):
                nxt = next((j for j in agent_idx if j > k), None)
                if nxt is not None:
                    keep.add(nxt)
        return [m for k, m in enumerate(msgs) if m['role'] != 'agent' or k in keep]
