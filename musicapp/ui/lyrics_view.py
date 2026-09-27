"""Back of the Now Playing card: the song's lyrics over a dimmed copy of its cover."""

import time

import gi

gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Graphene, Gtk  # noqa: E402

from ..art import LARGE  # noqa: E402

FOLLOW_PAUSE_S = 4.0  # after a manual scroll, stop auto-following for this long
LEAD_S = 0.15  # highlight a line slightly early so it feels on time
SCROLL_MS = 350


class LyricsView(Gtk.Overlay):
    """The playing song's lyrics over a dimmed copy of its cover. Synced lyrics highlight and scroll to the
    line being sung (clicking a line seeks there); after a manual scroll, following pauses for a few
    seconds. Messages for instrumental songs, no lyrics, or network trouble (with Retry)."""

    def __init__(self, win, size: int | None = None):
        """size: a fixed square size; None lets the container decide."""
        super().__init__()
        self.win = win
        self.player = win.player
        if size:
            self.set_size_request(size, size)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("lyrics-card")
        self._track = None
        self._lyrics = None
        self._labels: list[Gtk.Label] = []
        self._current = -1
        self._user_scrolled_at = 0.0

        self.background = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.background.add_css_class("lyrics-bg")
        self.set_child(self.background)
        dim = Gtk.Box()
        dim.add_css_class("lyrics-dim")
        self.add_overlay(dim)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                         width_request=32, height_request=32), "loading")

        self.lines_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                                 margin_top=150, margin_bottom=150, margin_start=18, margin_end=18)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.lines_box)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_user_scroll)
        self.scroller.add_controller(scroll)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_user_scroll)
        self.scroller.add_controller(drag)
        lines_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lines_page.append(self.scroller)
        self.scroller.set_vexpand(True)
        self.credit = Gtk.Label(label="Lyrics from LRCLIB", margin_bottom=6)
        self.credit.add_css_class("lyrics-credit")
        lines_page.append(self.credit)
        self.stack.add_named(lines_page, "lines")

        message_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                              margin_start=18, margin_end=18)
        self.message_icon = Gtk.Image(pixel_size=40)
        self.message = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.message.add_css_class("lyrics-message")
        self.retry = Gtk.Button(label="Retry", halign=Gtk.Align.CENTER)
        self.retry.add_css_class("pill")
        self.retry.connect("clicked", lambda *_: self._retry())
        for w in (self.message_icon, self.message, self.retry):
            message_box.append(w)
        self.stack.add_named(message_box, "message")
        self.add_overlay(self.stack)

        self._scroll_anim = Adw.TimedAnimation.new(
            self.scroller, 0, 0, SCROLL_MS,
            Adw.PropertyAnimationTarget.new(self.scroller.get_vadjustment(), "value"))
        self._scroll_anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)

        self.player.connect("position-changed", lambda _p, pos, _d: self._on_position(pos))
        self.player.connect("seeked", lambda _p, pos: self._on_position(pos, force=True))

    # ---- loading ----

    def load(self, track):
        """Show lyrics for `track` (no-op if they're already showing)."""
        if track is self._track:
            return
        self._track = track
        self._lyrics = None
        self._clear_lines()
        if track is None:
            self.background.set_paintable(None)
            return
        self.win.art.request_track(track, LARGE, lambda tex, t=track: self._set_background(t, tex))
        self._show_page("loading")
        self.win.app.lyrics.request(track, self._on_lyrics)

    def _show_page(self, name):
        """Switch between loading, lines and message."""
        # Crossfade only while on screen: a hidden stack can't draw the page it's fading from.
        transition = Gtk.StackTransitionType.CROSSFADE if self.get_mapped() else Gtk.StackTransitionType.NONE
        self.stack.set_visible_child_full(name, transition)

    def _set_background(self, track, texture):
        """Show the song's cover behind the lyrics, unless the song changed meanwhile."""
        if track is self._track:
            self.background.set_paintable(texture)

    def _retry(self):
        """Look the lyrics up again (forgetting the cached result)."""
        track, self._track = self._track, None
        if track is not None:
            self.win.app.lyrics.forget(track)
            self.load(track)

    def _on_lyrics(self, track, lyrics):
        """The lookup finished: show the lines or a message."""
        if track is not self._track:
            return  # a newer song was requested meanwhile
        self._lyrics = lyrics
        if lyrics.kind in ("synced", "plain"):
            self._show_lines(lyrics)
        elif lyrics.kind == "instrumental":
            self._show_message("audio-x-generic-symbolic", "Instrumental", retry=False)
        elif lyrics.kind == "none":
            self._show_message("dialog-question-symbolic", "No lyrics found for this song", retry=False)
        else:
            self._show_message("network-offline-symbolic", "Couldn't reach lrclib.net", retry=True)

    def _show_message(self, icon, text, retry):
        """Show a message (with Retry when it makes sense)."""
        self.message_icon.set_from_icon_name(icon)
        self.message.set_text(text)
        self.retry.set_visible(retry)
        self._show_page("message")

    def _clear_lines(self):
        """Remove the lyric lines."""
        while child := self.lines_box.get_first_child():
            self.lines_box.remove(child)
        self._labels = []
        self._current = -1

    def _show_lines(self, lyrics):
        """Show the lines; synced ones can be clicked to seek there."""
        self._clear_lines()
        synced = lyrics.kind == "synced"
        for index, (seconds, text) in enumerate(lyrics.lines):
            label = Gtk.Label(label=text or "♪", wrap=True, justify=Gtk.Justification.CENTER,
                              xalign=0.5, max_width_chars=30)
            label.add_css_class("lyric-line")
            if synced:
                label.add_css_class("lyric-upcoming")
                click = Gtk.GestureClick()
                click.connect("released", lambda *_a, s=seconds: self.player.seek(s))
                label.add_controller(click)
                label.set_cursor_from_name("pointer")
            self.lines_box.append(label)
            self._labels.append(label)
        self.credit.set_visible(lyrics.source == "lrclib")
        self._show_page("lines")
        self.scroller.get_vadjustment().set_value(0)
        if synced:
            self._on_position(self.player.get_position(), force=True)

    # ---- following the song ----

    def _on_user_scroll(self, *_):
        """The user scrolled: stop following the song for FOLLOW_PAUSE_S."""
        self._user_scrolled_at = time.monotonic()
        self._scroll_anim.pause()
        return False

    def _on_position(self, position, force=False):
        """Highlight the line being sung and scroll to it (synced lyrics only)."""
        lyrics = self._lyrics
        if lyrics is None or lyrics.kind != "synced" or self._track is not self.player.current:
            return
        index = -1
        for i, (seconds, _text) in enumerate(lyrics.lines):
            if seconds <= position + LEAD_S:
                index = i
            else:
                break
        if index == self._current and not force:
            return
        for i, label in enumerate(self._labels):
            for css in ("lyric-past", "lyric-current", "lyric-upcoming"):
                label.remove_css_class(css)
            label.add_css_class("lyric-current" if i == index else "lyric-past" if i < index else "lyric-upcoming")
        self._current = index
        if force or time.monotonic() - self._user_scrolled_at > FOLLOW_PAUSE_S:
            self._scroll_to(index)

    def _scroll_to(self, index):
        """Smoothly scroll so the line is in the middle."""
        if not (0 <= index < len(self._labels)) or not self.get_mapped():
            return
        label = self._labels[index]
        ok, point = label.compute_point(self.lines_box, Graphene.Point().init(0, 0))
        if not ok:
            return
        adj = self.scroller.get_vadjustment()
        target = point.y + label.get_height() / 2 - adj.get_page_size() / 2
        target = max(adj.get_lower(), min(target, adj.get_upper() - adj.get_page_size()))
        self._scroll_anim.pause()
        self._scroll_anim.set_value_from(adj.get_value())
        self._scroll_anim.set_value_to(target)
        self._scroll_anim.play()
