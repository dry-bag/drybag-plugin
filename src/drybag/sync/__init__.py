"""Unattended note sync for the private overlay: a machine whose machine file says
`[sync] enabled = true` commits the overlay's memory notes, takes in the other machines' and pushes
its own from the session hooks, through a detached job; a machine that does not say so runs nothing.

The state the job keeps under the data directory and the switch's reader are here; the git rules,
the job and the hooks are the area's other modules. The area publishes no `api.py` and no command:
nothing outside it imports it, and the switch is a key of the machine file.
"""
