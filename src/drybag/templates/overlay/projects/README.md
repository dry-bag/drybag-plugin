# Bound repositories

One directory per repository, named by its `[project] name` — the same string that project's
`drybag.toml` carries, so the binding cannot be ambiguous.

Each holds `project.toml`, which records the repository's own remote, and `memory/`, that
project's notes. The record is what a session checks before any note here reaches it: a store
whose record names a different remote is refused rather than read, which is what keeps one
project's notes out of another project's session.

`drybag attach` creates both. Do not hand-write the record's remote to make a check pass.
