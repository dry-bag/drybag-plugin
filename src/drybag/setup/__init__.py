"""Configure a machine for drybag, and a repository's own commit-message hook.

An area, discovered by name: `commands.py` gives it the `setup` command and `api.py` is what
another area may import. `setup` is the only writer of the machine configuration file — the
file `config.owner.resolve_owner` and `config.overlay.overlay_root` already read. It adds a
`[machine]` table beside what they read, and neither reader changes to suit the writer.
"""
