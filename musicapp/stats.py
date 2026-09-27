"""Listening history for the Stats tab: recording what is played, and the calculations shown there.

ListeningRecorder follows the Player and writes one row per listening session to the database's
`plays` table (see database.py):

- A session starts when a song is loaded, and again each time Repeat Current Song starts it over.
- Its listened time counts only while the song is actually playing (pauses and seeks don't add
  time).
- It counts as a play once it has been listened to for half the song or PLAY_MAX_SECONDS
  (4 minutes), whichever comes first.
- Sessions shorter than MIN_SESSION_SECONDS aren't kept (skipping through songs).
- The song in progress is saved every CHECKPOINT_SECONDS, so a crash loses little.

The functions below turn the stored history into what the Stats page shows: period starts, streaks
and formatted durations."""

import datetime as dt
import time
from dataclasses import dataclass

from gi.repository import GLib, GObject

MIN_SESSION_SECONDS = 5
PLAY_MAX_SECONDS = 240
CHECKPOINT_SECONDS = 30

# Period switch of the Stats page: (key, label, days back; None = all time). Periods are rolling.
PERIODS = (("week", "Week", 7), ("month", "Month", 30), ("year", "Year", 365), ("all", "All time", None))


@dataclass
class _Session:
    """The song being listened to now, while it's recorded."""
    track: object
    started: float  # unix time the session began
    listened: float = 0.0  # seconds of playing so far (up to running_since)
    running_since: float | None = None  # monotonic time playing (re)started, None while paused
    play_id: int | None = None  # database row, once saved


class ListeningRecorder(GObject.Object):
    """Records listening sessions from the player's signals (see the module docstring).
    Emits `changed` (main thread) after it saves something."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, db, player):
        super().__init__()
        self.db = db
        self.player = player
        self._session: _Session | None = None
        # Every relevant change goes through _sync, which compares the player with the session.
        player.connect("track-changed", lambda *_: self._sync())
        player.connect("state-changed", lambda *_: self._sync())
        player.connect("looped", lambda *_: self._restart())
        self._checkpoint = GLib.timeout_add_seconds(CHECKPOINT_SECONDS, self._on_checkpoint)

    def _sync(self):
        """Bring the session in line with the player: close it if the song changed, start or stop
        its clock with playback, and open a session for a newly loaded song."""
        track, playing, now = self.player.current, self.player.playing, time.monotonic()
        session = self._session
        if session is not None and session.track is not track:
            self._finish()
            session = None
        if session is not None:
            self._accumulate(session, now)
            session.running_since = now if playing else None
        elif track is not None:
            self._session = _Session(track, time.time(), running_since=now if playing else None)

    def _restart(self):
        """Repeat Current Song starts the song over: that's a new listen."""
        self._finish()
        self._sync()

    @staticmethod
    def _accumulate(session: _Session, now: float):
        """Add the playing time since the clock last started."""
        if session.running_since is not None:
            session.listened += now - session.running_since
            session.running_since = now

    def _finish(self):
        """Save and close the current session."""
        session, self._session = self._session, None
        if session is not None:
            self._accumulate(session, time.monotonic())
            self._save(session)

    def _save(self, session: _Session):
        """Write the session (if it's long enough, or already saved) and tell listeners."""
        if session.play_id is None and session.listened < MIN_SESSION_SECONDS:
            return
        track = session.track
        duration = track.duration or self.player.get_duration() or 0
        needed = min(duration / 2, PLAY_MAX_SECONDS) if duration > 0 else PLAY_MAX_SECONDS
        try:
            session.play_id = self.db.save_play(
                session.play_id, session.started, round(session.listened, 1), session.listened >= needed,
                track.id, track.title, track.artist_key, track.album)
        except Exception as exc:  # e.g. the database closed during shutdown: never break playback
            print(f"[stats] could not save: {exc}")
            return
        self.emit("changed")

    def _on_checkpoint(self):
        """Every CHECKPOINT_SECONDS: save the song in progress."""
        session = self._session
        if session is not None and session.running_since is not None:
            self._accumulate(session, time.monotonic())
            self._save(session)
        return True

    def shutdown(self):
        """Save the song in progress (called before the database closes)."""
        if self._checkpoint:
            GLib.source_remove(self._checkpoint)
            self._checkpoint = 0
        self._finish()


# ---- calculations for the Stats page ----

def period_start(days: int | None, now: float | None = None) -> float:
    """Unix time a rolling period of `days` starts (0 for all time)."""
    if days is None:
        return 0.0
    return (time.time() if now is None else now) - days * 86400


def streaks(days: list[str], today: dt.date) -> tuple[int, int]:
    """(current streak, longest streak) in days, from the days with listening ('YYYY-MM-DD').
    The current streak ends today, or yesterday if nothing was played yet today."""
    dates = sorted({dt.date.fromisoformat(d) for d in days})
    longest = run = 0
    previous = None
    for d in dates:
        run = run + 1 if previous is not None and (d - previous).days == 1 else 1
        longest = max(longest, run)
        previous = d
    have = set(dates)
    end = today if today in have else today - dt.timedelta(days=1)
    current = 0
    while end in have:
        current += 1
        end -= dt.timedelta(days=1)
    return current, longest


def format_listened(seconds: float) -> str:
    """Listening time for the cards and rankings: "42 min", "30 hr 35 min" (under a minute: "0 min")."""
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} hr {minutes} min" if hours else f"{minutes} min"
