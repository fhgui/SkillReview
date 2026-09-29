A local web app for reviewing the rules in your Claude Code skills one by one: approve, flag to tune later, deny
(removes the rule from the file and keeps it in a Removed list), and ask agents where a rule came from or to
refine or move it.

## What's new in 1.1.0

- **Use it from another PC on your network.** Settings > Other PCs on your network > **Share on my network**
  shows an address and an access code; the other PC signs in once. Other PCs can review, ask and refine, but
  can't change Settings or stop the app. **New code** signs them all out; **Stop sharing** turns it off.
- **Browse for a skill.** Add skill > **Browse…** opens the Windows Open window: pick the skill's SKILL.md.
- **Hover to pick a rule.** The rule under the mouse is the one the keyboard shortcuts act on.
- **One scroll bar with a conversation open.** The page behind it no longer scrolls.

## Install (Windows)

1. Download **SkillReview.exe** below.
2. Double-click it. The app opens in your browser at http://127.0.0.1:8765. Double-click it again any time to
   bring the page back.
3. Windows may say "Windows protected your PC" because the exe isn't code-signed. Click **More info**, then
   **Run anyway**.

To stop it, open **Settings** (the gear at the top right) and choose **Stop the app**.

For the Ask / Refine / Move agents you need [Claude Code](https://claude.com/claude-code) installed and logged
in on the same computer. Reviewing, approving and denying work without it.

Only your own computer can open the app unless you turn on network sharing. The first time you do, Windows asks
whether to allow Skill Review through the firewall: allow it on **Private** networks, and make sure your home
network is set to Private in Windows settings. It's plain HTTP for your home network, so don't share it on
public Wi-Fi.

Your votes, removed rules and conversations are saved in `%LOCALAPPDATA%\SkillReview`. The only things that leave
your computer go to Claude through Claude Code: a one-line "are you connected" check when the app starts, and the
questions you ask (with the rule and its history).

**Mac or Linux:** download the source code below and run `python3 server.py --open` (Python 3, nothing to
install).
