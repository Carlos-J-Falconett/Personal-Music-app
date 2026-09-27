"""Name prompts (playlists, slideshows) and delete confirmations."""

from gi.repository import Adw, Gtk


def ask_playlist_name(parent, **kwargs):
    """ask_name for a playlist."""
    ask_name(parent, placeholder="Playlist name", **kwargs)


def ask_name(parent, *, heading: str, confirm: str, initial: str = "", body: str = "", placeholder: str = "Name",
             on_done):
    """Show a name prompt; calls on_done(name) with a non-empty, stripped name."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(text=initial, placeholder_text=placeholder, activates_default=True, hexpand=True)
    entry.add_css_class("pill-entry")
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("ok", confirm)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")

    def sync(*_):
        """The confirm button needs a name."""
        dialog.set_response_enabled("ok", bool(entry.get_text().strip()))

    entry.connect("changed", sync)
    sync()

    def on_response(_d, response):
        """Confirmed with a name: pass it on."""
        name = entry.get_text().strip()
        if response == "ok" and name:
            on_done(name)

    dialog.connect("response", on_response)
    dialog.present(parent)
    entry.grab_focus()


def confirm_delete_playlist(parent, name: str, on_confirm):
    """Confirm deleting a playlist."""
    confirm_delete(parent, "Delete Playlist?",
                   f"“{name}” will be permanently deleted. The songs stay in your library.", on_confirm)


def confirm_delete(parent, heading: str, body: str, on_confirm):
    """A "Delete?" confirmation; on_confirm() runs only if Delete is chosen."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("delete", "Delete")
    dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: on_confirm() if r == "delete" else None)
    dialog.present(parent)
