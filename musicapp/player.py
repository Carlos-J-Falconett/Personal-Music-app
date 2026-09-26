"""GStreamer playback plus queue, shuffle and repeat logic."""

import random
import re
from collections import deque
from enum import IntEnum

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, GObject, Gst  # noqa: E402

PREVIOUS_RESTART_THRESHOLD = 3.0  # seconds

# Spectrum analysis for the visualizer.
SPECTRUM_BANDS = 256
SPECTRUM_THRESHOLD_DB = -80
SPECTRUM_INTERVAL_NS = 33_000_000  # ~30 results per second
SPECTRUM_POLL_MS = 16
_MAGNITUDE_RE = re.compile(r"magnitude=\(float\)\{([^}]*)\}")

# Equalizer (equalizer-10bands: centres 29 Hz … 15 kHz, shown with the usual round labels).
EQ_BANDS = ("31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k")
EQ_MIN_DB, EQ_MAX_DB = -12.0, 12.0
FLAT = [0.0] * len(EQ_BANDS)


class Repeat(IntEnum):
    OFF = 0
    ALL = 1
    ONE = 2


class Player(GObject.Object):
    """
    Queue model: `_tracks` holds the queue in its original (unshuffled) order.
    `_order` holds indices into `_tracks` in play order, and `_pos` is the
    current index into `_order`. Shuffling only rearranges `_order`, so turning
    shuffle off restores the original order at the current track.
    """

    __gsignals__ = {
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),  # is playing
        "track-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),  # TrackItem or None
        "position-changed": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),  # position, duration
        "queue-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "modes-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "volume-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "seeked": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Per-band magnitudes in dB (SPECTRUM_BANDS floats), timed to match what is being heard.
        "spectrum": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "eq-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self._playbin = Gst.ElementFactory.make("playbin3", "player") or Gst.ElementFactory.make("playbin", "player")
        fakevideo = Gst.ElementFactory.make("fakesink", "fakevideo")
        if fakevideo:
            self._playbin.set_property("video-sink", fakevideo)
        spectrum = Gst.ElementFactory.make("spectrum", "spectrum")
        if spectrum:
            spectrum.set_property("bands", SPECTRUM_BANDS)
            spectrum.set_property("threshold", SPECTRUM_THRESHOLD_DB)
            spectrum.set_property("interval", SPECTRUM_INTERVAL_NS)
            spectrum.set_property("post-messages", True)
            spectrum.set_property("message-magnitude", True)
        self._eq = Gst.ElementFactory.make("equalizer-10bands", "equalizer")
        audio_filter = self._build_filter(self._eq, spectrum) if self._eq else spectrum
        if audio_filter:
            self._playbin.set_property("audio-filter", audio_filter)
        bus = self._playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_error)
        bus.connect("message::async-done", self._on_async_done)
        bus.connect("message::element", self._on_element)

        self._tracks: list = []
        self._order: list[int] = []
        self._pos = -1
        self._shuffle = False
        self._repeat = Repeat.OFF
        self._volume = 1.0
        self._playing = False
        self._loaded = False
        self._pending_seek: float | None = None
        self._error_streak = 0
        self._last_reported = (-1.0, -1.0)
        self._duration = 0.0
        # Spectrum results arrive ahead of the audio (the pipeline decodes ahead), so they are
        # buffered by running time and released when playback reaches them.
        self._spectrum_queue: deque = deque(maxlen=256)
        self._spectrum_poll = 0
        self.default_tracks = None  # callable returning tracks to play when the queue is empty
        self.eq_lookup = None  # callable(track) -> saved gains for that song, or None for flat
        self._eq_gains = list(FLAT)
        self.set_volume(0.8)
        GLib.timeout_add(250, self._tick)

    # ---- properties -----------------------------------------------------------

    @property
    def current(self):
        if 0 <= self._pos < len(self._order):
            return self._tracks[self._order[self._pos]]
        return None

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @property
    def repeat(self) -> Repeat:
        return self._repeat

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def has_eq(self) -> bool:
        return self._eq is not None

    @property
    def eq_gains(self) -> list[float]:
        return list(self._eq_gains)

    @property
    def queue(self) -> list:
        """Tracks in play order."""
        return [self._tracks[i] for i in self._order]

    @property
    def queue_position(self) -> int:
        return self._pos

    def upcoming(self) -> list[tuple[int, object, bool]]:
        """What will play after the current track, in order, as (queue_index, track, wrapped).
        With Repeat Queue, tracks before the current one follow, marked wrapped=True."""
        if not (0 <= self._pos < len(self._order)):
            return []
        result = [(i, self._tracks[self._order[i]], False) for i in range(self._pos + 1, len(self._order))]
        if self._repeat == Repeat.ALL:
            result += [(i, self._tracks[self._order[i]], True) for i in range(self._pos)]
        return result

    def has_next(self) -> bool:
        return self._pos + 1 < len(self._order) or (self._repeat == Repeat.ALL and bool(self._order))

    def has_previous(self) -> bool:
        return bool(self._order)

    # ---- queue management -------------------------------------------------------

    def play_tracks(self, tracks: list, start: int = 0):
        """Replace the queue with `tracks` and start playing tracks[start]."""
        if not tracks:
            return
        start = max(0, min(start, len(tracks) - 1))
        self._tracks = list(tracks)
        self._order = list(range(len(tracks)))
        self._pos = start
        if self._shuffle:
            self._shuffle_order()
        self.emit("queue-changed")
        self._load_current(play=True)

    def shuffle_play(self, tracks: list):
        if not tracks:
            return
        if not self._shuffle:
            self._shuffle = True
            self.emit("modes-changed")
        self.play_tracks(tracks, random.randrange(len(tracks)))

    def play_next(self, tracks: list):
        """Insert tracks right after the current one."""
        if not self._order:
            self.play_tracks(tracks)
            return
        insert_at = self._pos + 1
        if self._shuffle:
            base = len(self._tracks)
            self._tracks.extend(tracks)
            self._order[insert_at:insert_at] = range(base, base + len(tracks))
        else:
            orig = self._order[self._pos] + 1
            self._tracks[orig:orig] = tracks
            self._order = list(range(len(self._tracks)))
        self.emit("queue-changed")

    def enqueue(self, tracks: list):
        """Add to Queue: the songs play right after the current one, ahead of anything
        queued earlier (adding A then B plays B, then A)."""
        self.play_next(tracks)

    def remove_from_queue(self, queue_index: int):
        if not (0 <= queue_index < len(self._order)) or queue_index == self._pos:
            return
        orig = self._order.pop(queue_index)
        del self._tracks[orig]
        self._order = [i - 1 if i > orig else i for i in self._order]
        if queue_index < self._pos:
            self._pos -= 1
        self.emit("queue-changed")

    def move_in_queue(self, src: int, dest: int):
        n = len(self._order)
        if not (0 <= src < n and 0 <= dest < n) or src == dest:
            return
        current_orig = self._order[self._pos]
        if self._shuffle:
            self._order.insert(dest, self._order.pop(src))
        else:
            self._tracks.insert(dest, self._tracks.pop(src))
        if not self._shuffle:
            # Recompute where the current track went.
            if src == self._pos:
                self._pos = dest
            elif src < self._pos <= dest:
                self._pos -= 1
            elif dest <= self._pos < src:
                self._pos += 1
        else:
            self._pos = self._order.index(current_orig)
        self.emit("queue-changed")

    def jump_to(self, queue_index: int):
        if 0 <= queue_index < len(self._order):
            self._pos = queue_index
            self._load_current(play=True)
            self.emit("queue-changed")

    def clear_upcoming(self):
        """Keep only the current track in the queue (it keeps playing)."""
        if self.current is None:
            return
        self._tracks = [self.current]
        self._order, self._pos = [0], 0
        self.emit("queue-changed")

    def clear_queue(self):
        self.stop()
        self._tracks, self._order, self._pos = [], [], -1
        self.emit("queue-changed")
        self.emit("track-changed", None)

    def remove_tracks(self, predicate):
        """Drop tracks (e.g. deleted from disk) from the queue."""
        cur = self.current
        kept = [i for i, t in enumerate(self._tracks) if not predicate(t)]
        if len(kept) == len(self._tracks):
            return
        if cur is not None and predicate(cur):
            self.clear_queue()
            return
        # Keep the play order (shuffled or not) of what's left.
        new_index = {old: new for new, old in enumerate(kept)}
        cur_orig = self._order[self._pos] if 0 <= self._pos < len(self._order) else None
        self._tracks = [self._tracks[i] for i in kept]
        self._order = [new_index[i] for i in self._order if i in new_index]
        self._pos = self._order.index(new_index[cur_orig]) if cur_orig is not None else -1
        self.emit("queue-changed")

    def _shuffle_order(self):
        """Current track goes first, the rest are shuffled after it."""
        if not self._order:
            return
        current = self._order[self._pos] if self._pos >= 0 else None
        rest = [i for i in range(len(self._tracks)) if i != current]
        random.shuffle(rest)
        self._order = ([current] if current is not None else []) + rest
        self._pos = 0 if current is not None else -1

    # ---- modes --------------------------------------------------------------------

    def set_shuffle(self, value: bool):
        if value == self._shuffle:
            return
        self._shuffle = value
        if self._order:
            if value:
                self._shuffle_order()
            else:
                current = self._order[self._pos] if self._pos >= 0 else 0
                self._order = list(range(len(self._tracks)))
                self._pos = current
            self.emit("queue-changed")
        self.emit("modes-changed")

    def toggle_shuffle(self):
        self.set_shuffle(not self._shuffle)

    def set_repeat(self, mode: Repeat):
        self._repeat = Repeat(mode)
        self.emit("modes-changed")

    def cycle_repeat(self):
        self.set_repeat({Repeat.OFF: Repeat.ALL, Repeat.ALL: Repeat.ONE, Repeat.ONE: Repeat.OFF}[self._repeat])

    def set_volume(self, value: float):
        value = max(0.0, min(1.0, value))
        self._volume = value
        # Cubic curve so the slider feels linear to the ear.
        self._playbin.set_property("volume", value ** 3)
        self.emit("volume-changed", value)

    @staticmethod
    def _build_filter(eq, spectrum):
        """audioconvert ! equalizer ! audioconvert [! spectrum], so the visualizer sees the EQ'd sound."""
        chain = [Gst.ElementFactory.make("audioconvert", None), eq, Gst.ElementFactory.make("audioconvert", None)]
        if spectrum:
            chain.append(spectrum)
        if None in chain:
            return spectrum
        bin_ = Gst.Bin.new("audio-filter")
        for element in chain:
            bin_.add(element)
        for a, b in zip(chain, chain[1:]):
            a.link(b)
        bin_.add_pad(Gst.GhostPad.new("sink", chain[0].get_static_pad("sink")))
        bin_.add_pad(Gst.GhostPad.new("src", chain[-1].get_static_pad("src")))
        return bin_

    def set_eq(self, gains):
        """Apply band gains (dB) live. Emits eq-changed."""
        gains = [max(EQ_MIN_DB, min(EQ_MAX_DB, float(g))) for g in (gains or FLAT)][:len(EQ_BANDS)]
        gains += [0.0] * (len(EQ_BANDS) - len(gains))
        self._eq_gains = gains
        if self._eq is not None:
            for i, g in enumerate(gains):
                self._eq.set_property(f"band{i}", g)
        self.emit("eq-changed")

    # ---- transport ------------------------------------------------------------------

    def play(self):
        if self.current is None:
            # Nothing queued (e.g. first launch, or Play from GNOME's media controls): play everything.
            tracks = self.default_tracks() if self.default_tracks else []
            if tracks:
                self.play_tracks(tracks, 0)
            return
        if not self._loaded:
            self._load_current(play=True)
            return
        self._playbin.set_state(Gst.State.PLAYING)
        self._set_playing(True)

    def pause(self):
        if self._loaded:
            self._playbin.set_state(Gst.State.PAUSED)
        self._set_playing(False)

    def play_pause(self):
        self.pause() if self._playing else self.play()

    def stop(self):
        self._playbin.set_state(Gst.State.NULL)
        self._loaded = False
        self._set_playing(False)
        self.emit("position-changed", 0.0, self._duration)

    def next(self, auto: bool = False):
        if not self._order:
            return
        if self._pos + 1 < len(self._order):
            self._pos += 1
        elif self._repeat == Repeat.ALL:
            self._pos = 0
        elif auto:
            # End of queue: stop and rewind to the first track.
            self._pos = 0
            self.stop()
            self._load_current(play=False)
            self.emit("queue-changed")
            return
        else:
            return
        self._load_current(play=True)
        self.emit("queue-changed")

    def previous(self):
        if not self._order:
            return
        if self.get_position() > PREVIOUS_RESTART_THRESHOLD or (self._pos == 0 and self._repeat != Repeat.ALL):
            self.seek(0)
            return
        self._pos = self._pos - 1 if self._pos > 0 else len(self._order) - 1
        self._load_current(play=True)
        self.emit("queue-changed")

    def seek(self, seconds: float):
        if self.current is None:
            return
        seconds = max(0.0, seconds)
        if not self._loaded:
            self._load_current(play=False, seek_to=seconds)
            return
        self._spectrum_queue.clear()
        ok = self._playbin.seek_simple(
            Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, int(seconds * Gst.SECOND)
        )
        if not ok:
            self._pending_seek = seconds
        self.emit("position-changed", seconds, self.get_duration())
        self.emit("seeked", seconds)

    def get_position(self) -> float:
        if not self._loaded:
            return self._pending_seek or 0.0
        ok, pos = self._playbin.query_position(Gst.Format.TIME)
        return pos / Gst.SECOND if ok else 0.0

    def get_duration(self) -> float:
        if self._loaded:
            ok, dur = self._playbin.query_duration(Gst.Format.TIME)
            if ok and dur > 0:
                self._duration = dur / Gst.SECOND
        return self._duration

    # ---- internals ---------------------------------------------------------------------

    def _load_current(self, play: bool, seek_to: float | None = None):
        track = self.current
        if track is None:
            return
        self._playbin.set_state(Gst.State.NULL)
        self._spectrum_queue.clear()
        self._playbin.set_property("uri", Gst.filename_to_uri(track.path))
        self._pending_seek = seek_to if seek_to else None
        self._duration = track.duration or 0.0
        self._loaded = True
        # Each song starts with its own EQ (or flat), applied before any audio flows.
        self.set_eq(self.eq_lookup(track) if self.eq_lookup else None)
        self._playbin.set_state(Gst.State.PLAYING if play else Gst.State.PAUSED)
        self._set_playing(play)
        self.emit("track-changed", track)
        self.emit("position-changed", seek_to or 0.0, self._duration)

    def _set_playing(self, playing: bool):
        if playing != self._playing:
            self._playing = playing
            self.emit("state-changed", playing)
        if playing and not self._spectrum_poll:
            self._spectrum_poll = GLib.timeout_add(SPECTRUM_POLL_MS, self._release_spectrum)

    # ---- spectrum (visualizer) ----

    def _running_time(self) -> int | None:
        clock = self._playbin.get_clock()
        if clock is None:
            return None
        return clock.get_time() - self._playbin.get_base_time()

    def _on_element(self, _bus, msg):
        structure = msg.get_structure()
        if structure is None or structure.get_name() != "spectrum":
            return
        # PyGObject can't read GstValueList fields, so parse the serialized structure.
        match = _MAGNITUDE_RE.search(structure.to_string())
        ok_rt, running = structure.get_uint64("running-time")
        ok_dur, duration = structure.get_uint64("duration")
        if not match or not ok_rt:
            return
        magnitudes = [float(v) for v in match.group(1).split(",")]
        self._spectrum_queue.append((running + (duration if ok_dur else 0), magnitudes))

    def _release_spectrum(self):
        if not self._playing:
            self._spectrum_poll = 0
            return False
        now = self._running_time()
        latest = None
        while self._spectrum_queue and now is not None and self._spectrum_queue[0][0] <= now:
            latest = self._spectrum_queue.popleft()[1]
        if latest is not None:
            self.emit("spectrum", latest)
        return True

    def _tick(self):
        if self._loaded and self._playing and self._pending_seek is None:
            pos, dur = self.get_position(), self.get_duration()
            if (round(pos, 1), dur) != self._last_reported:
                self._last_reported = (round(pos, 1), dur)
                self.emit("position-changed", pos, dur)
        return True

    def _on_async_done(self, _bus, _msg):
        self._error_streak = 0
        if self._pending_seek is not None:
            target, self._pending_seek = self._pending_seek, None
            self._spectrum_queue.clear()
            self._playbin.seek_simple(
                Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, int(target * Gst.SECOND)
            )
            self.emit("position-changed", target, self.get_duration())

    def _on_eos(self, _bus, _msg):
        if self._repeat == Repeat.ONE:
            self._spectrum_queue.clear()
            self._playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self.emit("seeked", 0.0)
            return
        self.next(auto=True)

    def _on_error(self, _bus, msg):
        err, debug = msg.parse_error()
        track = self.current
        name = track.title if track else "track"
        print(f"[player] error playing {track.path if track else '?'}: {err.message} ({debug})")
        self.emit("error", f"Couldn't play “{name}”: {err.message}")
        self._playbin.set_state(Gst.State.NULL)
        self._loaded = False
        self._error_streak += 1
        if self._error_streak < len(self._order):
            self.next(auto=True)
        else:
            self._error_streak = 0
            self._set_playing(False)

    def shutdown(self):
        self._playbin.set_state(Gst.State.NULL)
