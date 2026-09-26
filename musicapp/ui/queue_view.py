"""The play queue: the current song on top, then Up Next (reorder by dragging, remove, clear).
Used by the Now Playing panel and by the focus view."""

from gi.repository import Adw, Gdk, Gio, GObject, Gtk

from ..art import THUMB
from ..library import format_total
from .widgets import CoverArt, icon_button

QUEUE_DND_PREFIX = "musicapp-queue:"


class UpNextEntry(GObject.Object):
    __gtype_name__ = "UpNextEntry"

    def __init__(self, track, queue_index: int, wrapped: bool):
        super().__init__()
        self.track = track
        self.queue_index = queue_index
        self.wrapped = wrapped  # comes around again via Repeat Queue


class QueueView(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.win = win
        self.player = win.player
        self.append(self._build_queue_view())
        self.player.connect("track-changed", lambda *_: self._sync())
        self.player.connect("queue-changed", lambda *_: self._sync())
        self.player.connect("modes-changed", lambda *_: self._sync())  # Repeat Queue changes Up Next
        self._sync()

    def _build_queue_view(self):
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        stack.add_named(Adw.StatusPage(icon_name="musicapp-queue-symbolic", title="Queue Is Empty",
                                       description="Double-click a song to start listening"), "empty")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        playing_label = Gtk.Label(label="Now Playing", xalign=0, margin_start=16, margin_top=4)
        playing_label.add_css_class("title-4")
        playing_label.add_css_class("queue-heading")
        box.append(playing_label)
        card = Gtk.Box(spacing=10, margin_start=12, margin_end=12)
        card.add_css_class("now-card")
        self.card_cover = CoverArt(self.win.art, 48, art_size=THUMB)
        card.append(self.card_cover)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.card_title = Gtk.Label(xalign=0, ellipsize=3)
        self.card_title.add_css_class("now-card-title")
        self.card_artist = Gtk.Label(xalign=0, ellipsize=3)
        self.card_artist.add_css_class("dim")
        self.card_artist.add_css_class("caption")
        self.card_artist.add_css_class("now-card-artist")
        labels.append(self.card_title)
        labels.append(self.card_artist)
        card.append(labels)
        playing_icon = Gtk.Image(icon_name="audio-volume-high-symbolic")
        playing_icon.add_css_class("accent")
        card.append(playing_icon)
        box.append(card)

        up_header = Gtk.Box(margin_top=14, margin_start=16, margin_end=8)
        up_titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        up_label = Gtk.Label(label="Up Next", xalign=0)
        up_label.add_css_class("title-4")
        up_label.add_css_class("queue-heading")
        self.up_info = Gtk.Label(xalign=0)
        self.up_info.add_css_class("dim")
        self.up_info.add_css_class("caption")
        up_titles.append(up_label)
        up_titles.append(self.up_info)
        up_header.append(up_titles)
        self.clear_button = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER, tooltip_text="Remove upcoming songs")
        self.clear_button.add_css_class("flat")
        self.clear_button.connect("clicked", lambda *_: self.player.clear_upcoming())
        up_header.append(self.clear_button)
        box.append(up_header)

        self.store = Gio.ListStore(item_type=UpNextEntry)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        self.list = Gtk.ListView(model=Gtk.NoSelection(model=self.store), factory=factory,
                                 single_click_activate=True)
        self.list.add_css_class("navigation-sidebar")
        self.list.add_css_class("up-next")
        self.list.connect("activate", lambda lv, pos: self.player.jump_to(self.store.get_item(pos).queue_index))
        self.up_stack = Gtk.Stack(vexpand=True)
        self.up_stack.add_named(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                                   child=self.list, margin_start=4, margin_end=4), "list")
        up_empty = Gtk.Label(label="Nothing queued after this song", valign=Gtk.Align.START, margin_top=12)
        up_empty.add_css_class("dim")
        self.up_stack.add_named(up_empty, "empty")
        box.append(self.up_stack)

        stack.add_named(box, "content")
        self.queue_stack = stack
        return stack

    def _setup_row(self, _f, li):
        row = Gtk.Box(spacing=10, margin_top=2, margin_bottom=2)
        row.add_css_class("queue-row")
        handle = Gtk.Image(icon_name="list-drag-handle-symbolic")
        handle.add_css_class("dim")
        cover = CoverArt(self.win.art, 40, art_size=THUMB)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        title = Gtk.Label(xalign=0, ellipsize=3)
        title.add_css_class("queue-title")
        artist = Gtk.Label(xalign=0, ellipsize=3)
        artist.add_css_class("dim")
        artist.add_css_class("caption")
        artist.add_css_class("queue-artist")
        labels.append(title)
        labels.append(artist)
        duration = Gtk.Label()
        duration.add_css_class("number-cell")
        duration.add_css_class("queue-duration")
        remove = icon_button("window-close-symbolic", "Remove from Queue", circular=True)
        remove.connect("clicked", lambda b: self.player.remove_from_queue(row._entry.queue_index)
                       if row._entry else None)
        for w in (handle, cover, labels, duration, remove):
            row.append(w)
        row._widgets = (handle, cover, title, artist, duration)
        row._entry = None

        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        drag.connect("prepare", lambda src, x, y: Gdk.ContentProvider.new_for_value(
            f"{QUEUE_DND_PREFIX}{row._entry.queue_index}") if row._entry and not row._entry.wrapped else None)
        drag.connect("drag-begin", lambda src, d: src.set_icon(Gtk.WidgetPaintable.new(row), 20, 20))
        row.add_controller(drag)
        drop = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        drop.connect("enter", lambda *_: (row.add_css_class("drop-target"), Gdk.DragAction.MOVE)[1])
        drop.connect("leave", lambda *_: row.remove_css_class("drop-target"))
        drop.connect("drop", self._on_drop, row)
        row.add_controller(drop)
        li.set_child(row)

    def _bind_row(self, _f, li):
        row, entry = li.get_child(), li.get_item()
        handle, cover, title, artist, duration = row._widgets
        row._entry = entry
        t = entry.track
        cover.set_track(t)
        title.set_text(t.title)
        artist.set_text(t.artist)
        duration.set_text(t.duration_text)
        # Wrapped-around songs (Repeat Queue) can't be dragged: their position is the start of the queue.
        handle.set_opacity(0 if entry.wrapped else 1)
        row.set_tooltip_text("Plays again when the queue repeats" if entry.wrapped else None)

    def _on_drop(self, _target, value, _x, _y, row):
        row.remove_css_class("drop-target")
        entry = row._entry
        if not isinstance(value, str) or not value.startswith(QUEUE_DND_PREFIX) or entry is None or entry.wrapped:
            return False
        self.player.move_in_queue(int(value[len(QUEUE_DND_PREFIX):]), entry.queue_index)
        return True

    def _sync(self):
        track = self.player.current
        self.card_cover.set_track(track)
        if track is None:
            self.queue_stack.set_visible_child_name("empty")
            self.store.remove_all()
            return
        self.card_title.set_text(track.title)
        self.card_artist.set_text(track.artist)
        self.queue_stack.set_visible_child_name("content")

        upcoming = self.player.upcoming()
        self.store.splice(0, self.store.get_n_items(), [UpNextEntry(t, i, w) for i, t, w in upcoming])
        self.up_stack.set_visible_child_name("list" if upcoming else "empty")
        self.clear_button.set_sensitive(bool(upcoming))
        if upcoming:
            n = len(upcoming)
            self.up_info.set_text(f"{n:,} song{'s' if n != 1 else ''} · "
                                  f"{format_total(sum(t.duration for _, t, _ in upcoming))}")
        else:
            self.up_info.set_text("")
