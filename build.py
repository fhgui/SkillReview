"""Builds dist/SkillReview.exe: one file with Python, the app and its web files inside, so users need nothing else.

    python -m pip install -r requirements-build.txt
    python build.py

The GitHub release workflow (.github/workflows/release.yml) runs the same two commands. Nothing from data/ goes
in: the exe keeps its data in %LOCALAPPDATA%\\SkillReview.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


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
    subprocess.run(args, check=True, cwd=ROOT)
    print('Built', os.path.join(ROOT, 'dist', 'SkillReview.exe'))


if __name__ == '__main__':
    main()
