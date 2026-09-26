#!/usr/bin/env bash
# Adds the app launcher to GNOME Activities for the current user.
set -euo pipefail
cd "$(dirname "$0")"
APP_ID=io.github.carlosfalconett.MusicAppv5
APPS=~/.local/share/applications
ICONS=~/.local/share/icons/hicolor/512x512/apps
mkdir -p "$APPS" "$ICONS"
# The app used to ship an SVG icon; remove it so the theme picks up the Sun PNG.
rm -f ~/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg
sed "s|^Exec=.*|Exec=python3 $PWD/main.py|; s|^Path=.*|Path=$PWD|" "data/$APP_ID.desktop" > "$APPS/$APP_ID.desktop"
cp "data/icons/hicolor/512x512/apps/$APP_ID.png" "$ICONS/"
update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null || true
echo "Installed. Search for “Sun” in Activities."
