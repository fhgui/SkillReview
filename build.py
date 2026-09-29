"""Builds dist/SkillReview.exe: one file with Python, the app and its web files inside, so users need nothing else.

    python -m pip install -r requirements-build.txt
    python build.py

The GitHub release workflow (.github/workflows/release.yml) runs the same two commands. Nothing from data/ goes
in: the exe keeps its data in %LOCALAPPDATA%\\SkillReview.
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def tcl_env():
    """Add skill > Browse needs tkinter (Tcl/Tk). In a Python 3.13 venv on Windows, Tcl can't find its files (they
    stay in the base install's tcl folder) and PyInstaller then silently leaves tkinter out. Point Tcl there."""
    env = dict(os.environ)
    for var, pattern in (('TCL_LIBRARY', 'tcl8.*'), ('TK_LIBRARY', 'tk8.*')):  # the folders, e.g. tcl\tk8.6
        found = sorted(d for d in glob.glob(os.path.join(sys.base_prefix, 'tcl', pattern)) if os.path.isdir(d))
        if var not in env and found:
            env[var] = found[-1]
    probe = subprocess.run([sys.executable, '-c', 'import tkinter; tkinter.Tcl()'], env=env, capture_output=True,
                           text=True)
    if probe.returncode:
        sys.exit('tkinter (Tcl/Tk) does not work in this Python, so Browse would be missing from the exe:\n'
                 + probe.stderr.strip()[-600:])
    return env


def main():
    # absolute paths: PyInstaller resolves relative ones against the spec folder (build/), not this one
    static = os.path.join(ROOT, 'static')
    args = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--onefile',
        '--windowed',  # no console window; stop it from Settings > Stop the app
        '--name', 'SkillReview',
        '--icon', os.path.join(static, 'icon.ico'),
        '--add-data', f'{static}{os.pathsep}static',
        '--distpath', os.path.join(ROOT, 'dist'),
        '--workpath', os.path.join(ROOT, 'build'),
        '--specpath', os.path.join(ROOT, 'build'),
        os.path.join(ROOT, 'server.py'),
    ]
    subprocess.run(args, check=True, cwd=ROOT, env=tcl_env())
    warnings = os.path.join(ROOT, 'build', 'SkillReview', 'warn-SkillReview.txt')
    if os.path.isfile(warnings) and 'missing module named tkinter' in open(warnings, encoding='utf-8').read():
        sys.exit('PyInstaller left tkinter out of the exe, so Browse would not work. See ' + warnings)
    print('Built', os.path.join(ROOT, 'dist', 'SkillReview.exe'))


if __name__ == '__main__':
    main()
