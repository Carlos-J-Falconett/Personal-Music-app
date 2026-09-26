"""Small reusable widgets and helpers."""

import weakref

import gi

gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import GLib, Graphene, Gsk, Gtk  # noqa: E402

from ..art import THUMB
from .menus import AppMenu, menu_header

HEART_ON = "musicapp-heart-filled-symbolic"
HEART_OFF = "musicapp-heart-outline-symbolic"
PLAYLIST_ICON = "musicapp-playlist-symbolic"


def icon_button(icon: str, tooltip: str, *, flat=True, circular=False, action=None) -> Gtk.Button:
    btn = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    if flat:
        btn.add_css_class("flat")
    if circular:
        btn.add_css_class("circular")
    if action:
        btn.set_action_name(action)
    return btn


NARROW_WIDTH = 560


class NarrowColumn(Gtk.Widget):
    """Holds a child at min(NARROW_WIDTH, available) wide, pinned to the left edge, so the rest of
    the row is left empty (the window's backdrop shows through). The glass tint is drawn only
    behind the child."""

    __gtype_name__ = "NarrowColumn"

    def __init__(self, child: Gtk.Widget, width: int = NARROW_WIDTH):
        super().__init__(hexpand=True, vexpand=True)
        self.width = width
        self.child = child
        child.add_css_class("content-glass")
        child.set_overflow(Gtk.Overflow.HIDDEN)  # clip to the rounded card
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        minimum, natural, _, _ = self.child.measure(orientation, min(for_size, self.width)
                                                    if orientation == Gtk.Orientation.VERTICAL and for_size > 0
                                                    else for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            natural = max(natural, min(self.width, max(minimum, natural)))
        return minimum, natural, -1, -1

    def do_size_allocate(self, width, height, baseline):
        child_min, _, _, _ = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)
        self.child.allocate(max(child_min, min(width, self.width)), height, baseline, None)

    def do_dispose(self):
        if self.child is not None and self.child.get_parent() is self:
            self.child.unparent()
        self.child = None
        Gtk.Widget.do_dispose(self)


class SquareFit(Gtk.Widget):
    """Shows its child as a centred square as big as the space allows, up to `maximum`. Its own
    minimum is small, so in a short window it shrinks before the widgets around it have to."""

    __gtype_name__ = "SquareFit"

    def __init__(self, child: Gtk.Widget, maximum: int, minimum: int = 96):
        """Below `minimum` the child is hidden; this widget itself can shrink to nothing."""
        super().__init__(hexpand=True, vexpand=True)
        self.maximum, self.minimum = maximum, minimum
        self.child = child
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        minimum = 0
        natural = max(self.maximum, self.child.measure(orientation, -1)[0])
        if for_size > 0:  # the other side is known: a square no bigger than it
            natural = min(natural, for_size)
        return minimum, max(minimum, natural), -1, -1

    def do_size_allocate(self, width, height, baseline):
        side = min(width, height, self.maximum)
        # A child that wants more than the square (e.g. the equalizer) gets it where there's room.
        min_w, nat_w = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)[:2]
        w = max(side, min_w, min(nat_w, width))
        min_h, nat_h = self.child.measure(Gtk.Orientation.VERTICAL, w)[:2]
        h = max(side, min_h, min(nat_h, height))
        # Too small to be worth showing, or to fit what's showing (the window is very short):
        # leave the space empty rather than spill over the widgets around it.
        self.child.set_child_visible(side >= self.minimum and w <= width and h <= height)
        transform = Gsk.Transform().translate(Graphene.Point().init((width - w) / 2, (height - h) / 2))
        self.child.allocate(w, h, -1, transform)

    def do_dispose(self):
        if self.child is not None and self.child.get_parent() is self:
            self.child.unparent()
        self.child = None
        Gtk.Widget.do_dispose(self)


class PriorityColumn(Gtk.Widget):
    """A vertical column that, when short of height, fills its children up to their natural
    height in priority order (0 first); the others make do with their minimum. Height left over
    once all are natural goes to the `fill` child, or above everything (bottom-aligned)."""

    __gtype_name__ = "PriorityColumn"

    def __init__(self, spacing: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.spacing = spacing
        self.fill: Gtk.Widget | None = None
        self._children: list[tuple[Gtk.Widget, int]] = []

    def add(self, child: Gtk.Widget, priority: int, fill: bool = False):
        child.set_parent(self)
        self._children.append((child, priority))
        if fill:
            self.fill = child

    def _shown(self):
        return [(c, p) for c, p in self._children if c.should_layout()]

    def do_measure(self, orientation, for_size):
        shown = self._shown()
        if orientation == Gtk.Orientation.HORIZONTAL:
            sizes = [c.measure(orientation, -1)[:2] for c, _p in shown]
            return max((s[0] for s in sizes), default=0), max((s[1] for s in sizes), default=0), -1, -1
        sizes = [c.measure(orientation, for_size)[:2] for c, _p in shown]
        gaps = self.spacing * max(len(shown) - 1, 0)
        return sum(s[0] for s in sizes) + gaps, sum(s[1] for s in sizes) + gaps, -1, -1

    def do_size_allocate(self, width, height, baseline):
        shown = self._shown()
        sizes = [c.measure(Gtk.Orientation.VERTICAL, width)[:2] for c, _p in shown]
        heights = [minimum for minimum, _n in sizes]
        extra = height - sum(heights) - self.spacing * max(len(shown) - 1, 0)
        for i in sorted(range(len(shown)), key=lambda i: shown[i][1]):
            give = max(0, min(extra, sizes[i][1] - sizes[i][0]))
            heights[i] += give
            extra -= give
        y = 0
        if extra > 0:
            fill = next((i for i, (c, _p) in enumerate(shown) if c is self.fill), None)
            if fill is None:
                y = extra  # nothing grows: keep everything at the bottom
            else:
                heights[fill] += extra
        for (child, _p), h in zip(shown, heights):
            child.allocate(width, max(h, 0), -1, Gsk.Transform().translate(Graphene.Point().init(0, y)))
            y += h + self.spacing

    def do_dispose(self):
        for child, _p in self._children:
            if child.get_parent() is self:
                child.unparent()
        self._children = []
        Gtk.Widget.do_dispose(self)


def follow_compact(win, widget, apply):
    """Call apply(widget, level) now and whenever the window's compact level changes
    (0 = wide, 1 = narrow, 2 = phone-sized). Holds the widget weakly so popped pages can go away."""
    ref = weakref.ref(widget)

    def on_change(_win, level):
        target = ref()
        if target is not None:
            apply(target, level)

    win.connect("compact-changed", on_change)
    apply(widget, win.compact_level)


def ordering(a, b) -> Gtk.Ordering:
    if a < b:
        return Gtk.Ordering.SMALLER
    if a > b:
        return Gtk.Ordering.LARGER
    return Gtk.Ordering.EQUAL


def update_heart(button: Gtk.Button, favorite: bool):
    button.set_icon_name(HEART_ON if favorite else HEART_OFF)
    button.set_tooltip_text("Remove from Favorites" if favorite else "Add to Favorites")
    if favorite:
        button.add_css_class("favorited")
    else:
        button.remove_css_class("favorited")


class CoverArt(Gtk.Overlay):
    """Square album art with a music-note placeholder while loading or when missing."""

    def __init__(self, art, size: int, art_size: int = THUMB, large: bool = False):
        super().__init__()
        self.art = art
        self.art_size = art_size
        self._token = None
        self.set_size_request(size, size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("cover-large" if large else "cover")

        self.placeholder = Gtk.Image(icon_name="audio-x-generic-symbolic", pixel_size=max(16, size // 3))
        self.placeholder.add_css_class("cover-placeholder")
        self.placeholder.set_halign(Gtk.Align.CENTER)
        self.placeholder.set_valign(Gtk.Align.CENTER)
        self.set_child(self.placeholder)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        self.add_overlay(self.picture)

    def set_size(self, size: int):
        self.set_size_request(size, size)
        self.placeholder.set_pixel_size(max(16, size // 3))

    def set_track(self, item):
        self._token = item
        if item is None:
            self.picture.set_paintable(None)
            self.picture.set_visible(False)
            return
        self.art.request_track(item, self.art_size, lambda tex, tok=item: self._apply(tok, tex))

    def _apply(self, token, texture):
        if token is not self._token:
            return
        self.picture.set_paintable(texture)
        self.picture.set_visible(texture is not None)


class CurrentHeart(Gtk.Button):
    """Favorite toggle for whatever song is playing; hidden when nothing is."""

    def __init__(self, player):
        super().__init__(icon_name=HEART_OFF, tooltip_text="Add to Favorites", valign=Gtk.Align.CENTER,
                         action_name="app.favorite-current")
        self.add_css_class("flat")
        self.add_css_class("circular")
        self.add_css_class("heart-button")
        self._item = None
        self._handler = 0
        player.connect("track-changed", lambda _p, track: self._follow(track))
        self._follow(player.current)

    def _follow(self, track):
        if self._item is not None:
            self._item.disconnect(self._handler)
        self._item = track
        self.set_visible(track is not None)
        if track is not None:
            update_heart(self, track.favorite)
            self._handler = track.connect("notify::favorite", lambda t, _p: update_heart(self, t.favorite))


def song_actions(win, player) -> tuple[Gtk.Box, "CurrentHeart", "CurrentPlaylists"]:
    """The playing song's heart and playlists buttons, side by side on one line."""
    box = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
    box.add_css_class("song-actions")
    heart = CurrentHeart(player)
    playlists = CurrentPlaylists(win, player)
    box.append(heart)
    box.append(playlists)
    return box, heart, playlists


class CurrentPlaylists(Gtk.MenuButton):
    """Add the playing song to (or take it out of) any playlists. Tinted when the song is in at
    least one, with a badge counting how many; hidden when nothing is playing."""

    def __init__(self, win, player):
        super().__init__(valign=Gtk.Align.CENTER, has_frame=False)
        self.add_css_class("playlist-button")
        self.win = win
        self.player = player
        icon = Gtk.Image(icon_name=PLAYLIST_ICON)
        self.badge = Gtk.Label(halign=Gtk.Align.END, valign=Gtk.Align.START, visible=False, can_target=False)
        self.badge.add_css_class("playlist-count")
        overlay = Gtk.Overlay(child=icon)
        overlay.set_overflow(Gtk.Overflow.VISIBLE)  # the badge sits over the icon's corner
        overlay.add_overlay(self.badge)
        self.set_child(overlay)

        self.popover = AppMenu(width=270)
        self.popover.connect("show", lambda *_: self._fill())
        self.set_popover(self.popover)

        player.connect("track-changed", lambda *_: self._sync())
        win.library.connect("playlists-changed", lambda *_: self._sync())
        self._sync()

    def _sync(self):
        track = self.player.current
        self.set_visible(track is not None)
        if track is None:
            self.popover.popdown()
            return
        count = len(self.win.library.playlists_with(track))
        self.badge.set_text(str(count))
        self.badge.set_visible(count > 0)
        if count:
            self.add_css_class("in-playlist")
            self.set_tooltip_text(f"In {count} Playlist{'s' if count != 1 else ''}")
        else:
            self.remove_css_class("in-playlist")
            self.set_tooltip_text("Add to Playlist")

    def _fill(self):
        """Rebuilt on every open, so it always lists the current playlists."""
        track = self.player.current
        page = self.popover.page
        page.clear()
        if track is None:
            return
        page.add_header(song_header(self.win, [track]))
        fill_playlist_checks(page, self.win, [track])


def song_header(win, items) -> Gtk.Box:
    """The top of a song menu: the cover, title and artist (or a count for several songs)."""
    if len(items) == 1:
        cover = CoverArt(win.art, 40)
        cover.set_track(items[0])
        return menu_header(cover, items[0].title, items[0].artist)
    icon = Gtk.Image(icon_name="musicapp-queue-symbolic", pixel_size=20, width_request=40, height_request=40)
    icon.add_css_class("menu-stack-icon")
    return menu_header(icon, f"{len(items)} songs", "Selected")


def fill_playlist_checks(page, win, items):
    """A check for each playlist, then New Playlist…. For one song the checks show where it
    already is and toggle it in or out; for several, a tap adds them all."""
    library = win.library
    playlists = library.playlists()
    page.add_section("Add to Playlist")
    if playlists:
        inside = library.playlists_with(items[0]) if len(items) == 1 else set()
        rows = page.add_scrolling(300)
        for p in playlists:
            def toggle(on, pid=p["id"], count=[p["count"]]):
                if on:
                    count[0] += library.add_to_playlist(pid, items)
                    if len(items) > 1:
                        win.toast(f"Added {len(items)} songs to “{library.playlist_name(pid)}”")
                else:
                    library.remove_from_playlist(pid, items)
                    count[0] = max(0, count[0] - len(items))
                return count[0]
            rows.add_check(p["name"], p["id"] in inside, toggle, detail=str(p["count"]),
                           icon=PLAYLIST_ICON)
    else:
        page.add_note(PLAYLIST_ICON, "No playlists yet")
    page.add_section()
    page.add_item("list-add-symbolic", "New Playlist…", lambda: win.new_playlist_with(list(items)))
