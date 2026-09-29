'use strict';
/* Skill Review front end. Plain JS, no build step. The server is the source of truth; this file renders. */

const TOKEN = document.querySelector('meta[name=token]').content;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

// ---------------------------------------------------------------- preferences (per browser, optional)
const prefs = (() => {
  let p = {};
  try { p = JSON.parse(localStorage.getItem('skill-review') || '{}') || {}; } catch (e) { p = {}; }
  return {
    get: (k, d) => (k in p ? p[k] : d),
    set: (k, v) => { p[k] = v; try { localStorage.setItem('skill-review', JSON.stringify(p)); } catch (e) { /* private mode */ } },
  };
})();

// ---------------------------------------------------------------- api
async function api(path, body) {
  const opts = { method: body === undefined ? 'GET' : 'POST', headers: { 'X-Token': TOKEN } };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  let j = null;
  try { j = await r.json(); } catch (e) { /* empty */ }
  if (r.status === 403 && j && /token/i.test(j.error || '')) {
    // the app was restarted and issued a new token: reload once to pick it up
    let last = 0;
    try { last = +sessionStorage.getItem('sr-token-reload') || 0; } catch (e) { /* ignore */ }
    if (Date.now() - last > 10000) {
      try { sessionStorage.setItem('sr-token-reload', String(Date.now())); } catch (e) { /* ignore */ }
      location.reload();
    }
  }
  if (!r.ok) throw new Error((j && j.error) || `${r.status} ${r.statusText}`);
  return j;
}
/** Calls scoped to the skill that's showing; each skill has its own database on the server. */
function sapi(path, body) { return api(`/api/s/${encodeURIComponent(S.skill)}${path}`, body); }

// ---------------------------------------------------------------- icons
const ICON = {
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  ask: '<path d="M21 11.5a8.4 8.4 0 0 1-12.2 7.5L3 21l2-5.6A8.4 8.4 0 1 1 21 11.5z"/><path d="M9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2.3-2.5 2.3M12 15.5h.01"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
  restore: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  send: '<path d="M4 12l16-8-6 16-2.5-6.5z"/>',
  file: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5h13L22 12v6a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-6z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  flag: '<path d="M5 21V4"/><path d="M5 4h12l-2.5 4.5L17 13H5"/>',
};
const icon = (n, cls = '') => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICON[n]}</svg>`;

// ---------------------------------------------------------------- text helpers
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const base = (p) => String(p || '').split(/[\\/]/).pop();
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;

function inline(s) {
  const codes = [];
  s = String(s ?? '').replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = esc(s);
  s = s.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?![*\w])/g, '$1<em>$2</em>');
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) => (/^https?:/i.test(u)
    ? `<a href="${u}" target="_blank" rel="noopener">${t}</a>` : `<span title="${u}">${t}</span>`));
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
}

function paras(s) { return String(s || '').split(/\n\n+/).map((p) => `<p>${inline(p)}</p>`).join(''); }

function md(src) {
  const lines = String(src || '').replace(/\r/g, '').split('\n');
  let out = '';
  let i = 0;
  const isList = (l) => /^\s*([-*+]|\d+[.)])\s+/.test(l);
  const structural = (l) => /^\s*```/.test(l) || /^#{1,6}\s/.test(l) || /^\s*>/.test(l) || /^\s*\|/.test(l) || isList(l);
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if (/^\s*```/.test(l)) {
      const buf = []; i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++; out += `<pre class="code">${esc(buf.join('\n'))}</pre>`; continue;
    }
    if (/^#{1,6}\s/.test(l)) { out += `<h3>${inline(l.replace(/^#+\s*/, ''))}</h3>`; i++; continue; }
    if (/^\s*>/.test(l)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
      out += `<blockquote>${paras(buf.join('\n'))}</blockquote>`; continue;
    }
    if (/^\s*\|/.test(l)) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      let html = '<table>';
      rows.forEach((r, k) => {
        if (/^\s*\|?\s*:?-{2,}/.test(r)) return;
        const tag = k === 0 ? 'th' : 'td';
        html += '<tr>' + cells(r).map((c) => `<${tag}>${inline(c)}</${tag}>`).join('') + '</tr>';
      });
      out += html + '</table>'; continue;
    }
    if (isList(l)) {
      const items = [];
      while (i < lines.length && (isList(lines[i]) || (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length))) {
        const m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
        if (m) items.push({ ind: m[1].length, ord: /\d/.test(m[2]), text: m[3] });
        else items[items.length - 1].text += ' ' + lines[i].trim();
        i++;
      }
      const render = (start, ind) => {
        let html = items[start].ord ? '<ol>' : '<ul>';
        let k = start;
        while (k < items.length && items[k].ind >= ind) {
          if (items[k].ind > ind) { const [h, next] = render(k, items[k].ind); html = html.replace(/<\/li>$/, h + '</li>'); k = next; continue; }
          html += `<li>${inline(items[k].text)}</li>`; k++;
        }
        return [html + (items[start].ord ? '</ol>' : '</ul>'), k];
      };
      out += render(0, items[0].ind)[0]; continue;
    }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !structural(lines[i])) buf.push(lines[i++].trim());
    out += `<p>${inline(buf.join(' '))}</p>`;
  }
  return out;
}

function stripMarkers(t) { return String(t || '').split('\n').map((l) => l.replace(/^\s*(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?/, '').trim()).join(' ').replace(/\*\*/g, ''); }

function wdiff(a, b) {
  const A = stripMarkers(a).split(/(\s+)/); const B = stripMarkers(b).split(/(\s+)/);
  const n = A.length; const m = B.length;
  if (n * m > 400000) return `<del>${esc(A.join(''))}</del> <ins>${esc(B.join(''))}</ins>`;
  const dp = new Uint16Array((n + 1) * (m + 1));
  for (let x = n - 1; x >= 0; x--) for (let y = m - 1; y >= 0; y--) {
    dp[x * (m + 1) + y] = A[x] === B[y] ? dp[(x + 1) * (m + 1) + y + 1] + 1 : Math.max(dp[(x + 1) * (m + 1) + y], dp[x * (m + 1) + y + 1]);
  }
  let x = 0; let y = 0; let out = '';
  while (x < n && y < m) {
    if (A[x] === B[y]) { out += esc(A[x]); x++; y++; } else if (dp[(x + 1) * (m + 1) + y] >= dp[x * (m + 1) + y + 1]) { out += `<del>${esc(A[x])}</del>`; x++; } else { out += `<ins>${esc(B[y])}</ins>`; y++; }
  }
  while (x < n) out += `<del>${esc(A[x++])}</del>`;
  while (y < m) out += `<ins>${esc(B[y++])}</ins>`;
  return out;
}

function udiff(text) {
  return '<pre class="udiff">' + String(text || '').split('\n').map((l) => {
    const c = l.startsWith('+++') || l.startsWith('---') ? 'h' : l.startsWith('@@') ? 'h' : l.startsWith('+') ? 'a' : l.startsWith('-') ? 'd' : '';
    return `<span class="${c}">${esc(l) || ' '}</span>`;
  }).join('') + '</pre>';
}

function editDiff(oldT, newT) {
  const o = String(oldT || '').split('\n'); const n = String(newT || '').split('\n');
  let s = '';
  if (oldT) s += o.map((l) => '-' + l).join('\n') + '\n';
  s += n.map((l) => '+' + l).join('\n');
  return udiff(s);
}

// ---------------------------------------------------------------- dates
function when(iso) {
  if (!iso) return { main: '—', rel: '', full: '' };
  const d = new Date(iso); const now = new Date();
  if (isNaN(d)) return { main: iso, rel: '', full: iso };
  const t = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const day0 = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day0(now) - day0(d)) / 864e5);
  let main;
  if (days === 0) main = `Today, ${t}`;
  else if (days === 1) main = `Yesterday, ${t}`;
  else if (days > 1 && days < 7) main = `${d.toLocaleDateString(undefined, { weekday: 'long' })}, ${t}`;
  else if (d.getFullYear() === now.getFullYear()) main = `${d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}, ${t}`;
  else main = d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  const s = (now - d) / 1000;
  const unit = (v, u) => `${v} ${u}${v === 1 ? '' : 's'} ago`;
  const rel = s < 0 ? '' : s < 45 ? 'just now' : s < 3600 ? unit(Math.max(1, Math.round(s / 60)), 'minute')
    : s < 86400 ? unit(Math.round(s / 3600), 'hour') : s < 86400 * 45 ? unit(Math.round(s / 86400), 'day')
      : s < 86400 * 365 ? unit(Math.round(s / 86400 / 30), 'month') : unit(Math.round(s / 86400 / 365), 'year');
  const full = d.toLocaleString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' });
  return { main, rel, full };
}
const whenCell = (iso) => { const w = when(iso); return `<span class="when" title="${esc(w.full)}">${esc(w.main)}<span class="rel">${esc(w.rel)}</span></span>`; };
const whenText = (iso) => { const w = when(iso); return `<span title="${esc(w.full)}">${esc(w.main)}</span>`; };
function dayText(iso) {
  if (!iso) return '';
  const d = new Date(iso); const now = new Date();
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  const label = days === 0 ? 'today' : days === 1 ? 'yesterday'
    : d.toLocaleDateString(undefined, d.getFullYear() === now.getFullYear() ? { day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short', year: 'numeric' });
  return `<span title="${esc(when(iso).full)}">${esc(label)}</span>`;
}

// ---------------------------------------------------------------- state
const S = {
  boot: null, skill: null, data: null, v: -1,
  tab: prefs.get('tab', 'review'), filter: prefs.get('filter', 'todo'), search: '', file: 'all',
  focus: null, confirm: null, diffOpen: new Set(), sort: prefs.get('sort', {}),
  removedHow: 'all', eventGroup: 'all', listSearch: '',
  drawer: null, threads: null, events: null, running: [], agent: null, history: null, lastBatch: null,
  open: [], available: null, flagging: null,
};

const TABS = [
  ['review', 'Review'], ['approved', 'Approved'], ['flagged', 'Flagged'], ['removed', 'Removed'], ['questions', 'Questions'], ['activity', 'Activity'],
];
const ROUTE_ORDER = ['auto', 'historian', 'origin', 'editor', 'architect', 'advisor'];
const HOW = { denied: 'Denied by you', job: 'Removed by an agent', outside: 'Removed outside the app' };

// ---------------------------------------------------------------- data
function indexData() {
  const d = S.data;
  d.byId = new Map(d.rules.map((r) => [r.id, r]));
  d.kids = new Map();
  const fileOrder = (f) => d.files.indexOf(f) === -1 ? 999 : d.files.indexOf(f);
  d.present = d.rules.filter((r) => r.present).sort((a, b) => fileOrder(a.file) - fileOrder(b.file) || a.ord - b.ord);
  for (const r of d.present) if (r.parent_id) { if (!d.kids.has(r.parent_id)) d.kids.set(r.parent_id, []); d.kids.get(r.parent_id).push(r); }
  d.removed = d.rules.filter((r) => !r.present);
  const c = { todo: 0, changed: 0, new: 0, approved: 0, flagged: 0, all: d.present.length, pending: 0, edited: 0 };
  for (const r of d.present) {
    if (r.status === 'pending' || r.status === 'changed') c.todo++;
    if (r.status === 'changed') c.changed++;
    if (r.status === 'pending') c.pending++;
    if (r.status === 'approved') c.approved++;
    if (r.status === 'flagged') c.flagged++;
    if (r.is_new && r.status !== 'approved' && r.status !== 'flagged') c.new++;
    if (r.status === 'changed' || (r.status === 'pending' && r.changed_at)) c.edited++;
  }
  c.removed = d.removed.length;
  // the Flagged list shows a flagged block once (sub-rules flagged with it ride along), so count it the same way
  c.flaggedTop = d.present.filter((r) => r.status === 'flagged' && !(r.parent_id && (d.byId.get(r.parent_id) || {}).status === 'flagged')).length;
  d.counts = c;
  d.heads = new Map(d.headings.map((h) => [`${h.file}:${h.line}`, h]));
}

function descendants(id) {
  const out = []; const stack = [...(S.data.kids.get(id) || [])];
  while (stack.length) { const r = stack.shift(); out.push(r); stack.unshift(...(S.data.kids.get(r.id) || [])); }
  return out;
}

async function refresh() {
  if (!S.skill) { S.data = null; render(); return; }
  const want = S.skill;
  let data;
  try { data = await sapi('/state'); } catch (e) {
    if (/not open/.test(e.message)) { await pickSkill(null); return; }
    throw e;
  }
  if (S.skill !== want) return; // switched while loading
  S.data = data; S.v = data.version;
  if (S.file !== 'all' && !data.files.includes(S.file)) S.file = 'all';
  indexData();
  if (S.tab === 'questions') S.threads = await sapi('/threads');
  if (S.tab === 'activity') S.events = await sapi('/events');
  if (S.skill !== want) return;
  render();
}

// ---------------------------------------------------------------- skills (each open skill has its own database)
function skillName(id) { const s = S.open.find((x) => x.id === id); return s ? s.name + (s.source !== 'user' ? ` (${s.label})` : '') : id; }
function setHash() {
  const h = S.skill ? `#/${encodeURIComponent(S.skill)}/${S.tab}` : '#/';
  if (location.hash !== h) history.replaceState(null, '', h);
}
function parseHash() {
  const m = location.hash.match(/^#\/([^/]*)\/?(\w*)/);
  return m ? [decodeURIComponent(m[1]), m[2]] : [null, null];
}

async function switchSkill(id, tab) {
  if (tab && TABS.some(([k]) => k === tab)) { S.tab = tab; prefs.set('tab', tab); }
  if (id === S.skill && S.data) { setHash(); render(); return; }
  closeDrawer();
  Object.assign(S, { skill: id, data: null, focus: null, confirm: null, file: 'all', search: '', listSearch: '', threads: null, events: null, v: -1, diffOpen: new Set() });
  prefs.set('skill', id);
  setHash(); render();
  window.scrollTo(0, 0);
  await refresh();
}

async function pickSkill(preferred) {
  const ids = S.open.map((s) => s.id);
  const id = ids.includes(preferred) ? preferred : ids.includes(prefs.get('skill')) ? prefs.get('skill') : ids[0] || null;
  await switchSkill(id);
}

function renderSkillTabs() {
  const html = S.open.map((s) => `<div class="stab ${s.id === S.skill ? 'on' : ''}" data-act="switch-skill" data-skill="${esc(s.id)}" title="${esc(s.dir)}" role="tab" aria-selected="${s.id === S.skill}">
      <span class="nm">${esc(s.name)}</span>${s.source !== 'user' ? `<span class="src">${esc(s.label)}</span>` : ''}
      ${s.running ? '<span class="spin" style="width:11px;height:11px"></span>' : ''}
      ${s.missing ? '<span class="cnt warn" title="The folder or its SKILL.md is gone">missing</span>' : s.todo ? `<span class="cnt" title="${s.todo} to review">${s.todo}</span>` : `<span class="cnt done" title="Everything reviewed">${icon('check')}</span>`}
      <button class="x" data-act="close-skill" data-skill="${esc(s.id)}" title="Close this tab (your votes are kept)" aria-label="Close ${esc(s.name)}">${icon('close')}</button></div>`).join('');
  const el = $('#skillTabs');
  if (el.innerHTML !== html) {
    el.innerHTML = html;
    const on = $('.stab.on', el);
    if (on) on.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
}

function renderSkillInfo() {
  const el = $('#skillInfo');
  if (!S.data) { el.innerHTML = ''; return; }
  const files = S.data.files;
  el.innerHTML = `<span class="path" title="${esc(S.data.skill_dir)}">${esc(S.data.skill_dir)}</span>
    ${files.length > 1 ? `<select class="small" id="fileFilter" aria-label="File"><option value="all">All ${files.length} files</option>${files.map((f) => `<option ${S.file === f ? 'selected' : ''}>${esc(f)}</option>`).join('')}</select>` : ''}`;
}

// ---------------------------------------------------------------- top-level render
function render() {
  renderSkillTabs(); renderTabs(); renderAgent(); renderSkillInfo();
  setHash();
  const side = $('#side');
  if (!S.skill) {
    side.classList.add('hidden'); $('#layout').style.gridTemplateColumns = 'minmax(0,1fr)';
    $('#main').innerHTML = `<div class="empty">${icon('inbox')}<h3>No skill open</h3><p>Open a skill to start reviewing its rules. Each skill keeps its own votes.</p><button class="btn primary" data-act="add-skill">${icon('plus')}Add skill</button></div>`;
    return;
  }
  if (!S.data) { $('#main').innerHTML = '<div class="empty"><div class="spin" style="margin:0 auto"></div></div>'; return; }
  $('#layout').style.gridTemplateColumns = S.tab === 'review' ? '' : 'minmax(0,1fr)';
  side.classList.toggle('hidden', S.tab !== 'review');
  if (S.data.missing) {
    $('#main').innerHTML = `<div class="empty"><h3>Can't find this skill's files</h3><p>There's no SKILL.md in <code>${esc(S.data.skill_dir)}</code> any more. Your votes for it are kept; close this tab, or put the folder back.</p></div>`;
    return;
  }
  if (S.tab === 'review') { renderSide(); renderReview(); }
  else if (S.tab === 'approved') renderApproved();
  else if (S.tab === 'flagged') renderFlagged();
  else if (S.tab === 'removed') renderRemoved();
  else if (S.tab === 'questions') renderQuestions();
  else renderActivity();
}

function renderTabs() {
  const c = S.data ? S.data.counts : {};
  const cur = S.open.find((s) => s.id === S.skill);
  const running = cur ? cur.running : 0;
  const n = { review: c.todo, approved: c.approved, flagged: c.flaggedTop, removed: c.removed, questions: running ? `${running} running` : '' };
  $('#tabs').innerHTML = S.skill ? TABS.map(([k, label]) => `<button data-act="tab" data-tab="${k}" class="${S.tab === k ? 'on' : ''}">${label}${n[k] !== undefined && n[k] !== '' ? `<span class="n">${n[k]}</span>` : ''}</button>`).join('') : '';
}

// ---------------------------------------------------------------- add / close skills
async function addSkillModal() {
  modal(`<h3>Open a skill</h3>
    <label class="search" style="max-width:none">${icon('search')}<input id="skillSearch" type="search" placeholder="Search by name, description, project or folder" autocomplete="off"></label>
    <div id="skillList" class="avlist"><div class="working" style="padding:14px"><span class="spin"></span>Looking for skills on this computer…</div></div>
    <div class="field"><label for="skillPath">Or open any folder that has a SKILL.md</label>
      <div class="compose-row"><input id="skillPath" type="text" placeholder="C:\\path\\to\\my-skill" autocomplete="off" style="flex:1;border:1px solid var(--line-2);background:var(--panel-2);border-radius:8px;padding:6px 9px"><button class="btn" data-act="open-path">Open folder</button></div></div>
    <div class="foot"><button class="btn" data-act="close-modal">Close</button></div>`, 'wide');
  setTimeout(() => $('#skillSearch') && $('#skillSearch').focus(), 0);
  try { S.available = await api('/api/skills/available'); } catch (e) { S.available = []; toastErr(e); }
  renderAvailable();
}

function renderAvailable() {
  const box = $('#skillList'); if (!box) return;
  const q = ($('#skillSearch') ? $('#skillSearch').value : '').trim().toLowerCase();
  const list = (S.available || []).filter((s) => !q || `${s.name} ${s.description} ${s.label} ${s.dir}`.toLowerCase().includes(q));
  if (!list.length) { box.innerHTML = `<div class="muted" style="padding:14px">${q ? 'No skills match.' : 'No skills found.'} You can still open a folder below.</div>`; return; }
  let html = ''; let group = null;
  for (const s of list) {
    const g = s.label === s.source_label ? s.source_label : `${s.source_label} · ${s.label}`;
    if (g !== group) { html += `<div class="avgroup">${esc(g)}</div>`; group = g; }
    const tags = [];
    if (s.open_id) tags.push('<span class="tag ok">open</span>');
    else if (s.votes) tags.push(`<span class="tag plain">${plural(s.votes, 'vote')} saved</span>`);
    else if (s.has_data) tags.push('<span class="tag plain">opened before</span>');
    else if (!s.seen_before) tags.push('<span class="tag new">not reviewed yet</span>');
    if (s.source === 'plugin') tags.push('<span class="tag plain" title="Installed from a plugin; an update can overwrite your changes">plugin copy</span>');
    html += `<div class="av"><div class="av-main"><div class="av-name">${esc(s.name)} ${tags.join('')}</div>
      ${s.description ? `<div class="av-desc">${esc(s.description)}</div>` : ''}<div class="av-path">${esc(s.dir)}</div></div>
      ${s.open_id ? `<button class="btn sm" data-act="switch-skill" data-skill="${esc(s.open_id)}">Show</button>` : `<button class="btn sm primary" data-act="open-skill" data-dir="${esc(s.dir)}">Open</button>`}</div>`;
  }
  box.innerHTML = html;
}

async function openSkill(dir) {
  try {
    const res = await api('/api/skills/open', { dir });
    closeModal();
    await pollNow();
    await switchSkill(res.id);
    toast(`Opened ${skillName(res.id)}`);
  } catch (e) { toastErr(e); }
}

async function closeSkill(id) {
  try {
    await api('/api/skills/close', { id });
    const idx = S.open.findIndex((s) => s.id === id);
    S.open = S.open.filter((s) => s.id !== id);
    toast(`Closed ${id}. Its votes are kept; reopen it from Add skill.`);
    if (S.skill === id) await switchSkill((S.open[idx] || S.open[idx - 1] || {}).id || null);
    else renderSkillTabs();
  } catch (e) { toastErr(e); }
}

function renderAgent() {
  const a = S.agent || (S.boot && S.boot.agent) || {};
  const dot = $('#agentDot'); const txt = $('#agentText');
  dot.className = 'dot' + (S.running.length ? ' busy' : a.ok ? ' ok' : a.ok === false ? ' bad' : '');
  txt.textContent = S.running.length ? `${plural(S.running.length, 'agent')} working` : a.ok ? 'Agents ready' : a.ok === false ? (a.needs_login ? 'Log in needed' : 'Agents unavailable') : 'Checking agents…';
}

// ---------------------------------------------------------------- review
function fileOk(r) { return S.file === 'all' || r.file === S.file; }
function matchesFilter(r) {
  switch (S.filter) {
    case 'todo': return r.status === 'pending' || r.status === 'changed';
    case 'changed': return r.status === 'changed' || (r.status === 'pending' && !!r.changed_at);
    case 'new': return !!r.is_new && r.status !== 'approved' && r.status !== 'flagged';
    case 'approved': return r.status === 'approved';
    case 'flagged': return r.status === 'flagged';
    default: return true;
  }
}
function matchesSearch(r) {
  if (!S.search) return true;
  const q = S.search.toLowerCase();
  return (r.display || '').toLowerCase().includes(q) || (r.section || []).join(' ').toLowerCase().includes(q);
}
function computeVis() {
  const vis = new Map();
  const list = S.data.present;
  for (let k = list.length - 1; k >= 0; k--) {
    const r = list[k];
    if (!fileOk(r)) continue;
    if (matchesFilter(r) && matchesSearch(r)) vis.set(r.id, 'match');
    else if ((S.data.kids.get(r.id) || []).some((x) => vis.has(x.id))) vis.set(r.id, 'context');
  }
  return vis;
}

function todoUnder(file, path) {
  let n = 0;
  for (const r of S.data.present) {
    if (r.file !== file || !(r.status === 'pending' || r.status === 'changed')) continue;
    if (path.every((p, i) => r.section[i] === p)) n++;
  }
  return n;
}

function renderSide() {
  const d = S.data; const c = d.counts;
  const total = d.present.length || 1;
  const seg = (n, color) => (n ? `<i style="width:${(n / total) * 100}%;background:${color}"></i>` : '');
  let html = `<div class="progress"><div class="bar">${seg(c.approved, 'var(--ok)')}${seg(c.flagged, 'var(--flag)')}${seg(c.changed, 'var(--warn)')}</div>
    <div class="legend"><span><i class="sw" style="background:var(--ok)"></i>Approved <b>${c.approved}</b></span>
    ${c.flagged ? `<span><i class="sw" style="background:var(--flag)"></i>Flagged <b>${c.flagged}</b></span>` : ''}
    <span><i class="sw" style="background:var(--line-2)"></i>To review <b>${c.todo}</b></span>
    ${c.changed ? `<span><i class="sw" style="background:var(--warn)"></i>Changed <b>${c.changed}</b></span>` : ''}
    <span><i class="sw" style="background:var(--bad)"></i>Removed <b>${c.removed}</b></span></div></div><ul class="outline">`;
  for (const f of d.files) {
    if (S.file !== 'all' && S.file !== f) continue;
    html += `<li class="file">${esc(f)}</li>`;
    for (const h of d.headings.filter((x) => x.file === f && x.level > 1 && x.level <= 3)) {
      const n = todoUnder(f, h.path);
      html += `<li class="l${h.level}"><button data-act="jump" data-target="h-${esc(f)}-${h.line}"><span class="t">${inline(h.title)}</span><span class="c ${n ? '' : 'zero'}">${n || '✓'}</span></button></li>`;
    }
  }
  html += `</ul><div class="keys">Hover a rule, or <kbd>j</kbd>/<kbd>k</kbd>, to pick it<br><kbd>a</kbd> approve · <kbd>d</kbd> deny<br><kbd>f</kbd> flag to tune later · <kbd>q</kbd> ask<br><kbd>Enter</kbd> open · <kbd>u</kbd> undo · <kbd>/</kbd> search<br><kbd>[</kbd>/<kbd>]</kbd> switch skill · <kbd>+</kbd> add skill</div>`;
  $('#side').innerHTML = html;
}

function headingHTML(h, file) {
  const n = todoUnder(file, h.path);
  const tag = h.level <= 2 ? 'h2' : 'h3';
  return `<div class="sec" id="h-${esc(file)}-${h.line}"><${tag}>${inline(h.title)}</${tag}>
    <span class="count">${n ? `${n} to review` : 'all reviewed'}</span>
    ${n && S.filter !== 'approved' ? `<button class="btn sm ghost approve-all" data-act="approve-section" data-file="${esc(file)}" data-path="${esc(JSON.stringify(h.path))}" title="Approve every rule in this section that's still waiting">${icon('check')}Approve all ${n}</button>` : ''}</div>`;
}

function ruleBody(r) {
  if (r.kind === 'row') {
    const cells = r.extra.cells || [];
    return `<div class="row-table" style="--cols:${cells.length}">${cells.map((c) => `<div>${inline(c)}</div>`).join('')}</div>`;
  }
  if (r.kind === 'frontmatter') return `<p><span class="k">${esc(r.extra.key)}:</span>${inline(r.display)}</p>`;
  if (r.kind === 'code') return `<pre class="code">${esc(r.display)}</pre>`;
  return paras(r.display);
}

function flagParent(r) { const p = r.parent_id && S.data.byId.get(r.parent_id); return p && p.status === 'flagged' ? p : null; }
function flagTag(r) {
  const p = flagParent(r);
  const note = r.flag_note ? `: ${esc(r.flag_note)}` : p ? ' with its parent' : '';
  return `<span class="tag flag" title="Approved, but flagged to tune later">${icon('flag')}<span>Flagged ${dayText(r.flagged_at)}${note}</span></span>`;
}

function metaHTML(r) {
  const bits = [];
  if (r.busy) bits.push(`<span class="tag busy"><span class="spin" style="width:10px;height:10px"></span>Agent working</span>`);
  if (r.status === 'changed') bits.push(`<span class="tag changed">Changed since you approved it</span> <button class="link" data-act="show-diff" data-id="${r.id}">${S.diffOpen.has(r.id) ? 'Hide change' : 'Show change'}</button>`);
  else if (r.status === 'flagged' && r.changed_at && r.changed_at > (r.flagged_at || '')) bits.push(`<span class="tag changed">Edited since you flagged it</span> <button class="link" data-act="show-diff" data-id="${r.id}">${S.diffOpen.has(r.id) ? 'Hide change' : 'Show change'}</button>`);
  else if (r.status === 'pending' && r.changed_at) bits.push(`<span class="tag plain">Edited ${dayText(r.changed_at)}</span> <button class="link" data-act="show-diff" data-id="${r.id}">${S.diffOpen.has(r.id) ? 'Hide change' : 'Show change'}</button>`);
  if (r.reappeared_at) bits.push(`<span class="tag back" title="You or something else removed it before; it's back in the file">Came back ${dayText(r.reappeared_at)} after being removed</span>`);
  if (r.status === 'flagged') bits.push(flagTag(r));
  if (r.is_new && r.status !== 'approved' && r.status !== 'flagged') bits.push(`<span class="tag new">New ${dayText(r.first_seen)}${r.added_by && r.added_by.startsWith('job') ? ' · by an agent' : ''}</span>`);
  if (r.status === 'approved' && S.filter !== 'todo') bits.push(`<span class="tag ok">${icon('check')}Approved ${dayText(r.status_at)}</span>`);
  const p = r.prov;
  if (p && p.added) bits.push(`<span>Added ${dayText(p.added.ts)}</span>`);
  if (p && p.origin && (!p.added || p.origin.edit !== p.added.edit)) bits.push(`<span>first written ${dayText(p.origin.ts)} in ${esc(p.origin.file)}</span>`);
  if (r.moved_from) bits.push(`<span class="tag plain" title="The same text was removed from that skill">Moved from ${r.moved_from.map((x) => `<button class="link" data-act="switch-skill" data-skill="${esc(x)}">${esc(skillName(x))}</button>`).join(', ')}</span>`);
  if (r.threads) bits.push(`<button class="link" data-act="open" data-id="${r.id}">${plural(r.threads, 'conversation')}</button>`);
  return bits.join('');
}

function diffBase(r) { return r.status === 'changed' || r.status === 'flagged' ? r.approved_text : r.prev_text; }
function flagBox(r) {
  const n = r.status === 'flagged' ? 0 : descendants(r.id).filter((k) => k.status === 'pending' || k.status === 'changed').length;
  return `<div class="confirm flagbox"><div class="q">${r.status === 'flagged' ? 'What needs tuning?' : `Approve, but flag to tune later${n ? ` (with its ${plural(n, 'waiting sub-rule')})` : ''}.`} It leaves To review and waits in the Flagged list.</div>
    <input id="flagNote" type="text" placeholder="What needs tuning? (optional)" value="${esc(r.flag_note || '')}" autocomplete="off">
    <button class="btn flagbtn" data-act="flag-confirm" data-id="${r.id}">${icon('flag')}${r.status === 'flagged' ? 'Save note' : 'Flag'}</button>
    <button class="btn" data-act="flag-cancel">Cancel</button></div>`;
}

function ruleCard(r, context) {
  const cls = ['rule', `kind-${r.kind}`, context ? 'context' : '', r.is_new ? 'is-new' : '', S.focus === r.id ? 'focus' : ''].filter(Boolean).join(' ');
  const kids = (S.data.kids.get(r.id) || []).length;
  const approved = r.status === 'approved';
  const acts = `<div class="acts">
    ${approved ? `<button class="act approve on" data-act="unapprove" data-id="${r.id}" title="Approved. Click to send it back to review">${icon('check')}</button>`
      : `<button class="act approve" data-act="approve" data-id="${r.id}" title="Approve${kids ? ' (with its sub-rules)' : ''}  [a]" aria-label="Approve">${icon('check')}</button>`}
    ${r.status === 'flagged' ? `<button class="act flag on" data-act="flag" data-id="${r.id}" title="Flagged to tune later. Click to edit the note">${icon('flag')}</button>`
      : `<button class="act flag" data-act="flag" data-id="${r.id}" title="Approve, but flag to tune later  [f]" aria-label="Flag for later">${icon('flag')}</button>`}
    ${r.kind === 'frontmatter' ? '' : `<button class="act deny" data-act="deny" data-id="${r.id}" title="Deny: remove it from ${esc(r.file)}  [d]" aria-label="Deny">${icon('x')}</button>`}
    <button class="act ask" data-act="ask" data-id="${r.id}" title="Ask or refine  [q]" aria-label="Ask">${icon('ask')}${r.threads ? `<span class="badge">${r.threads}</span>` : ''}</button></div>`;
  let extra = '';
  if (S.diffOpen.has(r.id)) extra += `<div class="diff">${wdiff(diffBase(r), r.text)}</div>`;
  if (S.flagging === r.id) extra += flagBox(r);
  if (S.confirm === r.id) {
    const n = descendants(r.id).length;
    extra += `<div class="confirm"><div class="q">Remove this rule from ${esc(r.file)}${n ? ` along with its ${plural(n, 'sub-rule')}` : ''}? It stays in your Removed list and can be restored.</div>
      <input id="denyNote" type="text" placeholder="Why? (optional, saved with it)" autocomplete="off">
      <button class="btn danger" data-act="deny-confirm" data-id="${r.id}">${icon('x')}Remove</button>
      <button class="btn" data-act="deny-cancel">Cancel</button></div>`;
  }
  return `<div class="${cls}" data-id="${r.id}" data-status="${r.status}" style="--depth:${Math.min(r.depth, 5)}" tabindex="-1">
    <div class="body" data-act="focus" data-id="${r.id}">${ruleBody(r)}</div>${acts}<div class="meta">${metaHTML(r)}</div>${extra}</div>`;
}

function renderReview() {
  const d = S.data; const c = d.counts;
  const vis = computeVis();
  const chip = (k, label, n) => `<button class="chip ${S.filter === k ? 'on' : ''}" data-act="filter" data-filter="${k}">${label}<span class="n">${n}</span></button>`;
  let html = `<div class="page"><div class="toolbar">
    <label class="search">${icon('search')}<input id="search" type="search" placeholder="Search rules" value="${esc(S.search)}" autocomplete="off"></label>
    <div class="chips">${chip('todo', 'To review', c.todo)}${c.edited ? chip('changed', 'Changed', c.edited) : ''}${c.new ? chip('new', 'New', c.new) : ''}${chip('approved', 'Approved', c.approved)}${c.flagged ? chip('flagged', 'Flagged', c.flaggedTop) : ''}${chip('all', 'Everything', c.all)}</div>
  </div>`;
  let lastFile = null; let rendered = new Set(); let lastTable = null; let any = false;
  for (const r of d.present) {
    if (!vis.has(r.id)) continue;
    any = true;
    if (r.file !== lastFile) {
      const h1 = d.headings.find((h) => h.file === r.file && h.level === 1);
      html += `<div class="file-title"><h1>${inline(h1 ? h1.title : r.file)}</h1><span class="path">${esc(r.file)}</span></div>`;
      lastFile = r.file; rendered = new Set(); lastTable = null;
    }
    const h = d.heads.get(`${r.file}:${r.heading_line}`);
    if (h) {
      const chain = d.headings.filter((x) => x.file === r.file && x.level > 1 && x.line <= h.line && h.path.slice(0, x.path.length).join('\u0001') === x.path.join('\u0001'));
      for (const x of chain) if (!rendered.has(x.line)) { html += headingHTML(x, r.file); rendered.add(x.line); lastTable = null; }
    }
    if (r.kind === 'row' && r.extra.table_header && lastTable !== r.extra.table_line) {
      html += `<div class="table-head" style="--cols:${r.extra.table_header.length}">${r.extra.table_header.map((x) => `<div>${inline(x)}</div>`).join('')}</div>`;
    }
    lastTable = r.kind === 'row' ? r.extra.table_line : null;
    html += ruleCard(r, vis.get(r.id) === 'context');
  }
  if (!any) {
    html += S.filter === 'todo' && !S.search
      ? `<div class="empty">${icon('check')}<h3>All caught up</h3><p>Every rule in ${esc(S.skill)} is approved. New or edited rules will show up here.</p></div>`
      : `<div class="empty">${icon('search')}<h3>Nothing matches</h3><p>Try another filter or search.</p></div>`;
  }
  html += '</div>';
  const main = $('#main');
  const hadFocus = document.activeElement && document.activeElement.id === 'search';
  const selStart = hadFocus ? document.activeElement.selectionStart : 0;
  const note = $('#denyNote') ? $('#denyNote').value : '';
  main.innerHTML = html;
  if (hadFocus) { const s = $('#search'); s.focus(); s.setSelectionRange(selStart, selStart); }
  if (S.confirm && $('#denyNote')) { $('#denyNote').value = note; }
}

function visibleActionable() {
  return $$('#main .rule').filter((el) => !el.classList.contains('context')).map((el) => +el.dataset.id);
}

function setFocus(id, scroll = true) {
  S.focus = id;
  $$('#main .rule.focus').forEach((el) => el.classList.remove('focus'));
  const el = $(`#main .rule[data-id="${id}"]`);
  if (!el) return;
  el.classList.add('focus');
  if (!scroll) return;
  // bring it into view below the sticky header + toolbar (taller when the filter chips wrap in a narrow window);
  // a rule taller than the space left shows from its top, never with its top under the header
  const bar = $('#main .toolbar');
  const top = (bar ? bar.getBoundingClientRect().bottom : 0) + 12;
  const b = el.getBoundingClientRect();
  let dy = 0;
  if (b.top < top) dy = b.top - top;
  else if (b.bottom > innerHeight - 16) dy = Math.min(b.bottom - (innerHeight - 16), b.top - top);
  if (dy) window.scrollBy({ top: dy, behavior: 'smooth' });
}

function nextAfter(id) {
  const ids = visibleActionable();
  const skip = new Set([id, ...descendants(id).map((r) => r.id)]);
  const k = ids.indexOf(id);
  for (let j = k + 1; j < ids.length; j++) if (!skip.has(ids[j])) return ids[j];
  for (let j = k - 1; j >= 0; j--) if (!skip.has(ids[j])) return ids[j];
  return null;
}

// ---------------------------------------------------------------- actions
async function doApprove(id) {
  const next = nextAfter(id);
  const el = $(`#main .rule[data-id="${id}"]`);
  if (el && S.filter === 'todo') { el.classList.add('leaving'); descendants(id).forEach((k) => { const e = $(`#main .rule[data-id="${k.id}"]`); if (e) e.classList.add('leaving'); }); }
  try {
    const res = await sapi(`/rule/${id}/approve`, { cascade: true });
    if (S.filter === 'todo') S.focus = next;
    toast(res.count > 1 ? `Approved, with ${plural(res.count - 1, 'sub-rule')}` : 'Approved', res.batch);
    await refresh();
    if (S.focus) setFocus(S.focus);
  } catch (e) { if (el) el.classList.remove('leaving'); toastErr(e); }
}

async function doFlag(id) {
  const note = $('#flagNote') ? $('#flagNote').value.trim() : '';
  const r = S.data.byId.get(id);
  const wasFlagged = r && r.status === 'flagged';
  const next = S.tab === 'review' && S.filter === 'todo' && !wasFlagged ? nextAfter(id) : S.focus;
  try {
    const res = await sapi(`/rule/${id}/flag`, { note });
    S.flagging = null; if (S.drawer) S.drawer.confirmFlag = false;
    S.focus = next;
    toast(wasFlagged ? 'Note saved' : res.count > 1 ? `Flagged to tune later, with ${plural(res.count - 1, 'sub-rule')}` : 'Flagged to tune later', res.batch);
    await refresh();
    if (S.focus && S.tab === 'review') setFocus(S.focus);
    if (S.drawer) loadDrawer();
  } catch (e) { toastErr(e); }
}

async function doDeny(id) {
  const note = $('#denyNote') ? $('#denyNote').value.trim() : '';
  const next = nextAfter(id);
  try {
    const r = S.data.byId.get(id);
    const res = await sapi(`/rule/${id}/deny`, { note });
    S.confirm = null; S.focus = next;
    toast(`Removed from ${r ? r.file : 'the file'}${res.count > 1 ? ` (with ${plural(res.count - 1, 'sub-rule')})` : ''}`, res.batch);
    await refresh();
    if (S.focus) setFocus(S.focus);
  } catch (e) { toastErr(e); }
}

async function doUndo(batch) {
  try { await sapi('/undo', { batch }); toast('Undone'); S.lastBatch = null; await refresh(); if (S.drawer) loadDrawer(); } catch (e) { toastErr(e); }
}

// ---------------------------------------------------------------- lists (shared)
function sortState(table, key, dir) { return S.sort[table] || { key, dir }; }
function th(table, key, label, defKey, defDir) {
  const s = sortState(table, defKey, defDir);
  const arr = s.key === key ? (s.dir === 'asc' ? '▲' : '▼') : '';
  return `<th class="sort" data-act="sort" data-table="${table}" data-key="${key}" aria-sort="${s.key === key ? (s.dir === 'asc' ? 'ascending' : 'descending') : 'none'}">${label} <span class="arr">${arr}</span></th>`;
}
function sortRows(rows, table, getters, defKey, defDir) {
  const s = sortState(table, defKey, defDir);
  const g = getters[s.key] || getters[defKey];
  const m = s.dir === 'asc' ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const x = g(a); const y = g(b);
    if (x === y) return 0;
    if (x === null || x === undefined || x === '') return 1;
    if (y === null || y === undefined || y === '') return -1;
    return (x < y ? -1 : 1) * m;
  });
}
const secText = (r) => (r.section || []).slice(1).join(' › ') || (r.section || []).join(' › ');
const ruleCell = (r) => `<td class="rule-cell"><div class="clip">${r.kind === 'row' ? inline((r.extra.cells || []).join(' — ')) : inline(r.display)}</div></td>`;
function listTitle(title, sub) {
  const f = S.file !== 'all' ? ` · ${esc(S.file)}` : '';
  return `<div class="list-head"><h2>${title}</h2><span class="sub"><b>${esc(skillName(S.skill))}</b>${f} · ${sub}</span><div class="spacer"></div>${listSearchBox()}</div>`;
}
function listSearchBox() {
  return `<label class="search">${icon('search')}<input id="listSearch" type="search" placeholder="Search" value="${esc(S.listSearch)}" autocomplete="off"></label>`;
}
function listMatch(r) { const q = S.listSearch.toLowerCase(); return !q || (r.display || '').toLowerCase().includes(q) || secText(r).toLowerCase().includes(q) || (r.removed_note || '').toLowerCase().includes(q) || (r.flag_note || '').toLowerCase().includes(q); }
function keepListSearchFocus(fn) {
  const had = document.activeElement && document.activeElement.id === 'listSearch';
  const pos = had ? document.activeElement.selectionStart : 0;
  fn();
  if (had && $('#listSearch')) { $('#listSearch').focus(); $('#listSearch').setSelectionRange(pos, pos); }
}

function renderApproved() {
  const rows0 = S.data.present.filter((r) => r.status === 'approved' && fileOk(r) && listMatch(r));
  const showFile = S.data.files.length > 1 && S.file === 'all';
  const rows = sortRows(rows0, 'approved', {
    rule: (r) => (r.display || '').toLowerCase(), section: (r) => secText(r).toLowerCase(), file: (r) => r.file,
    approved: (r) => r.status_at, added: (r) => (r.prov && r.prov.added ? r.prov.added.ts : r.first_seen),
  }, 'approved', 'desc');
  keepListSearchFocus(() => {
    $('#main').innerHTML = `<div class="page wide">${listTitle('Approved', `${plural(rows0.length, 'rule')} you've signed off. They're hidden from Review unless they change.`)}
    ${rows.length ? `<div class="tablewrap"><table class="list"><thead><tr>${th('approved', 'rule', 'Rule', 'approved', 'desc')}${th('approved', 'section', 'Section', 'approved', 'desc')}${showFile ? th('approved', 'file', 'File', 'approved', 'desc') : ''}${th('approved', 'approved', 'Approved', 'approved', 'desc')}${th('approved', 'added', 'Added to skill', 'approved', 'desc')}<th></th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="click" data-act="open" data-id="${r.id}">${ruleCell(r)}<td class="sec-cell">${inline(secText(r))}</td>${showFile ? `<td class="sec-cell">${esc(r.file)}</td>` : ''}<td class="when">${whenCell(r.status_at)}</td><td class="when">${whenCell(r.prov && r.prov.added ? r.prov.added.ts : r.first_seen)}</td>
      <td class="acts-cell"><button class="btn sm" data-act="unapprove" data-id="${r.id}" title="Send it back to Review">${icon('undo')}Unapprove</button></td></tr>`).join('')}
    </tbody></table></div>` : `<div class="empty">${icon('inbox')}<h3>Nothing approved yet</h3><p>Approve rules on the Review tab and they collect here.</p></div>`}</div>`;
  });
}

function renderFlagged() {
  const all = S.data.present.filter((r) => r.status === 'flagged' && fileOk(r));
  const top = all.filter((r) => !flagParent(r) && (listMatch(r) || descendants(r.id).some((k) => k.status === 'flagged' && listMatch(k))));
  const edited = (r) => (r.changed_at && r.changed_at > (r.flagged_at || '') ? r.changed_at : null);
  const rows = sortRows(top, 'flagged', {
    rule: (r) => (r.display || '').toLowerCase(), note: (r) => (r.flag_note || '').toLowerCase(), section: (r) => secText(r).toLowerCase(),
    flagged: (r) => r.flagged_at, edited: (r) => edited(r),
  }, 'flagged', 'desc');
  const nEdited = top.filter(edited).length;
  keepListSearchFocus(() => {
    $('#main').innerHTML = `<div class="page wide">${listTitle('Flagged', `${plural(top.length, 'rule')} you approved but want to tune later${nEdited ? `; ${nEdited} edited since you flagged ${nEdited === 1 ? 'it' : 'them'}` : ''}. They stay in the skill and out of To review.`)}
    ${rows.length ? `<div class="tablewrap"><table class="list"><thead><tr>${th('flagged', 'rule', 'Rule', 'flagged', 'desc')}${th('flagged', 'note', 'What needs tuning', 'flagged', 'desc')}${th('flagged', 'section', 'Section', 'flagged', 'desc')}${th('flagged', 'flagged', 'Flagged', 'flagged', 'desc')}${th('flagged', 'edited', 'Edited since', 'flagged', 'desc')}<th></th></tr></thead><tbody>
    ${rows.map((r) => {
      const kids = descendants(r.id).filter((k) => k.status === 'flagged');
      const e = edited(r) || kids.map(edited).filter(Boolean).sort().pop();
      return `<tr class="click" data-act="open" data-id="${r.id}"><td class="rule-cell"><div class="clip">${r.kind === 'row' ? inline((r.extra.cells || []).join(' — ')) : inline(r.display)}</div>
        ${kids.length ? `<div class="muted" style="font-size:12px;margin-top:4px">+ ${plural(kids.length, 'sub-rule')} flagged with it</div>` : ''}
        ${S.diffOpen.has(r.id) ? `<div class="diff">${wdiff(diffBase(r), r.text)}</div>` : ''}</td>
        <td class="sec-cell" style="max-width:280px">${r.flag_note ? esc(r.flag_note) : '<button class="link" data-act="flag" data-id="' + r.id + '">add a note</button>'}</td>
        <td class="sec-cell">${inline(secText(r))}${showFileCol() ? `<div class="muted">${esc(r.file)}</div>` : ''}</td>
        <td class="when">${whenCell(r.flagged_at)}</td>
        <td class="when">${e ? `${whenCell(e)}${edited(r) ? `<button class="link" data-act="show-diff" data-id="${r.id}">${S.diffOpen.has(r.id) ? 'hide change' : 'show change'}</button>` : ''}` : '<span class="muted">not yet</span>'}</td>
        <td class="acts-cell"><button class="btn sm" data-act="refine-flagged" data-id="${r.id}" title="Ask the Refine agent to tune it, starting from your note">${icon('spark')}Refine</button>
          <button class="btn sm ok" data-act="approve" data-id="${r.id}" title="Done tuning: approve it and clear the flag">${icon('check')}Done</button></td></tr>`;
    }).join('')}
    </tbody></table></div>
    ${S.flagging && top.some((r) => r.id === S.flagging) ? `<div style="margin-top:12px">${flagBox(S.data.byId.get(S.flagging))}</div>` : ''}`
    : `<div class="empty">${icon('flag')}<h3>Nothing flagged</h3><p>Press <kbd>f</kbd> on a rule, or its flag button, to approve it but keep it here for tuning later.</p></div>`}</div>`;
  });
}
function showFileCol() { return S.data.files.length > 1 && S.file === 'all'; }

function removedGroup(r) { return r.status === 'denied' ? 'denied' : r.moved_to ? 'moved' : r.removed_how === 'job' ? 'job' : 'outside'; }
function removedHow(r) {
  if (r.status === 'denied') return r.removed_note === '(removed with its parent rule)' ? 'Denied with its parent' : HOW.denied;
  if (r.moved_to) return `Moved to ${r.moved_to.map(skillName).join(', ')}`;
  return HOW[r.removed_how] || 'Removed';
}
function renderRemoved() {
  const all = S.data.removed.filter(fileOk);
  const groups = { all: all.length, denied: 0, moved: 0, job: 0, outside: 0 };
  for (const r of all) groups[removedGroup(r)]++;
  const inGroup = (r) => S.removedHow === 'all' || removedGroup(r) === S.removedHow;
  const rows = sortRows(all.filter((r) => inGroup(r) && listMatch(r)), 'removed', {
    rule: (r) => (r.display || '').toLowerCase(), section: (r) => secText(r).toLowerCase(), removed: (r) => r.removed_at,
    how: (r) => removedHow(r), note: (r) => (r.removed_note || '').toLowerCase(),
  }, 'removed', 'desc');
  const chip = (k, label) => `<button class="chip ${S.removedHow === k ? 'on' : ''}" data-act="removed-how" data-how="${k}">${label}<span class="n">${groups[k]}</span></button>`;
  keepListSearchFocus(() => {
    $('#main').innerHTML = `<div class="page wide">${listTitle('Removed', 'everything that left this skill, kept here with its text.')}
    <div class="chips" style="margin-bottom:12px">${chip('all', 'All')}${chip('denied', 'Denied by you')}${groups.moved ? chip('moved', 'Moved to another skill') : ''}${chip('job', 'Removed by an agent')}${chip('outside', 'Removed outside the app')}</div>
    ${rows.length ? `<div class="tablewrap"><table class="list"><thead><tr>${th('removed', 'rule', 'Rule', 'removed', 'desc')}${th('removed', 'section', 'Section', 'removed', 'desc')}${th('removed', 'removed', 'Removed', 'removed', 'desc')}${th('removed', 'how', 'How', 'removed', 'desc')}${th('removed', 'note', 'Note', 'removed', 'desc')}<th></th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="click" data-act="open" data-id="${r.id}">${ruleCell(r)}<td class="sec-cell">${inline(secText(r))}<div class="muted">${esc(r.file)}</div></td><td class="when">${whenCell(r.removed_at)}</td>
      <td>${r.moved_to ? `Moved to ${r.moved_to.map((x) => `<button class="link" data-act="switch-skill" data-skill="${esc(x)}">${esc(skillName(x))}</button>`).join(', ')}` : esc(removedHow(r))}${r.removed_job ? `<div><button class="link" data-act="open-thread" data-id="${r.id}" data-thread="${r.removed_job.thread_id}">see the agent's conversation</button></div>` : ''}</td>
      <td class="sec-cell">${r.removed_note && r.removed_note !== '(removed with its parent rule)' ? esc(r.removed_note) : '<span class="muted">—</span>'}</td>
      <td class="acts-cell"><button class="btn sm" data-act="restore" data-id="${r.id}" title="Put it back where it was">${icon('restore')}Restore</button></td></tr>`).join('')}
    </tbody></table></div>` : `<div class="empty">${icon('inbox')}<h3>Nothing here</h3><p>Denied rules, and rules that disappear from the file, are listed here with their full text.</p></div>`}</div>`;
  });
}

function routeLabel(route) { return route && S.boot.routes[route] ? S.boot.routes[route].label : 'Auto'; }
function statusTag(st) {
  if (st === 'running' || st === 'queued') return '<span class="tag busy"><span class="spin" style="width:10px;height:10px"></span>Working</span>';
  if (st === 'error') return '<span class="tag back">Failed</span>';
  if (st === 'done') return '<span class="tag ok">Answered</span>';
  return `<span class="tag plain">${esc(st)}</span>`;
}
function renderQuestions() {
  const rows0 = (S.threads || []).filter((t) => (S.file === 'all' || (t.rule && t.rule.file === S.file))).filter((t) => !S.listSearch || (t.title || '').toLowerCase().includes(S.listSearch.toLowerCase()) || (t.rule && (t.rule.display || '').toLowerCase().includes(S.listSearch.toLowerCase())));
  const rows = sortRows(rows0, 'questions', {
    rule: (t) => (t.rule ? t.rule.display : '').toLowerCase(), title: (t) => (t.title || '').toLowerCase(), route: (t) => routeLabel(t.route),
    status: (t) => t.status, files: (t) => t.changed_files, updated: (t) => t.updated_at, created: (t) => t.created_at,
  }, 'updated', 'desc');
  keepListSearchFocus(() => {
    $('#main').innerHTML = `<div class="page wide">${listTitle('Questions', "every conversation you've had with an agent about a rule.")}
    ${rows.length ? `<div class="tablewrap"><table class="list"><thead><tr>${th('questions', 'title', 'You asked', 'updated', 'desc')}${th('questions', 'rule', 'About', 'updated', 'desc')}${th('questions', 'route', 'Agent', 'updated', 'desc')}${th('questions', 'status', 'Status', 'updated', 'desc')}${th('questions', 'files', 'Files changed', 'updated', 'desc')}${th('questions', 'created', 'Started', 'updated', 'desc')}${th('questions', 'updated', 'Last update', 'updated', 'desc')}</tr></thead><tbody>
    ${rows.map((t) => `<tr class="click" data-act="open-thread" data-id="${t.rule_id}" data-thread="${t.id}"><td class="rule-cell"><div class="clip">${esc(t.title)}</div>${t.jobs.length > 1 ? `<div class="muted">${plural(t.jobs.length, 'message')}</div>` : ''}</td>
      <td class="rule-cell"><div class="clip">${t.rule ? inline(t.rule.display) : ''}</div>${t.rule && !t.rule.present ? '<span class="tag back">removed</span>' : ''}</td>
      <td>${esc(routeLabel(t.route))}</td><td>${statusTag(t.status)}</td><td>${t.changed_files || '<span class="muted">—</span>'}</td>
      <td class="when">${whenCell(t.created_at)}</td><td class="when">${whenCell(t.updated_at)}</td></tr>`).join('')}
    </tbody></table></div>` : `<div class="empty">${icon('ask')}<h3>No questions yet</h3><p>Press <kbd>q</kbd> on any rule, or click its speech-bubble button, to ask where it came from or to change it.</p></div>`}</div>`;
  });
}

const EVENT_LABEL = {
  approved: 'Approved', flagged: 'Flagged to tune later', flag_note: 'Changed the tuning note', unapproved: 'Sent back to review', denied: 'Denied and removed from the file', restored: 'Restored to the file',
  undo: 'Undone', changed: 'Text changed', appeared: 'New rule appeared', removed: 'Disappeared from the file', reappeared: 'Came back',
  baseline: 'Started tracking this skill', asked: 'Asked an agent', answered: 'Agent answered', agent_error: 'Agent failed', reverted: "Agent's edit reverted",
};
const EVENT_GROUP = { votes: ['approved', 'flagged', 'flag_note', 'unapproved', 'denied', 'restored', 'undo'], file: ['changed', 'appeared', 'removed', 'reappeared', 'baseline'], agents: ['asked', 'answered', 'agent_error', 'reverted'] };
function byText(by) { return !by ? '' : by === 'outside' ? 'outside the app' : by.startsWith('job:') ? 'by an agent' : by.startsWith('revert:') ? "by reverting an agent's edit" : by; }
function eventDetail(e) {
  const d = e.detail || {};
  if (e.type === 'denied') return d.note ? `“${esc(d.note)}”` : d.with_parent ? 'with its parent' : '';
  if (e.type === 'approved') return d.cascade ? 'with its parent' : d.prev === 'flagged' ? 'done tuning; flag cleared' : d.prev === 'changed' ? 're-approved after a change' : '';
  if (e.type === 'flagged') return d.note ? `“${esc(d.note)}”` : d.cascade ? 'with its parent' : '';
  if (e.type === 'flag_note') return d.note ? `“${esc(d.note)}”` : 'note cleared';
  if (e.type === 'changed') return `${byText(d.by)}<div class="diff" style="margin-top:4px">${wdiff(d.old, d.new)}</div>`;
  if (e.type === 'appeared' || e.type === 'removed' || e.type === 'reappeared') return byText(d.by);
  if (e.type === 'asked') return `${esc(routeLabel(d.route))}: “${esc((d.message || '').slice(0, 160))}”`;
  if (e.type === 'answered') return `${esc(routeLabel(d.route))}${d.files_changed ? ` · changed ${plural(d.files_changed, 'file')}` : ''}`;
  if (e.type === 'baseline') return `${d.rules} rules`;
  if (e.type === 'undo') return `undid ${esc(d.undid || '')}`;
  return '';
}
function renderActivity() {
  const all = (S.events || []).filter((e) => S.file === 'all' || !e.rule_file || e.rule_file === S.file);
  const inGroup = (e) => S.eventGroup === 'all' || (EVENT_GROUP[S.eventGroup] || []).includes(e.type);
  const rows = sortRows(all.filter((e) => inGroup(e) && (!S.listSearch || JSON.stringify(e).toLowerCase().includes(S.listSearch.toLowerCase()))), 'activity', {
    when: (e) => e.ts, what: (e) => EVENT_LABEL[e.type] || e.type, rule: (e) => (e.rule_display || '').toLowerCase(),
  }, 'when', 'desc');
  const chip = (k, label) => `<button class="chip ${S.eventGroup === k ? 'on' : ''}" data-act="event-group" data-group="${k}">${label}</button>`;
  keepListSearchFocus(() => {
    $('#main').innerHTML = `<div class="page wide">${listTitle('Activity', "everything that happened to this skill's rules.")}
    <div class="chips" style="margin-bottom:12px">${chip('all', 'All')}${chip('votes', 'Your votes')}${chip('file', 'File changes')}${chip('agents', 'Agents')}</div>
    ${rows.length ? `<div class="tablewrap"><table class="list"><thead><tr>${th('activity', 'when', 'When', 'when', 'desc')}${th('activity', 'what', 'What', 'when', 'desc')}${th('activity', 'rule', 'Rule', 'when', 'desc')}<th>Details</th></tr></thead><tbody>
    ${rows.slice(0, 600).map((e) => `<tr class="${e.rule_id ? 'click' : ''}" ${e.rule_id ? `data-act="open" data-id="${e.rule_id}"` : ''}><td class="when">${whenCell(e.ts)}</td><td>${esc(EVENT_LABEL[e.type] || e.type)}</td>
      <td class="rule-cell"><div class="clip">${inline(e.rule_display || '')}</div></td><td class="sec-cell" style="max-width:420px">${eventDetail(e)}</td></tr>`).join('')}
    </tbody></table></div>` : `<div class="empty">${icon('clock')}<h3>No activity yet</h3></div>`}</div>`;
  });
}

// ---------------------------------------------------------------- drawer
async function openDrawer(id, opts = {}) {
  const same = S.drawer && S.drawer.id === id;
  if (!same) {
    S.drawer = { id, detail: null, history: null, ctx: {}, openCtx: new Set(), replyThread: null, route: 'auto', editId: null, editLabel: '', showKept: false };
  }
  if (opts.thread) { S.drawer.replyThread = opts.thread; }
  if (opts.route) S.drawer.route = opts.route;
  if (!$('#drawer')) renderDrawerShell();
  renderComposerState();
  await loadDrawer();
  if (!S.drawer.history) loadHistory();
  if (opts.prefill !== undefined) { $('#composeText').value = opts.prefill; }
  if (opts.focusComposer || opts.prefill !== undefined) { const ta = $('#composeText'); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
  if (opts.thread) { const el = $(`#thread-${opts.thread}`); if (el) el.scrollIntoView({ block: 'start' }); }
}
function closeDrawer() { S.drawer = null; $('#drawerRoot').innerHTML = ''; lockPage(false); }

// While the chat drawer is open only the drawer scrolls: the page is locked (its scroll bar beside the drawer's
// made two), and padded by the scroll bar's width so nothing behind the drawer jumps sideways.
function lockPage(on) {
  const root = document.documentElement;
  if (on === (root.style.overflow === 'hidden')) return;
  const gap = on ? window.innerWidth - root.clientWidth : 0;
  root.style.overflow = on ? 'hidden' : '';
  document.body.style.paddingRight = gap > 0 ? `${gap}px` : '';
}

function renderDrawerShell() {
  lockPage(true);
  $('#drawerRoot').innerHTML = `<div class="scrim" data-act="close-drawer"></div>
  <section class="drawer" id="drawer" role="dialog" aria-modal="true" aria-label="Rule details">
    <div class="drawer-head"><div class="crumbs" id="crumbs"></div><button class="icon-btn" data-act="close-drawer" title="Close (Esc)" aria-label="Close">${icon('close')}</button></div>
    <div class="drawer-body" id="drawerBody"><div class="spin"></div></div>
    <div class="composer">
      <div id="loginWarn"></div>
      <div class="replying hidden" id="replying"></div>
      <div class="routes" id="routes"></div>
      <div class="route-desc" id="routeDesc"></div>
      <div class="compose-row"><textarea id="composeText" rows="3" placeholder="Ask about this rule, or say how it should change…"></textarea>
        <button class="btn primary" data-act="send" id="sendBtn">${icon('send')}Send</button></div>
      <div class="hint"><kbd>Ctrl</kbd>+<kbd>Enter</kbd> to send. Auto reads your message and picks the agent.</div>
    </div>
  </section>`;
}

function renderComposerState() {
  if (!S.drawer || !$('#routes')) return;
  const dr = S.drawer;
  $('#routes').innerHTML = ROUTE_ORDER.map((k) => `<button class="route ${dr.route === k ? 'on' : ''}" data-act="route" data-route="${k}">${k === 'auto' ? 'Auto' : esc(S.boot.routes[k].label)}</button>`).join('');
  $('#routeDesc').textContent = dr.route === 'auto' ? 'Sends your message to whichever agent fits it best.' : S.boot.routes[dr.route].desc;
  const rep = $('#replying');
  let txt = '';
  if (dr.replyThread) {
    const t = dr.detail && dr.detail.threads.find((x) => x.id === dr.replyThread);
    txt = `Replying in “${esc(t ? t.title : 'conversation')}” <button class="link" data-act="new-thread">start a new one instead</button>`;
  } else if (dr.route === 'origin' && dr.editId) {
    txt = `Asking the agent from ${esc(dr.editLabel)} <button class="link" data-act="clear-edit">use the default</button>`;
  }
  rep.innerHTML = txt; rep.classList.toggle('hidden', !txt);
  const a = S.agent || {};
  $('#loginWarn').innerHTML = a.ok === false ? `<div class="info-box" style="margin-bottom:8px"><b>Agents can't run yet.</b> ${esc(a.needs_login ? 'Claude Code is logged out on this computer.' : a.message || '')}
    ${a.needs_login ? ' Open a terminal, run <code>claude</code>, type <code>/login</code>, then ' : ' '}<button class="link" data-act="agent-check">check again</button>.</div>` : '';
}

async function loadDrawer() {
  if (!S.drawer) return;
  const id = S.drawer.id;
  try {
    const det = await sapi(`/rule/${id}`);
    if (!S.drawer || S.drawer.id !== id) return;
    S.drawer.detail = det;
    renderDrawerBody();
    renderComposerState();
  } catch (e) { toastErr(e); }
}

async function loadHistory() {
  if (!S.drawer) return;
  const id = S.drawer.id;
  try {
    const h = await sapi(`/rule/${id}/history`);
    if (!S.drawer || S.drawer.id !== id) return;
    S.drawer.history = h;
    renderDrawerBody();
    if (h.indexing) setTimeout(() => { if (S.drawer && S.drawer.id === id) loadHistory(); }, 4000);
  } catch (e) { if (S.drawer) { S.drawer.history = { error: e.message }; renderDrawerBody(); } }
}

function drawerRuleHTML(r) {
  const kids = r.present ? descendants(r.id) : [];
  let body = `<div class="body">${ruleBody(r)}</div>`;
  if (!r.present && r.removed_block_preview) body = `<pre class="code">${esc(r.removed_block_preview)}</pre>`;
  body += kids.map((k) => `<div class="kid" style="--d:${Math.max(1, k.depth - r.depth)}">${k.kind === 'row' ? inline((k.extra.cells || []).join(' — ')) : inline(k.display)}</div>`).join('');
  return body;
}

function statusLine(r) {
  const bits = [];
  if (!r.present) {
    bits.push(`<span class="tag back">${esc(removedHow(r))} ${dayText(r.removed_at)}</span>`);
    bits.push(`<button class="btn sm" data-act="restore" data-id="${r.id}">${icon('restore')}Restore to ${esc(r.file)}</button>`);
  } else {
    if (r.status === 'approved') bits.push(`<span class="tag ok">${icon('check')}Approved ${dayText(r.status_at)}</span><button class="btn sm" data-act="unapprove" data-id="${r.id}">${icon('undo')}Unapprove</button><button class="btn sm" data-act="flag" data-id="${r.id}" data-from="drawer">${icon('flag')}Flag to tune</button>`);
    else if (r.status === 'flagged') bits.push(`${flagTag(r)}<button class="btn sm ok" data-act="approve" data-id="${r.id}" title="Done tuning: approve and clear the flag">${icon('check')}Done tuning</button><button class="btn sm" data-act="flag" data-id="${r.id}" data-from="drawer">${icon('flag')}${r.flag_note ? 'Edit note' : 'Add note'}</button><button class="btn sm" data-act="unapprove" data-id="${r.id}">${icon('undo')}Back to review</button>`);
    else bits.push(`<span class="tag ${r.status === 'changed' ? 'changed' : 'plain'}">${r.status === 'changed' ? 'Changed since you approved it' : 'Waiting for review'}</span><button class="btn sm ok" data-act="approve" data-id="${r.id}">${icon('check')}Approve</button><button class="btn sm" data-act="flag" data-id="${r.id}" data-from="drawer">${icon('flag')}Flag to tune</button>`);
    if (r.kind !== 'frontmatter') bits.push(`<button class="btn sm bad" data-act="deny" data-id="${r.id}" data-from="drawer">${icon('x')}Deny</button>`);
  }
  return bits.join('');
}

function renderDrawerBody() {
  const dr = S.drawer; if (!dr || !dr.detail) return;
  const det = dr.detail; const r = det.rule;
  const bodyEl = $('#drawerBody'); const scroll = bodyEl.scrollTop;
  const loc = r.present ? `${r.file} · line ${r.line_start + 1}${r.block_end - r.line_start > 1 ? `–${r.block_end}` : ''}` : r.file;
  $('#crumbs').innerHTML = `${esc(loc)}${secText(r) ? ` · ${inline(secText(r))}` : ''}`;
  let html = `<div class="drawer-rule">${drawerRuleHTML(r)}</div><div class="drawer-status">${statusLine(r)}</div>`;
  if (dr.confirmDeny) {
    const n = descendants(r.id).length;
    html += `<div class="confirm" style="margin-top:10px"><div class="q">Remove this rule from ${esc(r.file)}${n ? ` along with its ${plural(n, 'sub-rule')}` : ''}?</div>
      <input id="denyNote" type="text" placeholder="Why? (optional, saved with it)"><button class="btn danger" data-act="deny-confirm" data-id="${r.id}">${icon('x')}Remove</button><button class="btn" data-act="deny-cancel">Cancel</button></div>`;
  }
  if (dr.confirmFlag) html += `<div style="margin-top:10px">${flagBox(r)}</div>`;
  if (r.status === 'changed' || (r.status === 'pending' && r.changed_at) || (r.status === 'flagged' && r.changed_at && r.changed_at > (r.flagged_at || ''))) {
    html += `<div class="panel"><h4>What changed${r.status === 'flagged' ? ' since you flagged it' : ''}${r.changed_at ? ` · ${dayText(r.changed_at)}` : ''}</h4><div class="diff">${wdiff(diffBase(r), r.text)}</div></div>`;
  }
  // conversations
  html += `<div class="panel"><h4>Conversations${det.threads.length ? ` · ${det.threads.length}` : ''}</h4>`;
  html += det.threads.length ? det.threads.map(threadHTML).join('') : '<p class="muted" style="margin:0">None yet. Type below: ask where it came from, why it exists, or say how it should change.</p>';
  html += '</div>';
  // provenance
  html += `<div class="panel"><h4>Where it came from <button class="btn sm" data-act="explain" title="Have Claude read the sessions and explain">${icon('spark')}Explain with Claude</button></h4>${timelineHTML(dr.history)}</div>`;
  // review history
  html += `<div class="panel"><h4>Review history</h4><ul class="events">${det.events.map((e) => `<li><span class="when">${whenText(e.ts)}</span><span>${esc(EVENT_LABEL[e.type] || e.type)} ${eventDetail(e)}</span></li>`).join('') || '<li class="muted">Nothing yet.</li>'}</ul>
    <p class="muted" style="font-size:12px;margin-top:8px">Tracked since ${whenText(r.first_seen)}.</p></div>`;
  const note = $('#denyNote') ? $('#denyNote').value : '';
  bodyEl.innerHTML = html;
  bodyEl.scrollTop = scroll;
  if (dr.confirmDeny && $('#denyNote')) { $('#denyNote').value = note; }
}

function threadHTML(t) {
  return `<div class="thread" id="thread-${t.id}"><div class="thread-head"><span class="tag plain">${esc(routeLabel(t.route))}</span><span class="t">${esc(t.title)}</span>
    <span class="muted">${whenText(t.updated_at)}</span><button class="btn sm ghost" data-act="reply" data-thread="${t.id}" title="Continue this conversation">Reply</button></div>
    ${t.jobs.map(turnHTML).join('')}</div>`;
}

function turnHTML(j) {
  const running = j.status === 'running' || j.status === 'queued';
  const log = j.log || [];
  const steps = log.filter((l) => l.k === 'tool' || l.k === 'tool_error' || l.k === 'route' || l.k === 'start' || l.k === 'note' || (l.k === 'text' && (running || l.text !== j.result)));
  let html = `<div class="turn"><div class="q"><span class="who">You · ${whenText(j.created_at)}</span>${esc(j.message)}</div>`;
  if (j.route_reason) html += `<div class="muted" style="font-size:12px;margin:-2px 0 6px">Sent to <b>${esc(routeLabel(j.route))}</b>: ${esc(j.route_reason)}</div>`;
  if (steps.length) {
    html += `<details class="steps" ${running ? 'open' : ''}><summary>${running ? 'Working' : 'What it did'} (${plural(steps.length, 'step')})</summary>${steps.slice(-40).map((l) => `<div class="${l.k === 'tool_error' ? 'err' : ''}" title="${esc(l.text)}">${l.k === 'tool' ? '› ' : l.k === 'tool_error' ? '! ' : ''}${esc(String(l.text).split('\n')[0].slice(0, 200))}</div>`).join('')}</details>`;
  }
  if (running) html += `<div class="working"><span class="spin"></span>${j.status === 'queued' ? 'Starting…' : 'Working…'} <button class="btn sm ghost" data-act="cancel-job" data-job="${j.id}">${icon('stop')}Stop</button></div>`;
  else if (j.status === 'error') html += `<div class="err-box">${esc((j.error || 'Failed').slice(0, 1500))}</div>`;
  else if (j.result) html += `<div class="answer">${md(j.result)}</div>`;
  if (j.changes && j.changes.length) {
    html += `<div class="changes"><b style="font-size:13px">Changed ${plural(j.changes.length, 'file')}</b>
      ${j.reverted_at ? `<span class="tag plain">Reverted ${dayText(j.reverted_at)}</span>` : `<button class="btn sm" data-act="revert-job" data-job="${j.id}" style="margin-left:8px">${icon('undo')}Revert these changes</button>`}
      ${j.changes.map((c) => `<details><summary class="file">${icon('file')}${esc(c.file)} <span class="muted">(${esc(c.kind)})</span></summary>${udiff(c.diff)}</details>`).join('')}</div>`;
  }
  if (!running && (j.cost || j.model)) html += `<div class="muted" style="font-size:11.5px;margin-top:6px">${esc(j.model || '')}${j.cost ? ` · $${Number(j.cost).toFixed(3)}` : ''}${j.finished_at ? ` · ${whenText(j.finished_at)}` : ''}</div>`;
  return html + '</div>';
}

const KIND_LABEL = { origin: 'First written', copied: 'Copied into', added: 'Added to', revised: 'Reworded in', kept: 'Kept when rewriting', chosen: '' };
function timelineHTML(h) {
  if (!h) return '<div class="working"><span class="spin"></span>Searching your past sessions…</div>';
  if (h.error) return `<div class="err-box">${esc(h.error)}</div>`;
  if (h.indexing) return `<div class="working"><span class="spin"></span>Indexing your Claude session history for the first time (about a minute)…</div>`;
  const tl = h.timeline || [];
  if (!tl.length) return '<p class="muted" style="margin:0">No past session wrote this text with Edit or Write. It may have come from a script, from before your transcripts start, or from typing it by hand. <b>Explain with Claude</b> can search the transcripts for its key phrases.</p>';
  const kept = tl.filter((e) => e.kind === 'kept').length;
  const items = tl.filter((e) => S.drawer.showKept || e.kind !== 'kept');
  let html = '<ul class="timeline">';
  for (const e of items) {
    const f = base(e.file_path);
    const what = e.kind === 'origin' ? `First written in ${f}` : `${KIND_LABEL[e.kind] || e.kind} ${f}`;
    const who = e.app_job ? 'this app' : e.agent ? `subagent “${e.agent.description || e.agent.id}”` : 'main agent';
    html += `<li class="${e.kind}${e.app_job ? ' app' : ''}"><div class="what">${esc(what)}</div>
      <div class="sub">${whenText(e.ts)} · session “${esc(e.session_title)}” · ${esc(who)} · ${e.exact ? 'matches today’s wording' : `${Math.round(e.score * 100)}% similar to today’s wording`}</div>
      <div class="row-acts"><button class="btn sm" data-act="show-ctx" data-edit="${e.id}">${S.drawer.openCtx.has(e.id) ? 'Hide conversation' : 'Show conversation'}</button>
      <button class="btn sm" data-act="ask-agent" data-edit="${e.id}" data-label="${esc(`“${e.session_title}” (${when(e.ts).main})`)}">${icon('ask')}Ask this agent</button></div>
      ${S.drawer.openCtx.has(e.id) ? ctxHTML(S.drawer.ctx[e.id], e) : ''}</li>`;
  }
  html += '</ul>';
  if (kept) html += `<button class="link" data-act="show-kept">${S.drawer.showKept ? 'Hide' : 'Show'} ${plural(kept, 'rewrite')} that kept it unchanged</button>`;
  return html;
}

function ctxHTML(c, e) {
  if (!c) return '<div class="convo"><span class="spin"></span></div>';
  if (c.error) return `<div class="err-box">${esc(c.error)}</div>`;
  let html = '<div class="convo">';
  if (c.parent_messages && c.parent_messages.length) {
    html += '<div class="muted" style="font-size:12px">What the owner said to the lead agent:</div>';
    html += c.parent_messages.filter((m) => m.role === 'owner').map((m) => `<div class="msg owner"><div class="who">You · ${whenText(m.ts)}</div><div class="txt">${esc(m.text)}</div></div>`).join('');
  }
  if (c.agent_prompt) html += `<details><summary class="muted" style="font-size:12px;cursor:pointer">The subagent's instructions</summary><div class="msg"><div class="txt">${esc(c.agent_prompt)}</div></div></details>`;
  for (const m of c.messages) {
    if (m.role === 'tool') html += `<div class="msg tool">› ${esc(m.text)}</div>`;
    else html += `<div class="msg ${m.role === 'owner' ? 'owner' : ''}"><div class="who">${m.role === 'owner' ? 'You' : m.role === 'lead agent' ? 'Lead agent' : 'Agent'} · ${whenText(m.ts)}</div><div class="txt">${esc(m.text)}</div></div>`;
  }
  html += `<details open><summary class="muted" style="font-size:12px;cursor:pointer">The edit: ${esc(c.edit.tool)} ${esc(base(c.edit.file_path))}</summary>${editDiff(c.edit.old_text, c.edit.tool === 'Write' ? '(whole file written; showing the matching part)\n' + matchingPart(c.edit.new_text) : c.edit.new_text)}</details>`;
  const lab = c.label || {};
  html += `<div class="muted" style="font-size:11.5px;margin-top:6px;overflow-wrap:anywhere">Transcript: <code>${esc(lab.transcript)}</code> line ${lab.line}. To reopen that session yourself: <code>cd "${esc(lab.cwd || '')}"; claude --resume ${esc(lab.session_id)} --fork-session</code></div>`;
  return html + '</div>';
}

function matchingPart(text) {
  const r = S.drawer && S.drawer.detail && S.drawer.detail.rule;
  if (!r || !text) return String(text || '').slice(0, 2000);
  const words = stripMarkers(r.text).split(/\s+/).filter((w) => w.length > 4).slice(0, 6);
  const lines = String(text).split('\n');
  let best = 0; let bestScore = -1;
  lines.forEach((l, k) => { const s = words.filter((w) => l.includes(w)).length; if (s > bestScore) { bestScore = s; best = k; } });
  return lines.slice(Math.max(0, best - 3), best + 8).join('\n');
}

async function toggleCtx(editId) {
  const dr = S.drawer;
  if (dr.openCtx.has(editId)) { dr.openCtx.delete(editId); renderDrawerBody(); return; }
  dr.openCtx.add(editId); renderDrawerBody();
  if (!dr.ctx[editId]) {
    try { dr.ctx[editId] = await api(`/api/edit/${editId}/context`); } catch (e) { dr.ctx[editId] = { error: e.message }; }
    renderDrawerBody();
  }
}

async function send(textOverride, routeOverride) {
  const dr = S.drawer; if (!dr) return;
  const ta = $('#composeText');
  const text = (textOverride || ta.value).trim();
  if (!text) { ta.focus(); return; }
  const btn = $('#sendBtn'); btn.disabled = true;
  try {
    const res = await sapi('/ask', { rule_id: dr.id, message: text, route: routeOverride || dr.route, thread_id: routeOverride ? null : dr.replyThread, edit_id: dr.route === 'origin' ? dr.editId : null });
    if (!textOverride) ta.value = '';
    dr.replyThread = res.thread_id;
    await loadDrawer();
    const el = $(`#thread-${res.thread_id}`); if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
    poll(true);
  } catch (e) { toastErr(e); } finally { btn.disabled = false; }
}

// ---------------------------------------------------------------- toast / modal
function toast(msg, batch) {
  if (batch) S.lastBatch = batch;
  const el = document.createElement('div');
  el.className = 'toast';
  el.innerHTML = `<span>${esc(msg)}</span>${batch ? `<button data-act="undo" data-batch="${batch}">${icon('undo')} Undo</button>` : ''}`;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), batch ? 8000 : 3500);
}
function toastErr(e) {
  const el = document.createElement('div'); el.className = 'toast err'; el.textContent = e.message || String(e);
  $('#toasts').appendChild(el); setTimeout(() => el.remove(), 7000);
}
function modal(html, cls = '') { $('#modalRoot').innerHTML = `<div class="modal-wrap" data-act="close-modal"><div class="modal ${cls}" role="dialog" aria-modal="true">${html}</div></div>`; }
let confirmResolve = null;
function closeModal() { $('#modalRoot').innerHTML = ''; if (confirmResolve) { confirmResolve(false); confirmResolve = null; } }
function askConfirm(text, okLabel) {
  if (confirmResolve) confirmResolve(false);
  modal(`<p style="margin:4px 0 0">${esc(text)}</p><div class="foot"><button class="btn" data-act="close-modal">Cancel</button><button class="btn danger" data-act="confirm-ok">${esc(okLabel)}</button></div>`);
  setTimeout(() => $('[data-act="confirm-ok"]') && $('[data-act="confirm-ok"]').focus(), 0);
  return new Promise((res) => { confirmResolve = res; });
}

function agentModal() {
  const a = S.agent || {};
  modal(`<h3>Background agents</h3>
    <p><span class="dot ${a.ok ? 'ok' : a.ok === false ? 'bad' : ''}" style="display:inline-block;margin-right:6px"></span>${a.ok ? 'Connected. Questions and refinements run through Claude Code on this computer.' : esc(a.message || 'Not checked yet.')}</p>
    ${a.needs_login ? `<div class="info-box">Claude Code needs you to sign in once on this computer:<ol style="margin:6px 0 0;padding-left:18px"><li>Open a terminal (PowerShell).</li><li>Run <code>claude</code></li><li>Type <code>/login</code> and finish in the browser, then close it with <code>/exit</code>.</li><li>Click <b>Check again</b>.</li></ol></div>` : ''}
    <p class="muted" style="font-size:12.5px">Using: <code>${esc(a.exe || 'not found')}</code>${a.checked_at ? ` · checked ${whenText(a.checked_at)}` : ''}</p>
    <p class="muted" style="font-size:12.5px">History index: ${esc((S.history && S.history.status) || '')}</p>
    <div class="foot"><button class="btn" data-act="close-modal">Close</button><button class="btn primary" data-act="agent-check">Check again</button></div>`);
}

function settingsModal() {
  api('/api/settings').then((st) => {
    const models = ['opus', 'sonnet', 'haiku'];
    const roles = [['router', 'Auto router'], ...ROUTE_ORDER.slice(1).map((k) => [k, S.boot.routes[k].label])];
    modal(`<h3>Settings</h3>
      <div class="field"><label>Model for each agent</label><div class="grid2">${roles.map(([k, label]) => `<div><div class="muted" style="font-size:12px">${esc(label)}</div><select data-model="${k}">${models.map((m) => `<option ${st.models[k] === m ? 'selected' : ''}>${m}</option>`).join('')}</select></div>`).join('')}</div></div>
      <div class="field"><label for="budget">Spending cap per request (USD)</label><input id="budget" type="number" min="0" step="0.5" value="${esc(st.max_budget_usd)}"><div class="help">Claude stops a single request once it has used this much. 0 means no cap.</div></div>
      <div class="field"><label for="claudePath">Claude Code program</label><input id="claudePath" type="text" value="${esc(st.claude_path)}" placeholder="${esc((S.agent && S.agent.exe) || 'auto-detect')}"><div class="help">Leave empty to use the newest one found automatically.</div></div>
      <div class="field"><label for="readDirs">Folders agents may read for context</label><textarea id="readDirs" rows="3">${esc((st.read_dirs || []).join('\n'))}</textarea><div class="help">One per line. Read-only agents can look here; editing agents can only change files in the skills folder the skill lives in.</div></div>
      <div class="foot"><button class="btn" data-act="reindex">Rebuild history index</button><button class="btn bad" data-act="shutdown">Stop the app</button><div class="spacer"></div><button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="save-settings">Save</button></div>`);
  }).catch(toastErr);
}

async function saveSettings() {
  const models = {}; $$('[data-model]').forEach((s) => { models[s.dataset.model] = s.value; });
  const patch = { models, max_budget_usd: parseFloat($('#budget').value) || 0, claude_path: $('#claudePath').value.trim(), read_dirs: $('#readDirs').value.split('\n').map((x) => x.trim()).filter(Boolean) };
  try { await api('/api/settings', patch); closeModal(); toast('Settings saved'); } catch (e) { toastErr(e); }
}

// ---------------------------------------------------------------- events
document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('[data-act]');
  if (!t) return;
  const act = t.dataset.act; const id = t.dataset.id ? +t.dataset.id : null;
  if (act === 'close-modal' && t.classList.contains('modal-wrap') && ev.target !== t) return;
  if (ev.target.closest('.modal') && t.classList.contains('modal-wrap')) return;
  if (t.tagName === 'TR' && ev.target.closest('button')) return;
  switch (act) {
    case 'tab': S.tab = t.dataset.tab; prefs.set('tab', S.tab); S.listSearch = ''; window.scrollTo(0, 0); await refresh(); break;
    case 'switch-skill': if (ev.target.closest('.x')) break; closeModal(); await switchSkill(t.dataset.skill); break;
    case 'close-skill': ev.stopPropagation(); await closeSkill(t.dataset.skill); break;
    case 'add-skill': await addSkillModal(); break;
    case 'open-skill': await openSkill(t.dataset.dir); break;
    case 'open-path': { const v = $('#skillPath').value.trim(); if (v) await openSkill(v); else $('#skillPath').focus(); break; }
    case 'filter': S.filter = t.dataset.filter; prefs.set('filter', S.filter); render(); break;
    case 'focus': setFocus(id, false); break;
    case 'approve': await doApprove(id); if (S.drawer && S.drawer.id === id) loadDrawer(); break;
    case 'unapprove': try { const r = await sapi(`/rule/${id}/unapprove`, {}); toast('Sent back to review', r.batch); await refresh(); if (S.drawer) loadDrawer(); } catch (e) { toastErr(e); } break;
    case 'deny':
      if (t.dataset.from === 'drawer') { S.drawer.confirmDeny = true; renderDrawerBody(); $('#denyNote') && $('#denyNote').focus(); break; }
      S.confirm = id; setFocus(id, false); render(); setTimeout(() => $('#denyNote') && $('#denyNote').focus(), 0); break;
    case 'flag':
      if (t.dataset.from === 'drawer') { S.drawer.confirmFlag = true; renderDrawerBody(); setTimeout(() => $('#flagNote') && $('#flagNote').focus(), 0); break; }
      S.flagging = id; if (S.tab === 'review') setFocus(id, false); render(); setTimeout(() => $('#flagNote') && $('#flagNote').focus(), 0); break;
    case 'flag-cancel': S.flagging = null; if (S.drawer) { S.drawer.confirmFlag = false; renderDrawerBody(); } render(); break;
    case 'flag-confirm': await doFlag(id); break;
    case 'refine-flagged': {
      const r = S.data.byId.get(id);
      await openDrawer(id, { route: 'editor', prefill: r && r.flag_note ? `Tune this rule: ${r.flag_note}` : 'Tune this rule: ' });
      break;
    }
    case 'deny-cancel': S.confirm = null; if (S.drawer) { S.drawer.confirmDeny = false; renderDrawerBody(); } render(); break;
    case 'deny-confirm': await doDeny(id); if (S.drawer && S.drawer.id === id) { S.drawer.confirmDeny = false; loadDrawer(); } break;
    case 'ask': setFocus(id, false); await openDrawer(id, { focusComposer: true }); break;
    case 'open': await openDrawer(id); break;
    case 'open-thread': await openDrawer(id, { thread: +t.dataset.thread }); break;
    case 'show-diff': if (S.diffOpen.has(id)) S.diffOpen.delete(id); else S.diffOpen.add(id); render(); break;
    case 'approve-section':
      try { const res = await sapi('/section/approve', { file: t.dataset.file, path: JSON.parse(t.dataset.path) }); toast(`Approved ${plural(res.count, 'rule')}`, res.batch); await refresh(); } catch (e) { toastErr(e); } break;
    case 'restore': try { const res = await sapi(`/rule/${id}/restore`, {}); toast(`Restored${res.count > 1 ? ` with ${plural(res.count - 1, 'sub-rule')}` : ''}`); await refresh(); if (S.drawer) loadDrawer(); } catch (e) { toastErr(e); } break;
    case 'undo': t.closest('.toast') && t.closest('.toast').remove(); await doUndo(t.dataset.batch); break;
    case 'jump': { const el = document.getElementById(t.dataset.target); if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' }); break; }
    case 'sort': {
      const tb = t.dataset.table; const key = t.dataset.key; const cur = S.sort[tb];
      S.sort[tb] = { key, dir: cur && cur.key === key && cur.dir === 'desc' ? 'asc' : cur && cur.key === key ? 'desc' : (['rule', 'section', 'file', 'title', 'route', 'what', 'how', 'note'].includes(key) ? 'asc' : 'desc') };
      prefs.set('sort', S.sort); render(); break;
    }
    case 'removed-how': S.removedHow = t.dataset.how; render(); break;
    case 'event-group': S.eventGroup = t.dataset.group; render(); break;
    case 'close-drawer': closeDrawer(); break;
    case 'route': S.drawer.route = t.dataset.route; if (S.drawer.route !== 'origin') S.drawer.editId = null;
      if (S.drawer.replyThread) { const th0 = S.drawer.detail && S.drawer.detail.threads.find((x) => x.id === S.drawer.replyThread); if (th0 && S.drawer.route !== 'auto' && th0.route !== S.drawer.route) S.drawer.replyThread = null; }
      renderComposerState(); $('#composeText').focus(); break;
    case 'send': await send(); break;
    case 'reply': S.drawer.replyThread = +t.dataset.thread; S.drawer.route = 'auto'; renderComposerState(); $('#composeText').focus(); break;
    case 'new-thread': S.drawer.replyThread = null; renderComposerState(); $('#composeText').focus(); break;
    case 'clear-edit': S.drawer.editId = null; renderComposerState(); break;
    case 'ask-agent': S.drawer.route = 'origin'; S.drawer.editId = +t.dataset.edit; S.drawer.editLabel = t.dataset.label; S.drawer.replyThread = null; renderComposerState(); $('#composeText').focus(); break;
    case 'show-ctx': await toggleCtx(+t.dataset.edit); break;
    case 'show-kept': S.drawer.showKept = !S.drawer.showKept; renderDrawerBody(); break;
    case 'explain': S.drawer.replyThread = null; await send('Where did this rule come from? Who added it, when, and what led to it?', 'historian'); break;
    case 'cancel-job': try { await sapi(`/job/${t.dataset.job}/cancel`, {}); } catch (e) { toastErr(e); } break;
    case 'revert-job': if (!(await askConfirm('Put the files back the way they were before this agent ran?', 'Revert'))) break;
      try { const res = await sapi(`/job/${t.dataset.job}/revert`, {}); toast(`Reverted ${plural(res.reverted, 'file')}`); await refresh(); loadDrawer(); } catch (e) { toastErr(e); } break;
    case 'agent-check': t.disabled = true; try { S.agent = await api('/api/agent/check', {}); renderAgent(); renderComposerState(); if ($('.modal')) agentModal(); toast(S.agent.ok ? 'Agents connected' : 'Still not connected'); } catch (e) { toastErr(e); } finally { t.disabled = false; } break;
    case 'close-modal': closeModal(); break;
    case 'confirm-ok': { const r = confirmResolve; confirmResolve = null; closeModal(); if (r) r(true); break; }
    case 'save-settings': await saveSettings(); break;
    case 'reindex': await api('/api/history/reindex', {}); toast('Rebuilding the history index in the background'); break;
    case 'shutdown': if (await askConfirm('Stop the Skill Review app? Start it again from the desktop shortcut.', 'Stop the app')) { await api('/api/shutdown', {}); document.body.innerHTML = '<div class="empty"><h3>Skill Review stopped</h3><p>Start it again from the desktop shortcut.</p></div>'; } break;
    default: break;
  }
});

document.addEventListener('input', (ev) => {
  if (ev.target.id === 'search') { S.search = ev.target.value; renderReview(); }
  if (ev.target.id === 'listSearch') { S.listSearch = ev.target.value; render(); }
  if (ev.target.id === 'skillSearch') renderAvailable();
});
document.addEventListener('change', async (ev) => {
  if (ev.target.id === 'fileFilter') { S.file = ev.target.value; render(); }
});
$('#agentPill').addEventListener('click', agentModal);
$('#skillTabs').addEventListener('wheel', (ev) => {
  const el = ev.currentTarget;
  if (el.scrollWidth > el.clientWidth && Math.abs(ev.deltaY) > Math.abs(ev.deltaX)) { el.scrollLeft += ev.deltaY; ev.preventDefault(); }
}, { passive: false });
$('#settingsBtn').addEventListener('click', settingsModal);

// Hovering a rule makes it the one the keyboard shortcuts act on, and it stays highlighted after the pointer
// leaves. Only real pointer movement counts: j/k and the wheel scroll rules under a still pointer, and that must
// not take the focus back from the keyboard.
let lastPointer = null;
$('#main').addEventListener('mousemove', (ev) => {
  if (lastPointer && lastPointer.x === ev.clientX && lastPointer.y === ev.clientY) return;
  lastPointer = { x: ev.clientX, y: ev.clientY };
  if (S.tab !== 'review' || $('.modal')) return;
  const el = ev.target.closest('.rule');
  if (!el || el.classList.contains('context') || el.classList.contains('leaving')) return;
  const id = +el.dataset.id;
  if (id !== S.focus) setFocus(id, false);
});

document.addEventListener('keydown', async (ev) => {
  const tgt = ev.target;
  if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey) && tgt.id === 'composeText') { ev.preventDefault(); await send(); return; }
  if (ev.key === 'Enter' && tgt.id === 'skillPath') { ev.preventDefault(); const b = $('[data-act="open-path"]'); if (b) b.click(); return; }
  if (ev.key === 'Enter' && tgt.id === 'flagNote') { ev.preventDefault(); const b = $('[data-act="flag-confirm"]'); if (b) b.click(); return; }
  if (ev.key === 'Enter' && tgt.id === 'denyNote') { ev.preventDefault(); const b = $('[data-act="deny-confirm"]'); if (b) b.click(); return; }
  if (ev.key === 'Escape') {
    if ($('.modal')) { closeModal(); return; }
    if (S.confirm || S.flagging || (S.drawer && (S.drawer.confirmDeny || S.drawer.confirmFlag))) { S.confirm = null; S.flagging = null; if (S.drawer) { S.drawer.confirmDeny = false; S.drawer.confirmFlag = false; renderDrawerBody(); } render(); return; }
    if (tgt.matches('input, textarea, select')) { tgt.blur(); return; }
    if (S.drawer) { closeDrawer(); return; }
  }
  if (tgt.matches('input, textarea, select') || ev.ctrlKey || ev.metaKey || ev.altKey || $('.modal') || S.drawer) return;
  if (ev.key === '[' || ev.key === ']') {
    const ids0 = S.open.map((x) => x.id); const k0 = ids0.indexOf(S.skill);
    if (ids0.length > 1) await switchSkill(ids0[(k0 + (ev.key === ']' ? 1 : ids0.length - 1)) % ids0.length]);
    return;
  }
  if (ev.key === '+') { ev.preventDefault(); await addSkillModal(); return; }
  if (S.tab !== 'review' || !S.data) return;
  const ids = visibleActionable();
  const k = ids.indexOf(S.focus);
  const key = ev.key;
  if (key === 'j' || key === 'ArrowDown') { ev.preventDefault(); setFocus(ids[Math.min(ids.length - 1, k + 1)] ?? ids[0]); }
  else if (key === 'k' || key === 'ArrowUp') { ev.preventDefault(); setFocus(ids[Math.max(0, k - 1)] ?? ids[0]); }
  else if (key === '/') { ev.preventDefault(); $('#search').focus(); }
  else if (key === 'u' && S.lastBatch) { await doUndo(S.lastBatch); }
  else if (S.focus && ids.includes(S.focus)) {
    if (key === 'a') { await doApprove(S.focus); }
    else if (key === 'd' || key === 'x') { const r = S.data.byId.get(S.focus); if (r && r.kind !== 'frontmatter') { S.confirm = S.focus; render(); setTimeout(() => $('#denyNote') && $('#denyNote').focus(), 0); } }
    else if (key === 'f') { ev.preventDefault(); S.flagging = S.focus; render(); setTimeout(() => $('#flagNote') && $('#flagNote').focus(), 0); }
    else if (key === 'q' || key === 'i') { ev.preventDefault(); await openDrawer(S.focus, { focusComposer: true }); }
    else if (key === 'Enter' || key === 'o') { ev.preventDefault(); await openDrawer(S.focus); }
  }
});

// ---------------------------------------------------------------- polling
let pollTimer = null;
async function pollNow() {
  const v = await api(`/api/version?skill=${encodeURIComponent(S.skill || '')}`);
  S.open = v.open; S.agent = v.agent; S.running = v.running; S.history = v.history;
  renderSkillTabs(); renderAgent(); renderTabs();
  return v;
}
async function poll(soon) {
  clearTimeout(pollTimer);
  if (soon) { pollTimer = setTimeout(poll, 600); return; }
  try {
    const wasRunning = S.running.length;
    const v = await pollNow();
    if (S.skill && !S.open.some((x) => x.id === S.skill)) { await pickSkill(null); }
    if (S.drawer) renderComposerState();
    if (v.v !== S.v) {
      S.v = v.v;
      const typing = document.activeElement && ['search', 'listSearch', 'denyNote', 'flagNote'].includes(document.activeElement.id);
      if (!typing || wasRunning !== S.running.length) await refresh();
      if (S.drawer) await loadDrawer();
    }
  } catch (e) { /* server restarting */ }
  pollTimer = setTimeout(poll, S.running.length ? 1200 : 3000);
}

// ---------------------------------------------------------------- boot
(async function start() {
  try {
    S.boot = await api('/api/boot');
    S.agent = S.boot.agent;
    S.open = S.boot.open;
    const [hs, ht] = parseHash();
    const ids = S.open.map((x) => x.id);
    const first = ids.includes(hs) ? hs : ids.includes(prefs.get('skill')) ? prefs.get('skill') : ids[0] || null;
    await switchSkill(first, ht);
    poll();
    window.addEventListener('hashchange', async () => {
      const [h, tb] = parseHash();
      if (h && S.open.some((x) => x.id === h) && (h !== S.skill || (tb && tb !== S.tab))) await switchSkill(h, tb);
    });
  } catch (e) {
    $('#main').innerHTML = `<div class="empty"><h3>Couldn't load</h3><p>${esc(e.message)}</p></div>`;
  }
})();
