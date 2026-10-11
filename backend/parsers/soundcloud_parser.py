"""SoundCloud Track and Set/Playlist metadata parser using oEmbed and yt-dlp."""
import re
import logging
from typing import Dict, List, Any, Optional
import requests
import yt_dlp

logger = logging.getLogger("soundcloud_parser")

SC_URL_REGEX = re.compile(
    r"https?://(?:www\.)?soundcloud\.com/([a-zA-Z0-9_-]+)/([a-zA-Z0-9_-]+)(?:/([a-zA-Z0-9_-]+))?"
)


def format_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "0:00"
    total_sec = int(seconds)
    m = total_sec // 60
    s = total_sec % 60
    return f"{m}:{s:02d}"


def is_soundcloud_url(url: str) -> bool:
    return "soundcloud.com" in url.lower()


def fetch_soundcloud_metadata(url: str) -> Dict[str, Any]:
    """Extracts SoundCloud sets, playlists, or albums."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url.strip(), download=False)

    entries = info.get("entries")
    if entries is not None:
        # Playlist / Set
        title = info.get("title", "SoundCloud Playlist")
        description = info.get("description") or f"Curated by {info.get('uploader', 'SoundCloud Artist')}"
        cover_art = info.get("thumbnail") or ""
        tracks: List[Dict[str, Any]] = []

        for idx, entry in enumerate(entries, start=1):
            if not entry:
                continue
            dur = entry.get("duration") or 0
            uploader = entry.get("uploader") or entry.get("artist") or "SoundCloud Artist"
            t_title = entry.get("title", "Unknown Title")
            t_url = entry.get("webpage_url") or entry.get("url") or url

            tracks.append({
                "id": f"sc_{entry.get('id', idx)}",
                "title": t_title,
                "artist": uploader,
                "artists": [uploader],
                "album": title,
                "duration_ms": int(dur * 1000),
                "duration_formatted": format_duration(dur),
                "cover_art": entry.get("thumbnail") or cover_art,
                "track_number": idx,
                "source_url": t_url,
                "status": "queued",
            })

        return {
            "id": f"sc_set_{info.get('id', 'set')}",
            "title": title,
            "description": description,
            "cover_art": cover_art,
            "total_tracks": len(tracks),
            "source": "soundcloud",
            "tracks": tracks,
        }
    else:
        # Single track parsed as a 1-track collection
        t_title = info.get("title", "SoundCloud Track")
        uploader = info.get("uploader") or "SoundCloud Artist"
        dur = info.get("duration") or 0
        cover = info.get("thumbnail") or ""

        single_track = {
            "id": f"sc_{info.get('id', 'track')}",
            "title": t_title,
            "artist": uploader,
            "artists": [uploader],
            "album": "SoundCloud Single",
            "duration_ms": int(dur * 1000),
            "duration_formatted": format_duration(dur),
            "cover_art": cover,
            "track_number": 1,
            "source_url": info.get("webpage_url") or url,
            "status": "queued",
        }

        return {
            "id": f"sc_single_{info.get('id', 'single')}",
            "title": t_title,
            "description": f"Track by {uploader}",
            "cover_art": cover,
            "total_tracks": 1,
            "source": "soundcloud",
            "tracks": [single_track],
        }


def resolve_soundcloud_track(url: str) -> Dict[str, Any]:
    """Resolves single SoundCloud track for instant preview & download."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url.strip(), download=False)

    title = info.get("title", "SoundCloud Track")
    uploader = info.get("uploader") or "SoundCloud Artist"
    dur_sec = int(info.get("duration") or 0)
    cover_url = info.get("thumbnail") or ""
    upload_date = info.get("upload_date") or ""
    year = upload_date[:4] if upload_date and len(upload_date) >= 4 else ""

    return {
        "title": title,
        "artist": uploader,
        "artists_display": uploader,
        "album": "SoundCloud Release",
        "year": year,
        "cover_url": cover_url,
        "duration_str": format_duration(dur_sec),
        "duration_sec": dur_sec,
        "search_query": url,
        "source": "soundcloud",
        "source_url": url,
    }
