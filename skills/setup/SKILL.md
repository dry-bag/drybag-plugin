---
name: setup
description: Configure the machine for drybag — the preset, the personal parameters, and the per-repository git hook. Use on a new computer, when the user asks to install drybag's defaults, or when a commit-message hook is wanted.
---

# Machine setup

1. Run `drybag setup --preset recommended` and relay what it writes to the machine
   configuration file. It asks nothing, and installs no plugin unless `--plugin` names one.
2. For the commit-message hook, run `drybag setup --git-hooks` inside the repository and
   relay what it installed and what existing hook it kept and chained to;
   `drybag setup --git-hooks --uninstall` restores it.
3. A language preference the user wants followed is a personal standing rule, a note with
   `metadata.startup` in the overlay; do not guess it.
4. Offer the private overlay — one repository holding the user's own standing rules,
   cross-project notes and per-project bindings. `setup` is what makes and records it, through
   `--overlay`, and there are three ways in:
   - **They already have one**: `drybag setup --preset recommended --overlay /path/to/it`.
     The path is validated against the overlay's identity record, `drybag-overlay.toml`. An
     overlay made before the record shipped is refused with the file and its keys named: relay
     that, since the user adds the record by hand. Where the overlay lives is the user's choice;
     `drybag doctor` warns when another repository's work tree holds it.
   - **They want one on this machine only for now**: `drybag setup --preset recommended
     --overlay /path/that/does/not/exist` (or an empty directory they can write) renders the
     shipped template there with no network call, makes it a git repository, installs the secret
     scan and records it. Relay the note on giving it a remote, and the note that it replaced the
     overlay the machine recorded before, when there is one.
   - **They want one on GitHub**: `drybag setup --preset recommended --overlay create:<owner>/drybag-private`
     creates it on their GitHub account, from their own published template or the publisher's
     public one, and records it in one step. Creating a repository
     on their account needs their word first, so **ask before running it**: no flag stands in
     for it, and nothing about the overlay happens unless `--overlay` is named at all. A file at
     `~/<name>`, or a directory there holding something that is not an overlay, is refused before
     GitHub is asked anything; relay the refusal rather than moving what is there yourself. A
     refusal after GitHub was asked says the repository may exist and how to record it: relay it.

   Relay whether the secret scan installed.
5. After a drybag release, `drybag overlay upgrade --root PATH --dry-run` says what would
   change. Relay the report, and relay the `ASK FIRST` list separately: those two files can
   grant a capability, so the user agrees to them by name before the run without `--dry-run`.
6. `drybag setup --approval-boundary` prints the deny rule and the sandbox settings that keep
   an agent's shell out of the trust record's directory, and writes nothing. Relay its line for
   the user to set; never write a `sandbox` key or that deny rule yourself. With the sandbox on,
   the agent's shell cannot write the machine directory at all: when `setup` or `memory promote`
   says a person must run it in their own terminal, outside the agent's sandbox,
   relay that sentence and stop — never retry it outside the sandbox yourself.
7. **A plugin is installed only when named**, and only on Claude Code: add `--plugin NAME` once
   per plugin the user chose, never one they did not, as in
   `drybag setup --preset recommended --plugin superpowers`. Codex has no verified non-interactive marketplace source for them, so
   `setup` reports the gap as a note rather than attempting an unverified install. Relay that
   note and tell the user to add those plugins by hand on Codex; do not present the run as
   having configured both harnesses.
8. `--home` and `--machine` point the `--preset` run at a scratch destination instead of the
   real home directory and `~/.config/drybag/config.toml`. They are **not** a dry run — the
   same files are written, just elsewhere — and `--git-hooks` ignores both. Do not offer them
   as a way to preview what `setup` would do; there is no such mode.
