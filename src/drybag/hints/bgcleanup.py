"""The background-cleanup judge: hints about a cleanup a reap can skip, and never a refusal.

MEASURED 2026-09-02. A session reproducing a timing flake under CPU contention ran this as
ONE backgrounded command::

    for i in $(seq 1 14); do (while :; do :; done) & done
    LOADPIDS=$(jobs -p)
    sleep 2
    PYTHONPATH=$PWD/src .venv/bin/python .../rate.py 15 2>&1 | grep -E '^idle|^under|failures'
    kill $LOADPIDS 2>/dev/null

The harness reaped the parent shell before the `kill` line. The fourteen subshells were
`&`-backgrounded, so they survived, re-parented to PID 1, and busy-looped for 39 minutes at
~56% CPU each -- 7.4 of 10 cores -- until a later session found them. Their owning session had
ended and its PR had merged, so nothing was left that could ever have stopped them, and a
sibling session read the load as ambient contention and held a paid eval run waiting for it
to clear.

WHAT THIS FILE CANNOT FIX, stated so the boundary stays clear: the harness reaps the parent
shell without killing its process group. That is agent-harness behaviour, not repository
behaviour, and nothing here works around it.

WHAT IT CAN DO is name the command SHAPE, which is where the leak is actually authored. Cleanup
expressed as a trailing line of the same command is not cleanup -- it runs only if the shell
survives to reach it, which is exactly the assumption a reap breaks. `trap 'kill ...' EXIT INT
TERM` runs where a trailing line does not, because the shell fires it on the way out however it
leaves. The agent in the session writes the command, so it can spell around any reading here:
every reading is a hint naming the remedy, an honest mistake's convenience, never a refusal.

WHAT THIS MODULE DOES, four readings of that one shape:

1. A backgrounded command that leaks an `&` job with no cleanup (`LEAK_HINT`).
2. A backgrounded command whose FIRST simple command is `sleep` (`SLEEP_HINT`).
3. A command whose last line restores a backup outside a trap (`RESTORE_HINT`).
4. A backgrounded call whose chain ends in `; echo …`, because the harness's completion
   notification reports that echo's exit code and not the code of the command before it
   (`EXIT_ECHO_HINT`). It cannot fire together with 3: a restore is a last command, and so is
   the echo.

THE `&` READING, all three conditions required:

1. The call carries `run_in_background: true`. A foreground command that leaks is a much rarer
   accident, because the shell that would run the trailing line is the one the tool is waiting
   on.
2. The command backgrounds a job with a bare `&`. Read as a TOKEN from `bashscan.tokenize`,
   never as a regex over raw text -- `&&` and `2>&1` are precisely what a text scan gets
   wrong, and the tokenizer already resolves both. Read, too, from a command whose heredoc
   BODIES are gone: a body is standard input to the header's command, never an operator of the
   enclosing shell. `_scan` carries that measurement.
3. No cleanup: no `trap` naming `EXIT` anywhere in the command, and no trailing `wait`, which
   reaps the jobs before the shell exits.

THE `sleep` READING is one condition on top of the flag: the first simple command is `sleep`.
A backgrounded call returns at once -- the harness does not wait for it -- so the sleep buys
nothing and the check after it runs immediately, in the background, unread. Measured
2026-09-05: fifteen such calls, nine 'minutes' of sleeping, 39 seconds of wall clock. Only the
FIRST command counts: `pytest ...; sleep 1` is not the misunderstanding.

THE RESTORE HINT is the one reading NOT keyed on `run_in_background`, deliberately: the shape
it names was measured 2026-09-05 on a FOREGROUND call, which outlived the 120 s timeout, was
backgrounded by the harness and reaped before its restore line, leaving a workflow file
mutated. The flag cannot key it because at PreToolUse time the timeout has not happened. That
is also why its matching is the narrowest thing in this file: it reads every Bash call, so it
fires only on the ROUND TRIP -- an earlier `cp`/`mv` whose source and destination the last
command swaps back -- and stays silent on an ordinary chained copy (`cp src/a build/a; make;
cp build/a dist/a`), which shares the last command's shape and restores nothing.

A lone `cmd &` gets the leak hint too: under `run_in_background: true` the harness ALREADY
backgrounds the whole command, so an inner `&` adds nothing but a child the harness cannot
track and the reap cannot collect.

DEGRADES TOWARD SILENCE at every seam: an unparseable command, a trap this reads too
generously and a `&` nested past the recursion bound all get no hint. A false hint is the
cheaper error and has a known shape: a BITWISE `&` inside an arithmetic expansion
(`echo $((3 & 1))`) tokenizes to a bare `&`, and suppressing `&` inside a run opened by `((`
would also silence `((sleep 1; sleep 2) & echo x)`, a real leak.

WHAT IS NOT COVERED, recorded rather than implied:

- By the `&` and `sleep` readings, a command the harness moves to the background ITSELF, after
  the foreground timeout, with no `run_in_background` in the payload.
- `trap - EXIT`, which REMOVES a trap rather than installing one, reads here as a trap.
- A `&` inside a `$(...)` substitution, or in a body fed to a LANGUAGE interpreter rather than
  a shell (`python3 - <<'PY'`).
- A shell `-c` command string that another option in the same cluster displaces, as in
  `bash -cO extglob '<command>'`: `_nested_programs` takes the token after the cluster and
  only that one.
- A backup copied INTO A DIRECTORY (`cp ci.yml bak/`, `cp -t bak ci.yml`, `cp a b bak`), whose
  path this reader would have to construct rather than read.
- A restore line carrying a redirect (`cp a.bak a 2>/dev/null`), on either side of the round
  trip: this reader counts the redirect pieces as operands.
- A command longer than `MAX_COMMAND_CHARS`, which is not read at all. See that constant.
"""

from __future__ import annotations

import re
import shlex
from itertools import dropwhile
from pathlib import Path
from typing import NamedTuple

from drybag.hints import bashscan

# A named cap (CONTRIBUTING.md#named-caps), and no shipped file changes with it. Above it neither
# this guard nor `pipes` reads the command at all. 64 KiB is far past any command a person or a
# model types and far below where tokenizing costs seconds.
MAX_COMMAND_CHARS = 65_536

# The backgrounding operator, as an operator PIECE. `bashscan.tokenize` emits `&&`, `>&`,
# `&>` and `&>>` as their own compound tokens, so none of them can be mistaken for this --
# which is the whole reason this check reads tokens instead of text.
_BACKGROUND_OPERATOR = "&"

# Pieces that, standing immediately before an `&`, mean that `&` is part of a LONGER operator
# rather than the async one. `|&` (pipe stdout and stderr), `;&` and `;;&` (case fall-through)
# are deliberately absent from `bashscan._COMPOUND_OPERATORS`: for SEGMENTATION every
# piece of them ends a command, so letting them fall through to single characters yields the
# right number of breaks with no extra table. That is correct there and wrong here, so this
# file reads the pieces and then interprets them -- a naive scan for a `&` piece would flag
# `cmd |& grep x`, which backgrounds nothing.
_COMMAND_ENDING_PIECES = frozenset({"|", ";"})

# `trap ACTION SIGNAL...`. Both words are matched as EXACT TOKENS, which is what separates an
# installed trap from a mention of one: `echo 'trap ... EXIT'` tokenizes to
# `['echo', 'trap ... EXIT']` -- the quoted string is ONE token, matching neither -- while
# `trap 'kill ${=PIDS}' EXIT INT TERM` tokenizes to
# `['trap', 'kill ${=PIDS}', 'EXIT', 'INT', 'TERM']`. `INT`/`TERM` are accepted alongside by
# saying nothing about them: only `EXIT` is required, and extra signals are extra tokens.
_TRAP = "trap"
_EXIT_SIGNAL = "EXIT"

# `--` ends option parsing by the same getopt convention every tool here follows, so tokens
# after it are operands rather than words the shell or a command interprets. Without the
# cutoff, `printf '%s\n' -- trap EXIT` would read as a trap and excuse a real leak.
_OPTION_TERMINATOR = "--"

# Shells whose `-c <string>` argument is itself a shell command line, and whose heredoc body
# is a program rather than data. Membership rule: does this binary accept `-c <string>` and
# execute the string as a command line?
_SHELL_INTERPRETERS = frozenset(
    {"ash", "bash", "csh", "dash", "fish", "ksh", "mksh", "rbash", "sh", "tcsh", "zsh"}
)

# A single-dash short-option cluster containing `c`: `-c`, and equally `-lc`, `-ic`. Anchored
# at both ends so a long option (`--check`, `--color`) and an inline value (`-I{}`) never
# match.
#
# THE LEADING CLASS EXCLUDES `c` ON PURPOSE, and this is the whole difference between linear
# and quadratic. `-[A-Za-z]*c[A-Za-z]*\Z` lets the first run consume a `c` too, so on a token
# that has a `c` and then a non-letter -- `-ccc...c0` -- every `c` is a place the engine can
# re-enter the tail, and the tail is itself a linear scan to `\Z`. Measured through `judge`,
# INSIDE the 65,536-char cap this file's own comment says prevents exactly this, on every
# `PreToolUse` call: 20,002-char token 1.09 s, 40,002 4.44 s, 60,002 10.14 s -- the
# 4x-per-doubling signature of O(n^2). With `c` out of the leading class there is exactly one
# position at which the literal can match, so the tail runs at most once, and the same three
# calls take 0.03 s, 0.06 s and 0.13 s -- linear, and the same cost as a token with no `c` in
# it at all, which was never the slow shape. The language matched is unchanged -- "a `-`, then
# ASCII letters, at least one of them a lowercase `c`" -- and
# `test_the_shell_c_flag_matches_exactly_what_the_backtracking_pattern_did` holds that against
# an enumerated probe set rather than against a reading of this comment.
_SHELL_C_FLAG = re.compile(r"\A-[A-Zabd-z]*c[A-Za-z]*\Z")

# A named cap (CONTRIBUTING.md#named-caps) on descent into a command hidden inside a string, and no
# shipped file changes with it. Real nesting rarely goes past one level. Exceeding it stops looking,
# which says nothing: a shell inside a shell inside a shell is a shape nobody reaches by accident.
_MAX_RECURSION_DEPTH = 3

LEAK_HINT = (
    "This command backgrounds a job with `&` and expresses its cleanup as a later line of "
    "the same command. That is not cleanup: the harness can reap the parent shell before the "
    "line is reached, and an `&`-backgrounded child survives the reap, re-parents to PID 1, "
    "and runs until something finds it. Measured 2026-09-02: fourteen busy-loop subshells "
    "leaked this way burned 7.4 of 10 cores for 39 minutes, and the session that started them "
    "had already ended.\n"
    "Do one of these instead:\n"
    "- Put the cleanup in a trap, which runs however the shell exits: "
    "`trap 'kill ${=PIDS}' EXIT INT TERM`. Note `${=PIDS}` and not `$PIDS` -- zsh does not "
    "word-split an unquoted parameter, so `kill $PIDS` passes the whole list as ONE argument "
    "and silently fails.\n"
    "- Or do not background the helper at all. This call already carries "
    "`run_in_background: true`, so the harness is backgrounding the whole command for you; an "
    "inner `&` only adds a child that nothing can reap.\n"
    "- Best of all, size the run to finish in the foreground: parallelise the suite "
    "rather than wait on it."
)

SLEEP_HINT = (
    "This command is backgrounded (`run_in_background: true`) and begins with `sleep`. A "
    "backgrounded call returns immediately -- the harness does not wait for it -- so the sleep "
    "buys nothing and the check after it runs at once, in the background, unread. Measured "
    "2026-09-05: fifteen such calls, nine 'minutes' of sleeping, 39 seconds of wall clock.\n"
    "To wait for a condition, use the Monitor tool with an until-loop, or run the check in the "
    "foreground without the sleep."
)

# `{trap}` carries its own quoting -- the template must NOT wrap it in literal quotes. The
# text is rebuilt from tokens that have already lost theirs, so `cp a.bak "a; touch x"` would
# otherwise be handed back as `trap 'cp a.bak a; touch x' EXIT`: a DIFFERENT command, and one
# whose trap body ends at the `;`. `shlex.quote` per token restores an argv-equivalent line,
# and quoting that line again makes it one word for `trap`. The cost, recorded because it is
# real: a restore that meant to expand something (`cp $BAK $ORIG`) comes back quoted and inert.
# The tokens carry no memory of their quoting, so one of the two readings had to lose, and an
# over-quoted suggestion is visibly wrong to whoever reads it while an under-quoted one runs.
RESTORE_HINT = (
    "This command restores a backup on its last line (`{restore}`). That line runs only if the "
    "shell survives to reach it: a foreground call that outlives the 120 s timeout is "
    "backgrounded by the harness and may be reaped before it, leaving the tree mutated -- "
    "measured 2026-09-05 on a workflow file. Put the restore in a trap, which runs however the "
    "shell exits: `trap {trap} EXIT INT TERM` as the FIRST line."
)

_RESTORE_COMMANDS = frozenset({"cp", "mv"})

# `cp`/`mv` options that name the destination instead of leaving it as the last operand.
# Without them `mv -t reports/ report.json` reads its SOURCE as the destination, which is the
# opposite of what it is.
_TARGET_DIRECTORY_OPTIONS = frozenset({"-t", "--target-directory"})

# The completion notification of a backgrounded call reports the exit code of the LAST
# command in the chain, so `check --run > out.log; echo "exit=$?"` notifies 0 after `check`
# failed -- measured 2026-09-16 on an argparse usage error that exited 2. Advice, never a
# refusal: writing the code into the file is a legitimate use of the chain, as long as the
# notification is not the thing read. Keyed on the background flag, unlike the restore hint:
# a foreground call's status is read from the tool result itself.
EXIT_ECHO_HINT = (
    "This backgrounded command ends in `; {tail}`, so its completion notification will report "
    "that command's exit code, not the code of `{masked}`. Either make `{masked}` the last "
    "command of the chain, or read its status from the output it wrote -- never from the "
    "notification."
)
_ECHO_COMMANDS = frozenset({"echo", "printf"})
# Words that can stand at the front of the segment before the last `;` without naming a
# command anyone typed: the terminators and connectives of a compound statement. The last `;`
# of `for f in a b; do cat $f; done; echo done` falls after `done`, so the masked segment is
# the keyword alone, and the hint would advise making `done` the last command of the chain --
# a command that is not one and that the reader never wrote.
_STATEMENT_KEYWORDS = frozenset({"done", "fi", "esac", "then", "else", "elif", "do", "}", ")"})
# Every operator that ends a simple command, AFTER `operator_pieces` has split welded runs.
# Not `_COMMAND_ENDING_PIECES`: that set is read left of an `&` to decide whether the `&` is
# part of a longer operator; this one is read as the breaks between commands.
_CHAIN_OPERATORS = frozenset({";", "&&", "||", "|", "&"})


def judge(command: str, *, background: bool) -> str | None:
    """The whole contract, as a pure function of the command text and the background flag: the
    hint for one Bash call, or `None`. It never raises on a string input."""
    if len(command) > MAX_COMMAND_CHARS:
        return None
    if background:
        tokens = bashscan.tokenize(command)
        if tokens is not None and _begins_with_sleep(tokens):
            return SLEEP_HINT
        backgrounds, cleaned = _scan(command, depth=0)
        if backgrounds and not cleaned:
            return LEAK_HINT
    hints = [
        hint
        for hint in (_restore_hint(command), _exit_echo_hint(command) if background else None)
        if hint
    ]
    return "\n\n".join(hints) or None


def _begins_with_sleep(tokens: list[str]) -> bool:
    """The first simple command's program is `sleep`, however it is spelled.

    An exact `commands[0][:1] == ["sleep"]` comparison is the bare spelling and no other, so
    `/bin/sleep 30`, `FOO=1 sleep 30`, `env sleep 30` and `( sleep 30 )` all passed it, and
    none of them is adversarial. `command_words` resolves the assignment and wrapper prefixes;
    `Path(...).name` resolves the absolute path.

    `dropwhile`, NOT a filter over the whole segment, and the difference is a measured false
    hint rather than a nicety. A LEADING `(` is skipped because a subshell opens the same
    command, but `X=$(sleep 5) pytest -q` segments to
    `['X=$', '(', 'sleep', '5', ')', 'pytest', '-q']`, and dropping every `(` in it put
    `sleep` at the front of a command that does not begin with `sleep` at all. A `sleep` inside
    a substitution is not a leading `sleep`; after the leading run of `(` the first word decides,
    and here that word is the assignment's own token.

    STILL MISSED, recorded rather than implied: `time sleep 30`, `uv run sleep 30`, and any
    other prefix command `command_words` does not resolve -- it resolves `env` and no launcher.
    """
    commands = bashscan.segments(tokens)
    if not commands:
        return False
    segment = list(dropwhile(lambda t: t == "(", commands[0]))
    words = bashscan.command_words(segment)
    return bool(words) and Path(words[0]).name == "sleep"


def _restore_hint(command: str) -> str | None:
    """A trailing restore with no trap, foreground or not."""
    tokens = bashscan.tokenize(command)
    if tokens is None or _installs_an_exit_trap(tokens):
        return None
    restore = _restores_at_the_end(tokens)
    if not restore:
        return None
    return RESTORE_HINT.format(restore=restore, trap=shlex.quote(restore))


def _exit_echo_hint(command: str) -> str | None:
    """Advice for a backgrounded chain whose last command is an echo after a `;`."""

    tokens = bashscan.tokenize(command)
    if tokens is None:
        return None
    masked = _echo_after_a_semicolon(tokens)
    if masked is None:
        return None
    return EXIT_ECHO_HINT.format(masked=masked[0], tail=masked[1])


def _echo_after_a_semicolon(tokens: list[str]) -> tuple[str, str] | None:
    """``(masked, tail)`` -- the command whose exit code a trailing `; echo …` hides and that
    echo, both as shell text -- or None.

    Only a `;` masks: after `&&` a failing command skips the echo and the chain exits with
    its code, and after `||` the echo runs only when the code is already lost to `||`.
    Newlines arrive as `;` from the tokenizer, so a two-line chain is the same chain -- and
    so does the trailing newline every editor writes, which is why a chain's trailing
    separators are dropped before the breaks are counted. Without that, the last break had an
    empty tail and the flagship shape, authored into a file rather than typed, was read as
    having no echo at all.

    SILENT WHERE THE MASKED SEGMENT IS NOT A COMMAND, which is a false-hint rule rather than
    a tidiness one. A compound statement's terminator (`done`, `fi`) and a second echo both
    stand where a program with a meaningful exit code is expected, and naming either one
    hands the reader advice about a command they never wrote. A backgrounded loop ending in a
    progress echo is exactly the traffic this rule sits in front of, so the cost of naming it
    wrongly is the rule being routed around.
    """

    pieces: list[str] = []
    for token in tokens:
        pieces.extend(bashscan.operator_pieces(token))
    while pieces and pieces[-1] in _CHAIN_OPERATORS:
        pieces.pop()
    breaks = [index for index, piece in enumerate(pieces) if piece in _CHAIN_OPERATORS]
    if not breaks or pieces[breaks[-1]] != ";":
        return None
    tail = pieces[breaks[-1] + 1 :]
    words = bashscan.command_words(tail)
    if not words or Path(words[0]).name not in _ECHO_COMMANDS:
        return None
    start = breaks[-2] + 1 if len(breaks) > 1 else 0
    masked = _command_head(pieces[start : breaks[-1]])
    head = bashscan.command_words(masked)
    if not head:
        return None
    program = Path(head[0]).name
    if program in _STATEMENT_KEYWORDS or program in _ECHO_COMMANDS:
        return None
    return " ".join(masked), _as_shell_text(tail)


def _command_head(segment: list[str]) -> list[str]:
    """The command and its arguments, without its redirects -- imprecise by one token in a
    documented case.

    `cmd > out.log 2>&1` tokenizes to `['cmd', '>', 'out.log', '2', '>&', '1']`: the head
    stops at the first redirect operator, and a lone digit left in front of it is popped as a
    file descriptor.

    The stop is a membership test against `bashscan.REDIRECT_OPERATORS`, not a test of the
    piece's first character. `&>` and `&>>` are redirects that begin with neither `>` nor
    `<`, so the first-character test walked past them and rendered `pytest -q &> out.log` as
    the command's own name.

    THE POP IS A GUESS THE TOKENIZER CANNOT CHECK: a numbered file descriptor
    (`cmd 2> err.log`) and an ordinary trailing numeric argument before an unrelated redirect
    (`sleep 5 > out.log`) tokenize to the identical shape -- `['cmd', '2', '>', 'err.log']`
    next to `['sleep', '5', '>', 'out.log']` -- because tokenizing drops the whitespace that
    is the only thing telling them apart. Gating the pop on `>&`/`<&` does not help: neither
    example has one. So a rendered head can be short by one token (`sleep 5` comes back
    `sleep`); that is an accepted imprecision in an advisory-only reader, not a defect to
    chase here.

    THE LIMIT IS THE TOKEN LIST, NOT THE PROBLEM. Whitespace is what tells the two shapes
    apart, and there is no fix available from the tokens alone -- but the caller holds the
    raw command text and does not pass it down, so an adjacency test on that text would
    settle every one of these cases. Stated precisely because a later change that supplies
    the text is a correct change, not a heuristic sharpened past what it can know.
    """

    head: list[str] = []
    for piece in segment:
        if piece in bashscan.REDIRECT_OPERATORS:
            break
        head.append(piece)
    if len(head) > 1 and head[-1].isdigit():
        head.pop()
    return head


def _backgrounds_a_job(tokens: list[str]) -> bool:
    """Whether these tokens background a job with the async operator.

    The entire correctness of this check is that it is not a text search. `a && b`,
    `cmd 2>&1` and `cmd >file 2>&1 &` all contain the character `&`; only the last one
    backgrounds anything, and `bashscan.tokenize` is what tells them apart -- it emits
    `&&` and `>&` as single compound tokens, leaving a bare `&` to mean one thing.

    THE TOKEN LIST ALONE IS NOT ENOUGH, and testing `"&" in tokens` was a measured hole
    rather than a stylistic simplification. `shlex`'s `punctuation_chars` mode fuses a run
    of adjacent punctuation into ONE token, so `(sleep 300 &)` arrives as
    `['(', 'sleep', '300', '&)']` -- no bare `&` anywhere -- and the deliberate-detach idiom,
    the very thing this guard exists to name, was missed. So was the module docstring's own
    incident command with a single space deleted (`done)& done`). `_installs_an_exit_trap`
    never had the problem because it goes through `bashscan.segments`, which decomposes such
    runs; this was the one place that skipped the decomposition. `bashscan`'s own header
    records the identical defect being fixed inside that module ("A SEPARATOR WELDED TO ITS
    NEIGHBOUR IS STILL A SEPARATOR"), so the lesson was available and simply not applied here.

    Interpreting the pieces, rather than just scanning them, is the other half. `|&`, `;&` and
    `;;&` decompose to single characters by design, and each of their `&`s belongs to a pipe
    or a case fall-through, never to an async operator -- so a `&` directly preceded by `|` or
    `;` is skipped. See `_COMMAND_ENDING_PIECES`.
    """
    for token in tokens:
        pieces = bashscan.operator_pieces(token)
        for index, piece in enumerate(pieces):
            if piece != _BACKGROUND_OPERATOR:
                continue
            if index and pieces[index - 1] in _COMMAND_ENDING_PIECES:
                continue
            return True
    return False


def _installs_an_exit_trap(tokens: list[str]) -> bool:
    """Whether these tokens install a `trap` naming `EXIT`.

    Scanned per SEGMENT so the two words have to belong to the same simple command:
    `echo trap && echo EXIT` is two commands and neither is a trap. Within a segment the
    scan is positional rather than argv0-keyed, because a trap is routinely written inside
    a compound statement -- `for i in 1 2; do trap 'x' EXIT; done` gives a segment whose
    first token is the keyword `do`, and an argv0 test would miss it and wrongly flag.

    Reading a trap too generously says nothing, which is the safe direction here and the reason
    the looser positional test is the right one: the cost of a false trap is a missed hint,
    while the cost of a missed trap is a hint on a command that did the correct thing.
    """
    for segment in bashscan.segments(tokens):
        seen_trap = False
        for token in segment:
            if token == _OPTION_TERMINATOR:
                break
            if token == _TRAP:
                seen_trap = True
            elif seen_trap and token == _EXIT_SIGNAL:
                return True
    return False


class _Copy(NamedTuple):
    """What a `cp`/`mv` simple command moves, once its options are out of the way."""

    sources: tuple[str, ...]
    destination: str
    into_directory: bool


def _copy_operands(command: list[str]) -> _Copy | None:
    """A `cp`/`mv` command's operands, or None when it has too few to move anything.

    OPTIONS FIRST, because position alone is not the answer and reading it as one was the
    defect this replaced: `command[-1]` is the destination only for the zero-option spelling.
    `cp -a X X.bak` and `cp -p X X.bak` are how the measured incident ("mutate, test,
    restore") is ordinarily written, and both were invisible; `mv -t DIR src` was worse than
    invisible, reading a SOURCE as the destination.

    The split is deliberately shallow, and each decision is a direction to be wrong in:

    - Any token starting with `-` is an option and is dropped, INCLUDING a long option with an
      inline value (`--preserve=all`) -- which is why nothing here consumes a following token
      except the two that genuinely take one. A bare `-` is stdin/stdout, so it is an operand.
    - `--` ends option parsing, by the same getopt convention `_OPTION_TERMINATOR` already
      serves in this file; every token after it is an operand however it is spelled.
    - `-t DIR` / `--target-directory[=]DIR` name the destination, and every operand is then a
      source. NOT the inline cluster spelling `-tDIR`, which is dropped whole -- option and
      value together -- leaving the remaining operands to be read positionally and the
      destination wrong (measured: `cp -tDIR a b` reports `b`). Unpacking short-option clusters
      is real work for a spelling nobody writes, `cp -t` being rare already, and what it leaves
      behind is one pair in the backups set that a round trip has to match exactly before
      anything is said. Recorded rather than fixed, in the direction this file records the
      rest of its misses.
    - FEWER THAN TWO OPERANDS and no `-t` (`cp --`, `cp --help`, `cp x`) is not a move at all:
      None, rather than an index error on a command that names no destination.

    `into_directory` records that the destination is a DIRECTORY rather than a file, which the
    command's own shape says two ways: `-t` always means one, and so does a third operand
    (`cp a b DIR` cannot mean anything else). The backup then lives at `DIR/<name>`, a path
    this reader does not construct -- see the module docstring's uncovered list.
    """
    operands: list[str] = []
    destination: str | None = None
    index = 1
    operands_only = False
    while index < len(command):
        token = command[index]
        index += 1
        # `noqa: S105` -- `token` here is a shell word off a command line, never a credential,
        # and `-` is the getopt spelling of stdin/stdout. The rule keys on the variable name.
        if operands_only or not token.startswith("-") or token == "-":  # noqa: S105
            operands.append(token)
        elif token == _OPTION_TERMINATOR:
            operands_only = True
        elif token in _TARGET_DIRECTORY_OPTIONS:
            destination = command[index] if index < len(command) else destination
            index += 1
        elif token.partition("=")[0] in _TARGET_DIRECTORY_OPTIONS and "=" in token:
            destination = token.partition("=")[2]
    if destination is not None:
        return _Copy(tuple(operands), destination, True) if operands else None
    if len(operands) < 2:
        return None
    return _Copy(tuple(operands[:-1]), operands[-1], len(operands) > 2)


def _restores_at_the_end(tokens: list[str]) -> str | None:
    """The trailing restore command as shell text, or None.

    A restore is a ROUND TRIP: `cp X X.bak` early and `cp X.bak X` last -- the same two paths,
    source and destination swapped back -- or a `git restore` / `git checkout --`. Matching the
    swap, rather than only the backup's destination reappearing as a source, is what separates
    a restore from an ordinary chained copy: `cp src/a build/a; make; cp build/a dist/a` and
    `mv r.json runs/1.json; pytest; mv runs/1.json runs/final.json` both end by moving a file
    the command wrote earlier, and neither puts anything back. Firing on those -- and on every
    `cp` that copies output away -- would be the cry-wolf guard people learn to route around.
    `segments`, not a split on bare separators: `(pytest); cp a.bak a` fuses `);` into one
    token, and only `segments` puts the restore in a command of its own.

    The returned text is rebuilt with `shlex.quote` per token, because the tokens reaching here
    have already lost their quoting and the hint invites the reader to run this text.
    """

    commands = bashscan.segments(tokens)
    if len(commands) < 2:
        return None
    last = commands[-1]
    if last[:2] == ["git", "restore"] or last[:3] == ["git", "checkout", "--"]:
        return _as_shell_text(last)
    if last[0] not in _RESTORE_COMMANDS:
        return None
    restore = _copy_operands(last)
    if restore is None or restore.into_directory:
        return None
    backups: set[tuple[str, str]] = set()
    for command in commands[:-1]:
        if command[0] not in _RESTORE_COMMANDS:
            continue
        backup = _copy_operands(command)
        if backup is None or backup.into_directory:
            continue
        backups.add((backup.sources[0], backup.destination))
    if (restore.destination, restore.sources[0]) in backups:
        return _as_shell_text(last)
    return None


def _as_shell_text(command: list[str]) -> str:
    """A tokenized command back as text a shell reads the same way this file read it: words
    quoted, redirect operators bare.

    Quoting every piece alike broke the promise in the commonest shape this file renders.
    `echo "EXIT=$?" >> out.log` came back as ``echo 'EXIT=$?' '>>' out.log``, in which `>>`
    is a literal argument handed to `echo` and the append is gone -- a different command,
    offered to the reader as the one they typed.

    What it still cannot restore is a quoted argument that was punctuation from end to end
    (`echo '>>'`): the tokens carry no memory of their quoting, `tokenize` discards it while
    turning the command into a flat token stream, and nothing downstream can tell the
    argument from the operator it is spelled like. That loss predates this function and is
    recorded in `tokenize` itself; the exchange is one rare argument rendered bare against
    every redirect rendered right.
    """
    return " ".join(
        token if token in bashscan.REDIRECT_OPERATORS else shlex.quote(token) for token in command
    )


def _nested_programs(tokens: list[str], heredocs: list[bashscan.Heredoc]) -> list[str]:
    """Command text this command hands to a shell: `-c` strings and shell-fed heredoc bodies.

    Both are places a whole command line hides from the token stream. A `-c` string is ONE
    token however long the command inside it is, and a heredoc body with a QUOTED delimiter
    is removed from the text entirely by `bashscan.prepare` (which is what lets this
    project's own fixtures and tests be authored at all). So `bash -c 'sleep 9 & wait'` and
    `bash <<'EOF'` / `sleep 9 &` / `EOF` both present this guard with nothing but the word
    `bash` unless their contents are pulled back out and read as the programs they are.

    Only SHELL headers qualify for the heredoc half. A body fed to `cat`/`tee` is data being
    written to a file, and a body fed to `python3` is a different language -- reading either
    as a shell command line would be judging the wrong thing.
    """
    programs: list[str] = []
    for segment in bashscan.segments(tokens):
        shell: str | None = None
        for index, token in enumerate(segment):
            if Path(token).name in _SHELL_INTERPRETERS:
                shell = Path(token).name
            elif shell is not None and _SHELL_C_FLAG.match(token):
                # The NEXT token, for every cluster spelling. Taking "every remaining token"
                # for a non-exact cluster (`-lc`, `-ic`) was measured flagging an ordinary
                # argument: a candidate is re-tokenized without its original quoting, and
                # `tokenize("https://h/p?a=1&b=2")` yields a bare `&`. What this gives up is
                # the shape where another option in the cluster consumes the slot
                # (`bash -cO extglob '<command>'`), recorded in the module docstring.
                programs.extend(segment[index + 1 : index + 2])
                shell = None
    for heredoc in heredocs:
        header = bashscan.tokenize(heredoc.header)
        if header is None:
            continue
        if any(
            Path(token).name in _SHELL_INTERPRETERS
            for segment in bashscan.segments(header)
            for token in segment
        ):
            programs.append(heredoc.body)
    return programs


def _scan(command: str, depth: int) -> tuple[bool, bool]:
    """`(backgrounds a job, cleans up)` for `command` and everything nested in it: cleaning up
    is installing an EXIT trap or ending in `wait`.

    Both halves are collected over the WHOLE command tree rather than judged per simple
    command, because the question this guard asks is about the command as a whole: does it
    leave a process nothing will reap? A cleanup anywhere excuses a `&` written anywhere, which
    is the permissive direction.

    HEREDOC BODIES ARE NOT GRAMMAR, which is why both reads below ask for tokens with every
    body removed rather than `bashscan`'s default text. `prepare` re-inserts an UNQUOTED body
    into the scanned text, and that is right for a caller matching paths or words, because the
    shell expands such a body. It is wrong here, because a body is standard input to the
    header's command and never an operator of the enclosing shell. Measured, both directions,
    before this was keyed off:

    - `cat > README.md <<EOF` / `see https://h/p?a=1&b=2 for $USER` / `EOF` was flagged. An
      unquoted delimiter is what a writer must use to get `$VAR` expanded, so writing any file
      whose content carries an `&` was flagged.
    - `cat > notes.md <<EOF` / `don't stop for $USER` / `EOF` / `sleep 5 &` was missed. The
      apostrophe opened a quote that never closed, `tokenize` returned `None`, and a real
      unreaped job walked through the `None` path below.

    The bodies that ARE programs keep being judged, unchanged: `prepare` reports every heredoc
    whatever the flag says about the text, and `_nested_programs` picks the shell-fed ones out
    of that list and recurses into them.

    An unparseable command (unbalanced quotes) yields `(False, False)` and gets no hint: the
    token this reader needs is `&`, the one thing a raw-text scan provably gets wrong, so a
    text-level fallback would flag ordinary quoted arguments. With the bodies gone, an
    unbalanced quote in what remains is
    a command no shell would run -- which is the claim this docstring used to make about the
    whole command text, and which the apostrophe measurement above showed to be false while
    a body was part of it.
    """
    tokens = bashscan.tokenize(command, keep_unquoted_bodies=False)
    if tokens is None:
        return False, False
    # `prepare` is called separately for the heredocs alone. `tokenize` runs it internally on
    # the raw command, so the tokens already have comments stripped, line continuations
    # joined, and every unquoted newline rewritten to `;` -- which is what makes the
    # multi-line incident command read as the five separate commands it is. The heredoc list
    # is the same whichever way the flag is set; it is passed here so the two calls cannot
    # drift into reading the command two different ways.
    _text, heredocs = bashscan.prepare(command, keep_unquoted_bodies=False)
    backgrounds = _backgrounds_a_job(tokens)
    commands = bashscan.segments(tokens)
    cleaned = _installs_an_exit_trap(tokens) or (bool(commands) and commands[-1][0] == "wait")
    if depth < _MAX_RECURSION_DEPTH:
        for program in _nested_programs(tokens, heredocs):
            nested_backgrounds, nested_cleaned = _scan(program, depth + 1)
            backgrounds = backgrounds or nested_backgrounds
            cleaned = cleaned or nested_cleaned
    return backgrounds, cleaned
