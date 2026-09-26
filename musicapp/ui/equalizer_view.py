"""Back of the Now Playing card: a 10-band equalizer for the playing song only.
Changes are heard live and saved for that song; Reset returns it to flat."""

from gi.repository import GLib, Gio, Gtk

from ..art import LARGE
from ..player import EQ_BANDS, EQ_MAX_DB, EQ_MIN_DB, FLAT

SAVE_DELAY_MS = 400

PRESETS = {
    "Flat": FLAT,
    "Bass Boost": [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
    "Treble Boost": [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
    "Vocal": [-2, -2, -1, 1, 3, 4, 3, 1, 0, -1],
    "Rock": [4, 3, 1, -1, -2, -1, 1, 3, 4, 4],
    "Pop": [-1, 1, 3, 4, 3, 0, -1, -1, 1, 2],
    "Electronic": [5, 4, 1, 0, -2, 1, 0, 2, 4, 5],
    "Classical": [0, 0, 0, 0, 0, 0, -2, -3, -3, -4],
}


def is_flat(gains) -> bool:
    return all(abs(g) < 0.05 for g in gains)


class EqualizerView(Gtk.Overlay):
    def __init__(self, win, size: int | None = None):
        """size: a fixed square size; None lets the container decide."""
        super().__init__()
        self.win = win
        self.player = win.player
        self.db = win.app.db
        if size:
            self.set_size_request(size, size)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("lyrics-card")
        self.add_css_class("eq-card")
        self._track = None
        self._syncing = False  # true while sliders follow the player, so they don't write back
        self._save_source = 0
        self._pending = None  # (track, gains) waiting to be saved

        self.background = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.background.add_css_class("lyrics-bg")
        self.set_child(self.background)
        dim = Gtk.Box()
        dim.add_css_class("lyrics-dim")
        self.add_overlay(dim)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=14, margin_bottom=14, margin_start=12, margin_end=12)

        header = Gtk.Box(spacing=6)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, margin_start=4)
        title = Gtk.Label(label="Equalizer", xalign=0)
        title.add_css_class("heading")
        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("caption")
        self.status.add_css_class("eq-status")
        titles.append(title)
        titles.append(self.status)
        header.append(titles)
        menu = Gio.Menu()
        for name in PRESETS:
            menu.append(name, f"eq.preset::{name}")
        presets = Gtk.MenuButton(label="Presets", menu_model=menu, valign=Gtk.Align.CENTER)
        presets.add_css_class("flat")
        header.append(presets)
        group = Gio.SimpleActionGroup()
        action = Gio.SimpleAction.new("preset", GLib.VariantType.new("s"))
        action.connect("activate", lambda _a, name: self._apply_preset(name.get_string()))
        group.add_action(action)
        self.insert_action_group("eq", group)
        box.append(header)

        bands = Gtk.Box(homogeneous=True, vexpand=True, margin_top=6)
        self.scales: list[Gtk.Scale] = []
        self.values: list[Gtk.Label] = []
        for label_text in EQ_BANDS:
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            value = Gtk.Label(label="0")
            value.add_css_class("eq-value")
            scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, inverted=True, vexpand=True,
                              halign=Gtk.Align.CENTER, draw_value=False)
            scale.set_range(EQ_MIN_DB, EQ_MAX_DB)
            scale.set_increments(0.5, 3)
            scale.add_css_class("eq-scale")
            scale.connect("value-changed", self._on_scale_changed)
            freq = Gtk.Label(label=label_text)
            freq.add_css_class("eq-freq")
            for w in (value, scale, freq):
                column.append(w)
            bands.append(column)
            self.scales.append(scale)
            self.values.append(value)
        box.append(bands)

        self.reset = Gtk.Button(label="Reset to Default", halign=Gtk.Align.CENTER, margin_top=4)
        self.reset.add_css_class("pill")
        self.reset.add_css_class("eq-reset")
        self.reset.connect("clicked", lambda *_: self.reset_eq())
        box.append(self.reset)
        self.add_overlay(box)
        self.set_measure_overlay(box, True)  # the card is never smaller than the sliders and buttons

        self.player.connect("track-changed", lambda _p, track: self._on_track(track))
        self.player.connect("eq-changed", lambda *_: self._sync_from_player())
        self._on_track(self.player.current)

    # ---- following the player ----

    def _on_track(self, track):
        self._flush_save()  # a pending change belongs to the previous song
        self._track = track
        if track is None:
            self.background.set_paintable(None)
        else:
            self.win.art.request_track(track, LARGE, lambda tex, t=track: self._set_background(t, tex))
        self._sync_from_player()

    def _set_background(self, track, texture):
        if track is self._track:
            self.background.set_paintable(texture)

    def _sync_from_player(self):
        gains = self.player.eq_gains
        self._syncing = True
        try:
            for scale, value, g in zip(self.scales, self.values, gains):
                if abs(scale.get_value() - g) > 1e-6:
                    scale.set_value(g)
                value.set_text(_format_db(g))
        finally:
            self._syncing = False
        flat = is_flat(gains)
        self.status.set_text("Default for this song" if flat else "Custom for this song")
        self.reset.set_sensitive(not flat and self._track is not None)
        for scale in self.scales:
            scale.set_sensitive(self._track is not None)

    # ---- user changes ----

    def _on_scale_changed(self, _scale):
        if self._syncing or self._track is None:
            return
        self._set_gains([s.get_value() for s in self.scales])

    def _apply_preset(self, name):
        if self._track is None or name not in PRESETS:
            return
        self._set_gains(PRESETS[name])

    def reset_eq(self):
        if self._track is not None:
            self._set_gains(FLAT)

    def _set_gains(self, gains):
        self.player.set_eq(gains)  # heard immediately; eq-changed updates the sliders
        self._pending = (self._track, self.player.eq_gains)
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._on_save_timeout)

    def _on_save_timeout(self):
        self._save_source = 0
        self._flush_save()
        return False

    def _flush_save(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        pending, self._pending = self._pending, None
        if pending is not None:
            track, gains = pending
            if is_flat(gains):
                self.db.delete_track_eq(track.id)
            else:
                self.db.set_track_eq(track.id, gains)


def _format_db(g: float) -> str:
    if abs(g) < 0.05:
        return "0"
    return f"{g:+.1f}".rstrip("0").rstrip(".")
