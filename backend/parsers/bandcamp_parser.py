"""Bandcamp Album and Track Parser."""
import re
import logging
from typing import Dict, List, Any, Optional
import yt_dlp

logger = logging.getLogger("bandcamp_parser")


def format_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "0:00"
    total_sec = int(seconds)
    m = total_sec // 60
    s = total_sec % 60
    return f"{m}:{s:02d}"


def is_bandcamp_url(url: str) -> bool:
    return "bandcamp.com" in url.lower()


def fetch_bandcamp_metadata(url: str) -> Dict[str, Any]:
    """Extracts Bandcamp album or track collection."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url.strip(), download=False)

    title = info.get("title", "Bandcamp Release")
    artist = info.get("uploader") or info.get("artist") or "Bandcamp Artist"
    description = f"Album by {artist}"
    cover_art = info.get("thumbnail") or ""

    entries = info.get("entries")
    if entries is not None:
        # Album
        tracks: List[Dict[str, Any]] = []
        for idx, entry in enumerate(entries, start=1):
            if not entry:
                continue
            dur = entry.get("duration") or 0
            t_title = entry.get("title", "Unknown Title")
            t_artist = entry.get("artist") or entry.get("uploader") or artist
            t_cover = entry.get("thumbnail") or cover_art

            tracks.append({
                "id": f"bc_{entry.get('id', idx)}",
                "title": t_title,
                "artist": t_artist,
                "artists": [t_artist],
                "album": title,
                "duration_ms": int(dur * 1000),
                "duration_formatted": format_duration(dur),
                "cover_art": t_cover,
                "track_number": idx,
                "source_url": entry.get("webpage_url") or entry.get("url") or url,
                "status": "queued",
            })

        return {
            "id": f"bc_album_{info.get('id', 'album')}",
            "title": title,
            "description": description,
            "cover_art": cover_art,
            "total_tracks": len(tracks),
            "source": "bandcamp",
            "tracks": tracks,
        }
    else:
        # Single track
        dur = info.get("duration") or 0
        single = {
            "id": f"bc_{info.get('id', 'track')}",
            "title": title,
            "artist": artist,
            "artists": [artist],
            "album": "Bandcamp Single",
            "duration_ms": int(dur * 1000),
            "duration_formatted": format_duration(dur),
            "cover_art": cover_art,
            "track_number": 1,
            "source_url": info.get("webpage_url") or url,
            "status": "queued",
        }
        return {
            "id": f"bc_single_{info.get('id', 'single')}",
            "title": title,
            "description": description,
            "cover_art": cover_art,
            "total_tracks": 1,
            "source": "bandcamp",
            "tracks": [single],
        }
