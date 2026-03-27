"""YouTube search helper — adapted from Playlist-converter/spotify-youtube.py."""

from youtube_search import YoutubeSearch


def get_youtube_link(track):
    track_name = track["track"]["name"]
    artist_names = ", ".join(artist["name"] for artist in track["track"]["artists"])
    query = f"{track_name} {artist_names}"

    try:
        results = YoutubeSearch(query, max_results=1).to_dict()
        if results:
            return results[0]["id"]
        return None
    except Exception:
        return None
