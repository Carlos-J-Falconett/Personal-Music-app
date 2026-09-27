"""The pages of the Slideshows tab (Black Hole mode, see slideshows_view.py):
Slideshows (settings, Global and the groups) → a group (its individual slideshows) → a slideshow
(its images and videos). Each page follows changes live, including ones made with the keyboard
shortcuts."""

from gi.repository import Adw, Gio, GLib, Gtk

from ..art import SLIDE_SECONDS_MAX, SLIDE_SECONDS_MIN, THUMB, is_video
from .artists_view import back_button
from .dialogs import ask_name, confirm_delete
from .silent_video import SilentVideo
from .widgets import follow_compact, icon_button


def _count(n: int, word: str) -> str:
    """"1 word" / "N words"."""
    return f"{n} {word}{'s' if n != 1 else ''}"


def _items(n: int) -> str:
    """"1 item" / "N items"."""
    return _count(n, "item")


class _Page(Adw.NavigationPage):
    """A page of the Slideshows tab: a glass card with a page header (title, subtitle, action
    buttons; pushed pages also get a back button above it) and scrolling content in `self.box`.
    It rebuilds itself whenever the slideshows change, until it's detached.

    `host` is the SlideshowsView holding the pages: it provides .nav (the navigation view), .win
    (the main window), .art and .toast()."""

    def __init__(self, host, title: str, tag: str, back: str | None = None, on_back=None):
        super().__init__(title=title, tag=tag)
        self.host = host
        self.art = host.art
        self.slideshows = host.art.slideshows
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if back is not None:
            card.append(back_button(back, on_back))
        header = Gtk.Box(spacing=12)
        header.add_css_class("page-header")
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        self.title_label = Gtk.Label(label=title, xalign=0, ellipsize=3)
        self.title_label.add_css_class("page-title")
        self.subtitle = Gtk.Label(xalign=0, wrap=True)
        self.subtitle.add_css_class("page-subtitle")
        titles.append(self.title_label)
        titles.append(self.subtitle)
        header.append(titles)
        self.header_actions = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        header.append(self.header_actions)
        card.append(header)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                           margin_start=24, margin_end=24, margin_bottom=24)
        card.append(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                       child=Adw.Clamp(child=self.box, maximum_size=900)))
        follow_compact(host.win, self, _Page._apply_compact)
        card.add_css_class("content-glass")
        card.set_overflow(Gtk.Overflow.HIDDEN)  # clip to the rounded card
        self.set_child(card)
        self._syncing = False  # true while rows are rebuilt, so radio buttons don't act
        self._listener = self._on_changed
        self.art.slideshow_listeners.append(self._listener)

    @staticmethod
    def _apply_compact(page, level):
        """Phone-sized windows: slimmer side margins, so the settings rows still fit."""
        margin = 12 if level == 2 else 24
        page.box.set_margin_start(margin)
        page.box.set_margin_end(margin)

    def _set_heading(self, title: str):
        """Change the page's title (after a rename)."""
        self.set_title(title)
        self.title_label.set_text(title)

    def detach(self):
        """Stop following changes (the page was popped, or left the tab)."""
        if self._listener in self.art.slideshow_listeners:
            self.art.slideshow_listeners.remove(self._listener)

    def _on_changed(self):
        """The slideshows changed: rebuild (or stop listening, if the page has left the tab)."""
        if self.host.nav.find_page(self.get_tag()) is not self:  # no longer in the tab
            self.detach()
            return
        self._syncing = True
        try:
            self.refresh()
        finally:
            self._syncing = False

    def refresh(self):
        """Rebuild the page from the current slideshows."""
        raise NotImplementedError

    def _header_button(self, icon, tooltip, callback):
        """A round icon button at the right of the page header."""
        button = icon_button(icon, tooltip, circular=True)
        button.connect("clicked", lambda *_: callback())
        self.header_actions.append(button)
        return button

    def _radio_row(self, title, subtitle, showing, on_pick, group_with, on_open=None):
        """A row with a round 'showing' button; with on_open, clicking the row opens it."""
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        radio = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=showing,
                                tooltip_text="Showing" if showing else "Show This")
        if group_with is not None:
            radio.set_group(group_with)
        radio.connect("toggled", lambda b: b.get_active() and not self._syncing and self._pick(on_pick))
        row.add_prefix(radio)
        if on_open is not None:
            row.set_activatable(True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda *_: on_open())
        else:
            row.set_activatable_widget(radio)
        return row, radio

    def _pick(self, select):
        """Show something else: the same as the shortcuts (turns the slideshow on, toasts)."""
        GLib.idle_add(lambda: self.host.win.app._switch_slideshow(lambda: select() or True) and False)

    def _wide_button(self, label, icon, callback, suggested=True):
        """A pill button with an icon and label."""
        button = Gtk.Button(child=Adw.ButtonContent(icon_name=icon, label=label), halign=Gtk.Align.CENTER)
        button.add_css_class("pill")
        if suggested:
            button.add_css_class("suggested-action")
        button.connect("clicked", lambda *_: callback())
        return button

    def _boxed_list(self):
        """A list with the boxed (card) look."""
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        return rows


class SlideshowsPage(_Page):
    """Settings, then Global and the groups: pick what shows, open a group to manage it."""

    def __init__(self, host):
        super().__init__(host, "Slideshows", "slideshow")
        self.caption = self.subtitle  # the header's subtitle explains the slideshows

        self.switch = Adw.SwitchRow(title="Play Slideshow", subtitle="Off shows artist images again")
        self.switch.set_active(self.art.slideshow_enabled)
        # Goes through app.slideshow, so the menu's Slideshow item and the saved setting follow.
        self.switch.connect("notify::active", lambda row, _p: self.art.slideshow_enabled != row.get_active()
                            and host.win.app.change_action_state("slideshow", GLib.Variant("b", row.get_active())))
        self.seconds = Adw.SpinRow(title="Seconds per Image", subtitle="Videos always play to the end",
                                   adjustment=Gtk.Adjustment(lower=SLIDE_SECONDS_MIN, upper=SLIDE_SECONDS_MAX,
                                                             step_increment=1, page_increment=10,
                                                             value=self.art.slideshow_seconds))
        self.seconds.connect("notify::value", lambda row, _p: self.art.set_slideshow_seconds(int(row.get_value())))
        settings = self._boxed_list()
        settings.append(self.switch)
        settings.append(self.seconds)
        self.box.append(settings)

        heading = Gtk.Label(label="Showing", xalign=0, margin_top=6)
        heading.add_css_class("heading")
        self.box.append(heading)
        self.rows = self._boxed_list()
        self.box.append(self.rows)
        self.box.append(self._wide_button("New Group…", "list-add-symbolic", self._new_group))
        self.refresh()

    def refresh(self):
        """Update the settings and rebuild the Global / group rows."""
        if self.switch.get_active() != self.art.slideshow_enabled:
            self.switch.set_active(self.art.slideshow_enabled)
        seconds = self.art.slideshow_seconds
        if int(self.seconds.get_value()) != seconds:
            self.seconds.set_value(seconds)
        self.caption.set_label("Shown instead of artist images while Black Hole mode is on: each image for "
                               f"{_count(seconds, 'second')}, each video to its end. Group slideshows go "
                               "into groups; a group shows all of its slideshows, and Global shows everything.")
        self.rows.remove_all()
        ss = self.slideshows
        global_row, first = self._radio_row("Global", f"Everything · {_items(len(ss.all_media()))}",
                                            ss.is_showing(None), ss.select_global, None)
        self.rows.append(global_row)
        for group in ss.groups:
            subtitle = f"{_count(len(group.slideshows), 'slideshow')} · {_items(len(ss.group_media(group)))}"
            row, _radio = self._radio_row(group.name, subtitle, ss.is_showing(group),
                                          lambda g=group: ss.select_group(g), first,
                                          on_open=lambda g=group: self.host.nav.push(GroupPage(self.host, g)))
            self.rows.append(row)

    def _new_group(self):
        """Ask for a name, create the group and open it."""
        ss = self.slideshows
        ask_name(self.host, heading="New Group", confirm="Create", placeholder="Group name",
                 initial=ss.next_name("Group", [g.name for g in ss.groups]),
                 on_done=lambda name: self.host.nav.push(GroupPage(self.host, ss.create_group(name))))


class GroupPage(_Page):
    """A group: show the whole group or one of its slideshows, add, rename, delete."""

    def __init__(self, host, group):
        super().__init__(host, group.name, f"group:{group.id}", back="Slideshows",
                         on_back=lambda: host.nav.pop_to_tag("slideshow"))
        self.group = group
        self._header_button("user-trash-symbolic", "Delete Group", self._delete)
        self._header_button("document-edit-symbolic", "Rename Group", self._rename)
        self.rows = self._boxed_list()
        self.box.append(self.rows)
        self.box.append(self._wide_button("New Slideshow…", "list-add-symbolic", self._new_slideshow))
        self.refresh()

    def refresh(self):
        """Rebuild the Whole Group / slideshow rows."""
        ss = self.slideshows
        if ss.find_group(self.group.id) is None:  # deleted
            return
        self._set_heading(self.group.name)
        showing = " · showing now" if ss.is_showing(self.group) else ""
        self.subtitle.set_text(f"Group · {_count(len(self.group.slideshows), 'slideshow')}{showing}")
        self.rows.remove_all()
        whole, first = self._radio_row("Whole Group", f"All of its slideshows · {_items(len(ss.group_media(self.group)))}",
                                       ss.is_showing(self.group), lambda: ss.select_group(self.group), None)
        self.rows.append(whole)
        for slideshow in self.group.slideshows:
            row, _radio = self._radio_row(slideshow.name, _items(len(ss.media(slideshow))),
                                          ss.is_showing(slideshow), lambda s=slideshow: ss.select_slideshow(s), first,
                                          on_open=lambda s=slideshow: self.host.nav.push(
                                              SlideshowPage(self.host, s)))
            self.rows.append(row)

    def _new_slideshow(self):
        """Ask for a name, create the slideshow and open it."""
        ss = self.slideshows
        ask_name(self.host, heading="New Slideshow", confirm="Create", placeholder="Slideshow name",
                 body=f"In “{self.group.name}”",
                 initial=ss.next_name("Slideshow", [s.name for s in self.group.slideshows]),
                 on_done=lambda name: self.host.nav.push(
                     SlideshowPage(self.host, ss.create_slideshow(self.group, name))))

    def _rename(self):
        """Ask for the group's new name."""
        ask_name(self.host, heading="Rename Group", confirm="Rename", placeholder="Group name",
                 initial=self.group.name, on_done=lambda name: self.slideshows.rename_group(self.group, name))

    def _delete(self):
        """Confirm, then delete the group with its slideshows."""
        ss, group = self.slideshows, self.group

        def delete():
            """Leave the group's pages first, then delete."""
            self.host.nav.pop_to_tag("slideshow")
            ss.delete_group(group)
            self.host.toast(f"Deleted “{group.name}”")

        confirm_delete(self.host, f"Delete “{group.name}”?",
                       f"Its {_count(len(group.slideshows), 'slideshow')} and their "
                       f"{_items(len(ss.group_media(group)))} will be deleted. "
                       "The original files you added stay where they are.", delete)


class SlideshowPage(_Page):
    """An individual slideshow: its images and videos, show it, rename, delete."""

    def __init__(self, host, slideshow):
        super().__init__(host, slideshow.name, f"slideshow:{slideshow.id}", back=slideshow.group.name,
                         on_back=lambda: host.nav.pop())
        self.slideshow = slideshow
        self._header_button("user-trash-symbolic", "Delete Slideshow", self._delete)
        self._header_button("document-edit-symbolic", "Rename Slideshow", self._rename)

        self.show_button = self._wide_button("Show This Slideshow", "media-playback-start-symbolic",
                                             lambda: self._pick(lambda: self.slideshows.select_slideshow(slideshow)))
        self.box.append(self.show_button)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                min_children_per_line=2, max_children_per_line=6,
                                column_spacing=10, row_spacing=10, valign=Gtk.Align.START)
        self.empty = Adw.StatusPage(icon_name="image-x-generic-symbolic", title="No Images or Videos",
                                    description="Add a few to fill this slideshow", vexpand=True)
        self.empty.add_css_class("compact")
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.flow, "images")
        self.box.append(self.stack)

        # Side by side, or one above the other when the page is too narrow for both.
        buttons = Adw.WrapBox(child_spacing=12, line_spacing=10, halign=Gtk.Align.CENTER, margin_top=6)
        buttons.append(self._wide_button("Add Images or Videos…", "list-add-symbolic", self._choose))
        self.remove_all = Gtk.Button(label="Remove All")
        self.remove_all.add_css_class("pill")
        self.remove_all.connect("clicked", lambda *_: self._confirm_clear())
        buttons.append(self.remove_all)
        self.box.append(buttons)

        self._videos: list[SilentVideo] = []  # the video thumbnails' players
        self._shown: list | None = None  # the files the grid shows, so it's only rebuilt when they change
        self.refresh()

    def detach(self):
        """Stop listening and stop the video thumbnails."""
        super().detach()
        self._stop_videos()

    def _stop_videos(self):
        """Stop the video thumbnails' players."""
        for media in self._videos:
            media.stop()
        self._videos = []

    def refresh(self):
        """Update the header and button; rebuild the grid only if the files changed."""
        ss, slideshow = self.slideshows, self.slideshow
        if ss.find_slideshow(slideshow.id) is None:  # deleted
            return
        self._set_heading(slideshow.name)
        showing = ss.is_showing(slideshow)
        self.subtitle.set_text(f"In “{slideshow.group.name}”" + (" · showing now" if showing else ""))
        self.show_button.set_sensitive(not showing)
        media = ss.media(slideshow)
        if media != self._shown:  # rebuilding restarts the video thumbnails: only when needed
            self._shown = media
            self.flow.remove_all()
            self._stop_videos()
            for path in media:
                self.flow.append(self._thumb(path))
        self.stack.set_visible_child_name("images" if media else "empty")
        self.remove_all.set_sensitive(bool(media))

    def _thumb(self, path):
        """A square thumbnail with a remove button; videos show their first frame and a film badge."""
        # The picture is an overlay so its (wide) natural size doesn't stretch the square thumbnail.
        picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        overlay = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        overlay.add_css_class("slide-thumb")
        overlay.set_child(Gtk.Box(width_request=110, height_request=110))
        overlay.add_overlay(picture)
        remove = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Remove from Slideshow",
                            halign=Gtk.Align.END, valign=Gtk.Align.START, margin_top=4, margin_end=4)
        remove.add_css_class("circular")
        remove.add_css_class("slide-remove")
        remove.connect("clicked", lambda *_: self.slideshows.remove_media(path))
        overlay.add_overlay(remove)
        if is_video(path):
            # The video's first frame (a paused, silent video shows it once it's ready), plus a film badge.
            media = SilentVideo(path)  # no sound: see silent_video.py
            picture.set_paintable(media.paintable)
            self._videos.append(media)  # stopped on the next rebuild, or when the page closes
            badge = Gtk.Image(icon_name="video-x-generic-symbolic", halign=Gtk.Align.START,
                              valign=Gtk.Align.END, margin_start=6, margin_bottom=6)
            badge.add_css_class("slide-video-badge")
            overlay.add_overlay(badge)
            overlay.set_tooltip_text("Video")
        else:
            self.art.request_slide(path, THUMB, lambda texture: picture.set_paintable(texture))
        return overlay

    def _choose(self):
        """Open a file chooser for images and videos to add."""
        media = Gtk.FileFilter(name="Images and Videos")
        for mime in ("image/jpeg", "image/png", "image/webp", "video/*"):
            media.add_mime_type(mime)
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(media)
        chooser = Gtk.FileDialog(title=f"Add to “{self.slideshow.name}”", filters=filters, default_filter=media)
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if pictures:
            chooser.set_initial_folder(Gio.File.new_for_path(pictures))
        chooser.open_multiple(self.host.win, None, self._on_chosen)

    def _on_chosen(self, chooser, result):
        """Copy the chosen files into the slideshow (in the background) and say how it went."""
        try:
            files = chooser.open_multiple_finish(result)
        except GLib.Error:
            return  # cancelled
        paths = [f.get_path() for f in files if f.get_path()]
        if not paths:
            return

        def done(added, skipped):
            """Report how many were added."""
            text = f"Added {added} to “{self.slideshow.name}”"
            if skipped:
                text += f" ({skipped} couldn't be read)"
            self.host.toast(text)

        if any(is_video(p) for p in paths):
            self.host.toast("Adding…")  # videos take a moment to copy
        self.slideshows.add_media(self.slideshow, paths, done)

    def _confirm_clear(self):
        """Confirm, then remove every image and video from the slideshow."""
        alert = Adw.AlertDialog(heading="Remove All Images and Videos?",
                                body=f"“{self.slideshow.name}” will be empty. The original files stay where they are.")
        alert.add_response("cancel", "Cancel")
        alert.add_response("remove", "Remove All")
        alert.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.connect("response", lambda _a, r: self.slideshows.clear(self.slideshow) if r == "remove" else None)
        alert.present(self.host)

    def _rename(self):
        """Ask for the slideshow's new name."""
        ask_name(self.host, heading="Rename Slideshow", confirm="Rename", placeholder="Slideshow name",
                 initial=self.slideshow.name,
                 on_done=lambda name: self.slideshows.rename_slideshow(self.slideshow, name))

    def _delete(self):
        """Confirm, then delete the slideshow."""
        ss, slideshow = self.slideshows, self.slideshow

        def delete():
            """Leave the page first, then delete."""
            self.host.nav.pop()
            ss.delete_slideshow(slideshow)
            self.host.toast(f"Deleted “{slideshow.name}”")

        confirm_delete(self.host, f"Delete “{slideshow.name}”?",
                       f"Its {_items(len(ss.media(slideshow)))} will be deleted from the slideshow. "
                       "The original files you added stay where they are.", delete)
