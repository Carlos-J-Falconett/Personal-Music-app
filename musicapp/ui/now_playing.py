"""Right-hand Now Playing panel. The queue button switches between the Player view
(cover, info, time bar, controls, volume) and the Queue view (current song + Up Next).
The lyrics and equalizer buttons flip the cover over to show the song's lyrics or its EQ.
The focus button opens the full-window focus view (see focus_view.py).

When the window is too small for the library beside it, the panel fills the window in a compact
form: no cover (the artist backdrop shows through), the song and its controls at the bottom, and
the Queue / Lyrics / Equalizer buttons flip that whole view over to show their page."""

from gi.repository import Gtk

from ..art import LARGE
from .equalizer_view import EqualizerView, is_flat
from .flip_card import FlipCard
from .lyrics_view import LyricsView
from .player_controls import PlayerControls
from .queue_view import QueueView
from .visualizer import WaveVisualizer
from .widgets import CoverArt, PriorityColumn, SquareFit, song_actions

COVER_SIZE = 340  # cover / lyrics / equalizer card, at most
COVER_MIN = 96  # in short windows the cover shrinks down to this before the controls move
HEADINGS = {None: "Now Playing", "queue": "Queue", "lyrics": "Lyrics", "eq": "Equalizer"}


class SongInfo(Gtk.Box):
    """The playing song's title, artist and album links, heart and playlists buttons."""

    def __init__(self, win):
        super().__init__(spacing=6)
        self.win = win
        self.player = win.player
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.title = Gtk.Label(xalign=0, wrap=True, lines=2, ellipsize=3)
        self.title.add_css_class("now-title")
        self.artist = Gtk.Button(halign=Gtk.Align.START, tooltip_text="Go to Artist")
        self.artist.add_css_class("flat")
        self.artist.add_css_class("artist-link")
        self.artist.connect("clicked", self._goto_artist)
        self.album = Gtk.Button(halign=Gtk.Align.START, tooltip_text="Go to Album")
        self.album.add_css_class("flat")
        self.album.add_css_class("album-link")
        self.album.connect("clicked", self._goto_album)
        titles.append(self.title)
        titles.append(self.artist)
        titles.append(self.album)
        self.append(titles)
        actions, self.heart, self.playlists_button = song_actions(win, self.player)
        actions.set_valign(Gtk.Align.START)
        actions.set_margin_top(4)
        self.append(actions)
        self.player.connect("track-changed", lambda *_: self._sync())
        self._sync()

    def _sync(self):
        track = self.player.current
        self.artist.set_visible(track is not None)
        self.album.set_visible(track is not None)
        if track is None:
            self.title.set_text("Not Playing")
            return
        self.title.set_text(track.title)
        self.artist.set_label(track.artist)
        self.album.set_label(f"{track.album} · {track.year}" if track.year else track.album)

    def _goto_artist(self, *_):
        if self.player.current:
            self.win.show_artist(self.player.current.artist_key)

    def _goto_album(self, *_):
        t = self.player.current
        if t:
            self.win.show_album((t.artist_key, t.album))


class NowPlayingPanel(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("now-playing-panel")
        self.set_overflow(Gtk.Overflow.HIDDEN)  # clip to the rounded card
        self.win = win
        self.player = win.player
        self._back_face = "lyrics"  # which page the back of the card shows (or is turning to)
        self._compact = False
        self._compact_face: str | None = None  # the page the compact view is flipped to

        # ---- header ----
        header = Gtk.Box(spacing=4, margin_top=8, margin_bottom=4, margin_start=8, margin_end=8)
        header.add_css_class("panel-header")
        self.heading = Gtk.Label(label="Now Playing", xalign=0, hexpand=True, margin_start=8)
        self.heading.add_css_class("heading")
        header.append(self.heading)
        self.focus_button = Gtk.Button(icon_name="view-fullscreen-symbolic", valign=Gtk.Align.CENTER,
                                       tooltip_text="Focus on Now Playing (Ctrl+Shift+F)",
                                       action_name="win.focus-mode")
        self.focus_button.add_css_class("flat")
        self.focus_button.add_css_class("circular")
        header.append(self.focus_button)
        self.queue_button = Gtk.ToggleButton(icon_name="musicapp-queue-symbolic", tooltip_text="Show Queue (Ctrl+N)",
                                             valign=Gtk.Align.CENTER)
        self.queue_button.add_css_class("flat")
        self.queue_button.add_css_class("circular")
        self.queue_button.connect("toggled", self._on_queue_toggled)
        header.append(self.queue_button)
        self.lyrics_button = Gtk.ToggleButton(icon_name="musicapp-lyrics-symbolic",
                                              tooltip_text="Show Lyrics (Ctrl+L)", valign=Gtk.Align.CENTER)
        self.lyrics_button.add_css_class("flat")
        self.lyrics_button.add_css_class("circular")
        self.lyrics_button.connect("toggled", self._on_lyrics_toggled)
        header.append(self.lyrics_button)
        self.eq_button = Gtk.ToggleButton(icon_name="musicapp-equalizer-symbolic",
                                          tooltip_text="Show Equalizer (Ctrl+E)", valign=Gtk.Align.CENTER,
                                          visible=self.player.has_eq)
        self.eq_button.add_css_class("flat")
        self.eq_button.add_css_class("circular")
        self.eq_button.connect("toggled", self._on_eq_toggled)
        header.append(self.eq_button)
        self.append(header)
        self._buttons = {"queue": self.queue_button, "lyrics": self.lyrics_button, "eq": self.eq_button}

        # ---- views: the docked panel's, and the compact (window-filling) one ----
        self.views = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.views.add_named(self._build_player_view(), "player")
        self.views.add_named(QueueView(win), "queue")
        self.body = Gtk.Stack(vexpand=True)
        self.body.add_named(self.views, "normal")
        self.body.add_named(self._build_compact_view(), "compact")
        self.append(self.body)

        self.player.connect("track-changed", lambda *_: self._sync())
        self.player.connect("queue-changed", lambda *_: self._sync())
        self.player.connect("modes-changed", lambda *_: self._sync())
        self.player.connect("eq-changed", lambda *_: self._sync_eq_indicator())
        self._sync()

    # ---- public ----

    def toggle_queue(self):
        self.queue_button.set_active(not self.queue_button.get_active())

    def toggle_lyrics(self):
        if self.lyrics_button.get_sensitive():
            self.lyrics_button.set_active(not self.lyrics_button.get_active())

    def toggle_eq(self):
        if self.eq_button.get_visible() and self.eq_button.get_sensitive():
            self.eq_button.set_active(not self.eq_button.get_active())

    def set_collapsed(self, collapsed: bool):
        """Window too small for the library beside the panel: switch to the compact view."""
        if collapsed == self._compact:
            return
        for button in self._buttons.values():  # close whatever is open, the old way
            button.set_active(False)
        self._compact = collapsed
        self.body.set_visible_child_name("compact" if collapsed else "normal")
        (self.add_css_class if collapsed else self.remove_css_class)("compact")
        self.heading.set_text(HEADINGS[None])

    # ---- buttons ----

    def _on_queue_toggled(self, button):
        showing = button.get_active()
        button.set_tooltip_text("Hide Queue (Ctrl+N)" if showing else "Show Queue (Ctrl+N)")
        if self._compact:
            self._compact_show("queue", showing)
            return
        if showing:
            self.lyrics_button.set_active(False)
            self.eq_button.set_active(False)
        self.views.set_visible_child_name("queue" if showing else "player")
        self.heading.set_text(HEADINGS["queue" if showing else None])

    def _on_lyrics_toggled(self, button):
        showing = button.get_active()
        button.set_tooltip_text("Hide Lyrics (Ctrl+L)" if showing else "Show Lyrics (Ctrl+L)")
        if self._compact:
            self._compact_show("lyrics", showing)
            return
        if showing:
            self.lyrics_view.load(self.player.current)
        self._show_back("lyrics", showing)

    def _on_eq_toggled(self, button):
        showing = button.get_active()
        button.set_tooltip_text("Hide Equalizer (Ctrl+E)" if showing else "Show Equalizer (Ctrl+E)")
        if self._compact:
            self._compact_show("eq", showing)
            return
        self._show_back("eq", showing)

    def _show_back(self, name, showing):
        """Lyrics and EQ share the back of the card: switching between them while flipped turns the card over."""
        if showing:
            self.queue_button.set_active(False)  # both live on the player view
            other = self.eq_button if name == "lyrics" else self.lyrics_button
            previous, self._back_face = self._back_face, name
            if self.flip.flipped and previous != name:
                # Already showing the other face: flip over onto this one.
                self.flip.turn(lambda: self.back_stack.set_visible_child_name(name))
            else:
                self.back_stack.set_visible_child_name(name)
            other.set_active(False)
            self.flip.set_flipped(True)
        elif self._back_face == name:
            self.flip.set_flipped(False)

    def _compact_show(self, name, showing):
        """Compact view: the song and controls flip over to show the queue, lyrics or equalizer.
        Picking another while flipped turns the card onto it; picking the open one flips back."""
        if showing:
            previous, self._compact_face = self._compact_face, name  # set first: see below
            for other, button in self._buttons.items():
                if other != name:
                    button.set_active(False)  # their handlers see they're no longer the face
            if name == "lyrics":
                self.compact_lyrics.load(self.player.current)
            if self.compact_flip.flipped and previous not in (None, name):
                self.compact_flip.turn(lambda: self.compact_pages.set_visible_child_name(name))
            else:
                self.compact_flip.finish_turn()
                self.compact_pages.set_visible_child_name(name)
                self.compact_flip.set_flipped(True)
            self.heading.set_text(HEADINGS[name])
        elif self._compact_face == name:
            self._compact_face = None
            self.compact_flip.set_flipped(False)
            self.heading.set_text(HEADINGS[None])

    def _sync_eq_indicator(self):
        track = self.player.current
        custom = track is not None and not is_flat(self.player.eq_gains)
        (self.eq_button.add_css_class if custom else self.eq_button.remove_css_class)("eq-active")

    # ---- player view (docked panel) ----

    def _build_player_view(self):
        # When the window is short, the controls keep their room first, then the song's details,
        # then the wave; the cover takes what's left (and hides when that's too little).
        column = PriorityColumn(spacing=6, margin_start=16, margin_end=16, margin_bottom=4)
        self.cover = CoverArt(self.win.art, COVER_MIN, art_size=LARGE, large=True)
        self.cover.set_halign(Gtk.Align.FILL)
        self.cover.set_valign(Gtk.Align.FILL)
        self.lyrics_view = LyricsView(self.win)
        self.eq_view = EqualizerView(self.win)
        # FlipCard animates switches between these; each is sized to the one showing.
        self.back_stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.back_stack.add_named(self.lyrics_view, "lyrics")
        self.back_stack.add_named(self.eq_view, "eq")
        self.flip = FlipCard(self.cover, self.back_stack)
        # The card takes the height left over: in short windows it shrinks, the controls stay.
        square = SquareFit(self.flip, COVER_SIZE, COVER_MIN)
        square.set_margin_top(4)
        square.set_margin_bottom(6)
        column.add(square, 3, fill=True)

        self.info = SongInfo(self.win)
        self.heart, self.playlists_button = self.info.heart, self.info.playlists_button
        column.add(self.info, 1)

        # Audio wave, directly above the time bar.
        self.visualizer = WaveVisualizer(self.player, height=96, min_height=40)
        column.add(self.visualizer, 2)

        self.controls = PlayerControls(self.win)
        self.controls.set_margin_top(0)
        column.add(self.controls, 0)
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=column)

    # ---- compact view (the panel fills a small window) ----

    def _build_compact_view(self):
        # Front: the song and its controls at the bottom, over the artist backdrop.
        front = PriorityColumn(spacing=6, margin_start=16, margin_end=16, margin_bottom=8)
        front.add_css_class("compact-player")
        front.add(SongInfo(self.win), 1)
        front.add(WaveVisualizer(self.player, height=110, min_height=40), 2)
        front.add(PlayerControls(self.win), 0)
        front_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=front)

        # Back: the queue, lyrics or equalizer, filling the space as a glass card.
        self.compact_lyrics = LyricsView(self.win)
        self.compact_pages = Gtk.Stack(hexpand=True, vexpand=True)
        self.compact_pages.add_named(QueueView(self.win), "queue")
        self.compact_pages.add_named(self.compact_lyrics, "lyrics")
        self.compact_pages.add_named(EqualizerView(self.win), "eq")
        card = Gtk.Box(margin_start=12, margin_end=12, margin_bottom=12, margin_top=4)
        card.add_css_class("compact-card")
        card.set_overflow(Gtk.Overflow.HIDDEN)
        card.append(self.compact_pages)
        self.compact_flip = FlipCard(front_scroll, card)
        self.compact_flip.set_vexpand(True)
        return self.compact_flip

    # ---- sync with player ----

    def _sync(self):
        track = self.player.current
        self.cover.set_track(track)
        self.lyrics_button.set_sensitive(track is not None)
        self.eq_button.set_sensitive(track is not None)
        self._sync_eq_indicator()
        if self.lyrics_button.get_active():
            (self.compact_lyrics if self._compact else self.lyrics_view).load(track)
        if track is None:
            self.lyrics_button.set_active(False)
            self.eq_button.set_active(False)
