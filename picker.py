"""Add skill > Browse: the normal Windows "Open" window, on the PC running the app, to find a skill's SKILL.md.

A web page can't hand back the path of a file you pick, but this app runs on the same PC, so it opens the window
itself. It starts in your skills folder and lists SKILL.md files. The window is kept in front of the browser: the
app runs without a window of its own, and Windows won't let it take the focus from the browser you clicked in.
"""
import os
import threading

_open = threading.Lock()


class Busy(Exception):
    pass


def pick_skill_md(start_dir):
    """The chosen file's path, or '' if the window was cancelled. One window at a time."""
    if not _open.acquire(blocking=False):
        raise Busy('The window to find a skill is already open. It may be behind other windows: check the taskbar.')
    try:
        try:  # sharp text on scaled screens instead of a stretched, blurry window
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        try:
            path = filedialog.askopenfilename(
                parent=root, title='Find the skill: pick its SKILL.md',
                initialdir=start_dir if os.path.isdir(start_dir) else os.path.expanduser('~'),
                filetypes=[('Skill file', 'SKILL.md'), ('Markdown files', '*.md'), ('All files', '*.*')])
        finally:
            root.destroy()
        return os.path.normpath(path) if path else ''
    finally:
        _open.release()
