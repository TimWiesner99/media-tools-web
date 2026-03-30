"""YouTube search helper — adapted from Playlist-converter/spotify-youtube.py."""

import logging

from youtube_search import YoutubeSearch

logger = logging.getLogger("g2r.ytsearch")


def get_youtube_link(track):
    track_name = track["track"]["name"]
    artist_names = ", ".join(artist["name"] for artist in track["track"]["artists"])
    query = f"{track_name} {artist_names}"

    try:
        results = YoutubeSearch(query, max_results=1).to_dict()
        if results:
            logger.info("Found: %s", query)
            return results[0]["id"]
        logger.warning("Not found: %s", query)
        return None
    except Exception:
        logger.exception("Search failed: %s", query)
        return None
