# Skill Review

A local web app for reviewing the rules in your Claude Code skills one by one.

## Download (Windows)

Get **SkillReview.exe** from the [latest release](../../releases/latest) and double-click it. It opens the app
in your browser and keeps its data in `%LOCALAPPDATA%\SkillReview`. Windows may warn "Windows protected your PC"
because the exe isn't code-signed: click **More info**, then **Run anyway**. Stop it from **Settings > Stop the
app**.

For the Ask / Refine / Move agents you need **Claude Code** (the `claude` command) installed and logged in.
Reviewing, approving and denying work without it.

## Run from source (any system)

**Python 3** (tested with 3.13), standard library only, so there is nothing to install. Run `start.bat`
(Windows), or `py server.py --open` / `python3 server.py --open`. It opens http://127.0.0.1:8765 in your browser
and only listens on your own computer. Run from source, the data lives in `data/` next to the code.

## Your data stays on your computer

Everything the app records lives in its data folder (`data/` next to the code, or `%LOCALAPPDATA%\SkillReview`
for the exe; see [Files](#files)). `.gitignore` keeps `data/` out of git, because it holds your votes, copies of your skills and an index of your Claude Code
transcripts. The app never reads or stores your Claude login: the agents run the `claude` command, which uses
its own login. From Claude Code's settings file it reads only the list of your project folders.

## Skills

Each open skill is a tab along the top; switch with a click or `[` / `]`. **Add skill** (`+`) searches every
skill on this computer (your `~/.claude/skills`, project `.claude/skills` folders, installed plugins and
scheduled tasks), or opens any folder that has a `SKILL.md`. Closing a tab keeps its votes; reopening it brings
them back.

Every skill has its own database, and every list shows one skill at a time. For a skill with several files,
the file picker at the top right narrows every tab to one file.

## What each button does

- **Approve** (`a`): records your sign-off in that skill's database. The skill file is not touched. Approved
  rules drop out of the default **To review** view. If the rule's text changes later, it comes back marked
  "Changed since you approved it", with the change highlighted.
- **Flag** (`f`): approves the rule but marks it "to tune later", with an optional note on what needs tuning.
  It leaves To review and waits in the **Flagged** tab, which shows the note, when you flagged it, and whether
  it has been edited since (with the change highlighted). **Refine** there opens the refine agent with your
  note filled in; **Done** approves it and clears the flag. Flagging a rule also flags its waiting sub-rules.
- **Deny** (`d`): cuts the rule (and its sub-rules) out of the skill file, with an optional reason. It stays in
  the **Removed** tab with its full text and date, and **Restore** puts it back where it was. A copy of the
  file is saved before every change.
- **Ask** (`q`): opens the rule's panel. Type a question or a change; **Auto** routes it to one of five agents:
  - *Where did it come from?* traces the rule through your past Claude sessions.
  - *Ask the agent that wrote it* talks to a stand-in loaded with that session's conversation.
  - *Refine this rule* edits the wording in the skill.
  - *Move to another skill* moves or copies it to another skill, creating one if needed (a new skill opens
    as a tab automatically).
  - *Just ask* answers without changing anything.

  Reply in the same conversation to continue it. No agent writes files itself: Claude Code blocks headless edits
  under `~/.claude`, so Refine and Move agents put their exact changes in their answer and the app applies them
  (only `.md` files inside the skills folder, all or nothing). Every change shows as a diff in the conversation
  with a **Revert** button.

When a rule leaves one open skill and the same text is in another, the Removed list says
"Moved to <skill>" and the rule shows "Moved from <skill>" on the other side.

## Where it came from

The app indexes every Edit/Write of a `.md` file in your Claude Code transcripts
(`~/.claude/projects`). Each rule's panel shows when it was first written, where it was copied or reworded,
which session or subagent did it, and what you said just before. The first index takes about a minute and
updates incrementally after that.

## Agents need Claude Code to be logged in

The agents run `claude -p` on this computer. If the header says "Log in needed": open a terminal, run
`claude`, type `/login`, then click **Check again** in the app.

## Files

In `data/` (the exe: `%LOCALAPPDATA%\SkillReview`):

- `data/app.db`: which skills are open, and settings.
- `data/skills/<skill>/review.db`: that skill's votes, removed rules, conversations and activity log.
- `data/skills/<skill>/backups/`: copies of its files before each deny/restore, and snapshots before each
  agent edit.
- `data/history.db`: the transcript index, shared by all skills (safe to delete; it rebuilds).

## Making a release

Push a version tag and GitHub builds the exe and publishes the release
([.github/workflows/release.yml](.github/workflows/release.yml)):

```
git tag v1.1.0
git push origin v1.1.0
```

To build the exe yourself: `python -m pip install -r requirements-build.txt`, then `python build.py`. It lands in
`dist/SkillReview.exe`.
