# Sun

Sun is a music player for your local library, built with Python, GTK 4, libadwaita and GStreamer
for the GNOME desktop. It reads a music folder, keeps its catalogue in a small SQLite database,
and plays everything through GStreamer. The playing artist's picture fills the window behind
translucent "glass" panels.

- [Features](#features)
- [Requirements](#requirements)
- [Running and installing](#running-and-installing)
- [Using the app](#using-the-app)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Where things are stored](#where-things-are-stored)
- [How it works](#how-it-works)
- [Project layout](#project-layout)

## Features

- **Library** of every song under your music folder: All Songs, Artists (artist → albums → songs),
  Favorites and Playlists tabs, plus search across artists, albums and songs.
- **Automatic updates:** the folder is watched, and new, changed or removed files show up by
  themselves. Only files that changed are re-read, so rescans are quick.
- **Now Playing panel** with the cover, song details, an audio wave visualizer, a time bar,
  shuffle/repeat and volume. The cover card flips over to show the **lyrics** or the **equalizer**.
- **Stats tab:** how much you've listened this week, month, year or all time (time, plays, songs,
  artists), a year of days as a heatmap with your listening streaks, and your top artists, albums
  and songs.
- **Queue** ("Up Next"): play next, reorder by dragging, remove, clear. With Repeat Queue it shows
  what comes around again.
- **Lyrics** from the song's tags, a `.lrc`/`.txt` file next to it, or [LRCLIB](https://lrclib.net).
  Synced lyrics follow the song, and clicking a line jumps there.
- **Per-song equalizer** (10 bands, presets). Each song remembers its own EQ.
- **Focus view:** the whole window becomes Now Playing, with Queue / Lyrics / Equalizer pop-ups.
- **Colour themes:** make your own from hex colours, for the basics or for any single element,
  with a live preview.
- **Artist images:** choose your own picture for any artist.
- **Adaptive layout:** from wide windows down to phone size.
- **Desktop integration:** GNOME's media controls, the lock screen and media keys control the
  player (MPRIS). Closing the window keeps the music playing; Quit ends the app.

## Requirements

- Python 3.12 or newer
- GTK 4 and libadwaita 1.x with PyGObject (`python3-gobject`)
- GStreamer 1.x with the base and good plugins (`playbin`, `spectrum`, `equalizer-10bands`) and
  the codecs your music needs
- `mutagen` (`python3-mutagen`) for reading tags
- For videos in the Black Hole slideshow (see the hidden section below): `gtk4paintablesink`
  (`gstreamer1-plugin-gtk4`) and a decoder for the video format (e.g. `gstreamer1-plugin-openh264`
  for H.264)

On Fedora:

```sh
sudo dnf install python3-gobject gtk4 libadwaita python3-mutagen \
    gstreamer1-plugins-base gstreamer1-plugins-good gstreamer1-plugin-gtk4
```

## Running and installing

```sh
python3 main.py
```

The music folder is set in `musicapp/config.py` (`MUSIC_DIR`). To use another folder without
editing the code, set `MUSICAPP_DIR`:

```sh
MUSICAPP_DIR=~/Music python3 main.py
```

`./install.sh` adds a launcher for the current user, so the app shows up as **Sun** in GNOME's
Activities. It points at this folder, so run it again if you move the project.

The first launch reads the whole library (progress shows in a banner). After that, only new or
changed files are read.

## Using the app

- **Playing:** double-click a song to play its list from there, or use a page's **Play** /
  **Shuffle** buttons. Right-click a song (long-press on touch) for its menu: Play, Add to Queue,
  Add to Playlist, favorite, Go to Album / Artist. The menu acts on every selected song when you
  right-click a selection.
- **Now Playing:** the heart and playlist buttons act on the playing song. The artist and album
  names are links. The queue, lyrics and equalizer buttons sit at the top of the panel; drag the
  panel's left edge to resize it.
- **Search:** start typing anywhere, or press Ctrl+F. Every word must match. Results are split
  into All / Songs / Artists / Albums. Playing a song you found queues your whole library in All
  Songs order from that song, just as if you had double-clicked it on the All Songs tab.
- **Hide Interface** (Ctrl+H, or the switch in the main menu): everything under the top bar
  disappears so only the background shows. Clicking a tab, or Ctrl+H again, brings it back.
- **Stats:** the fifth tab. Pick Week / Month / Year / All time (the last 7, 30 or 365 days, or
  everything) for the four numbers and the rankings. The heatmap shows a year of days, darker to
  brighter by how long you listened; hover a day to see its date, time and plays, and use ‹ / ›
  to see earlier years. Its summary gives the days listened that year, your current streak and
  your longest. Click a ranked artist or album to open it, or a song to play the ranking from it.
  Listening is recorded from the first launch of this version on; nothing before that is known.
- **Main menu** (the Sun icon, top left): new playlist, rescan, Themes…, Artist Images…,
  keyboard shortcuts, About, Quit.
- **Themes:** pick a theme, or create one. Basic colours are required. Every element (tabs, song
  titles, sliders, lyrics, …) can have its own colour, or stay on **Auto** to follow the basics.
- **Artist images:** in Artist Images…, pick an artist and choose a picture. The app keeps its
  own copy. Without one, the app uses a MusicBee picture (`MusicBee/Artist Pictures` in the
  music folder) if there is one, else the artist's album cover.
- **Tags and covers:** songs without tags are named from the file and its folders
  (`Artist/Album/Song.ext`). Covers come from the file's embedded picture or an image in the
  album folder (`cover`, `folder`, `front`, `album`, `albumart`, then any image).
- **What is remembered:** volume, shuffle, repeat, theme, window size, the last tab and the panel
  width. The playing song and queue are not: each launch starts fresh.

## Keyboard shortcuts

| Keys | Action |
|---|---|
| Space | Play / pause (except while typing) |
| Ctrl+→ / Ctrl+← | Next / previous song |
| Ctrl+S | Shuffle on/off |
| Ctrl+R | Repeat: off → queue → current song |
| Ctrl+D | Favorite the playing song |
| Ctrl+F | Search |
| Ctrl+N | Show / hide the queue |
| Ctrl+L | Show / hide lyrics |
| Ctrl+E | Show / hide the equalizer |
| Ctrl+Shift+F | Focus view (Esc leaves it) |
| Ctrl+H | Hide / show the interface |
| F5 | Rescan the library |
| Ctrl+? | Keyboard shortcuts |
| Ctrl+Q | Quit |

## Where things are stored

| What | Where |
|---|---|
| Library, favorites, playlists, per-song EQ, listening history, settings | `~/.local/share/musicappv5/library.db` (SQLite) |
| Cached online lyrics | `~/.local/share/musicappv5/lyrics/` |
| Your artist images (copies) | `~/.local/share/musicappv5/artist-images/` |
| Scaled covers and images (safe to delete) | `~/.cache/musicappv5/covers/` |

Your music files are only ever read, never changed. `XDG_DATA_HOME` and `XDG_CACHE_HOME` are
respected.

## How it works

**Startup.** `main.py` calls `musicapp.application.main()`. `MusicApp.do_startup` creates the
shared services, loads `style.css`, restores settings and installs the actions and shortcuts.
The first activation builds the `MainWindow` and starts the first scan.

**Library scanning** (`scanner.py`, `tags.py`, `watcher.py`, `database.py`). The scanner walks the
music folder on a background thread. It compares each file's modification time and size with the
database, reads tags with mutagen only for new or changed files (with worker processes for big
scans), and removes files that are gone. If the whole music folder is missing or empty, nothing is
removed, so favorites and playlists survive an unplugged drive. After the first scan, `watcher.py`
watches every folder and triggers a partial rescan 2 seconds after the last change. All database
access goes through one connection guarded by a lock.

**The model** (`library.py`). Each track becomes one `TrackItem`, shared by every view.
Its `favorite` and `playing` properties are GObject properties, so a heart or the playing
highlight updates in every list at once. Artists and albums are grouped once per library change
and cached.

**Playback** (`player.py`). A GStreamer `playbin` with an audio filter
`audioconvert ! equalizer-10bands ! audioconvert ! spectrum`. The queue keeps the tracks in their
original order plus a separate play order, so shuffle can be turned off without losing your
place. Each song loads its own saved EQ. Spectrum results are buffered and released when the
audio they describe is heard, so the visualizer is in sync. The spectrum is measured only while the
window is on screen (so a wave hidden behind the lyrics or equalizer is in step the moment it's
back), not while the app plays in the background. Unplayable files are skipped with a message.

**Listening history** (`stats.py`, `ui/stats_view.py`). `ListeningRecorder` follows the
player and stores one row per listening session in the `plays` table. Each row keeps the title,
artist and album, so the history survives rescans and deleted files.
- Only time actually playing counts; pauses and seeks don't.
- A session counts as a play once half the song, or 4 minutes, has been heard.
- Sessions under 5 seconds (skips) aren't kept.
- Each loop of Repeat Current Song is a new session.
- The song in progress is saved every 30 seconds and when the app quits.

The Stats page reads the database only while it's showing. The heatmap is drawn with cairo in the
theme's "Listening Days" colour; the Stats theme group also colours the number cards and empty
days.

**Pictures** (`art.py`). Covers and artist images are scaled once on worker threads and cached on
disk per size: 128 px thumbnails, 512 px large covers, and a full-window 2560 px image with a
blurred copy for the background. The UI asks for a picture and gets a texture back on the main
thread.

**The window** (`ui/window.py`). Layers from the back: the backdrop (`ui/backdrop.py`), then the
header bar, the library pages and the Now Playing panel. `Adw.Breakpoint`s switch between three
layouts:

- **Wide:** the panel is docked on the right.
- **Narrow:** the panel fills the window, and the tabs become icons.
- **Phone-sized:** search moves into its own bar and everything is tighter.

**Lyrics** (`lyrics.py`) are looked up on worker threads in this order: tags, then a sidecar
file, then the disk cache, then LRCLIB. "Not found" answers are cached for a week, and network
errors aren't cached.

**Themes** (`themes.py`) are sets of hex colours turned into CSS variables and loaded over
`style.css` with a higher priority. The Default theme adds nothing.

**MPRIS** (`mpris.py`) publishes `org.mpris.MediaPlayer2.musicappv5` on the session bus.

<details>
<summary><b>Black Hole mode</b> (a hidden mode, with spoilers)</summary>

### Black Hole mode

Black Hole mode is a hidden second look for the app: a second set of artist images, and a
full-window background **slideshow** of your own images and videos that plays instead of the
artist images. It is found through the About dialog, and nothing in the normal interface
mentions it.

#### Entering and leaving

- **Enter:** open **About Sun** from the main menu and **triple-click the app icon**, then type
  **`w a s d`** within **5 seconds**. Shift is allowed; any other key cancels, and you start again
  with the triple-click. Every Sun icon flips into a black hole.
- **Leave** in any of these ways:
  - double-click the black hole icon at the top left;
  - main menu → **Exit Black Hole Mode**;
  - triple-click the About icon again.
- The app always starts in normal mode (Black Hole mode isn't remembered). Everything you set up
  in it is.

#### What changes

- **Artist images:** Artist Images… now edits a separate set of Black Hole images. An artist
  without one keeps their normal image.
- **A sixth tab, Slideshows,** appears in the top bar (and as an icon on small windows) to manage
  the background slideshows. It disappears again when you leave the mode.
- **Main menu → Black Hole section:**
  - **Slideshow** on/off
  - **Choose Slideshow** (marks the one showing; also **Manage Slideshows…**)
  - **Repeat Current Slide**
  - **Exit Black Hole Mode**
- The **Keyboard Shortcuts** window gets a "Black Hole Slideshows" section.

#### Slideshows

Slideshows are organised in three levels:

- **Individual slideshows** each have their own images and videos. Every individual slideshow
  belongs to exactly one group.
- **Groups** show all of their slideshows one after another, as one slideshow.
- **Global**, the default, shows everything from every group.

Manage them in the **Slideshows** tab (main menu → Choose Slideshow → Manage Slideshows… also
opens it). It has three pages, like the Artists and Playlists tabs:

1. **Slideshows:** the on/off switch and **Seconds per Image** (3–600, default 10).
   Below, a round button picks what shows: **Global** or one of the groups (click a group to open
   it). **New Group…** adds one.
2. **A group:** show the **Whole Group** or one of its slideshows; **New Slideshow…**; rename or
   delete the group. Deleting a group also deletes its slideshows.
3. **A slideshow:** its images and videos as thumbnails. Add more with **Add Images or Videos…**,
   remove one (×) or **Remove All**, **Show This Slideshow**, rename or delete it.

How it plays:

- **When it shows:** the slideshow replaces the artist image when Black Hole mode is on, the
  slideshow is switched on, it has something to show, and a song is loaded. It keeps going across
  songs. Changes crossfade.
- **Images** show for the chosen number of seconds. **Videos** always play to their end, with no
  sound, and a slideshow with a single slide loops it.
- **Unplayable files** are skipped. If none of the slides can be shown, the slideshow waits for the
  next slide time before trying again.
- **Picking something else** starts it from its first slide. What's showing is remembered across
  launches. If you delete it, Global shows instead.
- **Added files are copied** into the app's folder, so your originals are never changed or
  removed. Deleting a slideshow only deletes those copies.

The first time this version runs, a slideshow from an older version (a single set of files) is
moved into **Group 1 › Slideshow 1**. Nothing is deleted.

#### Slideshow controls (Black Hole mode only)

| Keys | Action |
|---|---|
| Ctrl+Shift+R | Repeat current slide on/off. An image stays, with its time paused; a video loops. When turned off, an image continues with the time it had left and a video finishes. The order is kept. |
| → → (twice within ½ s) | Next slide |
| ← ← (twice within ½ s) | Previous slide |
| Ctrl+] / Ctrl+[ | Next / previous slideshow in the group (wraps around) |
| Ctrl+Shift+] / Ctrl+Shift+[ | Next / previous group; shows its first slideshow (groups without slideshows are skipped) |
| Ctrl+G | Show the whole current group |
| Ctrl+Shift+G | Show the Global slideshow |

Using a slideshow shortcut also switches the slideshow on and shows what's playing now (for
example "Group B › Four"). While a slideshow is playing, the arrow keys belong to it, except
while you're typing in a text field.

#### How it works

- **State:** Black Hole mode is `ArtCache.turned` (`art.py`). `MusicApp.set_black_hole` switches
  it, and everything that looks different listens for the change.
- **The hidden entrance:** `MusicApp._show_about` and `_on_black_hole_key` in `application.py`
  (the word and time limit are `BLACK_HOLE_KEYS` and `BLACK_HOLE_SECONDS`).
- **Slideshow data** (`slideshows.py`): each individual slideshow's files are in
  `~/.local/share/musicappv5/artist-images/turned/slideshow/<id>/`, numbered in the order they
  were added. The groups, names and current selection are in `slideshows.json` in the same
  folder, written atomically. If that file is damaged, the slideshows are rebuilt from the
  folders.
- **Playback** (`ui/backdrop.py`): two picture layers take turns so each change crossfades. The
  image timer can be paused (for Repeat Current Slide) and resumes with the time that was left.
- **Videos** play through `ui/silent_video.py`: a `playbin` with audio switched off, drawn by
  `gtk4paintablesink`. The sound server remembers volume and mute per app, so a muted video
  stream would also mute the music; this way no video sound stream exists at all.
- **Keys:** the arrow and Shift shortcuts are handled in `MainWindow._slideshow_key`
  (`ui/window.py`) before GTK's own shortcut handling. Otherwise Shift+letter shortcuts could be
  mistaken for their plain Ctrl versions (Ctrl+Shift+R for Ctrl+R, Ctrl+Shift+G for Ctrl+G).
- **The Slideshows tab:** `ui/slideshows_view.py` (shown only in Black Hole mode, see
  `MainWindow._sync_black_hole`), with its pages in `ui/slideshow_pages.py`.

Black Hole images: `~/.local/share/musicappv5/artist-images/turned/`.

</details>

## Project layout

```
main.py                 entry point
install.sh              adds the GNOME launcher
data/                   app icon, symbolic icons, Sun / Black Hole images, .desktop file
musicapp/
  application.py        the Adw.Application: services, actions, shortcuts, Black Hole entrance
  config.py             paths and constants
  database.py           SQLite storage
  scanner.py tags.py    library scanning and tag reading
  watcher.py            folder monitoring
  library.py            the in-memory model
  player.py             GStreamer playback, queue, EQ, spectrum
  stats.py              listening history and the Stats tab's calculations
  mpris.py              MPRIS D-Bus service
  lyrics.py             lyrics lookup
  art.py                covers and artist images; Black Hole mode state
  slideshows.py         Black Hole slideshows (Global → groups → slideshows)
  themes.py             colour themes
  style.css             the stylesheet
  ui/                   windows, pages and widgets (overview in ui/__init__.py)
```

Every module starts with a docstring explaining its role, and `musicapp/__init__.py` and
`musicapp/ui/__init__.py` list what each module does.
