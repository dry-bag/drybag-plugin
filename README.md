# drybag

Private layers for coding agents, delivered into their sessions with nothing tracked in the
project. One plugin for Claude Code and Codex, one Python package with **no runtime dependencies**.

> **Pre-1.0.** What ships: a private overlay of your own and the commands that deliver it, the
> notes kept in it and synced between machines, a machine restored from it, the session hints, the
> documentation and plan lints, the commit-attribution check, and `drybag doctor`, which reports on
> the result. The hooks file that wires it into a session ships too, so on Claude Code installing
> the plugin is enough to make the hints fire and the memory bundles arrive
> ([what each agent enforces](#what-each-agent-enforces)). The first skills ship with them, and so
> does one command meant for a machine rather than for you — `hook`, which dispatches one harness
> event. **Not yet:** the memory MCP server, an importer for the notes
> you already keep, a visual map of what an overlay delivers, and adapters for Cursor or Hermes —
> each leaves this list in the change that ships it. The [Quickstart](#quickstart) shows the three
> keys that are enough to start a project by hand.
> [docs/cli.md](https://github.com/dry-bag/drybag/blob/main/docs/cli.md) is the reference; the command list below is held
> to the parser by a test, so it is complete for what ships.

A machine that ran this project under its former name starts fresh: none of its old paths, files
or variables is read, and [CHANGELOG.md](https://github.com/dry-bag/drybag/blob/main/CHANGELOG.md) lists what was renamed. The bug
ledger and the code-free method skills left and are not published separately yet: if you rely on them, stay on 0.6.1 for now.

## What drybag does

**Private layers.** Your hooks, skills, agents, allow rules, rules and notes live in a private
overlay: a versioned repository of your own, named by its identity record and with its own upgrade
manifest, rather than a dotfiles sync. `drybag setup --overlay` renders or generates one. It is
layered: `common/`, each tag a project lists (`groups/<tag>/`), `projects/<name>/`, then the machine
file, the later one winning. `drybag attach` binds a checkout to it and delivers it into that
repository's sessions, and what it places in the checkout is hidden from git, so the project tracks
none of it ([What it writes, and where](#what-it-writes-and-where) lists every path);
`drybag detach` takes it back. Your rules (`claude/rules/*.md`) arrive as a standing rule at every
session's start, or, with `paths:` frontmatter, the first time a session reads or edits a file its
globs match. The overlay also *declares* the drybag it needs, in its identity record; `drybag
doctor` reports it when the drybag running is too old — red when this project keeps its notes in
that overlay, a warning when it does not — and a session in a bound repository says so once at its
start.

**Memory.** Notes are Markdown files; `MEMORY.md` is a routing table rendered from them, and a note
counts as yours only once you promote it ([Memory, in one page](#memory-in-one-page)).

**Note sync.** A machine whose machine file says `[sync] enabled = true` commits the overlay's
notes, takes in the other machines' and pushes its own from Claude Code's session hooks, through a
detached job. Only notes move, a secret scan runs before every commit and push, and nothing is
forced ([docs/cli.md](https://github.com/dry-bag/drybag/blob/main/docs/cli.md#note-sync) names every step).

**Machine restore.** `drybag setup --restore --overlay <path>` runs a machine's setup and then
attaches every checkout the overlay's `machine/projects.toml` lists; `--yes` also records the
overlay's notes as reviewed on this machine, and `--sync` switches the note sync on.

**Session hints.** The background-cleanup hints, the pipe-status hint and the note after a red test
run add context and refuse nothing ([what each agent enforces](#what-each-agent-enforces)).

**Documentation and commit checks.** `drybag docs check` holds your documents to their budgets and
link targets, `drybag docs trail` keeps a design-and-plan trail, `drybag plan check` lints the plans
a change touches, and `drybag commit check` finds attribution lines in commit messages, which
`drybag setup --git-hooks` strips as you commit. They are commands, so they run where you put them;
CI is the place for one that has to hold.

The principles behind the design, with dated sources and an honest note where the backing is thin,
are in [docs/methodology/README.md](https://github.com/dry-bag/drybag/blob/main/docs/methodology/README.md).

**This is not a replacement for a process layer such as superpowers.** `drybag setup --plugin
superpowers --plugin context7` installs [superpowers](https://github.com/obra/superpowers) and
[context7](https://github.com/upstash/context7) on Claude Code — both ship in Anthropic's own
official marketplace, so `setup` needs no separate registration step for either. Neither is
installed unless you name it: each runs in every session, and neither is pinned. **Codex has no
verified non-interactive marketplace source for either plugin** (checked against this project's own
spike record and each plugin's own published install instructions, 2026-09-18): install `superpowers`
and `context7` by hand there if you use Codex, the same way you would install any other Codex
plugin — `setup` reports this as a note rather than guessing a marketplace name (nothing is vendored
on a guess).

## What each agent enforces

drybag reaches an agent in three ways, and they do not hold alike on every agent. The
**session notices** add context and refuse nothing (the background-cleanup hints, the pipe-status
hint, the red-run note, the standing rules, the volatile notes); the **repository checks** — the
documentation, plan, commit and trail commands — run where you put them, and hold in CI; and the
**skills** are instructions. Each column is an agent under its name in drybag's registry: `claude` is Claude
Code and `codex` is Codex.

| Surface | `claude` | `codex` |
|---|---|---|
| session notices | context only | not verified |
| repository checks | CI only | CI only |
| skills | instructions only | instructions only |

The background-cleanup hints, from the `bg-cleanup` handler that `drybag hook PreToolUse` runs
on every shell call, name a command the agent runs in the background that leaves an `&` job behind
with no `trap … EXIT` or trailing `wait` to stop it, or that begins with `sleep`, and the remedy;
they never refuse it. They run in every repository, one with no `drybag.toml` or one a session
cannot use included, and the session hears once, when it starts, what it could not use. A repository may switch them off by default in its
`drybag.toml`, and the owner's layers override it: the overlay's files, and the machine file
last ([`docs/cli.md`](https://github.com/dry-bag/drybag/blob/main/docs/cli.md#configuration)
names the key).

The pipe-status hint, from the `pipe-status` handler that runs beside it, names a test run piped into
`tail`, `head` or `grep` in a command whose text keeps no `pipefail` or `PIPESTATUS` (or zsh's spelling of either), where the pipeline's exit status
is the reader's and a red run reads as green, and the remedy; it says so once per session context
and never refuses the command. It reads a flat list of pipelines and nothing else: a command with a substitution or a
heredoc gets no hint. It reads the command alone, so it has no switch and
runs in every repository too.

Where a surface does not reach an agent in the session, the repository checks
you run in CI still hold there, since every agent's changes pass through it. What was measured of Codex
(Codex CLI 0.162.0-alpha.17.2) is its side: it runs a plugin's hooks once they are trusted in
`/hooks`, skips untrusted ones without a message and runs them anyway under
`--dangerously-bypass-hook-trust`, and a blocking `PreToolUse` hook's reason reaches the model.
Codex delivers a failed command on `PostToolUse` with no exit code, so the note after a failing
test run does not speak on Codex. Whether a drybag hook is trusted on Codex is not measured:
nothing here knows how Codex records hook trust, so the table above is where what holds on Codex
is said, and `drybag doctor` has no row for it. Of your overlay, `drybag attach` delivers
Codex its rules alone, copied into `.codex/rules/`; its hooks, skills and agents reach Claude Code
through the plugin `attach` renders and are not delivered to Codex.

## Install

**Released as 0.7.1.** The plugin form below takes that release's tag. `uv tool install
drybag` names no version, so it installs the newest release on PyPI:

As a Claude Code plugin:

```
/plugin marketplace add dry-bag/drybag@v0.7.1
/plugin install drybag@drybag
```

The marketplace in `dry-bag/drybag` fetches the plugin from
[`dry-bag/drybag-plugin`](https://github.com/dry-bag/drybag-plugin) at the release's
tag: a repository holding only what the plugin runs, generated from `dry-bag/drybag` at each
release.

As a command-line tool:

```bash
uv tool install drybag
```

**Requirements: Python 3.11 or newer, and a POSIX system.** The hooks take the `python3` on `PATH`,
or else uv's, both asked from `/` so that no repository file chooses the interpreter. Linux and
macOS are supported and tested; Windows is not. The containment this
project is built on uses `openat` with
`O_NOFOLLOW` and `O_DIRECTORY`, which have no Windows equivalent.

## Quickstart

Three keys in a `drybag.toml` at the root of a repository start a project; every other key
takes the `recommended` preset's default, and [docs/cli.md](https://github.com/dry-bag/drybag/blob/main/docs/cli.md#configuration) lists all
of them, annotated.

```toml
[drybag]
version = "0.7.1"

[project]
name = "widget"          # one lowercase path segment

[memory]
mode = "local-only"
```

`local-only` keeps notes under `.drybag/local/memory/`, one directory per group; `harness`,
the default, has no store for `memory index`. Write one note and render the index:

```bash
mkdir -p .drybag/local/memory/developer
printf -- '---\nname: first-note\ndescription: "When to open this note"\n---\n\nThe note.\n' \
  > .drybag/local/memory/developer/first-note.md
drybag memory index      # renders .drybag/local/memory/MEMORY.md from the notes
drybag doctor            # fourteen checks over this installation, one line; --json has the remedies
```

These notes are inside the repository, so they reach a session as data and never as standing
rules; [The threat model, in one paragraph](#the-threat-model-in-one-paragraph) says why.
Add `.drybag/local/` to `.gitignore` if it is not there already.

The other commands in [Commands](#commands) expect more of a repository than those three keys
create: `docs trail` wants a `docs/roadmap.md`, `docs check` an
`AGENTS.md`. Both are yours to write ([docs/cli.md](https://github.com/dry-bag/drybag/blob/main/docs/cli.md#drybag-docs-trail---check) gives the roadmap's shape), and `docs
check` names `AGENTS.md` until you do. In a repository you would rather grow by hand, add each path as you
start using the command that reads it — [docs/cli.md](https://github.com/dry-bag/drybag/blob/main/docs/cli.md#configuration) lists every default.

## What it writes, and where

drybag writes files. Being specific about which is the point of this section.

| Path | What it is | Written by |
|---|---|---|
| `drybag.toml` | Your project's configuration: committed if you want it in the project, and not needed in a checkout bound to an overlay | you |
| `docs/memory/` (configurable) | The note store, in `in-repo` and `overlay` mode | `drybag memory index` |
| `.drybag/local/memory/` | The note store in `local-only` mode — git-ignored | `drybag memory index` |
| `<store>/MEMORY.md` | The rendered routing index. **Generated — do not hand-edit** | `drybag memory index` |
| `docs/roadmap.md` (configurable) | The forward track; `docs trail` owns only the listing between its two markers | you; `drybag docs trail` rewrites the listing |
| `docs/trail.toml` (beside the roadmap) | Which theme each design or plan document belongs to, and which are not plainly delivered. Read by `docs trail`, never written by it | you |
| `~/.config/drybag/config.toml` | Machine-level settings: `[personal]`; `[guards]`, where `bg_cleanup = false` switches the background hint off on this machine, whatever a repository's `drybag.toml` sets as its default; `[budgets]`, the four budgets that bound what a session loads; `[overlay]`; `[sync]`, whose `enabled = true` `setup --overlay` writes for an overlay it makes; and `[machine]`. `~` is `HOME`, as the harness reads it ([docs/cli.md](https://github.com/dry-bag/drybag/blob/main/docs/cli.md#configuration)) | you, or `drybag setup` |
| `~/.config/drybag/trust.json` | Every approval recorded on this machine, as a versioned record of kinds and subjects, under the same home, the notes you promoted among them (kind `reviewed`) | `drybag memory promote`, which records a note |
| `hooks/hooks.json` and `hooks/run-hook.sh` | The zero-config wiring both harnesses read, and the wrapper they execute. **Shipped in the plugin; never written into a project** | nothing — they are part of the plugin |
| `${CLAUDE_PLUGIN_DATA}/drybag/` | Once-per-session markers and the hook diagnostics log. Deleted with the plugin | the hook dispatcher |
| `.git/drybag/attach.json` (in the git common directory every worktree shares) | What `attach` added to this repository, so `detach` can take exactly that back. A branch cannot write it | `drybag attach` |
| `<overlay>/common/drybag.toml` | Preferences, session budgets, personal values and hint switches for every checkout on a machine that records this overlay, above the repository's `drybag.toml` and below the machine file. Read the way a session reads `drybag.toml`: what this drybag cannot use is ignored with a note, and a gate's input or a fact about the repository is never taken from it | you |
| `<overlay>/projects/<name>/drybag.toml` | The same keys for one project, above the common file: read only for a checkout this machine binds to `<name>`, never for one whose own `drybag.toml` merely names it | you |
| `<overlay>/projects/<name>/project.toml` | The remote this project was first attached with, and when; it binds nothing | `drybag attach`, while it records no remote |
| `.git/drybag/binding` (in the git common directory every worktree shares) | The checkout's binding id: 32 random hexadecimal characters that never leave this machine and are printed only as their first eight. No clone carries it | `drybag attach`, once |
| `~/.local/share/drybag/bindings/<id>.toml` | What this machine binds that id to: the project name, the `origin` at attach time and the checkout. Where the binding is decided, never from a name the repository supplies. `~` is `HOME`; no `XDG_*` variable is read | `drybag attach` |
| `~/.local/share/drybag/delivery/<id>/` | The overlay's hooks, skills and agents rendered as a plugin, `overlay-<id>` in a marketplace `drybag-delivery-<id>`, both named by the binding id, and, when the overlay holds a rule with `paths:`, the `PostToolUse` entry that delivers it, run through whatever launched the drybag that ran `attach`: the plugin's hook wrapper, or an installed package's interpreter. A session runs the plugin from here in place, so each attach rewrites it, and `drybag detach` removes it | `drybag attach` |
| `~/.local/share/drybag/stores/<id>/` | The composed memory store the harness and drybag's own bundles read, named by the binding id: a link to each of the overlay's note groups for this project and one to its `MEMORY.md`, which `autoMemoryDirectory` in the main checkout's `.claude/settings.local.json` names. Each attach makes its links the overlay's current set and leaves a note the harness wrote there; `drybag detach` takes the links and the key, and the directory where nothing else is left | `drybag attach`, in `overlay` mode |
| `AGENTS.md`, `CLAUDE.md`, `drybag.toml` and any other regular file of `<overlay>/projects/<name>/checkout/` (never a `.env*` file or `settings.local.json`) | The files you keep outside the repository, in the root of a new linked worktree of a bound checkout, only where the worktree has nothing at the name and git ignores it. The main checkout is not touched, and `drybag detach` leaves them | the `SessionStart` hook |
| `~/.local/share/drybag/sessions/` | One empty file per session and file a path-scoped rule was said for, both names hashed, so the rule is said once per file in a session. Nothing removes them | the hook dispatcher |
| `~/.local/share/drybag/unreviewed/` | One file per note store, named by a hash of the store's path, holding a digest of the set of its unreviewed notes the last session was told about, so the count line is said only when that set changes. Nothing removes them | the hook dispatcher |

Every write into a repository goes through a path walk that refuses a symlink at any component
and refuses to leave the project root, and replaces files atomically, keeping the mode of the
file it replaced. Nothing is written by `--check` or `plan check`, and the scaffold engine's `plan` phase performs no writes at all.

## What drybag sends where

drybag makes no network request of its own and sends no telemetry: none of its modules imports
one of Python's network modules. The table has two kinds of row. Most are programs drybag
starts, each only when a command its row's **Run by** names runs, and as your user; drybag
installs none of them. The last two are files drybag writes that GitHub runs; drybag runs
none of them. drybag's skills start nothing themselves: what your coding agent runs on their
advice, through its own shell, is a command it chose, and reaches what that command reaches.

`tests/test_outbound.py` walks the Python package and holds the **Program** column, in both
directions, to the package's launches that can reach a network and the files its templates write
under `.github/`. The walk reads syntax, so a launch decided by data flow it does not follow —
dynamic dispatch, aliasing, a value computed at run time, a connection the standard library makes
on the package's behalf — is beyond it; the docstring of
[`tests/outbound/walk.py`](https://github.com/dry-bag/drybag/blob/main/tests/outbound/walk.py) says what it sees and names each such case.
The **Run by**, **Talks to** and **When** columns are kept by hand. The hook wrapper,
`hooks/run-hook.sh`, is not Python and is not walked: it starts `python3` or uv and `git`, the
last to find the project root, and reaches no network.

| Program | Run by | Talks to | When |
|---|---|---|---|
| `git fetch` | the note sync's job, which the session hooks start where the machine file says `[sync] enabled = true` | the overlay's `origin` (note 1) | the note sync on each run |
| `git push` | the note sync's job, which the session hooks start where the machine file says `[sync] enabled = true` | the overlay's `origin` (note 1) | the note sync, when the commits on `main` that `origin` lacks are all notes and pass the secret scan |
| `gh` | `drybag setup --overlay create:<owner>/<name>` | GitHub's API, as the account `gh` acts for, and the clones it makes over git (note 2) | note 2 |
| `git clone` | `drybag setup --overlay create:<owner>/<name>` | `git@github.com:<owner>/<name>.git`, the overlay just created | when `gh repo create --clone` reported success and left nothing at the destination |
| `claude plugin` | `drybag setup --plugin NAME` (`--preset NAME`; `recommended` when omitted), `drybag setup --restore`, `drybag attach` and `drybag detach` | for `setup`, the marketplace the preset names for Claude Code, and the named plugins' sources; for `setup --restore`, the marketplaces and plugins your overlay's `machine/claude/settings.json` names; for `attach` and `detach`, nothing past this machine (note 3) | `setup` when `--plugin` names a plugin and the preset names a marketplace for Claude Code; `setup --restore` when that file lists a marketplace or an enabled plugin; `attach` on every run; `detach` where the main checkout's `.claude/settings.local.json` names the plugin or its marketplace and no other checkout of the repository is attached |
| `codex plugin` | `drybag setup --plugin NAME` (`--preset NAME`; `recommended` when omitted) | the marketplace the preset names for Codex (note 3) | when `--plugin` names a plugin and the preset names a marketplace for Codex |
| `pre-commit install` | `drybag setup --overlay` | nothing as it installs; GitHub and Go's servers on the first commit after it (note 4) | when it renders an overlay at a path, records an existing one, or with `create:` |
| `.github/workflows/scan.yml` | the overlay's CI on GitHub (note 5) | GitHub, through `gitleaks/gitleaks-action` (note 5) | on every push, pull request and manual run, unless the repository is marked a template |
| `.github/dependabot.yml` | GitHub's Dependabot, from the file the same commands write into the overlay | GitHub: it looks up new releases of the scan workflow's two pinned actions and opens one grouped pull request in the overlay to move them | monthly, once the file is on GitHub |

1. **`git fetch`**: drybag passes every git it starts only `PATH` (its absolute entries),
   `HOME`, `LANG`, `LC_ALL`, `SYSTEMROOT`, `SSH_AUTH_SOCK` and the proxy variables (`http_proxy`, `https_proxy`,
   `HTTPS_PROXY`, `all_proxy`, `ALL_PROXY`, `no_proxy`, `NO_PROXY`) from your environment, so your
   ssh agent and proxy settings apply, and `GIT_SSH_COMMAND` is not used. Without
   `XDG_CONFIG_HOME` and `GIT_CONFIG_GLOBAL`, git reads your global configuration from
   `~/.gitconfig` and `~/.config/git/config` alone: a global configuration either variable points
   elsewhere, and the credential helpers it names, are not read, while the helpers the configuration
   git does read names are used. The note sync's fetch asks the overlay's `origin` for `main` alone,
   with `--no-recurse-submodules` so a submodule's remote is not asked, into a ref of its own, in
   that same environment; it never asks for a password
   (`GIT_TERMINAL_PROMPT=0`, no askpass, no standard input) and runs none of the overlay's hooks.
   Its **`git push`** goes the same way to the same `origin`: the sha it checked to `main`, never
   forced and with no tag. Its secret scan is the `gitleaks` on that same `PATH`, which reaches no
   network; pre-commit's build of the overlay's hook is not read.
2. **`gh`**: `setup --overlay create:` looks up the template, creates the private overlay from it
   with its clone, and asks whether it exists when that clone left nothing. It is asked nothing
   until `setup` has looked at `<home>/<name>`: a file there, or a directory that is not an
   overlay, is refused before any `gh` call, and the overlay an earlier run made there is left
   alone with none. `gh repo create --clone` makes its clone with git, over git's own transport, from github.com or the host
   `GH_HOST` names. drybag passes `gh` your environment, so a `GH_TOKEN` or `GITHUB_TOKEN` there
   is the account it acts as, over the one `gh` is signed in to, and `GH_HOST` can point it at a
   GitHub Enterprise host — while the clone `create:` falls back to is always `git@github.com:`.
3. **`claude plugin`** registers the marketplace the preset names for Claude Code
   (`anthropics/claude-plugins-official` in `recommended`), reads its list of plugins, and
   downloads each plugin `--plugin` named from the source its entry there names, which can be
   another repository. For `attach`, it registers the directory `attach` renders the overlay's
   hooks, skills and agents into, `~/.local/share/drybag/delivery/<id>/`, as a marketplace and
   installs the one plugin in it, whose source is a directory inside it, so neither names a source
   past this machine; `detach` uninstalls that plugin and removes that marketplace. **`codex plugin`** registers the marketplace the preset names for Codex and
   adds the named plugins from it; `recommended` names none, so it does not run. Once
   installed, a plugin runs in every session of its harness and reaches what it reaches.
4. **`pre-commit install`** reaches nothing as it installs. On the first commit after it,
   pre-commit clones `https://github.com/gitleaks/gitleaks` at the commit the overlay's
   `.pre-commit-config.yaml` pins, gitleaks 8.30.1, whose hook is written in Go: pre-commit builds
   it, fetching Go modules through Go's module proxy and checking them against Go's checksum
   database, `sum.golang.org`, and may first download a Go toolchain from Go's own download
   service where none is installed. Go honours `GOPROXY` and `GOSUMDB` from your environment,
   which pre-commit passes on, so those can send each request elsewhere; it honours your
   `GOTOOLCHAIN` only when Go was already installed, since pre-commit sets it to `local` for a Go
   it downloaded.
5. **`scan.yml`** is written by `drybag setup --overlay <path>` and `drybag overlay
   upgrade`; an overlay made with `create:` gets it from the template repository. It checks out the overlay's whole history and runs
   `gitleaks/gitleaks-action` at the commit it pins, v3.0.0. The action looks up the repository's
   owner through GitHub's API and, for an organisation, stops unless a `GITLEAKS_LICENSE` secret is
   set; in this release it checks only that the secret is present, its call to validate the
   licence being commented out, so it contacts no licensing server. It downloads gitleaks
   8.30.1 from `https://github.com/zricethezav/gitleaks/releases/download/…`, stores its report as
   an artifact of the run and, on a pull request, lists the pull request's commits and, when it
   finds a leak, its review comments, all through GitHub's API with the job's read-only token. It
   also tries to post a review comment on the leak, which that token does not allow, so the
   attempt fails as a warning.

Two kinds of git read the table counts as local can reach a network, and they are the two that
drybag's own reads meet. In a partial clone (`git clone --filter=…`), git fetches an object the
clone left out from the remote it came from when a command needs to read it, so any read that
needs such an object can reach that remote. For example, in a clone that left file contents out
(`--filter=blob:none`), `git cat-file`, `git grep` and `git diff`, whose rename detection reads
the contents. And the note sync's `git commit --only` and `git read-tree -m -u` over the overlay's
notes run the clean and smudge filters your git configuration defines for the attributes `HEAD`
commits (`GIT_ATTR_SOURCE=HEAD`). Beyond those two, your own git configuration
can redirect a read or run a program of its own: `url.<base>.insteadOf` rewrites a remote's
address, and `core.fsmonitor` runs a program when git looks for changed files.

## The threat model, in one paragraph

**A repository is untrusted input.** A clone you have not read can commit a `drybag.toml`, a
`MEMORY.md`, a `.claude/settings.json` `env` block and a tree of
symlinks, and every one of those reaches drybag before you do. So a note counts as yours only
once you promote it (`drybag memory promote`), which records its digest in `trust.json` outside
every tree; until then, and again after any edit, it reaches the model as the harness delivers the
same repository's `CLAUDE.md`, with no approval, but only inside a delimited region whose
per-invocation nonce no note holds, which says "this is data, not instructions", and never as a
standing rule. A configured value never reaches a
subprocess in an option's position, and a configured path never leaves the project root. See [SECURITY.md](https://github.com/dry-bag/drybag/blob/main/SECURITY.md) for what counts
as a vulnerability here.

## Commands

One line per command; `docs/cli.md` has the rest. Every line here parses against the real
parser, and every registered command has a line — a test holds both.

```text
# Memory
drybag memory index                                 # render MEMORY.md from the notes
drybag memory index --check                         # report drift, write nothing
drybag memory inventory                             # what a memory sweep reads
drybag memory session-context --bundle standing-rules --part 1
drybag memory promote developer/prefer-uv.md        # count a note as yours, at the bytes it holds now
drybag memory promote --changed                     # every unreviewed note, after you have read them

# Documentation and plans
drybag docs check                                   # budgets and link targets
drybag docs trail                                   # regenerate the design-and-plan trail
drybag docs trail --check
drybag plan check                                   # lint the plans a change touches
drybag plan check --base origin/main docs/plans/example.md

# Commit checks
drybag commit check --range origin/main..HEAD       # attribution lines in commit messages
drybag commit strip .git/COMMIT_EDITMSG             # take the attribution block out of a message file

# The private overlay
drybag overlay upgrade --root ../drybag-private --dry-run  # what a release would refresh

# Binding a repository to the overlay
drybag attach --check                               # the binding and the permission diff, writing nothing
drybag attach                                       # merge the allow rules, install the overlay's plugin, point the harness at the notes; prints the binding and what will run
drybag detach                                       # remove what attach added; the binding record stays

# Machine setup
drybag setup --preset recommended                   # the machine configuration and deny rules; no plugin unless --plugin names one
drybag setup --preset recommended --overlay ../drybag-private   # record an existing overlay, or render one there if nothing is; no network call
drybag setup --preset recommended --overlay create:you/drybag-private  # create one on GitHub from your <owner>/drybag-overlay-template if you published one, else drybag's
drybag setup --restore --overlay ../drybag-private   # restore a machine: machine setup, then attach every checkout the overlay's machine/projects.toml lists
drybag setup --git-hooks                            # install the commit-message hook into this repository
drybag setup --git-hooks --uninstall                # remove it; restore the hook it chained to
drybag setup --approval-boundary                    # print the deny rule and sandbox settings that keep an agent out of the trust record; writes nothing

# Diagnosing an installation
drybag doctor                                       # fourteen checks over this installation, one line
drybag doctor --json                                # every check with its status, detail and remedy

# Internal
drybag hook SessionStart                            # dispatch one harness hook event (internal)
```

Commands that read a project take `--root` (default: the current directory) and `--machine` (read
a machine configuration file other than the default); `memory` commands and `attach` take
`--store` as well. The commands that take neither, and the ones that mean something else by
either, are listed in [Shared flags](https://github.com/dry-bag/drybag/blob/main/docs/cli.md#shared-flags). `--json` is accepted anywhere and
prints one machine-readable object instead of one line. The commands that report a list of
findings — `docs check`, `plan check` — all spell it `findings`,
whatever their summary line calls them; every other command's keys are its own and are listed
with it in [docs/cli.md](https://github.com/dry-bag/drybag/blob/main/docs/cli.md).

Exit codes are the same everywhere: **0** success, **1** findings, **2** a refusal or an
internal error. A caller must never read 2 as permission. One command is deliberately
outside that rule: `drybag hook` degrades open with **0** on an internal error, on every event,
because a bug in drybag must not cost you a tool call, and on `UserPromptSubmit` an exit 2 would
erase what you typed. [docs/cli.md](https://github.com/dry-bag/drybag/blob/main/docs/cli.md) states it.

## Memory, in one page

A note is a Markdown file with frontmatter, under a group directory in the store:

```markdown
---
name: prefer-uv
description: This project uses uv, never pip
index: adding a dependency → use uv add
metadata:
  type: project
  startup: 1
---

Run `uv add`, not `pip install`. The lockfile is committed and CI runs `uv sync --locked`.
```

- **`index:`** is the routing line — the trigger and the answer, not a summary. Where it is
  missing, `memory index` renders the note's `metadata.index` or its description in its place and
  writes nothing into the note, unless the note has neither key and a line outside the index's
  markers in `MEMORY.md` names it: that line becomes its `index:`, written once, and the note reads
  as unreviewed until promoted again.
- **`metadata.startup`** flags the note as a standing rule, injected in full at session start
  and ranked by that number, once you have promoted it; until then it arrives with the volatile
  notes, labelled unreviewed and as data.
- **`metadata.as_of`** dates a volatile note; one past `volatile_ttl_days` is injected with a
  visible warning rather than dropped.
- **`group:`** files a note under a sub-heading inside its section.

`MEMORY.md` is rendered from the notes and is not a file you edit — curation lives in each
note's `index:` line. A second writer appending entries to `MEMORY.md` is expected, and
`memory index` harvests those back into the notes before it re-renders.

`memory.mode` decides where the store is. `harness`, the default where no layer sets it, leaves
memory to the coding agent's own and drybag resolves no store and injects no bundle.
`local-only` (`.drybag/local/memory`, git-ignored) and `in-repo` (committed) both put the notes
**inside the repository**; `overlay` (the composed store, a directory of links under
`~/.local/share/drybag/stores/` into a machine-level overlay shared across your projects) puts
them outside it. In every mode a note is yours only once promoted: `drybag memory promote`
records the digest of its bytes in `trust.json`, which no tree carries, so a note a clone shipped,
one an agent or the harness wrote, and one edited since you promoted it all arrive unreviewed —
inside the delimited region, labelled, and never as a standing rule. A session hears one line,
"N notes are unreviewed", when that set has changed since the last session.

## Skills

`skills/` ships one ported skill (`memory-sweep`) and thin wrappers for the commands the CLI
registers. Every skill is written in action
language — never a harness tool's name — with the per-harness mapping in
[skills/README.md](skills/README.md), and every `drybag …` invocation in a skill is
parsed against the real parser by a test. A wrapper written ahead of its command is listed in
that test until the command ships; none is today.

## Contributing

See [CONTRIBUTING.md](https://github.com/dry-bag/drybag/blob/main/CONTRIBUTING.md) — it states the three rules a change here has to satisfy,
which are not obvious from the code. Security reports go through
[SECURITY.md](https://github.com/dry-bag/drybag/blob/main/SECURITY.md), never a public issue.

## License

MIT. See [LICENSE](LICENSE).
