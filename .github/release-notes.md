A local web app for reviewing the rules in your Claude Code skills one by one: approve, flag to tune later, deny
(removes the rule from the file and keeps it in a Removed list), and ask agents where a rule came from or to
refine or move it.

## Install (Windows)

1. Download **SkillReview.exe** below.
2. Double-click it. The app opens in your browser at http://127.0.0.1:8765 and only listens on your own computer.
   Double-click it again any time to bring the page back.
3. Windows may say "Windows protected your PC" because the exe isn't code-signed. Click **More info**, then
   **Run anyway**.

To stop it, open **Settings** (the gear at the top right) and choose **Stop the app**.

For the Ask / Refine / Move agents you need [Claude Code](https://claude.com/claude-code) installed and logged
in on the same computer. Reviewing, approving and denying work without it.

Your votes, removed rules and conversations are saved in `%LOCALAPPDATA%\SkillReview`. The only things that leave
your computer go to Claude through Claude Code: a one-line "are you connected" check when the app starts, and the
questions you ask (with the rule and its history).

**Mac or Linux:** download the source code below and run `python3 server.py --open` (Python 3, nothing to
install).
