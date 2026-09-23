from __future__ import annotations

import sys


# Upstream's `update` downloads https://sidepulse.io/setup.sh and runs it
# unverified; that script installs upstream's default branch over this install.
# This fork publishes no build to pin instead, so updating means reinstalling
# from a checkout.
FORK_UPDATE_INSTRUCTIONS = (
    "sidepulse update is not available in this fork.\n"
    "To update, pull the fork's checkout and run scripts/install-user.sh from it, "
    "then run sidepulse setup."
)


def update_installation(*, dry_run: bool = False) -> int:
    print(FORK_UPDATE_INSTRUCTIONS, file=sys.stderr)
    return 1
