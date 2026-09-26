"""Playlist name prompt and delete confirmation."""

from gi.repository import Adw, Gtk


def ask_playlist_name(parent, *, heading: str, confirm: str, initial: str = "", body: str = "", on_done):
    """Show a name prompt; calls on_done(name) with a non-empty, stripped name."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(text=initial, placeholder_text="Playlist name", activates_default=True, hexpand=True)
    entry.add_css_class("pill-entry")
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("ok", confirm)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")

    def sync(*_):
        dialog.set_response_enabled("ok", bool(entry.get_text().strip()))

    entry.connect("changed", sync)
    sync()

    def on_response(_d, response):
        name = entry.get_text().strip()
        if response == "ok" and name:
            on_done(name)

    dialog.connect("response", on_response)
    dialog.present(parent)
    entry.grab_focus()


def confirm_delete_playlist(parent, name: str, on_confirm):
    dialog = Adw.AlertDialog(
        heading="Delete Playlist?",
        body=f"“{name}” will be permanently deleted. The songs stay in your library.",
    )
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("delete", "Delete")
    dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: on_confirm() if r == "delete" else None)
    dialog.present(parent)
