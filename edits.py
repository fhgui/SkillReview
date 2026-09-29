"""Apply the file changes an editing agent proposes.

Claude Code treats everything under ~/.claude as sensitive: a headless agent can't get permission to edit skill
files there. So editing agents only read, and write their changes into their final message in this format; the
app checks them and writes the files itself (after its usual snapshot, so the owner can diff and revert):

    <edit file="roblox-game-design/SKILL.md">
    <old>
    exact text to replace, copied from the file
    </old>
    <new>
    the replacement (empty to delete)
    </new>
    </edit>

    <create file="new-skill/SKILL.md">
    the whole new file
    </create>

Paths are relative to the skills folder (absolute paths inside it are accepted too). All changes apply, or none.
"""
import os
import re

EDIT_RE = re.compile(r'<edit\s+file="([^"]+)"\s*>\s*<old>\n?(.*?)\n?</old>\s*<new>\n?(.*?)\n?</new>\s*</edit>', re.S)
CREATE_RE = re.compile(r'<create\s+file="([^"]+)"\s*>\n?(.*?)\n?</create>', re.S)
BLOCK_RE = re.compile(r'<edit\s+file="[^"]+"\s*>.*?</edit>|<create\s+file="[^"]+"\s*>.*?</create>', re.S)
FENCE_RE = re.compile(r'```[a-zA-Z]*\n((?:<edit|<create)[\s\S]*?)```')

FORMAT_HELP = """You can't write files yourself: Claude Code blocks edits under ~/.claude. Put every change in your \
final message instead, in this exact format, and the app applies them (the owner sees the diff and can revert):

<edit file="SKILL_FOLDER/FILE.md">
<old>
the exact text to replace, copied from the file WITHOUT the line-number prefixes the Read tool shows
</old>
<new>
the replacement text (leave it empty to delete the old text)
</new>
</edit>

<create file="NEW_FOLDER/SKILL.md">
the whole content of a new file
</create>

Paths are relative to the skills folder. Each <old> must match exactly one place in its file; include whole lines \
and enough of them to be unique. Put the blocks at the end of your message, after your short summary."""


class EditError(Exception):
    pass


def parse(text):
    """(operations, message without the blocks). An operation is ('edit', file, old, new) or ('create', file, body)."""
    text = text or ''
    ops = []
    for m in EDIT_RE.finditer(text):
        ops.append(('edit', m.group(1).strip(), m.group(2), m.group(3)))
    for m in CREATE_RE.finditer(text):
        ops.append(('create', m.group(1).strip(), m.group(2)))
    message = FENCE_RE.sub(lambda m: '' if BLOCK_RE.search(m.group(1)) else m.group(0), text)
    message = BLOCK_RE.sub('', message)
    message = re.sub(r'\n{3,}', '\n\n', message).strip()
    return ops, message


def resolve(root, name):
    root = os.path.normpath(os.path.abspath(root))
    name = name.replace('/', os.sep)
    path = os.path.normpath(name if os.path.isabs(name) else os.path.join(root, name))
    if os.path.normcase(os.path.commonpath([path, root])) != os.path.normcase(root) or path == root:
        raise EditError(f'{name} is outside the skills folder')
    if not path.lower().endswith('.md'):
        raise EditError(f'{name}: only .md files can be changed')
    return path


def _replace(content, old, new, label):
    """Replace old with new once. Tries the exact text, then a whole-line match that ignores trailing spaces and
    line endings. Deleting whole lines removes their line breaks too."""
    nl = '\r\n' if '\r\n' in content else '\n'
    body = content.replace('\r\n', '\n')
    old_n = old.replace('\r\n', '\n')
    new_n = new.replace('\r\n', '\n')
    if not old_n.strip():
        raise EditError(f'{label}: an <old> block is empty')
    count = body.count(old_n)
    if count > 1:
        raise EditError(f'{label}: the <old> text appears {count} times; include more lines so it is unique')
    if count == 1:
        start = body.index(old_n)
        end = start + len(old_n)
        if new_n == '' and (start == 0 or body[start - 1] == '\n') and end < len(body) and body[end] == '\n':
            end += 1  # deleting whole lines: take the line break with them
        body = body[:start] + new_n + body[end:]
        return body.replace('\n', nl)
    lines = body.split('\n')
    want = [l.rstrip() for l in old_n.strip('\n').split('\n')]
    hits = [i for i in range(len(lines) - len(want) + 1)
            if [l.rstrip() for l in lines[i:i + len(want)]] == want]
    if not hits:
        raise EditError(f"{label}: couldn't find the <old> text in the file")
    if len(hits) > 1:
        raise EditError(f'{label}: the <old> text appears {len(hits)} times; include more lines so it is unique')
    i = hits[0]
    replacement = [] if new_n.strip('\n') == '' else new_n.strip('\n').split('\n')
    lines[i:i + len(want)] = replacement
    return '\n'.join(lines).replace('\n', nl)


def apply(root, ops):
    """Check every operation first, then write them all. Returns the list of files written (relative)."""
    pending = {}  # path -> new content
    for op in ops:
        path = resolve(root, op[1])
        rel = os.path.relpath(path, root).replace('\\', '/')
        if op[0] == 'create':
            if os.path.exists(path) or path in pending:
                raise EditError(f'{rel} already exists; edit it instead of creating it')
            body = op[2] if op[2].endswith('\n') else op[2] + '\n'
            pending[path] = body
            continue
        if path in pending:
            current = pending[path]
        elif os.path.isfile(path):
            with open(path, encoding='utf-8', newline='') as fh:
                current = fh.read()
        else:
            raise EditError(f'{rel} does not exist')
        pending[path] = _replace(current, op[2], op[3], rel)
    written = []
    for path, content in pending.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(content)
        written.append(os.path.relpath(path, root).replace('\\', '/'))
    return written
