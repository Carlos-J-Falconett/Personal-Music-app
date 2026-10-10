"""Themes dialog: pick the app's colour theme, or create and edit themes by typing hex codes.
While editing, valid colours are previewed live on the whole app; leaving without saving reverts.
Besides the basic colours, every element has its own colour, grouped by area; left empty ("Auto")
it follows the basic colours."""

import math

from gi.repository import Adw, Gdk, GLib, Gtk

from ..themes import (COLORS, DEFAULT_NAME, GROUPS, REQUIRED, Theme, default_theme, effective, normalize_hex,
                      to_hex, to_rgb)
from .widgets import weak_self


class Swatch(Gtk.DrawingArea):
    """A small colour dot with a faint outline, so dark colours stay visible."""

    def __init__(self, color: str | None = None, size: int = 14):
        super().__init__(content_width=size, content_height=size, valign=Gtk.Align.CENTER)
        self._rgb = to_rgb(color) if color else None
        self._auto = False
        self.set_draw_func(Swatch._draw)  # static: a bound method here would keep the swatch alive

    def set_color(self, color: str | None, auto: bool = False):
        """auto: the colour is only followed, not set; drawn smaller inside a dashed ring."""
        rgb = to_rgb(color) if color else None
        if (rgb, auto) != (self._rgb, self._auto):
            self._rgb, self._auto = rgb, auto
            self.queue_draw()

    @staticmethod
    def _draw(self, cr, width, height):
        """Draw the dot (and, for Auto colours, a smaller dot in a dashed ring). `self` is the swatch
        being drawn (GTK passes the area first)."""
        r = min(width, height) / 2 - 1
        cr.arc(width / 2, height / 2, r - (3 if self._auto else 0), 0, 2 * math.pi)
        if self._rgb is None:
            cr.set_source_rgba(1, 1, 1, 0.08)  # invalid / empty
        else:
            cr.set_source_rgb(*self._rgb)
        cr.fill()
        cr.arc(width / 2, height / 2, r, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 0.45 if self._auto else 0.25)
        cr.set_line_width(1)
        if self._auto:
            cr.set_dash([2, 2])
        cr.stroke()


class ThemeDialog(Adw.Dialog):
    """The Themes dialog: pick the active theme, and create, edit, duplicate or delete themes."""

    def __init__(self, win):
        super().__init__(title="Themes", content_width=460, content_height=640)
        self.win = win
        me = weak_self(self)  # for handlers of widgets this owns (see widgets.weak_self)
        self.themes = win.app.themes

        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.toasts.set_child(self.nav)
        self.set_child(self.toasts)

        # ---- list page ----
        new_button = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New Theme")
        new_button.connect("clicked", lambda *_: me.edit(None, start_from=me.themes.get(me.themes.active)))
        header = Adw.HeaderBar()
        header.pack_start(new_button)
        self.group = Adw.PreferencesGroup(title="Color Theme",
                                          description="Pick a theme, or create your own with hex colors")
        page = Adw.PreferencesPage()
        page.add(self.group)
        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(header)
        self.list_page = Adw.NavigationPage(title="Themes", tag="list", child=toolbar)
        self.nav.add(self.list_page)
        self._rows: list[Gtk.Widget] = []
        self._reload()

        self._listener = self._reload
        self.themes.listeners.append(self._listener)
        self.nav.connect("popped", lambda _nav, page: page.discard() if isinstance(page, ThemeEditor) else None)
        self.connect("closed", lambda *_: self._on_closed())

    def _on_closed(self):
        """Stop listening, and drop any unsaved preview."""
        if self._listener in self.themes.listeners:
            self.themes.listeners.remove(self._listener)
        editor = self.nav.find_page("editor")
        if editor is not None:
            editor.discard()

    def toast(self, text):
        """Show a short message inside the dialog."""
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    # ---- theme list ----

    def _reload(self):
        """Rebuild the theme list (each with its colour swatches and buttons)."""
        me = weak_self(self)  # for handlers of widgets this owns (see widgets.weak_self)
        for row in self._rows:
            self.group.remove(row)
        self._rows = []
        first_check = None
        for theme in self.themes.themes():
            row = Adw.ActionRow(title=GLib.markup_escape_text(theme.name))
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=theme.name == self.themes.active)
            if first_check is None:
                first_check = check
            else:
                check.set_group(first_check)
            check.connect("toggled", lambda b, name=theme.name: me._select(name) if b.get_active() else None)
            row.add_prefix(check)
            row.set_activatable_widget(check)

            strip = Gtk.Box(spacing=3, valign=Gtk.Align.CENTER, margin_end=6)
            for key in REQUIRED:
                strip.append(Swatch(theme.colors[key], 12))
            row.add_suffix(strip)
            if theme.is_default:
                row.set_subtitle("The original look")
                row.add_suffix(self._icon_button("edit-copy-symbolic", "Duplicate",
                                                 lambda *_, t=theme: me.edit(None, start_from=t)))
            else:
                row.add_suffix(self._icon_button("document-edit-symbolic", "Edit",
                                                 lambda *_, t=theme: me.edit(t)))
                row.add_suffix(self._icon_button("user-trash-symbolic", "Delete",
                                                 lambda *_, t=theme: me._confirm_delete(t)))
            self.group.add(row)
            self._rows.append(row)

    @staticmethod
    def _icon_button(icon, tooltip, callback):
        """A flat icon button for a theme row."""
        button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.connect("clicked", callback)
        return button

    def _select(self, name):
        """A theme was ticked: make it active."""
        if name != self.themes.active:
            GLib.idle_add(lambda: self.themes.select(name) and False)  # rebuilds the rows: not mid-signal

    def _confirm_delete(self, theme):
        """Confirm, then delete a theme."""
        alert = Adw.AlertDialog(heading=f"Delete “{theme.name}”?",
                                body="This theme will be removed. If it's in use, the Default theme returns.")
        alert.add_response("cancel", "Cancel")
        alert.add_response("delete", "Delete")
        alert.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.connect("response", lambda _a, r: self._delete(theme) if r == "delete" else None)
        alert.present(self)

    def _delete(self, theme):
        """Delete a theme and say so."""
        self.themes.delete(theme.name)
        self.toast(f"Deleted “{theme.name}”")

    # ---- editor ----

    def edit(self, theme: Theme | None, start_from: Theme | None = None):
        """Open the editor on a theme, or (theme None) on a new theme copied from `start_from`."""
        if self.nav.find_page("editor") is not None:
            return
        self.nav.push(ThemeEditor(self, theme, start_from or theme or default_theme()))


class ThemeEditor(Adw.NavigationPage):
    """Edits a theme's name and colours. Valid colours preview live on the whole app; leaving without
    saving reverts. Basic colours are required; each detailed colour may be left on Auto."""

    def __init__(self, dialog: ThemeDialog, theme: Theme | None, source: Theme):
        super().__init__(title="Edit Theme" if theme else "New Theme", tag="editor")
        self.dialog = dialog
        me = weak_self(self)  # for handlers of widgets this owns (see widgets.weak_self)
        self.themes = dialog.themes
        self.original = theme  # None for a new theme
        self._saved = False
        self._filling = True  # no validating (or previewing) until every row exists

        self.save_button = Gtk.Button(label="Save")
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda *_: me._save())
        header = Adw.HeaderBar()
        header.pack_end(self.save_button)

        page = Adw.PreferencesPage()
        name_group = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title="Name")
        if theme:
            self.name_row.set_text(theme.name)
        else:
            self.name_row.set_text(self._unique_name(f"{source.name} Copy" if not source.is_default else "My Theme"))
        self.name_row.connect("changed", lambda *_: me._validate())
        name_group.add(self.name_row)
        page.add(name_group)

        colors_group = Adw.PreferencesGroup(title="Basic Colors",
                                            description="Type a hex code, like #1e90ff, or click a dot to pick one. "
                                                        "Changes preview live.")
        # key → (row, swatch); detailed rows may be empty (Auto).
        self.rows: dict[str, tuple[Adw.EntryRow, Swatch]] = {}
        for key, label, desc in COLORS:
            row, swatch = self._color_row(key, label, desc)
            row.set_text(source.colors[key])
            colors_group.add(row)
        page.add(colors_group)

        detail_group = Adw.PreferencesGroup(title="Every Element",
                                            description="Each element can have its own color. "
                                                        "Left on Auto, it follows the basic colors.")
        self._auto_labels: dict[str, tuple[Gtk.Label, Gtk.Button]] = {}
        self._expanders: list[tuple[Adw.ExpanderRow, list[str], str]] = []
        for title, entries in GROUPS:
            expander = Adw.ExpanderRow(title=GLib.markup_escape_text(title))
            names = GLib.markup_escape_text(", ".join(label for _k, label, *_r in entries[:3])
                                            + ("…" if len(entries) > 3 else ""))
            keys = []
            for key, label, desc, _follows, _alpha in entries:
                row, _swatch = self._color_row(key, label, desc)
                auto = Gtk.Label(label="Auto", valign=Gtk.Align.CENTER)
                auto.add_css_class("dim-label")
                clear = Gtk.Button(icon_name="edit-clear-symbolic", tooltip_text="Back to Auto",
                                   valign=Gtk.Align.CENTER)
                clear.add_css_class("flat")
                # The row is found from the button: capturing it would keep the row alive (weak_self).
                clear.connect("clicked", lambda b: b.get_ancestor(Adw.EntryRow).set_text(""))
                row.add_suffix(auto)
                row.add_suffix(clear)
                self._auto_labels[key] = (auto, clear)
                row.set_text(source.colors.get(key) or "")
                expander.add_row(row)
                keys.append(key)
            self._expanders.append((expander, keys, names))
            detail_group.add(expander)
        page.add(detail_group)

        reset_group = Adw.PreferencesGroup()
        reset = Gtk.Button(label="Reset to Default Colors", halign=Gtk.Align.CENTER)
        reset.add_css_class("pill")
        reset.connect("clicked", lambda *_: me._fill(default_theme().colors))
        reset_group.add(reset)
        page.add(reset_group)

        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)
        self._filling = False
        self._validate()

    def _color_row(self, key, label, desc):
        """A colour row: hex entry, with a swatch button that opens the colour picker."""
        me = weak_self(self)  # for handlers of widgets this owns (see widgets.weak_self)
        row = Adw.EntryRow(title=GLib.markup_escape_text(label), tooltip_text=desc)
        swatch = Swatch(size=18)
        # The swatch is also a button that opens the colour picker for this row.
        pick = Gtk.Button(child=swatch, tooltip_text=f"Pick a Color for {label}", valign=Gtk.Align.CENTER)
        pick.add_css_class("flat")
        pick.add_css_class("circular")
        pick.connect("clicked", lambda _b: me._pick(key, label))
        row.add_prefix(pick)
        row.connect("changed", lambda *_: me._validate())
        self.rows[key] = (row, swatch)
        return row, swatch

    def _pick(self, key, label):
        """GTK's colour chooser (palette + custom editor with a hex field), starting at the row's colour
        (or, on Auto, the colour it's following)."""
        row = self.rows[key][0]
        values, _bad = self._values()
        chooser = Gtk.ColorDialog(title=f"Pick a Color: {label}", with_alpha=False, modal=True)
        initial = Gdk.RGBA()
        initial.parse(normalize_hex(row.get_text()) or effective(self._shown(values), key))
        chooser.choose_rgba(self.dialog.win, initial, None, lambda d, result: self._on_picked(d, result, row))

    def _on_picked(self, chooser, result, row):
        """Put the picked colour in the row."""
        try:
            rgba = chooser.choose_rgba_finish(result)
        except GLib.Error:
            return  # cancelled: leave the row as it was
        row.set_text(to_hex((rgba.red, rgba.green, rgba.blue)))  # validation + live preview follow

    def _unique_name(self, base):
        """`base`, or "base 2", "base 3"… if it's taken."""
        name, n = base, 2
        while self.themes.name_taken(name):
            name, n = f"{base} {n}", n + 1
        return name

    def _fill(self, colors):
        """Set every row from `colors`; detailed colours it doesn't have go back to Auto."""
        self._filling = True
        for key, (row, _swatch) in self.rows.items():
            row.set_text(colors.get(key) or "")
        self._filling = False
        self._validate()

    def _values(self) -> tuple[dict[str, str | None], set[str]]:
        """The typed colours (None = empty) and the keys whose text isn't a valid colour."""
        values, bad = {}, set()
        for key, (row, _s) in self.rows.items():
            text = row.get_text().strip()
            values[key] = normalize_hex(text) if text else None
            if values[key] is None and (text or key in REQUIRED):
                bad.add(key)
        return values, bad

    @staticmethod
    def _shown(values):
        """What the theme would be: the basics plus the detailed colours that are set."""
        return {k: v for k, v in values.items() if v}

    def _validate(self):
        """Mark invalid entries, update swatches and group subtitles, enable Save, and preview."""
        if self._filling:
            return
        values, bad = self._values()
        shown = self._shown(values)
        basics_ok = not any(k in bad for k in REQUIRED)
        for key, (row, swatch) in self.rows.items():
            (row.add_css_class if key in bad else row.remove_css_class)("error")
            if key in self._auto_labels:
                auto, clear = self._auto_labels[key]
                is_auto = values[key] is None and key not in bad
                auto.set_visible(is_auto)
                clear.set_visible(not is_auto)
                if is_auto:
                    swatch.set_color(effective(shown, key) if basics_ok else None, auto=True)
                    continue
            swatch.set_color(values[key])
        for expander, keys, names in self._expanders:
            custom = sum(1 for k in keys if values[k])
            wrong = sum(1 for k in keys if k in bad)
            expander.set_subtitle(names + (f" · {custom} custom" if custom else "")
                                  + (f" · {wrong} invalid" if wrong else ""))
        name = self.name_row.get_text().strip()
        name_ok = bool(name) and name != DEFAULT_NAME and not self.themes.name_taken(
            name, except_name=self.original.name if self.original else None)
        (self.name_row.remove_css_class if name_ok else self.name_row.add_css_class)("error")
        colors_ok = not bad
        self.save_button.set_sensitive(name_ok and colors_ok)
        if colors_ok:
            self.themes.preview(shown)

    def _save(self):
        """Save the theme (renaming it if the name changed) and make it active."""
        values, bad = self._values()
        values = self._shown(values)
        name = self.name_row.get_text().strip()
        if bad or not name:
            return
        self._saved = True
        self.themes.save(Theme(name, values), old_name=self.original.name if self.original else None)
        self.dialog.nav.pop()
        self.dialog.toast(f"Saved “{name}”")

    def discard(self):
        """Left without saving: drop the live preview."""
        if not self._saved:
            self.themes.end_preview()
