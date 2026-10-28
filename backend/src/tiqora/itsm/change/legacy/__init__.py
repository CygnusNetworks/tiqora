"""Znuny ITSMChangeManagement changes, read-only (Mode A live view; the archive reads the same).

``reader`` holds every SQL statement against Znuny's ``change_*`` tables, ``mapping`` the pure
rules (sentinel times, plain texts, the change window from the work orders), ``sources`` the
source the API reads from. Nothing here writes a Znuny table.
"""
