"""Tag reading with mutagen. Kept free of GTK imports so it is cheap to call from worker threads."""

import os
from pathlib import Path

import mutagen


def _first(tags, *keys):
    if tags is None:  # not `not tags`: len() on EasyID3 rebuilds its whole key list
        return None
    for key in keys:
        try:
            value = tags.get(key)
        except (KeyError, ValueError):
            value = None
        if value:
            text = str(value[0] if isinstance(value, list) else value).strip()
            if text:
                return text
    return None


def _number(text):
    """Parse '3', '3/12' or '03' into an int."""
    if not text:
        return 0
    try:
        return int(str(text).split("/")[0].strip() or 0)
    except ValueError:
        return 0


def safe_read_tags(args) -> dict | None:
    """Process-pool entry point: (path, root) -> record, or None if the file vanished."""
    try:
        return read_tags(*args)
    except OSError:
        return None


def read_tags(path: str, root: str) -> dict:
    """Return a track record for `path`. Never raises: unreadable files still get a record
    built from the filename and folder names, so they show up and can be played."""
    st = os.stat(path)
    p = Path(path)
    try:
        rel_parts = p.relative_to(root).parts
    except ValueError:
        rel_parts = p.parts

    # Folder layout is usually Artist/Album/Song.ext
    folder_artist = rel_parts[0] if len(rel_parts) >= 2 else None
    folder_album = rel_parts[-2] if len(rel_parts) >= 3 else None

    tags = None
    duration = 0.0
    try:
        audio = mutagen.File(path, easy=True)
        if audio is not None:
            tags = audio.tags
            duration = float(getattr(audio.info, "length", 0.0) or 0.0)
    except Exception:
        pass

    artist = _first(tags, "artist", "albumartist") or folder_artist or "Unknown Artist"
    album_artist = _first(tags, "albumartist") or ""
    return {
        "path": path,
        "dir": str(p.parent),
        "mtime": st.st_mtime,
        "size": st.st_size,
        "title": _first(tags, "title") or p.stem,
        "artist": artist,
        "album": _first(tags, "album") or folder_album or "Unknown Album",
        "album_artist": album_artist,
        "track_no": _number(_first(tags, "tracknumber")),
        "disc_no": _number(_first(tags, "discnumber")),
        "duration": duration,
        "genre": _first(tags, "genre") or "",
        "year": (_first(tags, "date", "year") or "")[:4],
    }
