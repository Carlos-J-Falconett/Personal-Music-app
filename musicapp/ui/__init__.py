"""The user interface. MainWindow (window.py) puts it together:

- window             the main window: header bar, tabs, search, adaptive layout, window keys
- backdrop           the full-window background: artist image or the Black Hole slideshow
- song_list          the sortable song table used by every song page, with the song menu
- artists_view       Artists tab: artist grid → albums → an album's songs
- playlists_view     Playlists tab: playlist list → a playlist's songs
- search_view        search results
- stats_view         Stats tab: period numbers, a year heatmap, top artists/albums/songs
- now_playing        the Now Playing panel (docked, or window-filling on small windows)
- player_controls    time bar, playback buttons, volume
- queue_view         the queue (Now Playing + Up Next)
- lyrics_view        synced/plain lyrics over the blurred cover
- equalizer_view     the per-song 10-band equalizer
- visualizer         the audio wave drawn from the spectrum
- focus_view         the full-window focus view
- flip_card          the two-sided flipping card used by the panel and focus view
- cards, widgets     shared widgets (album/artist cards, cover art, hearts, layout helpers)
- menus, dialogs     the app's popover menus, name prompts and confirmations
- theme_dialog       the Themes dialog and editor
- artist_image_dialog  choosing artist images (normal and Black Hole mode)
- slideshows_view, slideshow_pages
                     Black Hole mode's Slideshows tab and its pages
- silent_video       sound-free video playback for the slideshow
- mode_icon          the Sun / Black Hole icons
"""
