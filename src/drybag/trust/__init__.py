"""Every approval a person gave on this machine, in one record drybag keeps.

An approval is one kind and one subject, held while what was approved still hashes to the digest
recorded for it. `record` keeps them all in `trust.json` beside the machine file, never in an
overlay and never under a directory an environment variable names. Other packages import
`drybag.trust.api`, never a module of this one.
"""
