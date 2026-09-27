#!/usr/bin/env python3
"""Launch the music player."""

import sys


def run() -> int:
    """Start the app and return its exit status."""
    # Imported lazily: the scanner's worker processes re-import this module,
    # and they shouldn't pay for loading GTK.
    from musicapp.application import main
    return main(sys.argv)


if __name__ == "__main__":
    sys.exit(run())
