"""YouTube and YouTube Music Playlist Parser."""
import re
import logging
from typing import Dict, List, Any, Optional
import yt_dlp

logger = logging.getLogger("yt_playlist_parser")

YT_PLAYLIST_REGEX = re.compile(
    r"(?:youtube\.com|youtu\.be|music\.youtube\.com)/(?:playlist\?list=|watch\?.*list=)([a-zA-Z0-9_-]+)"
)


def format_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "0:00"
    total_sec = int(seconds)
    m = total_sec // 60
    s = total_sec % 60
    return f"{m}:{s:02d}"


def is_yt_playlist_url(url: str) -> bool:
    return bool(YT_PLAYLIST_REGEX.search(url.strip()))


def fetch_yt_playlist_metadata(url: str) -> Dict[str, Any]:
    """Extracts YouTube or YouTube Music playlist items."""
    is_ytm = "music.youtube.com" in url.lower()
    source_name = "ytm" if is_ytm else "youtube"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url.strip(), download=False)

    title = info.get("title", "YouTube Playlist")
    uploader = info.get("uploader") or info.get("channel") or "YouTube Curator"
    description = info.get("description") or f"Playlist curated by {uploader}"
    thumbnails = info.get("thumbnails", [])
    cover_art = thumbnails[-1].get("url") if thumbnails else (info.get("thumbnail") or "")

    entries = info.get("entries", [])
    tracks: List[Dict[str, Any]] = []

    for idx, entry in enumerate(entries, start=1):
        if not entry:
            continue
        raw_title = entry.get("title", "Unknown Title")
        dur = entry.get("duration") or 0
        v_uploader = entry.get("uploader") or entry.get("channel") or uploader
        if v_uploader.endswith(" - Topic"):
            v_uploader = v_uploader.replace(" - Topic", "").strip()

        # Clean title & artist if title has "Artist - Song"
        t_artist = v_uploader
        t_title = raw_title
        if " - " in raw_title:
            parts = raw_title.split(" - ", 1)
            t_artist = parts[0].strip()
            t_title = parts[1].strip()

        v_id = entry.get("id") or str(idx)
        track_url = f"https://www.youtube.com/watch?v={v_id}" if entry.get("id") else url
        t_thumbs = entry.get("thumbnails", [])
        t_cover = t_thumbs[-1].get("url") if t_thumbs else (entry.get("thumbnail") or cover_art)

        tracks.append({
            "id": f"yt_{v_id}",
            "title": t_title,
            "artist": t_artist,
            "artists": [t_artist],
            "album": title,
            "duration_ms": int(dur * 1000),
            "duration_formatted": format_duration(dur),
            "cover_art": t_cover,
            "track_number": idx,
            "source_url": track_url,
            "status": "queued",
        })

    return {
        "id": f"yt_playlist_{info.get('id', 'list')}",
        "title": title,
        "description": description,
        "cover_art": cover_art,
        "total_tracks": len(tracks),
        "source": source_name,
        "tracks": tracks,
    }
