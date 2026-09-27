"""Focus view: the whole window becomes Now Playing. The artist backdrop fills the background,
the song and its controls sit centred at the bottom, and the Queue / Lyrics / Equalizer buttons
on the right pop their panel out (one at a time; clicking the open one again hides it)."""

import gi

gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Graphene, Gsk, Gtk  # noqa: E402

from .equalizer_view import EqualizerView
from .flip_card import FlipCard
from .lyrics_view import LyricsView
from .player_controls import PlayerControls
from .queue_view import QueueView
from .visualizer import WaveVisualizer
from .widgets import follow_compact, song_actions

POPUP_WIDTH = 400  # the popup card's size when there's room for it
POPUP_HEIGHT = 440
POPUP_TOP = 72  # the popup and its buttons hang from here, clear of the song and controls below
CHIPS_ROOM = 96  # beside the column of buttons on the right
PHONE_TOP = 80  # phone-sized windows: the buttons are a row at the top, the popup below them
EDGE = 8  # the closest the popup comes to the window's edges
FLIP_ROOM = 16  # space around the popup card so its edge isn't clipped while it flips
CONTENT_WIDTH = 620  # the song and controls at the bottom, at most


class PopupArea(Gtk.Widget):
    """Fills the view and puts its child (the popup) at place(width, height) → (x, y, w, h),
    so the popup takes what room there is. Clicks outside the popup go through to what's below."""

    __gtype_name__ = "FocusPopupArea"

    def __init__(self, child: Gtk.Widget, place):
        super().__init__()
        self.child, self.place = child, place
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_size_allocate(self, width, height, baseline):
        x, y, w, h = self.place(width, height)
        # The child includes FLIP_ROOM around the card; it never gets less than it needs.
        w = max(w + 2 * FLIP_ROOM, self.child.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        h = max(h + 2 * FLIP_ROOM, self.child.measure(Gtk.Orientation.VERTICAL, w)[0])
        transform = Gsk.Transform().translate(Graphene.Point().init(x - FLIP_ROOM, y - FLIP_ROOM))
        self.child.allocate(w, h, -1, transform)

    def do_contains(self, x, y):
        return False

    def do_dispose(self):
        if self.child is not None and self.child.get_parent() is self:
            self.child.unparent()
        self.child = None
        Gtk.Widget.do_dispose(self)


class FocusView(Gtk.Overlay):
    """The focus view (see the module docstring): exit button, the song with its controls, and the Queue /
    Lyrics / Equalizer buttons whose popup card flips between pages."""

    def __init__(self, win):
        super().__init__()
        self.add_css_class("focus-view")
        self.win = win
        self.player = win.player
        self._popup: str | None = None

        veil = Gtk.Box(hexpand=True, vexpand=True)
        veil.add_css_class("focus-veil")
        self.set_child(veil)

        # ---- exit (top left) ----
        exit_button = Gtk.Button(icon_name="view-restore-symbolic", tooltip_text="Exit Focus (Esc)",
                                 halign=Gtk.Align.START, valign=Gtk.Align.START, margin_start=20, margin_top=20)
        exit_button.add_css_class("circular")
        exit_button.add_css_class("focus-chip")
        exit_button.connect("clicked", lambda *_: win.set_focus_mode(False))
        self.add_overlay(exit_button)

        # ---- song + controls (bottom centre) ----
        # Up to CONTENT_WIDTH wide, narrower in a narrow window; in a short one it stays below the
        # buttons at the top (the wave gives up height first).
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        bottom.add_css_class("focus-bottom")
        title_row = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.title = Gtk.Label(ellipsize=3, max_width_chars=40)
        self.title.add_css_class("focus-title")
        title_row.append(self.title)
        actions, self.heart, self.playlists_button = song_actions(win, self.player)
        title_row.append(actions)
        bottom.append(title_row)

        links = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER)
        self.artist = Gtk.Button(tooltip_text="Go to Artist")
        self.artist.add_css_class("flat")
        self.artist.add_css_class("focus-artist")
        self.artist.connect("clicked", self._goto_artist)
        self.dot = Gtk.Label(label="·")
        self.dot.add_css_class("focus-album")
        self.album = Gtk.Button(tooltip_text="Go to Album")
        self.album.add_css_class("flat")
        self.album.add_css_class("focus-album")
        self.album.connect("clicked", self._goto_album)
        for w in (self.artist, self.dot, self.album):
            links.append(w)
        bottom.append(links)

        self.visualizer = WaveVisualizer(self.player, height=150, min_height=48)
        self.visualizer.set_margin_top(10)
        bottom.append(self.visualizer)
        self.controls = PlayerControls(win)
        bottom.append(self.controls)
        self.add_overlay(Adw.Clamp(child=bottom, maximum_size=CONTENT_WIDTH, tightening_threshold=CONTENT_WIDTH,
                                   valign=Gtk.Align.END, margin_top=PHONE_TOP, margin_bottom=28,
                                   margin_start=16, margin_end=16))

        # ---- Queue / Lyrics / Equalizer (right side) ----
        chips = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                        halign=Gtk.Align.END, valign=Gtk.Align.START, margin_end=24, margin_top=POPUP_TOP)
        self.chips = chips
        self.buttons: dict[str, Gtk.ToggleButton] = {}
        for name, icon, label, key in (("queue", "musicapp-queue-symbolic", "Queue", "N"),
                                       ("lyrics", "musicapp-lyrics-symbolic", "Lyrics", "L"),
                                       ("eq", "musicapp-equalizer-symbolic", "Equalizer", "E")):
            button = Gtk.ToggleButton(icon_name=icon, tooltip_text=f"{label} (Ctrl+{key})")
            button.add_css_class("circular")
            button.add_css_class("focus-chip")
            button.connect("toggled", self._on_toggled, name)
            chips.append(button)
            self.buttons[name] = button
        self.buttons["eq"].set_visible(self.player.has_eq)
        self.add_overlay(chips)
        self._phone = False

        # ---- the popup card, just left of the buttons (below them on phone-sized windows) ----
        self.queue_view = QueueView(win)
        self.lyrics_view = LyricsView(win)
        self.eq_view = EqualizerView(win)
        self.pages = Gtk.Stack(vhomogeneous=False, hhomogeneous=True)  # the card's flip animates switches
        self.pages.add_named(self.queue_view, "queue")
        self.pages.add_named(self.lyrics_view, "lyrics")
        self.pages.add_named(self.eq_view, "eq")
        # Every page gets the same card size, whatever width it would like.
        clamp = Adw.Clamp(child=self.pages, maximum_size=POPUP_WIDTH - 24, tightening_threshold=POPUP_WIDTH - 24,
                          margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.popup_clamp = clamp
        card = Gtk.Box()
        card.add_css_class("focus-popup")
        card.set_overflow(Gtk.Overflow.HIDDEN)
        clamp.set_hexpand(True)
        card.append(clamp)
        # The card is the permanently-showing back of a FlipCard, so switching pages can turn it over.
        self.flip = FlipCard(Gtk.Box(), card)
        self.flip.set_flipped(True, animate=False)
        for side in ("top", "bottom", "start", "end"):
            getattr(self.flip, f"set_margin_{side}")(FLIP_ROOM)
        self.revealer = Gtk.Revealer(child=self.flip, reveal_child=False, transition_duration=220,
                                     transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.add_overlay(PopupArea(self.revealer, self._place_popup))
        follow_compact(win, self, FocusView._apply_compact)

        self.player.connect("track-changed", lambda *_: self._sync())
        self._sync()

    # ---- layout ----

    @staticmethod
    def _apply_compact(view, level):
        """Phone-sized windows: the buttons become a row at the top right, level with the exit
        button, so the popup can use the window's width below them."""
        phone = level == 2
        view._phone = phone
        view.chips.set_orientation(Gtk.Orientation.HORIZONTAL if phone else Gtk.Orientation.VERTICAL)
        view.chips.set_margin_top(20 if phone else POPUP_TOP)
        view.chips.set_margin_end(20 if phone else 24)
        for side in ("start", "end", "top", "bottom"):
            getattr(view.popup_clamp, f"set_margin_{side}")(8 if phone else 12)
        view.queue_resize()

    def _place_popup(self, width, height):
        """The card's (x, y, w, h): POPUP_WIDTH × POPUP_HEIGHT, or less if the window is small.
        When short it may cover the song and controls below; it's closed again with its button."""
        if self._phone:
            top, right = PHONE_TOP, width - EDGE
            w = min(POPUP_WIDTH, width - 2 * EDGE)
        else:
            top, right = POPUP_TOP, width - CHIPS_ROOM
            w = min(POPUP_WIDTH, right - EDGE)
        h = min(POPUP_HEIGHT, height - top - EDGE)
        return right - w, top, w, h

    # ---- popups ----

    def toggle(self, name: str):
        """Keyboard shortcuts: same as clicking that button."""
        button = self.buttons[name]
        if button.get_visible() and button.get_sensitive():
            button.set_active(not button.get_active())

    def close_popup(self) -> bool:
        """Hide the open popup, if any. Returns whether one was open."""
        if self._popup is None:
            return False
        self.buttons[self._popup].set_active(False)
        return True

    def _on_toggled(self, button, name):
        """A popup button was toggled: open its page (turning the card if another was open) or close it."""
        if button.get_active():
            previous, self._popup = self._popup, name
            if name == "lyrics":
                self.lyrics_view.load(self.player.current)
            if previous is not None and previous != name:
                # Switching while open: flip the card over onto the new page.
                self.flip.turn(lambda: self.pages.set_visible_child_name(name))
            else:
                self.flip.finish_turn()
                self.pages.set_visible_child_name(name)  # opening: it slides in already showing it
            for other, b in self.buttons.items():
                if other != name:
                    b.set_active(False)
            self.revealer.set_reveal_child(True)
        elif self._popup == name:
            self._popup = None
            self.revealer.set_reveal_child(False)

    # ---- following the player ----

    def _sync(self):
        """A new song: update the title and links, and what the popup buttons can do."""
        track = self.player.current
        playing = track is not None
        for name in ("lyrics", "eq"):
            self.buttons[name].set_sensitive(playing)
            if not playing:
                self.buttons[name].set_active(False)
        self.artist.set_visible(playing)
        self.album.set_visible(playing)
        self.dot.set_visible(playing)
        if not playing:
            self.title.set_text("Not Playing")
            return
        self.title.set_text(track.title)
        self.artist.set_label(track.artist)
        self.album.set_label(f"{track.album} · {track.year}" if track.year else track.album)
        if self._popup == "lyrics":
            self.lyrics_view.load(track)

    def _goto_artist(self, *_):
        """Leave the focus view and go to the song's artist."""
        if self.player.current:
            self.win.set_focus_mode(False)
            self.win.show_artist(self.player.current.artist_key)

    def _goto_album(self, *_):
        """Leave the focus view and go to the song's album."""
        t = self.player.current
        if t:
            self.win.set_focus_mode(False)
            self.win.show_album((t.artist_key, t.album))
