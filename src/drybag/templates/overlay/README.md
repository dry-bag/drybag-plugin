# Your drybag overlay

This repository is the private half of drybag. It holds the things that are yours rather
than any one project's: the standing rules you want every session to start with, the notes
that span projects, and one record per repository binding that repository to this overlay.

**Keep it private.** Nothing here is meant to be published. It may name hosts, accounts and
the paths of environment files; it must never hold a credential, and two scans run to keep it
that way — a pre-commit hook and a workflow on every push.

**It is not drybag.** The tool ships separately, as a public plugin and a command-line
program. This repository carries no code: it carries content drybag reads, and it depends on
a drybag recent enough to understand this layout. That dependency is declared, in
`drybag-overlay.toml` under `requires`, and two things read it: `drybag doctor` reports red
when the drybag running does not satisfy it, and a session in a bound repository says so once
at its start. Neither harness reads it, so an older drybag is told rather than stopped.

## What is where

`drybag setup --overlay <path>` renders twelve files here: the eleven this layout ships, and
`.drybag/manifest.json`, which the scaffold engine writes beside them and which the last table
below accounts for. These are the ones that are yours to fill in:

| Path | What it holds |
|---|---|
| `common/memory/` | notes that belong to you rather than to one project, and your personal standing rules: a note carrying `metadata.startup` is injected in full at the start of every session |
| `common/claude/permissions.json` | **your own** `permissions.allow` rules. `drybag attach` merges this list into a bound repository's `.claude/settings.local.json`, and nothing else in the file is read — a `deny` list here reaches nothing. It ships empty, because a plugin author may never grant a permission; only you may, on your own instance. |
| `common/claude/hooks.json` | **your own** hook entries, which `drybag attach` renders into the plugin it installs for a bound repository on this machine. Ships empty for the same reason. |
| `common/claude/rules/*.md` | **your own** rules, for every bound repository; `projects/<name>/claude/rules/*.md` holds one project's, and a file there replaces the common file of its name. A rule whose frontmatter has `paths:` (`paths: ["src/**/*.py"]`, globs relative to the checkout's root) reaches a session the first time it reads, edits or writes a file one of them matches, once per file in a session; any other is a standing rule, given to every session at its start. No directory ships: create it when you write your first rule. |
| `common/codex/common.rules` | the same, for the other harness: one standing-rule file, which `attach` copies into a bound repository's `.codex/rules/`. Ships with a header comment and nothing else. |
| `projects/<name>/` | one directory per bound repository: its record (`project.toml`), and its notes |

And these are the machinery. Leave them alone unless you know why:

| Path | What it holds |
|---|---|
| `drybag-overlay.toml` | what makes this tree a drybag overlay: `requires` (the drybag this layout needs, which `drybag doctor` and a session's first lines read, above) and `layout = 1`, and nothing of yours, so `drybag overlay upgrade` can raise the floor. `drybag setup --overlay` records a tree only when it carries this file, and `drybag overlay upgrade` refreshes only such a tree. |
| `.pre-commit-config.yaml` | the gitleaks hook, pinned at a revision: the commit-time half of "no credential enters this repository". `drybag setup --overlay` installs it when it makes or records this overlay. |
| `.github/workflows/scan.yml` | the push-time half, with every action pinned to a commit sha. |
| `.github/dependabot.yml` | what keeps those pins from rotting: a monthly grouped pull request that moves each sha and the version comment beside it. You read it before you merge it. |
| `.gitignore` | env files, in every spelling, so a credential cannot be added by accident. |
| `README.md` | this file. |
| `.drybag/manifest.json` | the digest of every file above as `drybag setup --overlay <path>` rendered it. `drybag overlay upgrade` compares against it to tell a file you have edited from one you have not, and refreshes only the second kind. It is the twelfth file, and the one nothing in `templates/overlay/` holds: the scaffold engine writes it when it renders the rest. |

Your `.env` files are denied to the agent by `drybag setup`, which writes those deny rules into
`~/.claude/settings.json` at machine scope — once, for every project on the machine. They are not
kept here, because a second copy of a security rule is a copy that drifts.

## Making it yours

`drybag setup --overlay` made this repository, installed the secret scan, and recorded it as
this machine's overlay. On another machine, clone it and record it there with `drybag setup
--overlay <path>`, which installs the secret scan in that clone too. After that, `drybag attach` binds a repository to it — drybag renders what this
overlay holds into a plugin of its own on each machine, so nothing here is installed by hand — and
`drybag overlay upgrade` refreshes the files you have not edited when a new drybag is
released.
