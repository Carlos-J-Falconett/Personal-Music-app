"""Video without sound, for the Black Hole slideshow. Gtk.MediaFile always opens an audio stream,
and muting it mutes the whole app at the sound server (which remembers volume and mute per
application): the music would go silent too. This plays the picture only, so no audio stream
exists at all. It offers the parts of Gtk.MediaFile's interface the slideshow uses."""

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, GObject, Gst  # noqa: E402

_PLAY_FLAG_AUDIO = 1 << 1  # GstPlayFlags.AUDIO


class SilentVideo(GObject.Object):
    __gtype_name__ = "SilentVideo"

    prepared = GObject.Property(type=bool, default=False)
    ended = GObject.Property(type=bool, default=False)
    error = GObject.Property(type=str, default=None)

    def __init__(self, path):
        super().__init__()
        self.paintable = None
        self._loop = False
        self._playing = False
        self._pipeline = Gst.ElementFactory.make("playbin", None)
        sink = Gst.ElementFactory.make("gtk4paintablesink", None)
        if self._pipeline is None or sink is None:
            self._pipeline = None
            GLib.idle_add(lambda: self.set_property("error", "Video playback isn't available") and False)
            return
        self.paintable = sink.get_property("paintable")
        self._pipeline.set_property("video-sink", sink)
        self._pipeline.set_property("flags", self._pipeline.get_property("flags") & ~_PLAY_FLAG_AUDIO)
        self._pipeline.set_property("uri", Gst.filename_to_uri(str(path)))
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::async-done", self._on_async_done)
        bus.connect("message::eos", self._on_eos)
        bus.connect("message::error", self._on_error)
        self._pipeline.set_state(Gst.State.PAUSED)  # shows the first frame once prepared

    # ---- Gtk.MediaFile-like interface ----

    def is_prepared(self) -> bool:
        return self.prepared

    def get_ended(self) -> bool:
        return self.ended

    def get_error(self):
        return self.error or None  # a str property reads as "" when unset

    def set_loop(self, loop: bool):
        self._loop = loop

    def play(self):
        if self._pipeline is None:
            return
        if self.ended:  # like Gtk.MediaFile: playing again after the end starts over
            self._pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            self.ended = False
        self._playing = True
        self._pipeline.set_state(Gst.State.PLAYING)

    def pause(self):
        self._playing = False
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.PAUSED)

    def stop(self):
        """Release the pipeline for good."""
        if self._pipeline is not None:
            self._pipeline.get_bus().remove_signal_watch()
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

    # ---- bus ----

    def _on_async_done(self, *_):
        if not self.prepared:
            self.prepared = True

    def _on_eos(self, *_):
        if self._loop:
            self._pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
        else:
            self._playing = False
            self.ended = True

    def _on_error(self, _bus, msg):
        err, _debug = msg.parse_error()
        print(f"[video] {err.message}")
        self.stop()
        self.error = err.message or "Couldn't play the video"
