"""Split a skill's markdown into reviewable items, and cut or re-insert blocks of lines.

An item is one bullet (with its wrapped continuation lines), one table row, one paragraph, one fenced code
block, or one frontmatter key. Bullets nest: a bullet's children are the deeper bullets under it, and its
"block" is its own lines plus all of its children's lines.
"""
import hashlib
import re

BULLET = re.compile(r'^(\s*)([-*+]|\d{1,3}[.)])\s+(?:\[([ xX])\]\s+)?(.*)$')
HEADING = re.compile(r'^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$')
HRULE = re.compile(r'^\s{0,3}([-*_])(\s*\1){2,}\s*$')
TABLE = re.compile(r'^\s*\|')
TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')
FENCE = re.compile(r'^\s*(`{3,}|~{3,})')
MARKER = re.compile(r'^\s*(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?')
WORD = re.compile(r'[a-z0-9]+')

STOP = set('the and for are but not you your with this that from they them then than into onto over each '
           'its it\'s was were has have had any all can may use used using one two when what which who '
           'will would should must also only just like more most less very such out off per via'.split())


def split_text(text):
    """Return (newline, lines, trailing_newline) so join_text() can write the file back unchanged."""
    nl = '\r\n' if '\r\n' in text else '\n'
    body = text.replace('\r\n', '\n')
    trailing = body.endswith('\n')
    if trailing:
        body = body[:-1]
    return nl, body.split('\n'), trailing


def join_text(nl, lines, trailing):
    return nl.join(lines) + (nl if trailing else '')


def norm(text):
    """Identity text: no list markers, emphasis or backticks, single spaces."""
    parts = [MARKER.sub('', ln, count=1).strip() for ln in text.split('\n')]
    t = ' '.join(p for p in parts if p)
    t = t.replace('**', '').replace('`', '')
    return re.sub(r'\s+', ' ', t).strip()


def text_hash(text):
    return hashlib.sha1(norm(text).encode('utf-8')).hexdigest()[:16]


def tokens(text, min_len=3):
    return {w for w in WORD.findall(norm(text).lower()) if len(w) >= min_len and w not in STOP}


def _indent(line):
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(' '))


def _structural(line):
    return bool(BULLET.match(line) or HEADING.match(line) or TABLE.match(line) or HRULE.match(line)
                or FENCE.match(line))


def _cells(line):
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return [c.strip() for c in s.split('|')]


def _display_bullet(own, first_content):
    out = [first_content.strip()]
    for ln in own[1:]:
        if not ln.strip():
            out.append('\n\n')
        else:
            out.append(ln.strip())
    s = ' '.join(out)
    return re.sub(r' ?\n\n ?', '\n\n', s)


def parse(text):
    """Parse markdown into {'items': [...], 'headings': [...]}. Line numbers are 0-based, ends exclusive."""
    _, lines, _ = split_text(text)
    n = len(lines)
    items = []
    headings = []
    path = []  # current heading stack of heading dicts
    i = 0

    def add(kind, start, end, own, depth=0, parent=None, display='', **extra):
        it = {
            'kind': kind, 'start': start, 'end': end, 'block_end': end, 'depth': depth, 'parent': parent,
            'text': '\n'.join(own), 'display': display, 'heading': path[-1]['index'] if path else None,
            'section': [h['title'] for h in path],
        }
        it.update(extra)
        it['hash'] = text_hash(it['text'])
        items.append(it)
        return len(items) - 1

    # YAML frontmatter
    if n and lines[0].strip() == '---':
        j = 1
        while j < n and lines[j].strip() != '---':
            j += 1
        if j < n:
            k = 1
            while k < j:
                m = re.match(r'^([A-Za-z0-9_-]+):(.*)$', lines[k])
                if not m:
                    k += 1
                    continue
                start = k
                k += 1
                while k < j and (lines[k][:1] in (' ', '\t') or not lines[k].strip()):
                    k += 1
                end = k
                while end > start + 1 and not lines[end - 1].strip():
                    end -= 1
                own = lines[start:end]
                value = ' '.join(x.strip() for x in [m.group(2)] + own[1:] if x.strip())
                add('frontmatter', start, end, own, display=value, key=m.group(1))
            i = j + 1

    stack = []  # (indent, item index) of open bullets
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        ind = _indent(line)

        mh = HEADING.match(line)
        if mh:
            level = len(mh.group(1))
            while path and path[-1]['level'] >= level:
                path.pop()
            h = {'index': len(headings), 'level': level, 'title': mh.group(2).strip(), 'line': i}
            h['path'] = [x['title'] for x in path] + [h['title']]
            headings.append(h)
            path.append(h)
            stack = []
            i += 1
            continue

        if HRULE.match(line) and not BULLET.match(line):
            stack = []
            i += 1
            continue

        mf = FENCE.match(line)
        if mf:
            fence = mf.group(1)
            j = i + 1
            while j < n and not lines[j].strip().startswith(fence[:3]):
                j += 1
            end = min(j + 1, n)
            while stack and stack[-1][0] >= ind:
                stack.pop()
            add('code', i, end, lines[i:end], depth=len(stack),
                parent=stack[-1][1] if stack else None, display='\n'.join(lines[i:end]))
            i = end
            continue

        mb = BULLET.match(line)
        if mb:
            bind = _indent(line)
            while stack and stack[-1][0] >= bind:
                stack.pop()
            parent = stack[-1][1] if stack else None
            start = i
            i += 1
            while i < n:
                nxt = lines[i]
                if not nxt.strip():
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and _indent(lines[j]) > bind and not _structural(lines[j]):
                        i = j
                        continue
                    break
                if _structural(nxt):
                    break
                i += 1
            own = lines[start:i]
            kind = 'check' if mb.group(3) is not None else 'bullet'
            idx = add(kind, start, i, own, depth=len(stack), parent=parent,
                      display=_display_bullet(own, mb.group(4)), marker=mb.group(2),
                      checked=(mb.group(3) or '').lower() == 'x')
            stack.append((bind, idx))
            continue

        if TABLE.match(line):
            j = i
            while j < n and TABLE.match(lines[j]):
                j += 1
            header = None
            first = i
            if j - i >= 2 and TABLE_SEP.match(lines[i + 1]):
                header = _cells(lines[i])
                first = i + 2
            for r in range(first, j):
                add('row', r, r + 1, [lines[r]], display=' | '.join(_cells(lines[r])), cells=_cells(lines[r]),
                    table_header=header, table_line=i)
            stack = []
            i = j
            continue

        start = i
        while i < n and lines[i].strip() and not _structural(lines[i]):
            i += 1
        own = lines[start:i]
        add('para', start, i, own, display=' '.join(x.strip() for x in own))
        stack = []

    for idx in range(len(items) - 1, -1, -1):
        p = items[idx]['parent']
        if p is not None:
            items[p]['block_end'] = max(items[p]['block_end'], items[idx]['block_end'])
    return {'items': items, 'headings': headings, 'line_count': n}


def chunks(text):
    """Own texts of every item in a fragment of markdown (used to match rules against past edits)."""
    try:
        return [it['text'] for it in parse(text)['items']]
    except Exception:
        return [p for p in re.split(r'\n\s*\n', text) if p.strip()]


def remove_lines(text, start, end):
    nl, lines, trailing = split_text(text)
    del lines[start:end]
    if 0 < start < len(lines) and not lines[start - 1].strip() and not lines[start].strip():
        del lines[start]
    while lines and not lines[-1].strip() and len(lines) > 1 and not lines[-2].strip():
        lines.pop()
    return join_text(nl, lines, trailing)


def insert_lines(text, at, block, spaced=False):
    """Insert block (a list of lines) before line index `at`. spaced=True keeps blank lines around it."""
    nl, lines, trailing = split_text(text)
    at = max(0, min(at, len(lines)))
    block = list(block)
    if spaced:
        if at > 0 and lines[at - 1].strip():
            block.insert(0, '')
        if at < len(lines) and lines[at].strip():
            block.append('')
    lines[at:at] = block
    return join_text(nl, lines, trailing)


def section_end(parsed, section_titles):
    """Line index just past the content of the section with this heading path (before trailing blanks/rules)."""
    heads = parsed['headings']
    target = None
    for h in heads:
        if h['path'] == list(section_titles):
            target = h
    if target is None:
        return None
    end = parsed['line_count']
    for h in heads:
        if h['line'] > target['line'] and h['level'] <= target['level']:
            end = h['line']
            break
    return end
