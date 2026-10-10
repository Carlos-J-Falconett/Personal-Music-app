#!/usr/bin/env python3
"""Launch the music player."""

import sys


M_MMAP_THRESHOLD = -3  # glibc's mallopt() parameter number
MMAP_THRESHOLD_BYTES = 128 * 1024


def limit_heap_growth():
    """Have glibc give large blocks straight back to the system when they're freed.

    Covers and backdrops are decoded into big pixel buffers (up to ~26 MB) that come and go with
    every song. By default glibc raises its threshold for such blocks after the first ones are
    freed, and later ones then stay in the heap after they're freed, so memory use kept climbing
    song after song (620 MB+ after ~200 song changes in testing, against ~320 MB with this).
    Fixing the threshold keeps every block of 128 KiB or more in its own mapping that's returned on
    free. Does nothing where glibc isn't the C library."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").mallopt(M_MMAP_THRESHOLD, MMAP_THRESHOLD_BYTES)
    except (OSError, AttributeError):
        pass


def run() -> int:
    """Start the app and return its exit status."""
    limit_heap_growth()
    # Imported lazily: the scanner's worker processes re-import this module,
    # and they shouldn't pay for loading GTK.
    from musicapp.application import main
    return main(sys.argv)


if __name__ == "__main__":
    sys.exit(run())
