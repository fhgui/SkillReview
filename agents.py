"""Hand the owner's questions and change requests to headless Claude Code agents.

Each request is routed to one of five roles. Every role runs `claude -p` with its own tools and permissions,
streams its progress back into the app, and keeps its session id so the owner can reply and continue the same
conversation. Roles that may edit files run with cwd = the skills folder the skill lives in (e.g. ~/.claude/skills)
and acceptEdits, so they can only write there; the app snapshots that folder first so any change can be diffed
and reverted.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback

import skillparse as sp
from provenance import tool_summary
import edits as edit_ops
from store import now_iso, revert_snapshot, snapshot, snapshot_diff

ROUTES = {
    'historian': {
        'label': 'Where did it come from?',
        'desc': 'Traces the rule back through past Claude sessions: who added it, when, and why.',
        'edits': False,
    },
    'origin': {
        'label': 'Ask the agent that wrote it',
        'desc': "Talks to a stand-in for the session that wrote the rule, loaded with that session's conversation.",
        'edits': False,
    },
    'editor': {
        'label': 'Refine this rule',
        'desc': 'Rewrites, clarifies, splits or merges the rule inside this skill.',
        'edits': True,
    },
    'architect': {
        'label': 'Move to another skill',
        'desc': 'Moves or copies the rule into another skill, creating a new skill if none fits.',
        'edits': True,
    },
    'advisor': {
        'label': 'Just ask',
        'desc': 'Answers a question about the rule without changing anything.',
        'edits': False,
    },
}

COMMON_SYSTEM = """You are running headless inside the owner's "Skill Review" app, not in a chat window. The owner is going \
through one of their Claude Code skills rule by rule (approve / deny / ask) and sent you a request about one rule. They read \
only your final message, in the app. Keep it short, plain and concrete; lead with the answer. You can't ask questions mid-run: \
if something is ambiguous, make the most reasonable choice and say what you assumed. The owner can reply to continue."""

ROLE_SYSTEM = {
    'historian': """Your job: trace where a rule came from. Claude Code session transcripts are JSONL files under {projects} \
(one file per session; subagents in <session>/subagents/). In them, "type":"user" records with plain string content are \
the owner's own messages; "type":"assistant" records hold the agent's text and tool calls (Edit/Write inputs show exactly \
what was written). The app has already found the edits that match the rule; start from those and Read around the given \
line numbers only when you need more. Grep the transcripts only if the evidence is thin. Answer with: when it was first \
written and by which session (title) or subagent, what the owner said or what went wrong that led to it (quote the owner \
briefly), and how it changed since. Under about 200 words, with dates. If the transcripts don't show a reason, say so \
instead of guessing. Don't modify any files. Transcripts are huge (often 100+ MB): never Read a whole one. Read with \
offset/limit around a line number (limit 40 or less), and Grep with a pattern specific enough to return a few lines.""",
    'origin': """You stand in for the Claude agent that wrote a rule during an earlier session, which has ended. The \
conversation that led to the edit is included in the message, and you may Read or Grep that session's transcript for more. \
Answer as that agent would: what you were responding to and why you worded the rule the way you did. Quote the owner's \
words when they were the trigger. If the transcript doesn't show a reason, say so plainly rather than inventing one. \
Don't modify any files.""",
    'editor': """Your job: refine one rule in the owner's skill, exactly as they ask. Skills live in {skills}.
- Change only what the owner asked for. Leave every other rule in the file exactly as it is.
- Match the file's voice: short imperative bullets with a bold lead-in, plain English, details as sub-bullets, lines \
wrapped at the same width as the surrounding text.
- Keep it one rule unless the owner asks to split or merge.
- If the owner is only asking a question, answer it and propose no edit.
- Start with 2-4 sentences: what you changed and why, quoting the new wording. Then the edit blocks.

{edit_format}""",
    'architect': """Your job: move (or copy) a rule into the right skill. Skills live in {skills}; each is a folder with a \
SKILL.md that starts with YAML frontmatter (`name`: the folder name, kebab-case; `description`: one paragraph saying what \
the skill covers and when to use it, written so it triggers at the right moments), then the rules.
- First list the existing skills and read their frontmatter. Reuse one that fits; otherwise create a new skill folder with \
a good name, a strong description and a short intro line.
- Write the rule in the target skill's voice.
- Unless the owner says to copy or keep it, remove the rule from the source file (an <edit> with an empty <new>).
- Start with a short summary: the target skill (path), whether it's new, and whether the rule was removed from the \
source. Then the edit blocks.

{edit_format}""",
    'advisor': """Your job: answer the owner's question about a rule in their skill. You may read the skill files and, for \
context, the owner's project folders. Don't modify any files; if the answer is "the rule should change", say how and \
suggest they use "Refine this rule".""",
}

ROUTER_PROMPT = """Pick who should handle the owner's request about a rule in one of their Claude Code skills.
historian: asks where, when, why or by whom the rule was added; its history or source.
origin: wants to ask or talk to the specific agent/session that wrote the rule about its reasoning.
editor: wants the rule's wording changed, clarified, shortened, split, merged, corrected, made stricter or looser.
architect: says the rule belongs in a different or new skill, or wants it moved, copied or extracted elsewhere.
advisor: any other question or discussion that shouldn't change files.

Rule: {rule}
Request: {message}

Reply with only JSON: {{"route": "<one of historian|origin|editor|architect|advisor>", "reason": "<under 15 words>"}}"""

AUTH_HINTS = ('authenticate', 'login', '/login', 'oauth', 'api key', '401', 'credit balance')


def heuristic_route(message):
    m = message.lower()
    if re.search(r'\b(ask|talk to|question)\b.*\b(agent|session|author|whoever)\b', m):
        return 'origin'
    if re.search(r'where did|where does|come from|came from|who (added|wrote)|why (was|is|did)|when was|origin|history|'
                 r'source of|added this', m):
        return 'historian'
    if re.search(r'new skill|another skill|different skill|separate skill|other skill|belongs? (in|to)|move (it|this)|'
                 r'extract|split (it )?out', m):
        return 'architect'
    if re.search(r'reword|rewrite|rephrase|shorten|clarify|simplif|change|should say|make it|split|merge|fix|'
                 r'stricter|looser|add |remove the|replace', m):
        return 'editor'
    return 'advisor'


def version_key(path):
    parts = re.findall(r'\d+', os.path.basename(os.path.dirname(path)))
    return tuple(int(p) for p in parts)


def find_claude(settings):
    p = (settings or {}).get('claude_path') or ''
    if p and os.path.exists(p):
        return p
    if p and shutil.which(p):
        return shutil.which(p)
    bundled = glob.glob(os.path.join(os.environ.get('APPDATA', ''), 'Claude', 'claude-code', '*', 'claude.exe'))
    if bundled:
        return max(bundled, key=version_key)
    return shutil.which('claude')


def command(exe):
    """A .py wrapper (handy for testing, or to add logging) runs under this Python."""
    return [sys.executable, exe] if exe.lower().endswith('.py') else [exe]


def clean_env():
    env = dict(os.environ)
    inside_host = 'CLAUDE_CODE_ENTRYPOINT' in env
    for k in list(env):
        if k.startswith('CLAUDE') or (inside_host and k == 'ANTHROPIC_BASE_URL'):
            env.pop(k)
    return env


def creationflags():
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def fmt_ts(ts):
    return (ts or '')[:16].replace('T', ' ') + ' UTC' if ts else ''


class Runner:
    """Runs agent requests. Jobs and conversations live in the database of the skill they're about."""

    def __init__(self, app, prov, projects_dir):
        self.app = app
        self.prov = prov
        self.projects_dir = projects_dir
        self.live = {}   # (skill id, job id) -> log lines while running
        self.procs = {}
        self.editing = {}  # skills folder -> number of editing agents running there (auto-sync waits for them)
        self.agent_status = {'ok': None, 'message': 'Not checked yet', 'checked_at': None, 'exe': None}

    # ---------- public ----------
    def submit(self, sid, rule_id, message, route='auto', thread_id=None, edit_id=None):
        store = self.app.store(sid)
        rule = store.rule(rule_id)
        if not rule:
            raise ValueError('Unknown rule.')
        if route != 'auto' and route not in ROUTES:
            raise ValueError('Unknown route.')
        now = now_iso()
        thread = store.q1('SELECT * FROM threads WHERE id=?', (thread_id,)) if thread_id else None
        if thread and route not in ('auto', thread['route']):
            thread = None  # a different agent starts a new conversation
        if thread and route == 'auto':
            route = thread['route']
        if not thread:
            thread_id = store.x('INSERT INTO threads(skill, rule_id, route, title, created_at, updated_at, edit_id) '
                                'VALUES (?,?,?,?,?,?,?)', (sid, rule_id, route if route != 'auto' else None,
                                                           message.strip()[:120], now, now, edit_id))
            thread = store.q1('SELECT * FROM threads WHERE id=?', (thread_id,))
        job_id = store.x('INSERT INTO jobs(thread_id, skill, rule_id, route, message, status, created_at, log) '
                         "VALUES (?,?,?,?,?,'queued',?,'[]')", (thread['id'], sid, rule_id, route, message, now))
        store.x('UPDATE threads SET updated_at=? WHERE id=?', (now, thread['id']))
        with store.tx():
            store.event(rule_id, 'asked', {'message': message[:500], 'route': route, 'thread': thread['id']},
                        job_id=job_id)
        self.live[(sid, job_id)] = []
        threading.Thread(target=self._run, args=(sid, job_id, edit_id), daemon=True).start()
        return {'job_id': job_id, 'thread_id': thread['id']}

    def running(self):
        return [{'skill': k[0], 'job_id': k[1]} for k in self.live]

    def cancel(self, sid, job_id):
        p = self.procs.get((sid, job_id))
        if p and p.poll() is None:
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True,
                           creationflags=creationflags())
            self._log(sid, job_id, 'note', 'Stopped by the owner.')
            return True
        return False

    def revert(self, sid, job_id):
        store = self.app.store(sid)
        job = store.q1('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not job or not job['snapshot'] or not job['changes']:
            raise ValueError('That job made no file changes.')
        if job['reverted_at']:
            raise ValueError('Already reverted.')
        changes = json.loads(job['changes'])
        root = revert_snapshot(job['snapshot'], changes)
        store.x('UPDATE jobs SET reverted_at=? WHERE id=?', (now_iso(), job_id))
        focus = job['rule_id'] if job['route'] == 'editor' else None
        self.app.after_agent_edit(root, changes, {'by': f'revert:{sid}:{job_id}', 'focus': focus, 'focus_skill': sid},
                                  open_new=False)
        with store.tx():
            store.event(job['rule_id'], 'reverted', {'job': job_id}, job_id=job_id)
        return {'reverted': len(changes)}

    def check(self, settings):
        exe = find_claude(settings)
        self.agent_status = {'ok': False, 'exe': exe, 'checked_at': now_iso(), 'message': ''}
        if not exe:
            self.agent_status['message'] = 'Claude Code CLI not found. Install it, or set its path in Settings.'
            return self.agent_status
        try:
            r = subprocess.run(command(exe) + ['-p', '--model', 'haiku', '--output-format', 'json', '--tools', '',
                                               '--no-session-persistence', '--strict-mcp-config'],
                               input='Reply with exactly: ok', capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=120, env=clean_env(), cwd=os.path.expanduser('~'),
                               creationflags=creationflags())
            try:
                j = json.loads(r.stdout)
            except Exception:
                j = {'is_error': True, 'result': (r.stdout or r.stderr or '').strip()[:400]}
            if j.get('is_error') or r.returncode:
                msg = j.get('result') or r.stderr.strip()[:400] or 'Claude exited with an error.'
                self.agent_status['message'] = msg
                self.agent_status['needs_login'] = any(h in msg.lower() for h in AUTH_HINTS)
            else:
                self.agent_status.update(ok=True, message='Connected')
        except subprocess.TimeoutExpired:
            self.agent_status['message'] = 'Claude did not answer within 2 minutes (often an expired login).'
            self.agent_status['needs_login'] = True
        except OSError as e:
            self.agent_status['message'] = str(e)
        return self.agent_status

    def job_view(self, sid, job):
        j = dict(job)
        key = (sid, job['id'])
        j['log'] = list(self.live[key]) if key in self.live else json.loads(job['log'] or '[]')
        j['changes'] = json.loads(job['changes']) if job['changes'] else []
        for c in j['changes']:
            c.pop('after_sha', None)
        return j

    # ---------- internals ----------
    def _log(self, sid, job_id, kind, text):
        self.live.setdefault((sid, job_id), []).append({'k': kind, 'text': text, 'ts': now_iso()})
        try:
            self.app.store(sid).version += 1
        except ValueError:
            pass

    def _run(self, sid, job_id, edit_id):
        store = self.app.store(sid)
        job = store.q1('SELECT * FROM jobs WHERE id=?', (job_id,))
        settings = self.app.registry.settings()
        root = os.path.dirname(store.dir)  # the skills folder this skill lives in; editing agents work here

        def setj(**fields):
            sets = ','.join(f'{k}=?' for k in fields)
            store.x(f'UPDATE jobs SET {sets} WHERE id=?', list(fields.values()) + [job_id])

        def log(kind, text):
            self._log(sid, job_id, kind, text)
        snap, released = None, False
        try:
            setj(status='running', started_at=now_iso())
            rule = store.rule(job['rule_id'])
            thread = store.q1('SELECT * FROM threads WHERE id=?', (job['thread_id'],))
            route = job['route']
            if route == 'auto':
                route, reason = self._route(job['message'], rule, settings)
                setj(route=route, route_reason=reason)
                store.x('UPDATE threads SET route=? WHERE id=? AND route IS NULL', (route, thread['id']))
                log('route', f"{ROUTES[route]['label']} — {reason}")
            exe = find_claude(settings)
            if not exe:
                raise RuntimeError('Claude Code CLI not found. Install it, or set its path in Settings.')
            resume = thread['session_id'] if thread and thread['session_id'] and thread['route'] == route else None
            message = job['message'] if resume else self._compose(store, route, rule, job['message'],
                                                                  edit_id or (thread or {}).get('edit_id'))
            model = settings['models'].get(route, 'sonnet')
            args = command(exe) + ['-p', '--output-format', 'stream-json', '--verbose', '--model', model,
                                   '--strict-mcp-config', '--append-system-prompt', self._system(route, root)]
            budget = settings.get('max_budget_usd')
            if budget:
                args += ['--max-budget-usd', str(budget)]
            # every role only reads; editing roles write their changes into their answer and the app applies them
            # (Claude Code refuses headless edits under ~/.claude, see edits.py)
            args += ['--tools', 'Read,Glob,Grep', '--permission-mode', 'dontAsk', '--allowedTools', 'Read,Glob,Grep']
            if not ROUTES[route]['edits']:
                dirs = [self.projects_dir] + [d for d in settings.get('read_dirs') or [] if os.path.isdir(d)]
                args += ['--add-dir'] + dirs
            if resume:
                args += ['--resume', resume]
            if ROUTES[route]['edits']:
                snap = snapshot(root, os.path.join(store.backup_dir, 'jobs', f'job-{job_id}'))
                setj(snapshot=snap)
                edit_key = os.path.normcase(os.path.normpath(root))
                self.editing[edit_key] = self.editing.get(edit_key, 0) + 1
            setj(model=model, cwd=root)
            log('start', f"{ROUTES[route]['label']} · {model}" + (' · continuing' if resume else ''))
            p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 cwd=root, env=clean_env(), text=True, encoding='utf-8', errors='replace',
                                 creationflags=creationflags())
            self.procs[(sid, job_id)] = p
            err_chunks = []
            threading.Thread(target=lambda: err_chunks.append(p.stderr.read()), daemon=True).start()
            p.stdin.write(message)
            p.stdin.close()
            result, cost, session_id, is_error = None, None, None, False
            for raw in p.stdout:
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                t = d.get('type')
                if t == 'system' and d.get('subtype') == 'init':
                    session_id = d.get('session_id') or session_id
                    if session_id:
                        setj(session_id=session_id)
                elif t == 'assistant':
                    for c in (d.get('message') or {}).get('content') or []:
                        if c.get('type') == 'text' and c.get('text', '').strip():
                            log('text', c['text'])
                        elif c.get('type') == 'tool_use':
                            log('tool', tool_summary(c))
                elif t == 'user':
                    for c in (d.get('message') or {}).get('content') or []:
                        if isinstance(c, dict) and c.get('type') == 'tool_result' and c.get('is_error'):
                            body = c.get('content')
                            if isinstance(body, list):
                                body = ' '.join(x.get('text', '') for x in body if isinstance(x, dict))
                            log('tool_error', str(body)[:400])
                elif t == 'result':
                    result = d.get('result')
                    cost = d.get('total_cost_usd')
                    session_id = d.get('session_id') or session_id
                    is_error = bool(d.get('is_error')) or d.get('subtype', 'success') != 'success'
                    if is_error and not result:
                        result = f"Stopped: {d.get('subtype')}"
            p.wait()
            self.procs.pop((sid, job_id), None)
            stderr = ''.join(x for x in err_chunks if x).strip()
            changes = []
            if snap:
                if result and not is_error:
                    ops, message = edit_ops.parse(result)
                    if ops:
                        try:
                            written = edit_ops.apply(root, ops)
                            log('note', 'Applied the changes to ' + ', '.join(written))
                        except edit_ops.EditError as e:
                            log('tool_error', f'Could not apply the proposed change: {e}')
                            message += f'\n\n**Not applied:** {e}. Reply to ask for a corrected change.'
                    result = message
                _, changes = snapshot_diff(snap)
                self._done_editing(root)
                released = True
                if changes:
                    self.app.after_agent_edit(root, changes, {'job_id': job_id, 'by': f'job:{sid}:{job_id}', 'focus': job['rule_id'] if route == 'editor' else None, 'focus_skill': sid})
            if session_id and thread:
                store.x('UPDATE threads SET session_id=?, route=COALESCE(route, ?), updated_at=? WHERE id=?',
                        (session_id, route, now_iso(), thread['id']))
            status, error = 'done', None
            if result is None:
                status, error = 'error', (stderr[-1500:] or f'Claude exited with code {p.returncode}.')
            elif is_error:
                status, error = 'error', result
            if error and any(h in error.lower() for h in AUTH_HINTS):
                self.agent_status.update(ok=False, needs_login=True, message=error[:400], checked_at=now_iso())
            elif status == 'done':
                self.agent_status.update(ok=True, needs_login=False, message='Connected', checked_at=now_iso())
            setj(status=status, result=result, error=error, cost=cost, finished_at=now_iso(),
                 changes=json.dumps(changes) if changes else None, log=json.dumps(self.live.get((sid, job_id), [])))
            with store.tx():
                store.event(job['rule_id'], 'answered' if status == 'done' else 'agent_error',
                            {'route': route, 'files_changed': len(changes), 'thread': job['thread_id']}, job_id=job_id)
        except Exception as e:
            log('error', str(e))
            setj(status='error', error=f'{e}\n{traceback.format_exc()[-1500:]}', finished_at=now_iso(),
                 log=json.dumps(self.live.get((sid, job_id), [])))
        finally:
            if snap and not released:
                self._done_editing(root)
            self.live.pop((sid, job_id), None)
            self.procs.pop((sid, job_id), None)
            store.version += 1

    def _done_editing(self, root):
        key = os.path.normcase(os.path.normpath(root))
        if key in self.editing:
            self.editing[key] -= 1
            if self.editing[key] <= 0:
                del self.editing[key]

    def is_editing(self, skill_dir):
        return os.path.normcase(os.path.dirname(os.path.normpath(skill_dir))) in self.editing

    def _route(self, message, rule, settings):
        guess = heuristic_route(message)
        exe = find_claude(settings)
        if not exe:
            return guess, 'matched keywords (Claude CLI not found)'
        prompt = ROUTER_PROMPT.format(rule=sp.norm(rule['text'])[:600], message=message[:1500])
        try:
            r = subprocess.run(command(exe) + ['-p', '--model', settings['models'].get('router', 'haiku'),
                                               '--output-format', 'json', '--tools', '', '--no-session-persistence',
                                               '--strict-mcp-config'],
                               input=prompt, capture_output=True, text=True, encoding='utf-8', errors='replace',
                               timeout=90, env=clean_env(), cwd=os.path.expanduser('~'), creationflags=creationflags())
            out = json.loads(r.stdout)
            if out.get('is_error'):
                raise RuntimeError(out.get('result'))
            m = re.search(r'\{.*\}', out.get('result') or '', re.S)
            pick = json.loads(m.group(0))
            if pick.get('route') in ROUTES:
                return pick['route'], (pick.get('reason') or '').strip() or 'picked by the router'
        except Exception:
            pass
        return guess, 'matched keywords'

    def _system(self, route, root):
        role = ROLE_SYSTEM[route].replace('{edit_format}', edit_ops.FORMAT_HELP)
        return COMMON_SYSTEM + '\n\n' + role.format(skills=root, projects=self.projects_dir)

    # ---------- prompt building ----------
    def rule_block(self, store, rule):
        path = os.path.join(store.dir, rule['file'].replace('/', os.sep))
        section = json.loads(rule['section'] or '[]')
        section = section[1:] or section
        status = {'pending': 'not reviewed yet', 'approved': 'approved by the owner',
                  'changed': 'approved earlier, but edited since', 'denied': 'denied (removed) by the owner',
                  'flagged': 'approved, but flagged by the owner as needing tuning',
                  'gone': 'removed from the file outside the review'}.get(rule['status'], rule['status'])
        if rule['present']:
            try:
                _, lines, _ = sp.split_text(store.read_file(rule['file']))
                body = '\n'.join(lines[rule['line_start']:rule['block_end']])
            except OSError:
                body = rule['text']
            where = f"{path}, lines {rule['line_start'] + 1}-{rule['block_end']}"
        else:
            body = rule['removed_block'] or rule['text']
            where = f"{path} (no longer in the file; removed {fmt_ts(rule['removed_at'])})"
        name = os.path.basename(store.dir)
        out = [f'Skill: {name} ({store.dir})', f'File: {where}']
        if section:
            out.append('Section: ' + ' > '.join(section))
        out.append(f'Owner review status: {status}')
        if rule['status'] == 'flagged' and rule.get('flag_note'):
            out.append(f"Owner's note on what needs tuning: {rule['flag_note']}")
        if rule['removed_note']:
            out.append(f"Owner's note when removing it: {rule['removed_note']}")
        out += ['The rule:', '<rule>', body, '</rule>']
        return '\n'.join(out)

    def _timeline(self, rule):
        try:
            self.prov.refresh()
            return self.prov.match(rule['text'])
        except Exception:
            return []

    def _ctx_text(self, edit, max_chars=6000):
        ctx = self.prov.context(edit['id'])
        if not ctx:
            return ''
        lab = ctx['label']
        lines = []
        if ctx.get('agent_prompt'):
            a = lab.get('agent') or {}
            lines.append(f"(This was a subagent: {a.get('description') or a.get('id')}. Its instructions began:)")
            lines.append('> ' + ctx['agent_prompt'][:1500].replace('\n', '\n> '))
            for m in ctx.get('parent_messages') or []:
                if m['role'] == 'owner':
                    lines.append(f"[owner, to the lead agent, {fmt_ts(m['ts'])}] {m['text'][:1200]}")
        for m in ctx['messages']:
            if m['role'] == 'tool':
                lines.append(f"[tool] {m['text']}")
            else:
                lines.append(f"[{m['role']}, {fmt_ts(m['ts'])}] {m['text'][:1200]}")
        return '\n'.join(lines)[-max_chars:]

    def _compose(self, store, route, rule, message, edit_id=None):
        block = self.rule_block(store, rule)
        root = os.path.dirname(store.dir)
        if route == 'historian':
            tl = [e for e in self._timeline(rule) if e['kind'] != 'kept']
            parts = [block, '']
            if tl:
                parts.append('Edits in past session transcripts that wrote this rule (oldest first):')
                for i, e in enumerate(tl[:15], 1):
                    lab = self.prov.label(e)
                    who = f"subagent \"{(lab.get('agent') or {}).get('description')}\"" if lab.get('agent') else 'main agent'
                    parts.append(f"{i}. {fmt_ts(e['ts'])} - {e['kind']} (match {e['score']}) - {e['tool']} "
                                 f"{e['file_path']} - session \"{lab['session_title']}\" ({e['session_id']}), {who}. "
                                 f"Transcript: {lab['transcript']} line {lab['line']}.")
                key = [tl[0]] + [e for e in tl[1:] if e['kind'] in ('copied', 'revised')][-2:]
                for e in key:
                    parts += ['', f"Conversation just before edit {tl.index(e) + 1} ({fmt_ts(e['ts'])}):",
                              self._ctx_text(e, 5000)]
            else:
                parts.append('The app found no Edit/Write in past transcripts that wrote this text. It may have been '
                             'written by a script or before the transcripts start; search the transcripts for its key '
                             'phrases.')
            parts += ['', f"The owner's question: {message}"]
            return '\n'.join(parts)
        if route == 'origin':
            e = None
            if edit_id:
                e = self.prov.edit(edit_id)
            if not e:
                tl = self._timeline(rule)
                sdn = store.dir.replace('\\', '/').lower() + '/'
                in_skill = [x for x in tl if x['file_norm'].startswith(sdn) and x['kind'] != 'kept']
                pick = in_skill[-1] if in_skill else None
                if pick and pick['tool'] == 'Write' and pick['kind'] == 'copied':
                    pick = tl[0]  # a bulk copy into the skill; the real author is where it was first written
                e = pick or (tl[0] if tl else None)
            if not e:
                return (block + "\n\nThe app couldn't find the session that wrote this rule in the transcripts. Say so, "
                        'then search the transcripts under ' + self.projects_dir + ' for its key phrases and report '
                        f'what you find.\n\nThe owner asks: {message}')
            full = self.prov.edit(e['id'])
            lab = self.prov.label(full)
            head = [f"Session: \"{lab['session_title']}\" ({full['session_id']}), {fmt_ts(full['ts'])}; working folder "
                    f"{full['cwd']}. Transcript: {lab['transcript']} (JSONL; the edit is on line {lab['line']}).",
                    '', 'What happened just before the edit (oldest first):', self._ctx_text(full, 9000), '',
                    f"The edit you made ({full['tool']} on {full['file_path']}):"]
            if full['old_text']:
                head += ['--- replaced this:', full['old_text'][:2500]]
            head += ['--- with this:', (full['new_text'] or '')[:3500], '', 'The rule as it reads in the skill now:',
                     block, '', f'The owner asks you: {message}']
            return '\n'.join(head)
        if route == 'editor':
            return f"{block}\n\nThe owner's request for this rule: {message}"
        if route == 'architect':
            siblings = sorted(os.path.basename(os.path.dirname(p))
                              for p in glob.glob(os.path.join(root, '*', 'SKILL.md')))
            return (f"{block}\n\nExisting skills in {root}: {', '.join(siblings)}.\n\n"
                    f"The owner's request: {message}")
        return f"{block}\n\nThe owner's question: {message}"
