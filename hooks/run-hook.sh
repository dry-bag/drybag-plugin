#!/bin/sh
# drybag hook wrapper: runs the launcher beside it under the `python3` on PATH, else uv's
# interpreter, with `-I`.
# Any fault, the launcher's non-zero exit included, is exit 0 with one line on stderr, so a session
# never dies of drybag's own fault. `/bin/sh` is SIP-protected on macOS, so `DYLD_*` variables
# never reach the interpreter below; keep the shebang a system shell.
skip() { echo "drybag: $1; continuing open" >&2; exit 0; }
# No usable python3 is drybag off, and stderr on a `0` reaches nobody, so the one entry that
# dispatches `SessionStart` says so as the JSON Claude Code shows the person (`systemMessage`) and
# adds to the model's context (`additionalContext`). A `PATH` directory may be named anything, so
# the path goes out narrowed to bytes a JSON string takes unescaped.
off() {
  if test "$call" = "hook SessionStart"; then
    why="there is no python3 on PATH"
    if test -n "$1"; then
      why="$(printf %s "$1" | LC_ALL=C command -p tr -c 'A-Za-z0-9/._+ ~_-' '?')"
      why="$why is not Python 3.11 or newer"
    fi
    line="drybag's hooks are off in this session: $why. Install Python 3.11 or newer on PATH."
    printf '{"systemMessage": "%s", "hookSpecificOutput": ' "$line"
    printf '{"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' "$line"
  fi
  skip "$2"
}
call="$1 $2"

case $0 in */*) here=${0%/*} ;; *) here=. ;; esac
launcher="$(CDPATH= cd -- "${here:-/}/.." && pwd)/scripts/drybag"
test -f "$launcher" && test -r "$launcher" || skip "no readable launcher at $launcher"

# An inherited GIT_DIR or GIT_WORK_TREE would make every git answer about another repository.
unset GIT_DIR GIT_WORK_TREE
# Every entry but `hook` reads its `--root` default, the directory it runs in, as the project.
root=${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}
test -z "$root" || CDPATH= cd -- "$root" 2>/dev/null || skip "cannot enter the project root $root"

# The interpreter comes from `PATH` resolved in `/`, which a repository cannot move because it
# writes no file there: a relative `PATH` element, a version manager's shim and uv each answer for
# the directory they run in. The `python3` found there prints the binary it really is, which runs
# the launcher from the project root; uv's answer is that binary already.
python=$(cd / && { python3 -I -c 'import sys
sys.exit(1) if sys.version_info < (3, 11) or not sys.executable else print(sys.executable)' ||
  uv python find '>=3.11'; } 2>/dev/null) || python=
if test -z "$python"; then
  bad=$(cd / && command -v python3) || off "" "no python3 on PATH"
  off "$bad" "$bad is not python 3.11 or newer"
fi
"$python" -I "$launcher" "$@" || skip "drybag exited $?"
