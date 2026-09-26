"""MPRIS2 D-Bus service so GNOME's media controls, lock screen and media keys drive the player."""

from gi.repository import Gio, GLib

from .art import LARGE
from .config import APP_ID, APP_NAME, COVER_DIR
from .player import Repeat

BUS_NAME = "org.mpris.MediaPlayer2.musicappv5"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
TRACK_PATH = "/io/github/carlosfalconett/MusicAppv5/track/"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

INTROSPECTION = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/>
    </method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""

LOOP_TO_MPRIS = {Repeat.OFF: "None", Repeat.ALL: "Playlist", Repeat.ONE: "Track"}
MPRIS_TO_LOOP = {v: k for k, v in LOOP_TO_MPRIS.items()}


class MprisService:
    def __init__(self, app, player, art):
        self.app = app
        self.player = player
        self.art = art
        self._conn = None
        self._art_url = ""
        self._owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE, self._on_bus_acquired, None, None
        )
        player.connect("state-changed", lambda *_: self._changed(PLAYER_IFACE, "PlaybackStatus", "CanPlay", "CanPause"))
        player.connect("track-changed", self._on_track_changed)
        player.connect("queue-changed", lambda *_: self._changed(PLAYER_IFACE, "CanGoNext", "CanGoPrevious"))
        player.connect("modes-changed", lambda *_: self._changed(PLAYER_IFACE, "LoopStatus", "Shuffle", "CanGoNext"))
        player.connect("volume-changed", lambda *_: self._changed(PLAYER_IFACE, "Volume"))
        player.connect("seeked", self._on_seeked)

    def _on_bus_acquired(self, conn, _name):
        self._conn = conn
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        for iface in node.interfaces:
            conn.register_object(OBJECT_PATH, iface, self._method_call, self._get_property, self._set_property)

    # ---- incoming calls ----

    def _method_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        p = self.player
        if method == "Raise":
            if self.app.props.active_window:
                self.app.props.active_window.present()
        elif method == "Quit":
            self.app.quit()
        elif method == "Next":
            p.next()
        elif method == "Previous":
            p.previous()
        elif method == "Pause":
            p.pause()
        elif method == "PlayPause":
            p.play_pause()
        elif method == "Stop":
            p.stop()
        elif method == "Play":
            p.play()
        elif method == "Seek":
            (offset,) = params.unpack()
            target = p.get_position() + offset / 1e6
            if target >= p.get_duration():
                p.next()
            else:
                p.seek(max(0.0, target))
        elif method == "SetPosition":
            track_id, position = params.unpack()
            if p.current and track_id == self._track_path(p.current) and 0 <= position / 1e6 <= p.get_duration():
                p.seek(position / 1e6)
        invocation.return_value(None)

    def _get_property(self, _conn, _sender, _path, iface, prop):
        value = self._properties(iface).get(prop)
        return value

    def _set_property(self, _conn, _sender, _path, _iface, prop, value):
        v = value.unpack()
        if prop == "LoopStatus" and v in MPRIS_TO_LOOP:
            self.player.set_repeat(MPRIS_TO_LOOP[v])
        elif prop == "Shuffle":
            self.player.set_shuffle(bool(v))
        elif prop == "Volume":
            self.player.set_volume(float(v))
        return True

    # ---- property values ----

    def _properties(self, iface) -> dict[str, GLib.Variant]:
        if iface == ROOT_IFACE:
            return {
                "CanQuit": GLib.Variant("b", True),
                "CanRaise": GLib.Variant("b", True),
                "HasTrackList": GLib.Variant("b", False),
                "Identity": GLib.Variant("s", APP_NAME),
                "DesktopEntry": GLib.Variant("s", APP_ID),
                "SupportedUriSchemes": GLib.Variant("as", ["file"]),
                "SupportedMimeTypes": GLib.Variant("as", ["audio/mpeg", "audio/flac", "audio/ogg", "audio/mp4"]),
            }
        p = self.player
        has_track = p.current is not None
        return {
            "PlaybackStatus": GLib.Variant("s", "Playing" if p.playing else ("Paused" if has_track else "Stopped")),
            "LoopStatus": GLib.Variant("s", LOOP_TO_MPRIS[p.repeat]),
            "Rate": GLib.Variant("d", 1.0),
            "Shuffle": GLib.Variant("b", p.shuffle),
            "Metadata": GLib.Variant("a{sv}", self._metadata()),
            "Volume": GLib.Variant("d", p.volume),
            "Position": GLib.Variant("x", int(p.get_position() * 1e6)),
            "MinimumRate": GLib.Variant("d", 1.0),
            "MaximumRate": GLib.Variant("d", 1.0),
            "CanGoNext": GLib.Variant("b", p.has_next()),
            "CanGoPrevious": GLib.Variant("b", p.has_previous()),
            "CanPlay": GLib.Variant("b", True),
            "CanPause": GLib.Variant("b", has_track),
            "CanSeek": GLib.Variant("b", has_track),
            "CanControl": GLib.Variant("b", True),
        }

    @staticmethod
    def _track_path(track) -> str:
        return f"{TRACK_PATH}{track.id}"

    def _metadata(self) -> dict:
        t = self.player.current
        if t is None:
            return {"mpris:trackid": GLib.Variant("o", NO_TRACK)}
        md = {
            "mpris:trackid": GLib.Variant("o", self._track_path(t)),
            "mpris:length": GLib.Variant("x", int((t.duration or 0) * 1e6)),
            "xesam:title": GLib.Variant("s", t.title),
            "xesam:artist": GLib.Variant("as", [t.artist]),
            "xesam:album": GLib.Variant("s", t.album),
            "xesam:url": GLib.Variant("s", GLib.filename_to_uri(t.path)),
        }
        if t.album_artist:
            md["xesam:albumArtist"] = GLib.Variant("as", [t.album_artist])
        if t.track_no:
            md["xesam:trackNumber"] = GLib.Variant("i", t.track_no)
        if self._art_url:
            md["mpris:artUrl"] = GLib.Variant("s", self._art_url)
        return md

    # ---- outgoing notifications ----

    def _on_track_changed(self, _player, track):
        self._art_url = ""
        self._changed(PLAYER_IFACE, "Metadata", "CanPlay", "CanPause", "CanSeek", "CanGoNext", "CanGoPrevious")
        if track is None:
            return

        def on_art(texture):
            if texture is not None and self.player.current is track:
                path = COVER_DIR / f"{self.art.album_key(track)}_{LARGE}.jpg"
                if path.exists():
                    self._art_url = GLib.filename_to_uri(str(path))
                    self._changed(PLAYER_IFACE, "Metadata")

        self.art.request_track(track, LARGE, on_art)

    def _on_seeked(self, _player, seconds):
        if self._conn:
            self._conn.emit_signal(None, OBJECT_PATH, PLAYER_IFACE, "Seeked", GLib.Variant("(x)", (int(seconds * 1e6),)))

    def _changed(self, iface, *props):
        if not self._conn:
            return
        values = self._properties(iface)
        changed = {p: values[p] for p in props}
        self._conn.emit_signal(
            None, OBJECT_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", (iface, changed, [])),
        )

    def shutdown(self):
        Gio.bus_unown_name(self._owner_id)
