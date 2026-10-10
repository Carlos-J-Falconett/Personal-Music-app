"""Sun, a GTK 4 / libadwaita music player for a local library.

Modules (main.py starts application.main):

- application  the Adw.Application: creates the services below, the main window, app-wide actions and
               keyboard shortcuts, and the hidden way into Black Hole mode
- config       paths (music folder, data/cache folders) and constants
- database     SQLite storage: track cache, favorites, playlists, per-song EQ, settings
- scanner      background library scan (only new/changed files are read), tags.py reads the tags
- watcher      folder monitors that trigger debounced partial rescans
- library      the in-memory model (TrackItem, AlbumItem, Library) shared by every view
- player       GStreamer playback, the queue with shuffle/repeat, equalizer and spectrum analysis
- stats        listening history (what was played, for how long) and the Stats tab's calculations
- mpris        the MPRIS D-Bus service (GNOME media controls, media keys)
- lyrics       lyrics lookup: tags, sidecar files, disk cache, lrclib.net; songs without lyrics,
               whole-library downloads, lyrics added from local files
- art          cover art and artist images, scaled and cached; Black Hole mode state
- slideshows   Black Hole mode's slideshows: Global → groups → individual slideshows
- themes       colour themes applied over style.css
- ui/          every widget and window (see ui/__init__.py)
"""
