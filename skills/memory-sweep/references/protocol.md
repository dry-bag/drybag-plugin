# The memory protocol

The store lives where `[paths] memory` says (`[memory] mode` decides whether that is a real
directory in the repository, a git-ignored local one, or a link into a private overlay). Its
groups are `[memory] groups`, in index order; the convention is audience: a cross-project
group for durable preferences and working lessons, a project-stable group for durable
project facts absent from the repository's documents, and a project-volatile group for
temporary state, where every note carries `metadata.as_of`.

`MEMORY.md` is a **routing table**, rendered by `drybag memory index` from each note's
`index:` line — never edited by hand, and budgeted (`memory_index_words`). Every entry is a
pointer, phrased *trigger → what the note settles*. It must not read as a finished claim: an
entry that can be quoted gets quoted instead of opened, and the note always carries the part
that decides the work. No status tail either — a factual tail is the same failure in another
position.

Notes with `metadata.startup: <rank>` are standing rules once the owner promotes them
(`drybag memory promote`), injected whole at session start in rank order; an edit makes a note
unreviewed again until it is promoted anew; volatile notes are injected whole too, and
`drybag memory session-context --bundle volatile-notes` reports one past its `as_of` or its
budget. Before ranking a note, decide where it belongs at all: a rule that fires on an
*action* belongs on that action's hook; a rule already mechanized by a repository setting or a
CI gate belongs in neither, and the note is deleted.

## Before writing a note

1. Do not copy facts from code, the agents file, specs, runbooks or git history.
2. One fact per note, with `name`, `description` and `metadata.type` frontmatter
   (`user`, `feedback`, `project` or `reference`) and an `index:` line.
3. **Write the rule, not the incident.** The shortest thing that changes what the next
   session does: the rule, one line of why, how to apply it, and a bare pointer (an issue
   identifier, a pull request number) to where the evidence already lives. Retelling the
   incident duplicates the roadmap or the retro that owns it, and length is a
   cost paid every session for an injected note. Identifiers are written plain:
   `[[wiki-links]]` address notes, so a bracketed identifier is a permanent dangling edge.
4. Update an existing note instead of adding a near-duplicate, and **merge on close
   coupling, not only on duplication**: two notes that fire on the same trigger, or that
   each need the other to be usable, are one note.
5. Delete resolved, contradicted, one-session or otherwise stale notes and their links.
6. Volatile notes arrive in context already; the standing job is to verify their named
   paths, flags and dates before use, and to delete each one the moment it resolves.
7. **A backticked path asserts the file is in the tree.** No command checks it — the store is
   git-ignored in most modes, so no CI runner has one to scan — and a sweep opens each path a
   note names before it keeps the note. Write the path in *italics* wherever it deliberately
   does not resolve: a file the note records as deleted, or one belonging to another repository.

## In a worktree

The store is linked, not copied, so a note written from either side is the same note and
nothing is lost when the worktree goes. Linking is automatic, and it is not the job of
`drybag memory index`: a `SessionStart` handler makes one link per group at the start of a
session and says how many it made. When a group is missing from the worktree, read that line
first — the handler reports a path it refused rather than failing the session.
