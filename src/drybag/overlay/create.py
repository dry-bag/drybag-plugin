"""Create the owner's private overlay: generated on GitHub, or rendered here.

Two sources, one result, both reached through `drybag setup --overlay`. `generate` asks GitHub
to generate a private repository from a public template and clone it — the owner's own
`<owner>/drybag-overlay-template` when they have published one, and the publisher's
otherwise; `render` renders `templates/overlay/` at a path through the scaffold engine, makes a
git repository of it, and touches no network. A template and not a fork: a fork's visibility is
bound to the upstream network and cannot be made private, and an overlay that is not private is
the one outcome this whole area exists to prevent.

`finish` is what every overlay `setup` makes or records runs next: it installs the commit-time
secret scan, and writes nothing into the tree, so the identity record stays the bytes the template
shipped and `overlay upgrade` can refresh it. Generating is idempotent — `gh` may give up on the
clone with the repository already created, so a second run is the ordinary case rather than the
exception — and so is `finish`.

A render writes its identity record last and a clone in the order git takes
(`layout.OVERLAY_FILES`), each into nothing or an empty directory (`fsops.vacant`), and every
refusal after `gh repo create` ends in `UNRECORDED`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from drybag import fsops
from drybag.errors import Failure, Refusal
from drybag.jsonobject import json_object
from drybag.overlay.identity import overlay_fault, segment
from drybag.overlay.layout import IDENTITY_RECORD
from drybag.overlay.template import templates
from drybag.printed import answered, quoted
from drybag.runner import NOT_FOUND, TIMED_OUT, Completed, Runner
from drybag.scaffold import apply, plan

TEMPLATE_REPOSITORY = "drybag-overlay-template"
# Whose public copy of that repository `generate` falls back to when the owner has published none of
# their own. The publisher's account is a fact about where this project's template lives, and
# `scripts/publish_template.py` keeps reading `TEMPLATE_REPOSITORY` alone for its `--name` default:
# what an owner *publishes* is theirs to name, what they *generate from* has this one fallback.
TEMPLATE_PUBLISHER = "dry-bag"
# The fallback named with its host, and never host-relative: `gh` resolves an unqualified
# `OWNER/REPO` on `GH_HOST`, which the runner keeps, so on a GitHub Enterprise host the fallback
# would be whoever owns `dry-bag` there, and the owner's private overlay, whose hooks run in
# every session, would be generated from it. `gh repo create --template` and `gh repo view` look a
# `HOST/OWNER/REPO` name up on that host whatever `GH_HOST` says (measured against gh 2.101.0).
# The owner's own `<owner>/…` probe stays host-relative: that repository is on the owner's host.
TEMPLATE_PUBLISHER_HOST = "github.com"
PUBLISHED_TEMPLATE = f"{TEMPLATE_PUBLISHER_HOST}/{TEMPLATE_PUBLISHER}/{TEMPLATE_REPOSITORY}"
# How long to wait before the one retry, when `gh` says the repository exists and the clone brought
# nothing down. The spike record's *template creation race and renaming* trial did not reproduce
# that race in its one attempt, so this is carried on the strength of reasoning rather than of a
# measurement: generation is asynchronous on GitHub's side and one clean run cannot rule out a slow
# one.
RETRY_WAIT_SECONDS = 10
# What `docs/cli.md` says about the template, and what a failure has to say too. `create:`
# generates from the owner's own copy when they have published one, and from the publisher's public
# copy otherwise, so an account that never published one still has somewhere to generate from. A
# failure that does not say so sends the owner to `gh auth status` for a repository that was never
# there.
TEMPLATE_PRECONDITION = (
    f"`create:` generates from <owner>/{TEMPLATE_REPOSITORY} when the owner has published one, "
    f"and from {PUBLISHED_TEMPLATE} otherwise; `drybag setup --overlay <path>` renders the "
    f"same tree at a path with no network call"
)
# What `gh repo view` prints on stderr, whatever the name, when the repository does not exist or
# this token cannot see it (measured against gh 2.101.0, `drybag/definitely-missing-xyz`):
# `GraphQL: Could not resolve to a Repository with the name '<slug>'. (repository)`, exit 1. It is
# the only thing that tells "not found" from an expired token (`HTTP 401: Bad credentials`, also
# exit 1) and from a network failure, so it is matched as the whole phrase and never as the
# shorter "Could not resolve", which `Could not resolve host` shares. A named string, not a cap:
# it bounds nothing and no shipped file changes with it.
NOT_FOUND_ANSWER = "Could not resolve to a Repository"
# What every refusal after `gh repo create` ends with (`{path}` through `printed.quoted`): the
# repository may exist whatever `gh` answered, and a second `create:` fails on its name.
UNRECORDED = (
    "{slug} may now exist on GitHub, and nothing was recorded in the machine configuration; once "
    "{path} holds a clone of it, record it with `drybag setup --overlay {path}`"
)


@dataclass(frozen=True)
class Created:
    root: Path
    notes: tuple[str, ...]
    # The template repository a `generate` run generated from (`<account>/<name>`), so a caller
    # that reports the run can name it without parsing a note. `None` for `render` and for a
    # directory that was already there and was left alone.
    template: str | None = None


def target_root(root: Path, owner: str, name: str) -> tuple[Path, str]:
    """Where `generate(owner, name, root=root)` would put the overlay, and the folded owner.

    Nothing is created and nothing is asked of the network, which is the point: `setup
    --overlay create:<owner>/<name>` has to be able to refuse the *destination* — a file there,
    say — before it runs `gh repo create` on somebody's account. The
    destination is knowable from the arguments alone, and it used to be computed only by the
    call that had already created the repository.

    Folded before it is validated, because `SEGMENT` has a lowercase leading class and a
    mixed-case GitHub login is ordinary: validating the raw value refused `OctoCat` while another
    command folded it and accepted it, and the refusal said "is not one path segment" about a value
    that plainly is one. Folding is safe: GitHub logins are case-insensitive, and the folded value
    is what reaches the slug and the remote alike.
    """
    account = segment("owner", owner.strip().lower())
    segment("name", name)
    return root / name, account


def render_template(root: Path, name: str) -> Path:
    """Render the shipped template at `root/<name>` through the scaffold engine, and nothing
    else: `render` makes a repository of it, and `scripts/publish_template.py` copies it into the
    template repository's clone."""
    target = root / name
    # **Everything that can refuse runs before anything is created**, and the order is
    # load-bearing rather than tidy: a refusal costs nothing on disk. A write that fails part-way
    # (a full disk) leaves a tree with no identity record, which is written last, and so no tree
    # that `setup` or `create:` takes for an overlay already there.
    #
    # `plan` reads and decides and writes nothing, and it is happy with a root that does not
    # exist yet: `Manifest.read` finds no file and every artifact reads as absent.
    planned = plan(target, templates())
    # The engine writes through `fsops` and walks every component with `O_NOFOLLOW`, but it
    # cannot open a root that is not there yet. `mkdirs_within` creates a target's *parents*, so
    # the instance directory is asked for as the parent of the first file that goes into it —
    # through the same walk, rather than with the `Path.mkdir(parents=True)` this project does
    # not allow into a module that puts files into a repository.
    fsops.mkdirs_within(root, f"{name}/{IDENTITY_RECORD}")
    apply(target, planned)
    return target


def render(target: Path, *, runner: Runner) -> Created:
    """Render the shipped template at `target`, whose parent is a directory, and make it a git
    repository on `main` with no remote.

    The repository is made here and not in `render_template`, which the template's publisher
    also renders through: its scratch tree is cloned over and replaced, and a repository of its
    own there would be pushed as a nested one. A repository is what this owner does next with the
    tree (commit it, give it a private remote), so it is made now, with no remote: the remote is a
    repository that has to exist on GitHub first, and that is the owner's to create.
    """
    rendered = render_template(target.parent, target.name)
    return Created(rendered, _initialise_repository(rendered, runner))


def _initialise_repository(target: Path, runner: Runner) -> tuple[str, str]:
    """The notes a local render ends with: the repository it made, and how to give it a remote.

    A `git` that cannot run is a note and not a failure: the tree is on disk by now, so refusing
    would hide a directory that exists, and the note says the one command that finishes the job.
    `target` was empty or not there before the render (`setup` renders only at a path
    `fsops.vacant` answers for), so there is no repository of the owner's for `git init` to meet.
    """
    rendered = "rendered from the shipped template; no network call was made"
    # `git init` and then the branch, and not `git init -b main`: `-b` arrived in git 2.28, and an
    # older `git` refuses the option, so the one command that makes the repository failed there.
    # `symbolic-ref` names the unborn branch on every version.
    remote = (
        "`git remote add origin <the private repository>` and `git push -u origin main` once you "
        "have created one on GitHub"
    )
    step = "git init"
    done = runner.launch(["git", "init"], target)
    if done.code == 0:
        step = "git symbolic-ref HEAD refs/heads/main"
        done = runner.launch(["git", "symbolic-ref", "HEAD", "refs/heads/main"], target)
    if done.code != 0:
        return (
            rendered,
            f"`{step}` {_ended(done)} ({answered(done)}), so this is not a git repository on main "
            f"yet: run `git init` and `git symbolic-ref HEAD refs/heads/main` in it, then {remote}",
        )
    return (rendered, f"made it a git repository on main with no remote; give it one with {remote}")


def _ended(done: Completed) -> str:
    """How a subprocess that did not succeed ended, in words true of each way: one that could
    not be launched did not run, one that hung did not finish, and one that ran has an exit
    code."""
    if done.code == NOT_FOUND:
        return "could not be run"
    if done.code == TIMED_OUT:
        return "did not finish"
    return f"exited {done.code}"


def _template_for(owner: str, *, root: Path, runner: Runner) -> str:
    """The template repository to generate from: the owner's own when it is published, else the
    publisher's.

    One question, asked once: `gh repo view <owner>/<TEMPLATE_REPOSITORY> --json isTemplate`. A
    template there is used. A repository that is not one, and `gh`'s explicit not-found answer,
    both mean the owner has published nothing, and the publisher's public copy is used.

    **Anything else refuses, and never falls back.** `gh` exits 1 for a missing repository and
    for an expired token alike, so the exit code cannot be read as "not found"; only the phrase
    `NOT_FOUND_ANSWER` on stderr can. A launch failure, a timeout, an authentication or network
    failure and an answer that is not the JSON asked for are each a state in which this run does
    not know whether the owner has a template, and guessing "no" would silently generate the
    overlay of somebody who has one from somebody else's. What `gh` said is quoted through
    `printed.answered`, which clips it.
    """
    mine = f"{owner}/{TEMPLATE_REPOSITORY}"
    theirs = PUBLISHED_TEMPLATE
    probe = runner.launch(["gh", "repo", "view", mine, "--json", "isTemplate"], root)
    if probe.code == 0:
        answer = _is_template(probe.stdout)
        if answer is not None:
            return mine if answer else theirs
    elif probe.code not in (NOT_FOUND, TIMED_OUT) and NOT_FOUND_ANSWER in probe.stderr:
        return theirs
    # `gh` could not be launched, hung, was declined, or answered something this cannot read.
    # `gh repo create` is the irreversible act, and it has not run.
    launched = probe.code not in (NOT_FOUND, TIMED_OUT)
    if not launched:
        what = "could not be run"
    elif probe.code == 0:
        what = "answered something other than the `isTemplate` object it was asked for"
    else:
        what = f"exited {probe.code}"
    raise Failure(
        f"`gh repo view {mine} …` {what} ({answered(probe)}), so it is not known whether "
        f"you have published a template, and nothing was created."
        + ("" if launched else " Install `gh` and authenticate it, or render the overlay locally.")
        + f" {TEMPLATE_PRECONDITION}"
    )


def _is_template(printed_json: str) -> bool | None:
    """`isTemplate` from what `gh repo view --json isTemplate` printed, or `None` for anything
    that is not exactly an object carrying a boolean there."""
    try:
        # Through `jsonobject`, the one reader of a JSON object: an answer that is not one, or is
        # past the parser or its depth bound, says nothing about `isTemplate`.
        document = json_object(printed_json, "`gh repo view`", error=ValueError)
    except ValueError:
        return None
    value = document.get("isTemplate")
    return value if isinstance(value, bool) else None


def generate(
    owner: str,
    name: str,
    *,
    root: Path,
    runner: Runner,
    wait: Callable[[float], None] = time.sleep,
) -> Created:
    """Generate `<owner>/<name>` on GitHub from the template repository and clone it to
    `root/<name>`; `root` is a directory."""
    # One spelling of "where this lands and what the owner is called", shared with the caller
    # that has to ask before it calls (`setup`'s `--overlay create:`); see `target_root`.
    target, owner = target_root(root, owner, name)
    # Through `printed.quoted` in every message, as `UNRECORDED` prints it.
    shown = quoted(str(target))
    if overlay_fault(target) is None:
        # Creating the overlay is idempotent by rule: a second run finds the overlay the first one
        # cloned and must not create it again. Anything else already there but an empty directory
        # is `setup`'s to refuse, before this is called.
        return Created(target, (f"{shown} already exists and was left alone",))
    template = _template_for(owner, root=root, runner=runner)
    slug = f"{owner}/{name}"
    created = runner.launch(
        [
            "gh",
            "repo",
            "create",
            slug,
            "--private",
            "--template",
            template,
            "--clone",
        ],
        root,
    )
    if not fsops.vacant(target):
        return _arrived(target, slug, f"created {slug} from {template}", template)
    if created.code == NOT_FOUND:
        # `gh` could not be launched, so nothing ran on GitHub: a finding about this machine, which
        # `gh repo view` would only ask the same absent binary again, never a traceback.
        raise Failure(
            f"`gh repo create {slug} …` could not be run ({answered(created)}), so nothing was "
            f"created and nothing was cloned. Install `gh` and authenticate it, or render the "
            f"overlay locally: {TEMPLATE_PRECONDITION}"
        )
    if created.code != 0:
        # `gh` ran and declined, or hung until the seam gave up. Its own stderr is the cause — a
        # missing template repository, an expired token, a name already taken — and it is quoted
        # rather than replaced by a guess. The clone is retried below only when `gh` *succeeded*
        # and nothing arrived, which is the one shape the race can take.
        raise Failure(
            f"`gh repo create {slug} …` {_ended(created)} ({answered(created)}), so no tree "
            f"arrived at {shown}. {TEMPLATE_PRECONDITION}. {_unrecorded(slug, target)}"
        )

    # `gh` reported success and nothing arrived. Which of the two failures it was decides whether
    # waiting can help, so ask before retrying: `gh repo view` printing the name back is the
    # evidence the repository exists and the clone raced its generation.
    view = runner.launch(["gh", "repo", "view", slug, "--json", "name", "--jq", ".name"], root)
    raced = view.code == 0 and bool(view.stdout.strip())
    if raced:
        wait(RETRY_WAIT_SECONDS)
    # The clone is retried either way, and that is deliberate: `gh repo view` answering nothing
    # is not proof of absence — a rate limit or an expired token answers the same — and one fast
    # failure is cheaper than refusing to try.
    cloned = runner.launch(["git", "clone", "--", f"git@github.com:{slug}.git", name], root)
    if not fsops.vacant(target):
        note = f"cloned {slug}, generated from {template}, on the second attempt"
        return _arrived(target, slug, note, template)
    raise Failure(
        f"{slug} produced no tree at {shown}: `gh repo create --clone` reported success and "
        f"left nothing, and the "
        + ("retried" if raced else "one further")
        + f" `git clone` exited {cloned.code} ({answered(cloned)}). "
        + (
            "GitHub reports the repository exists, so generation may still be running"
            if raced
            else f"`gh repo view` did not confirm the repository exists ({answered(view)}); "
            f"check `gh auth status`. {TEMPLATE_PRECONDITION}"
        )
        + f". {_unrecorded(slug, target)}"
    )


def _unrecorded(slug: str, target: Path) -> str:
    """`UNRECORDED` for `slug` and its destination `target`."""
    return UNRECORDED.format(slug=slug, path=quoted(str(target)))


def _arrived(target: Path, slug: str, note: str, template: str) -> Created:
    """The tree a clone left at `target`, or, before `finish` runs in it, a refusal that it is
    not an overlay."""
    fault = overlay_fault(target)
    if fault is not None:
        raise Refusal(f"{fault}. {_unrecorded(slug, target)}")
    return Created(target, (note,), template)


def finish(root: Path, *, runner: Runner) -> str:
    """Install the commit-time secret scan, or say why it is not installed.

    The overlay's secret scanning runs gitleaks twice and this is one of the two; the other is
    the push workflow the template ships, which is what makes `--no-verify` not the last word.
    A missing `pre-commit` is a reported finding, never a traceback.
    """
    done: Completed = runner.launch(["pre-commit", "install"], root)
    if done.code == 0:
        return "installed the commit-time secret scan with `pre-commit install`"
    return (
        f"`pre-commit install` did not run ({answered(done)}), so the commit-time secret scan "
        f"is not installed; install pre-commit and run it in {quoted(str(root))}. The push-time "
        "scan still runs"
    )
