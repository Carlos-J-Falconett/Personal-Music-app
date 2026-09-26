"""Paths and constants shared across the app."""

import os
from pathlib import Path

APP_ID = "io.github.carlosfalconett.MusicAppv5"
APP_NAME = "Sun"
VERSION = "1.0.0"

MUSIC_DIR = Path(os.environ.get("MUSICAPP_DIR", "/home/carlosfalconett/Music/Music"))

_data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
_cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

DATA_DIR = _data_home / "musicappv5"
CACHE_DIR = _cache_home / "musicappv5"
DB_PATH = DATA_DIR / "library.db"
COVER_DIR = CACHE_DIR / "covers"
ARTIST_IMAGE_DIR = DATA_DIR / "artist-images"  # images chosen by the user

# Optional folder of artist photos (MusicBee layout), used for artist avatars.
ARTIST_PICTURE_DIRS = [
    MUSIC_DIR / "MusicBee" / "Artist Pictures" / "Thumb",
    MUSIC_DIR / "MusicBee" / "Artist Pictures",
]

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".mp4",
    ".aac", ".wav", ".wma", ".aiff", ".aif", ".ape", ".wv", ".mpc",
}

COVER_FILENAMES = ("cover", "folder", "front", "album", "albumart")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
ICONS_DIR = PROJECT_DIR / "data" / "icons"
IMAGES_DIR = PROJECT_DIR / "data" / "images"
SUN_ICON = IMAGES_DIR / "sun.png"  # app icon, and the mode icon in normal mode
BLACK_HOLE_ICON = IMAGES_DIR / "black-hole.png"  # mode icon in Black Hole mode
