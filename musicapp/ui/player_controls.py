"""Transport controls for the Now Playing panel: seek bar, playback buttons and volume."""

from gi.repository import GLib, Gtk

from ..library import format_time
from ..player import Repeat
from .widgets import icon_button

SEEK_SETTLE_MS = 200

REPEAT_ICONS = {
    Repeat.OFF: ("media-playlist-repeat-symbolic", "Repeat: Off"),
    Repeat.ALL: ("media-playlist-repeat-symbolic", "Repeat: Queue"),
    Repeat.ONE: ("media-playlist-repeat-song-symbolic", "Repeat: Current Song"),
}


class PlayerControls(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("player-controls")
        self.player = win.player
        self._seeking = False
        self._seek_timeout = 0
        self._muted_volume = None

        # ---- seek bar ----
        self.adjustment = Gtk.Adjustment(lower=0, upper=1, step_increment=5, page_increment=10)
        self.scale = Gtk.Scale(adjustment=self.adjustment, hexpand=True, draw_value=False)
        self.scale.add_css_class("seek-scale")
        self.scale.connect("change-value", self._on_seek_changed)
        self.append(self.scale)
        times = Gtk.Box(margin_start=10, margin_end=10)
        self.elapsed = Gtk.Label(label="0:00", xalign=0, hexpand=True)
        self.elapsed.add_css_class("time-label")
        self.total = Gtk.Label(label="0:00", xalign=1)
        self.total.add_css_class("time-label")
        times.append(self.elapsed)
        times.append(self.total)
        self.append(times)

        # ---- transport ----
        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        self.shuffle_btn = icon_button("media-playlist-shuffle-symbolic", "Shuffle", circular=True, action="app.shuffle")
        self.shuffle_btn.add_css_class("mode-button")
        prev_btn = icon_button("media-skip-backward-symbolic", "Previous", circular=True, action="app.previous")
        self.play_btn = icon_button("media-playback-start-symbolic", "Play", flat=False, circular=True,
                                    action="app.play-pause")
        self.play_btn.add_css_class("play-button")
        self.play_btn.add_css_class("suggested-action")
        next_btn = icon_button("media-skip-forward-symbolic", "Next", circular=True, action="app.next")
        self.repeat_btn = icon_button("media-playlist-repeat-symbolic", "Repeat: Off", circular=True, action="app.repeat")
        self.repeat_btn.add_css_class("mode-button")
        for w in (prev_btn, next_btn):
            w.add_css_class("transport")
        for w in (self.shuffle_btn, prev_btn, self.play_btn, next_btn, self.repeat_btn):
            controls.append(w)
        self.append(controls)

        # ---- volume ----
        volume_row = Gtk.Box(spacing=4, margin_start=24, margin_end=24)
        self.volume_btn = icon_button("audio-volume-high-symbolic", "Mute")
        self.volume_btn.add_css_class("volume-button")
        self.volume_btn.connect("clicked", self._toggle_mute)
        volume_row.append(self.volume_btn)
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.02)
        self.volume.set_draw_value(False)
        self.volume.add_css_class("volume-scale")
        self.volume.set_hexpand(True)
        self.volume.set_value(self.player.volume)
        self._volume_handler = self.volume.connect("value-changed", lambda s: self.player.set_volume(s.get_value()))
        volume_row.append(self.volume)
        self.append(volume_row)

        self.player.connect("track-changed", self._on_track_changed)
        self.player.connect("state-changed", self._on_state_changed)
        self.player.connect("position-changed", self._on_position)
        self.player.connect("modes-changed", lambda *_: self._on_modes())
        self.player.connect("volume-changed", self._on_volume)
        self._on_track_changed(self.player, self.player.current)
        self._on_state_changed(self.player, self.player.playing)
        self._on_modes()
        self._on_volume(self.player, self.player.volume)

    # ---- player -> UI ----

    def _on_track_changed(self, _player, track):
        self.scale.set_sensitive(track is not None)
        if track is None:
            self._set_time(0, 0)

    def _on_state_changed(self, _player, playing):
        self.play_btn.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play_btn.set_tooltip_text("Pause" if playing else "Play")

    def _on_position(self, _player, position, duration):
        if not self._seeking:
            self._set_time(position, duration)

    def _set_time(self, position, duration):
        self.adjustment.set_upper(max(duration, 1))
        self.adjustment.set_value(min(position, max(duration, 1)))
        self.elapsed.set_text(format_time(position))
        self.total.set_text(format_time(duration))

    def _on_modes(self):
        if self.player.shuffle:
            self.shuffle_btn.add_css_class("active")
            self.shuffle_btn.set_tooltip_text("Shuffle: On")
        else:
            self.shuffle_btn.remove_css_class("active")
            self.shuffle_btn.set_tooltip_text("Shuffle: Off")
        icon, tip = REPEAT_ICONS[self.player.repeat]
        self.repeat_btn.set_icon_name(icon)
        self.repeat_btn.set_tooltip_text(tip)
        if self.player.repeat == Repeat.OFF:
            self.repeat_btn.remove_css_class("active")
        else:
            self.repeat_btn.add_css_class("active")

    def _on_volume(self, _player, value):
        self.volume.handler_block(self._volume_handler)
        self.volume.set_value(value)
        self.volume.handler_unblock(self._volume_handler)
        if value == 0:
            icon = "audio-volume-muted-symbolic"
        elif value < 0.34:
            icon = "audio-volume-low-symbolic"
        elif value < 0.67:
            icon = "audio-volume-medium-symbolic"
        else:
            icon = "audio-volume-high-symbolic"
        self.volume_btn.set_icon_name(icon)
        self.volume_btn.set_tooltip_text("Unmute" if value == 0 else "Mute")

    # ---- UI -> player ----

    def _on_seek_changed(self, _scale, _scroll, value):
        """While the user drags, only move the handle and labels; seek once they settle."""
        if self.player.current is None:
            return True
        value = max(0.0, min(value, self.adjustment.get_upper()))
        self._seeking = True
        self.adjustment.set_value(value)
        self.elapsed.set_text(format_time(value))
        if self._seek_timeout:
            GLib.source_remove(self._seek_timeout)
        self._seek_timeout = GLib.timeout_add(SEEK_SETTLE_MS, self._commit_seek)
        return True

    def _commit_seek(self):
        self._seek_timeout = 0
        self.player.seek(self.adjustment.get_value())
        self._seeking = False
        return False

    def _toggle_mute(self, *_):
        if self.player.volume > 0:
            self._muted_volume = self.player.volume
            self.player.set_volume(0)
        else:
            self.player.set_volume(self._muted_volume or 0.8)
