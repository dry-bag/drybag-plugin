"""Bash-command scanning shared by the session hints.

The one promise every caller here relies on: this module reports what the TEXT of a command
says, and a caller judges that text. It never runs anything, and it must never hand a caller
LESS text than a shell would execute -- every place below that discards text is a place a
command can hide in, so each one is justified individually and none of them discards text a
shell would run.

Comments are removed BEFORE tokenizing (`strip_comments`), and the lexer's own comment mode
is switched off by `tokenize`. `shlex`'s `commenters='#'` ends a token at ANY unquoted `#`,
which a shell does not do -- a `#` only opens a comment at the START of a word. The
difference is not cosmetic: `shlex` truncated `echo a#b && cat secrets/.env` to
`['echo', 'a']` and dropped the rest of the command on the floor, which made every check
built on these tokens blind to whatever followed.

Heredocs (`scan_heredocs`) are where the same promise was previously broken outright. The
original implementation stripped every heredoc body, quoted delimiter and unquoted alike,
before any check ran -- so `bash <<EOF` / `cat secrets/.env` / `EOF` presented the
scanner with nothing but the word `bash`, and every guard built on it allowed the command.
Two different things were being conflated:

- What a QUOTED delimiter (`<<'TAG'`, `<<"TAG"`, `<<\\TAG`) buys is that the body is not
  EXPANDED -- no variable, no substitution, no glob. It buys nothing about whether the body
  is EXECUTED: `bash <<'EOF'` runs its body exactly as `bash <<EOF` does.
- What the ORIGINAL stripping was for is a genuine and separate need: this repository's own
  guard tests write fixture files whose content legitimately mentions protected names, and a
  scanner that read `cat > tests/x.py <<'EOF'`'s body could never let its own tests be
  authored.

So the two questions are answered separately, and neither answer is allowed to stand in for
the other:

1. QUOTING decides whether the body stays in the text handed back for general scanning. A
   quoted body is inert data as far as expansion goes and is removed, which is what keeps
   fixture authoring possible. An unquoted body is expanded by the shell and is KEPT, so
   every token in it is visible to the caller's ordinary checks.
2. THE COMMAND THE HEREDOC FEEDS decides whether the body is also handed back as COMMAND
   TEXT, regardless of quoting. `scan_heredocs` returns every heredoc it found, each with
   the header line carrying its redirect, so a caller can ask whether that line's command is
   a shell or an interpreter and judge the body as a command in its own right. A body fed to
   `cat`/`tee` is data; a body fed to `bash`/`python3` is a program.

An unterminated QUOTED heredoc still strips to the end of the command -- there is no
terminator to stop at, and the caller treats missing evidence as absence. An unterminated
UNQUOTED heredoc keeps every remaining line, for the same reason its terminated form does.

WHICH `<<` IS A REDIRECT AT ALL is a quoting question too, and answering it by regex alone
was a live false-open (`_quoted_spans`). `_HEREDOC.finditer` used to run against the RAW
line, so a `<<'TAG'` sequence that merely APPEARED INSIDE A QUOTED STRING was taken for a
real quoted heredoc and every following line up to a `TAG` line -- or, with no such line, to
the end of the command -- was deleted from the text every caller reads. `git commit -m "note
<<'E' "` followed by any second line is enough, which makes this reachable by accident and
not only on purpose; the deleted line could equally have been `cat <an env file>` or
`sed -i '' ... scripts/guard.py`. `strip_comments` above already tracks quote state
carefully for exactly this class of question, and `scan_heredocs` now does the same, carrying
the state ACROSS lines so a newline inside a quoted string does not reset it. Quote state
deliberately does NOT advance through a heredoc BODY: a body is data to the shell's parser,
not a place quotes open and close. And when the quotes do not balance -- a command no shell
would run -- the quote-aware pass is thrown away and the scan redone without it, because a
wrong "this is inside a string" verdict HIDES a real heredoc from the caller; that regression
was measured, not imagined (see `scan_heredocs`).

A NEWLINE IS A COMMAND SEPARATOR, and `_newlines_to_separators` is what makes the rest of the
module see that. `shlex` with `whitespace_split = True` discards a newline as ordinary
whitespace, so `segments` never saw a break and a multi-line command collapsed into ONE
segment whose argv0 was the FIRST line's program. Token-based checks survive that -- they
scan every token -- but any argv0-keyed caller then judges the whole command by its first
line: `echo hi` + newline + `rm scripts/guard.py` presented one segment beginning `echo`,
while the same command spelled with `;` presented two. Rewriting the newline to a
space-padded `;` discards nothing and restores the shell's own reading. It must run LAST,
after `strip_comments` and `scan_heredocs`: both are line-based, and a `;` where they expect
a line break would make a comment swallow the rest of the command and a heredoc lose its body
boundaries.

A SEPARATOR WELDED TO ITS NEIGHBOUR IS STILL A SEPARATOR (`operator_pieces`). This is the
same defect as the newline one, and it was found while fixing it: `shlex`'s
`punctuation_chars` mode emits a RUN of adjacent punctuation as ONE token, so `);`, `;;`,
`&;`, `));` and `;>` are none of them members of `_SEPARATORS`, and `segments` carried
straight past them into a single segment. No newline and no obfuscation is needed to reach
it: measured on this module, `(cd /tmp); rm scripts/guard.py` tokenized to
`['(', 'cd', '/tmp', ');', 'rm', 'scripts/guard.py']` and `echo hi;>scripts/guard.py` to
`['echo', 'hi', ';>', 'scripts/guard.py']`, and a membership-only split returned each whole,
as ONE segment -- one an ordinary subshell followed by an ordinary semicolon, the other an
ordinary redirect. `segments` now decomposes such a run into the operators bash reads it as,
which also restores a caller's redirect lookback: the `>` of a fused `;>` becomes the first
token of the new segment, so a caller asking whether the piece before a path is a redirect
operator can see it.

Tokenizing is built on `shlex.shlex` directly rather than `shlex.split`: `punctuation_chars`
is a constructor argument of `shlex.shlex`, not a parameter of the `split()` convenience
function, so `shlex.split(..., punctuation_chars=True)` raises `TypeError`. Constructing the
lexer directly, with `whitespace_split = True`, keeps `punctuation_chars`' behaviour of
surfacing redirects and separators (`>`, `&&`, `||`, `;`, `|`, `&`) as their own tokens.

The heredoc-recognition regex intentionally accepts a narrower shape than bash's full
grammar. It only calls a `<<`/`<<-` a heredoc redirect when: the character before it is not
a word character (excludes the arithmetic shift `1<<3`, which is never a redirect); the
character right after `<<`/`<<-` is not another `<` (excludes the here-string `<<<`, a
different operator entirely); and the delimiter word is followed by whitespace or end of
line, not by another non-space character (excludes `$((1 << 3))`, where the "delimiter"
would otherwise be the digit run before the closing parens). This is deliberately
conservative: a no-space form like `cat<<EOF` (delimiter preceded by a word character) is
also rejected, even though bash accepts it. Missing that shape now leaves its body
unstripped AND unrecognised as a command body -- the body's own tokens are still visible to
the caller's scan, and the newline rewriting above makes each of those lines a segment of
its own, so an argv0-keyed check sees them too; only the shell-body recursion does not run
for it. Wrongly treating an arithmetic shift as a heredoc start, by contrast,
silently discards every following line, which is exactly the false-open failure this module
exists to avoid.

A BACKSLASH-NEWLINE PAIR IS A LINE CONTINUATION (`_strip_line_continuations`), and until
that function existed nothing in the module joined one: `shlex` strips the backslash but
leaves the newline sitting inside the token, so `cat secrets/\\<newline>.env` presented every
path-matching check with a token that never equalled `secrets/.env` -- measured as passing an
env read, a boundary write, a boundary removal (confirmed deleting a real file in a
disposable tree), and a sealed-script invocation. Bash joins the pair both outside quotes and
inside double quotes, but never inside single quotes, where `\\<newline>` stays two literal
characters -- verified with `bash -c` and `od -c` for both directions.
`_strip_line_continuations` matches that exactly rather than stripping the pair everywhere: a
quote-blind global strip would invent a match in an argument bash never produces
(`'secrets/\\<newline>.env'` is not the path `secrets/.env` to bash, only to a scanner that
stopped looking at quotes), which is a false denial, not a bypass -- but it is still a wrong
model of the command, and this module has already paid once for a wrong model of what bash
executes. See the function's own docstring for the placement in `prepare`'s pipeline, which
is exactly as load-bearing as the ordering already documented above for comments, heredocs,
and newlines.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from drybag.hints.runners import is_launcher, names_a_test_run, past_run

_SEPARATORS = frozenset({";", "&&", "||", "|", "&"})
# `shlex`'s `punctuation_chars` mode emits a RUN of adjacent punctuation as ONE token, so a
# separator glued to whatever stands beside it -- `);`, `;;`, `&;`, `));`, `;>` -- is not a
# member of `_SEPARATORS` and `segments` used to carry straight past it. That is the SAME
# argv0-blindness as the newline defect, reached with an ordinary typed `;` and no newline
# anywhere: `(cd /tmp); rm scripts/guard.py` and `echo hi;>scripts/guard.py` each came back
# as ONE segment (measured; see the module docstring). `operator_pieces` below decomposes
# such a run back into the operators bash reads it as.
_PUNCTUATION_CHARS = frozenset("();<>|&")
# The characters that END a command. A run without one of these (`((`, `))`, `>>`, `<<`,
# `<<<`, `<>`) cannot be hiding a separator, so it is handed back untouched -- the narrowest
# rule that closes the hole, and the reason the arithmetic and heredoc shapes are unaffected.
_SEPARATOR_CHARS = frozenset(";&|")
# Multi-character operators that must survive decomposition WHOLE, longest first. Two groups,
# both load-bearing: `&>`/`&>>`/`>&`/`<&`/`>|` contain a separator character but are single
# redirect operators, and splitting them would both invent a command break and destroy the
# lookback a caller uses to find a write target -- asking whether the piece standing before a
# path is a redirect operator; `>>`/`<<`/`<<<`/`<>` carry no separator themselves but can
# share a run with one (`>>;`), and greedy matching keeps them intact there too. Deliberately
# ABSENT: `;;`, `;&`, `;;&` and `|&` -- every piece of those ends a command, so letting them
# fall through to single characters yields the right number of breaks with no extra table.
_COMPOUND_OPERATORS = ("&>>", "<<<", "&>", ">&", "<&", ">|", ">>", "<<", "<>")
# Every redirect operator, spelled as `operator_pieces` hands it back: the compound table
# above -- all nine of whose members are redirects -- plus the two single characters no table
# lists because they need no greedy match. Public because a caller that walks pieces looking
# for where a command's arguments stop cannot get this set from a first-character test:
# `&>` and `&>>` begin with neither `>` nor `<`, and a head that stopped only on those two
# characters rendered them as part of the command's own name.
REDIRECT_OPERATORS = frozenset(_COMPOUND_OPERATORS) | {">", "<"}
# Four delimiter spellings, and the first three mean the same thing to bash: `<<'TAG'`,
# `<<"TAG"` and `<<\TAG` all suppress expansion of the body, while a bare `<<TAG` does not.
# They are one alternation rather than an optional quote group because a backreference to an
# optional group (`(['\"]?)(\w+)\1`) cannot distinguish "no quote" from "quote" without also
# accepting a mismatched pair.
_HEREDOC = re.compile(
    # `(?<!<)` alone, NOT `(?<![\w<])`. The `<` half is load-bearing -- it is what stops a
    # herestring's second and third `<` being read as the start of a heredoc -- but the `\w`
    # half rejected two spellings bash accepts: `cat<<'EOF'` with no space, and `2<<EOF`,
    # where the word character is a file descriptor. Both were measured valid with `bash -n`
    # and by running them, and the first is an ordinary spelling of the multi-line commit
    # this scan exists to stop denying, so the narrower lookbehind reached only half of its
    # own case. Recognising MORE real heredocs is the safe direction here: a quoted body
    # leaves the general text (which is what the shell does with it too) but `scan_heredocs`
    # still REPORTS it, so `bgcleanup._nested_programs` judges it as a program either way.
    # `$((1<<2))` stays unmatched -- the trailing `(?!\S)` rejects it, since `)` follows the
    # tag.
    r"(?<!<)<<-?(?!<)\s*"
    r"(?:(?P<quote>['\"])(?P<quoted_tag>\w+)(?P=quote)|(?P<backslash>\\)?(?P<tag>\w+))"
    r"(?!\S)"
)
# A `#` opens a comment only at the start of a word; these are the characters that end the
# previous word for that purpose (whitespace is handled separately).
_WORD_BOUNDARY = frozenset({";", "&", "|", "(", ")", "<", ">"})
# `FOO=1 BAR=2 pytest`: a shell assignment token, not the command. `\A...\Z`-free on
# purpose -- `.match` already anchors at index 0, and the token's own tail (the value) is
# irrelevant to "is this an assignment", so nothing needs to anchor the end.
_ASSIGNMENT = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*=")
# `env pytest`: a bare wrapper token, not the command. Deliberately not a general argv
# resolver: `env` and `uv run` (`hints/runners.py`) are the only launchers read through, and
# `poetry run`, `npx`, `sudo` and `time` are not, unless they stand behind `uv run`, where every
# word before the first the runner table reads as a test run is the launcher's.
_SINGLE_WRAPPERS = frozenset({"env"})


class Heredoc(NamedTuple):
    """One heredoc redirect found in a command.

    `header` is the source line carrying the `<<TAG` redirect -- the command the body is
    standard input to. `quoted` is True for `<<'TAG'`/`<<"TAG"`/`<<\\TAG`, whose body the
    shell does not expand; `body` is the body's raw text either way, so a caller that wants
    to judge it as a program has it regardless of quoting.
    """

    tag: str
    quoted: bool
    body: str
    header: str


def strip_comments(command: str) -> str:
    """``command`` with its shell comments removed, as a shell would read them.

    Not `shlex`'s own `commenters`, which ends a token at ANY unquoted `#`: that turns
    `git add notes#1.md` into `git add notes` and, worse, truncates `echo a#b && cat
    secrets/.env` to `echo a` -- discarding a real command a shell would run, which is
    the one thing this module must never do. A shell opens a comment only at the start of a
    word.

    A comment runs to the end of its LINE, not to the end of the command: a multi-line
    command whose second line is a comment still runs its third. Quotes are tracked across
    lines for the same reason, so a `#` inside a multi-line quoted string stays in it.

    Runs BEFORE heredoc recognition, which is the order that matters: `echo hi # <<'EOF'`
    is a comment, not a heredoc, and recognising it as one would swallow every following
    line INCLUDING the real commands a shell would go on to run. Stripping comments first
    makes that line carry no redirect at all. The reverse order's own risk -- a `#` inside a
    heredoc BODY being read as a comment -- is not a hole: bash reads it as a comment too
    when the body is executed, and when the body is inert data the text removed is data the
    caller was never entitled to act on.

    Runs BEFORE `_newlines_to_separators` for the mirror-image reason: a comment ends at a
    LINE break, so rewriting newlines first would let `ls # note` swallow every command
    after it.
    """

    kept: list[str] = []
    quote = ""
    escaped = False
    commented = False
    previous = ""  # the last character a shell would still be reading a word from
    for character in command:
        if commented:
            if character != "\n":
                continue
            commented, previous = False, character
        elif escaped:
            escaped = False
        elif quote == "'":
            quote = "" if character == "'" else quote  # single quotes escape nothing
        elif quote:
            if character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
        elif character == "\\":
            escaped = True
        elif character in "'\"":
            quote = character
        elif character == "#" and _starts_a_word(previous):
            commented, previous = True, character
            continue
        kept.append(character)
        previous = character
    return "".join(kept)


def _starts_a_word(previous: str) -> bool:
    """Whether a `#` following ``previous`` opens a comment rather than sitting in a word."""

    return previous == "" or previous.isspace() or previous in _WORD_BOUNDARY


def _quoted_spans(line: str, quote: str) -> tuple[list[bool], str]:
    """``(per-character "inside a quote" flags, quote state at the end of the line)``.

    The quote model is `strip_comments`' own, factored out for `scan_heredocs`: single
    quotes escape nothing, double quotes honour a backslash, and a backslash outside quotes
    escapes the next character. ``quote`` is passed in and handed back so a caller can carry
    the state across lines -- a quoted string may span newlines, and a `<<'TAG'` on its
    second line is text, not a redirect.

    The opening quote character is itself flagged as inside, so is the closing one: what a
    caller asks of this list is "would the shell read the character at this offset as part of
    a quoted word", and both delimiters are.
    """

    flags: list[bool] = []
    escaped = False
    for character in line:
        inside = bool(quote)
        if escaped:
            escaped = False
        elif quote == "'":
            if character == "'":
                quote = ""
        elif quote:
            if character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
        elif character == "\\":
            escaped = True
        elif character in "'\"":
            quote = character
            inside = True
        flags.append(inside)
    return flags, quote


def scan_heredocs(text: str, *, keep_unquoted_bodies: bool = True) -> tuple[str, list[Heredoc]]:
    """``(text with quoted heredoc bodies removed, every heredoc found)``.

    Expects comment-stripped text (see `strip_comments` for why that order); `prepare`
    below is the entry point that guarantees it.

    Only a QUOTED delimiter's body is removed. An unquoted body is expanded by the shell, so
    every line of it stays in the returned text and reaches the caller's ordinary token scan.
    Both kinds are reported in the list, because quoting says nothing about whether the body
    is EXECUTED -- see the module docstring.

    ``keep_unquoted_bodies=False`` removes those bodies too, and is for the one caller shape
    that asks a GRAMMAR question rather than a text one. A body is standard input to the
    header's command; it is never command syntax of the enclosing shell. Keeping it is right
    for a caller matching paths or words, because the shell expands it -- and wrong for a
    caller asking "does this command carry an async operator", for which a bare `&` in a
    README line is data and an apostrophe in one is not an unbalanced quote. Both kinds are
    still reported in the list, so a caller that wants to judge a shell-fed body as a program
    still has it. See `bgcleanup._scan`, which is that caller.

    Multiple redirects on one line (`cmd <<A <<B`) are consumed in order, the way bash reads
    them, rather than only the first: taking one per line and leaving the rest to be
    rediscovered would misalign every body after it.

    A `<<TAG` INSIDE A QUOTED STRING is not a redirect (`_quoted_spans`). Skipping such a
    match is the only direction that can be taken here: treating it as a heredoc deletes
    every following line from the text -- the exact false-open this module exists to prevent,
    and reachable by typing an ordinary `git commit -m "... <<'E' "`. Quote state is carried
    from line to line, because a quoted string may span newlines, but it is deliberately NOT
    advanced through a heredoc BODY: to the shell's parser a body is data, so an apostrophe
    in it opens nothing.

    THE QUOTE-AWARE PASS IS ABANDONED WHEN ITS OWN PREMISE FAILS, and this is not a nicety:
    it was measured turning a deny into an allow. If the quotes do not balance over the lines
    the shell actually parses, then "this `<<` is inside a string" is an unreliable claim, and
    acting on it hides a REAL heredoc from the caller. Measured: `echo "unbalanced` + newline
    + `bash <<'EOF'` + `rm scripts/guard.py` + `EOF` is returned as one `Heredoc` by the two-
    pass scan and as NO heredoc at all under the quote-aware pass alone -- so the only route
    this module offers to a shell-fed body as a PROGRAM (the list a caller like
    `bgcleanup._nested_programs` reads) comes back empty, and a body that is a command line is
    left to whatever a caller's ordinary token scan makes of it. So the pass runs, and if it
    ends still inside a quote the whole scan is redone with quote tracking OFF -- exactly what
    the quote-blind scan did, for exactly the commands that cannot balance their quotes.
    Nothing is given up by that: bash will not run an unbalanced command either, so every
    command a shell would EXECUTE gets the quote-aware answer. The balance is judged only
    over non-body lines, which is why the fallback does not fire for the ordinary
    `cat > x <<'EOF'` / `don't` / `EOF` shape.

    Lines are split on `\\n` alone rather than by `str.splitlines`, which also breaks on
    `\\v`, `\\f`, `\\x1c`-`\\x1e`, `\\x85`, `\\u2028` and `\\u2029`. A shell treats none of
    those as a line break, and since `_newlines_to_separators` now turns every line break
    into a COMMAND SEPARATOR, an invented break would invent a command -- and would also
    silently rewrite such a character inside a quoted argument. `split("\\n")` also preserves
    a trailing newline instead of eating it, which keeps the round trip text-preserving.
    """

    lines = text.split("\n")
    out, found, unbalanced = _scan_lines(
        lines, quote_aware=True, keep_unquoted_bodies=keep_unquoted_bodies
    )
    if unbalanced:
        out, found, _ = _scan_lines(
            lines, quote_aware=False, keep_unquoted_bodies=keep_unquoted_bodies
        )
    return "\n".join(out), found


def _scan_lines(
    lines: list[str], *, quote_aware: bool, keep_unquoted_bodies: bool
) -> tuple[list[str], list[Heredoc], str]:
    """One heredoc pass over ``lines``; see `scan_heredocs` for what the two passes are for.

    Returns the kept lines, the heredocs found, and the quote state left open at the end --
    which is `""` for every command a shell would agree to run, and the signal `scan_heredocs`
    uses to decide that this pass's quote reasoning cannot be trusted.
    """

    out: list[str] = []
    found: list[Heredoc] = []
    index = 0
    quote = ""
    while index < len(lines):
        line = lines[index]
        out.append(line)
        index += 1
        if quote_aware:
            inside, quote = _quoted_spans(line, quote)
        else:
            inside = [False] * len(line)
        for match in _HEREDOC.finditer(line):
            if inside[match.start()]:
                continue  # `<<TAG` inside a string is text the shell never reads as a redirect
            quoted = bool(match.group("quote") or match.group("backslash"))
            tag = match.group("quoted_tag") or match.group("tag")
            body: list[str] = []
            while index < len(lines) and lines[index].strip() != tag:
                body.append(lines[index])
                index += 1
            index += 1  # drop the closing tag line too; harmless if absent
            found.append(Heredoc(tag=tag, quoted=quoted, body="\n".join(body), header=line))
            if not quoted and keep_unquoted_bodies:
                out.extend(body)
    return out, found, quote


def _newlines_to_separators(text: str) -> str:
    """``text`` with every unquoted, unescaped newline rewritten to `;`.

    A newline separates two commands in shell exactly as `;` does, but `shlex` with
    `whitespace_split = True` treats it as ordinary whitespace and drops it, so `segments`
    never sees a break and a multi-line command becomes ONE segment. Every argv0-keyed check
    downstream then judges the whole command by its FIRST line's program -- which is how
    `echo hi` + newline + `rm scripts/guard.py` was allowed while the `;` spelling
    of the same command was denied.

    Runs LAST, after `strip_comments` and `scan_heredocs`, both of which are line-based: a
    comment ends at a line break and a heredoc body is delimited by them, so neither can be
    handed text whose newlines have already become separators.

    A newline INSIDE quotes is a literal character of the word, not a separator, and a
    backslash-newline is a line continuation that separates nothing -- both are left exactly
    as they are, so this substitution never invents a command boundary the shell does not
    have.

    The separator is written SPACE-PADDED, and that is load-bearing rather than cosmetic:
    `shlex`'s `punctuation_chars` mode emits a RUN of adjacent punctuation as ONE token, so a
    bare `;` glued to whatever the previous line ended with produces `;;`, `&;`, `);` or
    `));` -- none of which is in `_SEPARATORS`, so `segments` does not split there and the
    hole this function exists to close stays open. Measured on the proposed module before the
    padding was added: `echo hi;` + newline + `rm scripts/guard.py` tokenized to
    `['echo', 'hi', ';;', 'rm', 'scripts/guard.py']` -- ONE segment, argv0 `echo`,
    allowed. A trailing semicolon is not obfuscation, so the unpadded form would have left a
    one-character variant of the very bypass being fixed.

    `operator_pieces` now decomposes such a run anyway, so the padding is no longer the ONLY
    thing standing there -- and it stays regardless, because the two protections fail
    differently: the padding means a newline separator is never welded in the first place, so
    it survives a future mistake in the operator table (adding `;;` to `_COMPOUND_OPERATORS`,
    say, which is a plausible thing for someone to do since `;;` really is a bash operator,
    would re-fuse exactly this case). Two characters of text for a guarantee that does not
    depend on a table being right.
    """

    out: list[str] = []
    quote = ""
    escaped = False
    for character in text:
        if escaped:
            escaped = False
        elif quote == "'":
            quote = "" if character == "'" else quote  # single quotes escape nothing
        elif quote:
            if character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
        elif character == "\\":
            escaped = True
        elif character in "'\"":
            quote = character
        elif character == "\n":
            out.append(" ; ")
            continue
        out.append(character)
    return "".join(out)


def _strip_line_continuations(text: str) -> str:
    """``text`` with every bash line continuation -- an unescaped `\\` immediately followed
    by a newline -- removed, outside single quotes.

    THE BUG THIS CLOSES: bash joins `\\<newline>` into nothing, both outside quotes and
    inside double quotes (verified with `bash -c`: `cat secrets/\\<newline>.env` reads
    `secrets/.env`; so does the double-quoted form). Before this function existed,
    `_newlines_to_separators` deliberately left such pairs untouched (correctly, for ITS
    OWN job -- see its docstring) and nothing downstream ever removed them, so `tokenize`
    handed `shlex` a token still carrying a literal embedded newline (`secrets/<newline>.env`),
    which `shlex` turns into an escaped-but-not-rejoined token -- not the single joined word
    bash actually reads. Every path-matching check built on `tokenize` inherited the resulting
    hole. Four shapes were measured against a path-matching guard built on these tokens, with
    `TMPDIR` on an empty scratch directory: `cat secrets/\\<newline>.env` (env read),
    `echo x > scripts/\\<newline>guard.py` (boundary write),
    `rm scripts/\\<newline>guard.py` (boundary removal, confirmed executed in a disposable
    tree), and `scripts/\\<newline>sealed.sh TOKEN` (sealed script) all passed it; the removal
    case really did delete the file when run for real.

    QUOTE-AWARE, NOT A NAIVE GLOBAL STRIP -- the direction is load-bearing, not a style
    choice. Inside single quotes bash gives `\\` no special meaning at all: `\\<newline>`
    stays as two literal characters (verified with `od -c`: the backslash AND the newline
    both survive `echo 'secrets/\\<newline>.env'`). A NAIVE strip that removed every
    `\\<newline>` pair regardless of quoting would make the guard see `'secrets/.env'`
    for an argument bash actually reads as `secrets/` + a literal newline + `.env` --
    not a path that exists, and not the one the guard would deny. That is a FALSE DENIAL
    (the naive strip invents a match bash's own argument never contains), which is the
    fail-CLOSED direction a guard must take when its model and bash disagree -- but it
    is still a wrong model of what bash executes, and a wrong model is a liability the
    far side of the ledger has already been burned by (see the module docstring's
    heredoc history). This function stays quote-aware instead: exactly
    as precise as bash itself, so it introduces no new mismatch in either direction on any
    shape measured for this fix -- both the four that must now deny and the false-denial
    probes (a wrapped `docker compose` invocation, a continued `find`, and a heredoc body
    containing a continuation inside single quotes) that must still allow.

    Runs on `scan_heredocs`' OUTPUT, after heredoc extraction and before
    `_newlines_to_separators`, and both placements are load-bearing:

    - AFTER `strip_comments`, never merged into it or run before it. A backslash has no
      special meaning inside a comment (verified: `echo hi # comment \\<newline>rm ...`
      still runs `rm ...` as a separate statement -- the comment ends at the real newline
      regardless of the backslash before it). `strip_comments` already gets this right
      because its comment mode just skips to the next literal `\\n`, ignoring backslashes
      entirely; running continuation-stripping first would glue the "comment" line to the
      next one BEFORE `strip_comments` ever sees a boundary, hiding a real command inside
      what looks like a longer comment -- a false OPEN, and the reverse of what this
      function exists to fix.
    - AFTER `scan_heredocs`, not before it. A quoted-delimiter heredoc body is already
      entirely absent from this text (bash performs no expansion on it at all, so no
      joining happens there either -- confirmed: `<<'EOF'` with a body continuation keeps
      both the backslash and the newline). Running before extraction would have this
      function look for quotes inside heredoc bodies, where a stray apostrophe is a literal
      character, not a real shell quote, and could misjudge a continuation because of it. A
      known, narrower residual of running after extraction: an UNQUOTED heredoc body fed to
      an interpreter is judged from `Heredoc.body`, captured before this function ever
      runs, so a continuation hidden inside such a body is not joined. That body is read --
      `bgcleanup._nested_programs` re-tokenizes a shell-fed one as a command line of its own
      -- so the residual is reachable, and it is recorded rather than closed: joining it
      means continuation-stripping each captured body separately, a second escaping model to
      keep in step with this one, and this module degrades toward reporting LESS about a
      body rather than inventing text no shell would run.
    - BEFORE `_newlines_to_separators`, so no bare `\\<newline>` pair reaches it: every one
      outside single quotes is already gone, and every one still present is inside single
      quotes, which that function's own quote tracking already leaves alone.

    The escaping model matches `_newlines_to_separators`' own (single quotes escape
    nothing; a backslash elsewhere marks exactly the next character as escaped) precisely
    so the two functions never disagree about which side of a quote a character is on. A
    trailing unresolved backslash (nothing follows it) is written back unchanged -- there
    is no next character to decide anything about, so nothing here elides it.
    """

    out: list[str] = []
    quote = ""
    escaped = False
    for character in text:
        if escaped:
            escaped = False
            if character == "\n":
                continue  # `\` + newline elided together: bash's line continuation
            out.append("\\")
            out.append(character)
        elif quote == "'":
            out.append(character)
            if character == "'":
                quote = ""
        elif quote:
            if character == "\\":
                escaped = True
            else:
                if character == quote:
                    quote = ""
                out.append(character)
        elif character == "\\":
            escaped = True
        else:
            if character in "'\"":
                quote = character
            out.append(character)
    if escaped:
        out.append("\\")  # a dangling backslash at the very end: nothing follows to elide
    return "".join(out)


def prepare(command: str, *, keep_unquoted_bodies: bool = True) -> tuple[str, list[Heredoc]]:
    """``command`` reduced to the text a scanner should read, plus its heredocs.

    Comments removed, quoted heredoc bodies removed, unquoted heredoc bodies kept in place,
    every backslash-newline line continuation outside single quotes joined the way bash
    joins it, and every remaining unquoted newline rewritten to the separator it is. The
    single entry point for all four, so no caller can apply one and forget another -- and
    the ORDER is the contract: the two line-based passes first, then continuation-joining,
    then newline rewriting last (see `_strip_line_continuations` for why each placement
    matters).

    ``keep_unquoted_bodies=False`` drops the unquoted bodies as well; `scan_heredocs` states
    which question that answers and why the default is the other way.
    """

    text, heredocs = scan_heredocs(
        strip_comments(command), keep_unquoted_bodies=keep_unquoted_bodies
    )
    text = _strip_line_continuations(text)
    return _newlines_to_separators(text), heredocs


def tokenize(command: str, *, keep_unquoted_bodies: bool = True) -> list[str] | None:
    """Shell tokens with redirects/separators preserved; ``None`` when unparseable.

    ``keep_unquoted_bodies=False`` tokenizes the command with every heredoc body gone, which
    is what a caller asking a grammar question wants; see `scan_heredocs`.
    """
    lexer = shlex.shlex(
        prepare(command, keep_unquoted_bodies=keep_unquoted_bodies)[0],
        posix=True,
        punctuation_chars=True,
    )
    lexer.whitespace_split = True
    lexer.commenters = ""  # `strip_comments` has handled them, correctly; see its docstring
    tokens: list[str] = []
    try:
        for token in lexer:
            tokens.append(token)
    except ValueError:
        return None
    return tokens


def operator_pieces(token: str) -> list[str]:
    """``token`` as the operators bash reads it as; anything else comes back untouched.

    `shlex`'s `punctuation_chars` mode fuses a run of adjacent punctuation into one token, so
    a separator standing next to any other operator arrives welded to it and `segments`'
    membership test misses it. Three gates decide whether a token is such a run, and each one
    is there to keep an ordinary token out:

    1. Already a known separator (`;`, `&&`, `|`, ...) -- nothing to decompose.
    2. Carries no `;`/`&`/`|` -- it cannot be hiding a command break, so `((`, `))`, `>>`,
       `<<`, `<<<` and `<>` are returned exactly as they arrived.
    3. Not punctuation from end to end -- a real word (`notes#1.md`, `--format=%h|%s`,
       `-I{}`) is never taken apart, whatever punctuation it happens to contain.

    What survives all three is decomposed greedily against `_COMPOUND_OPERATORS`, longest
    match first, so `);` becomes `)` + `;` and `;>` becomes `;` + `>` while `&>` and `>&`
    stay whole.

    The known cost, recorded rather than discovered later: a QUOTED argument that is
    punctuation from end to end and contains a separator character (`echo ';;'`) is
    decomposed too, because the tokens reaching here carry no memory of their quoting. That
    is not new in kind -- `echo ';'` already split on shipped, since `;` is in `_SEPARATORS`.

    A caller whose quoted arguments can carry operator characters owes the same reckoning on
    its own side; this function does not do it for them.
    """

    if token in _SEPARATORS or not token:
        return [token]
    if not _SEPARATOR_CHARS & set(token):
        return [token]
    if not set(token) <= _PUNCTUATION_CHARS:
        return [token]
    pieces: list[str] = []
    index = 0
    while index < len(token):
        for operator in _COMPOUND_OPERATORS:
            if token.startswith(operator, index):
                pieces.append(operator)
                index += len(operator)
                break
        else:
            pieces.append(token[index])
            index += 1
    return pieces


def segments(tokens: list[str]) -> list[list[str]]:
    """Split a token stream into simple commands at shell separators.

    A token that is a fused run of punctuation is decomposed first (`operator_pieces`), so a
    separator welded to a neighbouring operator still ends the command it ends in bash. The
    non-separator pieces are kept, in place: `);` leaves the `)` at the end of the segment it
    closed, and `;>` puts the `>` at the FRONT of the segment it opens -- which is what lets a
    caller's redirect lookback, the test that the piece before a path is a redirect operator,
    find the write target of `echo hi;>scripts/guard.py`, where the fused `;>` token matched
    neither a separator nor a redirect and the write was invisible to both.
    """

    return [command for pipeline in pipelines(tokens) for command in pipeline]


def pipelines(tokens: list[str]) -> list[list[list[str]]]:
    """Each pipeline of a token stream as its simple commands: the commands a `|` joins. `;`,
    `&&`, `||` and `&` end one, and a fused run of punctuation is decomposed first
    (`operator_pieces`); `segments` is these pipelines' commands, flattened. A `;` straight after
    a `|` does not end the pipeline: `prepare` rewrites a line break to `;`, and a line break
    after `|` continues the pipeline in bash and zsh (a literal `|;` is a syntax error, so no
    valid command differs).

    THE BOUND IS DOCUMENTED: `|&` reaches this as the pieces `|` and `&`, and the `&` ends the
    pipeline, so the two commands it joins are read as two pipelines.
    """

    result: list[list[list[str]]] = []
    pipeline: list[list[str]] = []
    current: list[str] = []
    after_pipe = False
    for token in tokens:
        for piece in operator_pieces(token):
            if piece not in _SEPARATORS:
                current.append(piece)
                after_pipe = False
                continue
            if piece == ";" and after_pipe:
                continue
            after_pipe = piece == "|"
            if current:
                pipeline.append(current)
                current = []
            if piece != "|" and pipeline:
                result.append(pipeline)
                pipeline = []
    if current:
        pipeline.append(current)
    if pipeline:
        result.append(pipeline)
    return result


def _launched(segment: Sequence[str], index: int) -> int | None:
    """Where the command that the `uv` at `index` launches starts: the first word after `run`
    the runner table reads as a test run; `None` where no `run` follows or no such word does."""
    start = past_run(segment, index + 1)
    if start is None:
        return None
    for at in range(start, len(segment)):
        if names_a_test_run(segment[at:]):
            return at
    return None


def command_words(segment: list[str], *, test_runs: bool = False) -> list[str]:
    """This segment's own command and arguments, with any leading environment-assignment
    tokens and the wrapper prefixes -- `_SINGLE_WRAPPERS`, and, given `test_runs`, `uv run`
    with every word up to the first the runner table reads as a test run -- stripped off the
    front, repeatedly, so `env FOO=1 pytest` and `FOO=1 uv run --locked pytest` both resolve to
    `pytest` as the real command, not to the assignment or the launcher.

    `Path(segment[0]).name` alone missed `PYTHONPATH=src pytest` and `FOO=1 BAR=2 pytest`
    entirely, and neither is contrived: an assignment prefix is the ordinary way to run a
    suite against a checkout that has no venv of its own.

    THE BOUND IS DOCUMENTED, not accidental: a `uv` command in which the table reads no test
    run, or one with no `run`, is left whole and its program is `uv`, and no other launcher
    is read through. Behind `uv run` every word before the first the table reads as a test run
    is the launcher's, so a runner named as a uv option's value or as another
    program's argument (`uv run --with pytest echo`, `uv run grep -m pytest x`) is read as the
    command, and a warn-only caller delivers its note to a run of no test.
    """

    index = 0
    while index < len(segment):
        token = segment[index]
        if _ASSIGNMENT.match(token):
            index += 1
            continue
        name = Path(token).name
        if test_runs and is_launcher(name):
            launched = _launched(segment, index)
            if launched is None:
                break
            index = launched
            continue
        if name in _SINGLE_WRAPPERS:
            index += 1
            continue
        break
    return segment[index:]
