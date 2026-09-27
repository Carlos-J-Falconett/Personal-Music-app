"""The app's menus: glass popovers with icon rows, sections, switches, checks and sliding
submenus, so the app menu, the song menu and the playlists popup all look alike."""

from gi.repository import GLib, Gtk


def accel_label(accel: str | None) -> str | None:
    """'<Control>h' → 'Ctrl+H'."""
    if not accel:
        return None
    ok, key, mods = Gtk.accelerator_parse(accel)
    return Gtk.accelerator_get_label(key, mods) if ok else accel


class MenuPage(Gtk.Box):
    """One page of a menu. Rows are added top to bottom."""

    def __init__(self, menu: "AppMenu"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.menu = menu
        self._has_rows = False

    def clear(self):
        """Remove every row."""
        while (child := self.get_first_child()) is not None:
            self.remove(child)
        self._has_rows = False

    # ---- building blocks ----

    def add_header(self, widget: Gtk.Widget):
        """A header at the top (e.g. the song's cover and title)."""
        widget.add_css_class("menu-header")
        self.append(widget)
        self._has_rows = True

    def add_section(self, title: str | None = None):
        """Start a group: a hairline from the previous one, and an optional small caps title."""
        if self._has_rows:
            separator = Gtk.Separator()
            separator.add_css_class("menu-separator")
            self.append(separator)
        if title:
            label = Gtk.Label(label=title.upper(), xalign=0)
            label.add_css_class("menu-section")
            self.append(label)

    def _row(self, icon, label, accel=None, trailing=None, detail=None) -> Gtk.Button:
        """A menu row: icon, label, then optional detail text, shortcut hint and trailing widget."""
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("menu-row")
        box = Gtk.Box(spacing=12)
        if icon:
            image = Gtk.Image(icon_name=icon)
            image.add_css_class("menu-icon")
            box.append(image)
        text = Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=3, max_width_chars=30)
        box.append(text)
        if detail is not None:
            extra = Gtk.Label(label=detail)
            extra.add_css_class("menu-detail")
            box.append(extra)
        shortcut = accel_label(accel)
        if shortcut:
            hint = Gtk.Label(label=shortcut)
            hint.add_css_class("menu-accel")
            box.append(hint)
        if trailing is not None:
            box.append(trailing)
        button.set_child(box)
        self.append(button)
        self._has_rows = True
        return button

    def add_item(self, icon, label, callback, accel=None, destructive=False, sensitive=True,
                 detail=None) -> Gtk.Button:
        """A row that closes the menu and then runs callback()."""
        row = self._row(icon, label, accel, detail=detail)
        row.set_sensitive(sensitive)
        if destructive:
            row.add_css_class("destructive")
        row.connect("clicked", lambda *_: self.menu.run(callback))
        return row

    def add_switch(self, icon, label, active, on_toggle, accel=None) -> Gtk.Switch:
        """A row with a switch; the menu stays open. on_toggle(active)."""
        switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER, can_focus=False, can_target=False)
        row = self._row(icon, label, accel, trailing=switch)
        row.connect("clicked", lambda *_: switch.set_active(not switch.get_active()))
        switch.connect("notify::active", lambda s, _p: on_toggle(s.get_active()))
        return switch

    def add_check(self, label, active, on_toggle, detail=None, icon=None):
        """A row with a round check; the menu stays open. on_toggle(checked) may return the
        row's new detail text (e.g. an updated song count)."""
        mark = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, hexpand=False)  # don't share the row's space
        mark.add_css_class("menu-check")
        mark.append(Gtk.Image(icon_name="object-select-symbolic", hexpand=True))
        row = self._row(icon, label, trailing=mark, detail=detail)
        row.add_css_class("menu-check-row")
        state = {"on": active}

        def show():
            """Draw the check mark for the current state."""
            (mark.add_css_class if state["on"] else mark.remove_css_class)("checked")
            row.update_state([Gtk.AccessibleState.CHECKED], [state["on"]])

        def clicked(*_):
            """Toggle, and show the new detail text if on_toggle returned one."""
            state["on"] = not state["on"]
            show()
            new_detail = on_toggle(state["on"])
            if new_detail is not None and detail is not None:
                row.get_child().get_last_child().get_prev_sibling().set_label(str(new_detail))

        row.connect("clicked", clicked)
        show()
        return row

    def add_submenu(self, icon, label, build, detail=None) -> Gtk.Button:
        """A row with ›; it slides to a page filled by build(page), with a way back."""
        arrow = Gtk.Image(icon_name="go-next-symbolic")
        arrow.add_css_class("menu-arrow")
        row = self._row(icon, label, trailing=arrow, detail=detail)
        row.connect("clicked", lambda *_: self.menu.open_page(label, build))
        return row

    def add_note(self, icon, text):
        """A quiet line, e.g. for an empty list."""
        box = Gtk.Box(spacing=10)
        box.add_css_class("menu-note")
        if icon:
            box.append(Gtk.Image(icon_name=icon))
        box.append(Gtk.Label(label=text, xalign=0, wrap=True))
        self.append(box)
        self._has_rows = True

    def add_scrolling(self, max_height=300) -> "MenuPage":
        """A sub-list that scrolls once it's taller than max_height; returns it to fill."""
        inner = MenuPage(self.menu)
        scroller = Gtk.ScrolledWindow(child=inner, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      propagate_natural_height=True, max_content_height=max_height)
        self.append(scroller)
        self._has_rows = True
        return inner


class AppMenu(Gtk.Popover):
    """A glass popover menu. Fill `menu.page`; submenus slide in on their own pages."""

    def __init__(self, width: int = 260):
        super().__init__(has_arrow=False)
        self.add_css_class("app-menu")
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=180, vhomogeneous=False, interpolate_size=True,
                               width_request=width)
        self.page = MenuPage(self)
        self.stack.add_named(self.page, "main")
        self.set_child(self.stack)
        self._pages: list[Gtk.Widget] = []
        self.connect("closed", lambda *_: self._back_to_main(animate=False))

    def run(self, callback):
        """Close, then act (so a dialog it opens isn't fighting the closing popover)."""
        self.popdown()
        GLib.idle_add(lambda: callback() and False)

    def open_page(self, title, build):
        """Slide to a new page built by build(page), with a back row at the top."""
        page = MenuPage(self)
        back = Gtk.Button()
        back.add_css_class("flat")
        back.add_css_class("menu-row")
        back.add_css_class("menu-back")
        box = Gtk.Box(spacing=12)
        box.append(Gtk.Image(icon_name="go-previous-symbolic"))
        heading = Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=3)
        heading.add_css_class("menu-back-title")
        box.append(heading)
        back.set_child(box)
        back.connect("clicked", lambda *_: self._back_to_main(animate=True))
        page.append(back)
        page._has_rows = True
        build(page)
        self.stack.add_child(page)
        self._pages.append(page)
        self.stack.set_visible_child(page)
        # The popup sizes itself to every page it holds: set the main one aside meanwhile,
        # so it fits the submenu (once it has slid out of view).
        GLib.timeout_add(self.stack.get_transition_duration() + 20,
                         lambda: self.page.set_visible(self.stack.get_visible_child() is self.page) and False)

    def _back_to_main(self, animate: bool):
        """Return to the main page and drop the submenu pages."""
        if not self._pages:
            return
        if not animate:
            self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.page.set_visible(True)
        self.stack.set_visible_child(self.page)
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        pages, self._pages = self._pages, []
        # Remove once the slide back is over.
        GLib.timeout_add(250 if animate else 0, lambda: [self.stack.remove(p) for p in pages
                                                        if p.get_parent() is self.stack] and False)


def menu_header(icon_widget: Gtk.Widget, title: str, subtitle: str | None = None) -> Gtk.Box:
    """Picture + title + dim subtitle, for the top of a menu."""
    box = Gtk.Box(spacing=12)
    icon_widget.set_valign(Gtk.Align.CENTER)
    box.append(icon_widget)
    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
    heading = Gtk.Label(label=title, xalign=0, ellipsize=3, max_width_chars=26)
    heading.add_css_class("menu-title")
    labels.append(heading)
    if subtitle:
        sub = Gtk.Label(label=subtitle, xalign=0, ellipsize=3, max_width_chars=30)
        sub.add_css_class("menu-subtitle")
        labels.append(sub)
    box.append(labels)
    return box
