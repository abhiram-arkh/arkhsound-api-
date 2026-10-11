"""Deezer Playlist, Album, and Track metadata parser using Deezer public REST API."""
import re
import logging
from typing import Dict, List, Any, Optional
import requests

logger = logging.getLogger("deezer_parser")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

DEEZER_URL_REGEX = re.compile(
    r"deezer\.com(?:\/[a-z]{2})?\/(playlist|album|track)\/(\d+)"
)


def parse_deezer_url(url: str) -> Optional[Dict[str, str]]:
    """Extracts entity type (playlist, album, track) and Deezer ID."""
    match = DEEZER_URL_REGEX.search(url.strip())
    if match:
        return {"type": match.group(1), "id": match.group(2)}
    return None


def format_duration(seconds: int) -> str:
    m = seconds // 60
    s = seconds % 60
    return f"{m}:{s:02d}"


def fetch_deezer_metadata(url: str) -> Dict[str, Any]:
    """Fetches full playlist or album metadata from Deezer public REST API."""
    parsed = parse_deezer_url(url)
    if not parsed or parsed["type"] not in ["playlist", "album"]:
        raise ValueError("Invalid Deezer URL. Expected a playlist or album URL (e.g. deezer.com/playlist/...).")

    entity_type = parsed["type"]
    entity_id = parsed["id"]
    api_url = f"https://api.deezer.com/{entity_type}/{entity_id}"

    resp = requests.get(api_url, headers=HEADERS, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Deezer API returned HTTP {resp.status_code}")

    data = resp.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", "Deezer API error."))

    title = data.get("title", "Deezer Collection")
    description = ""
    if entity_type == "playlist":
        description = data.get("description") or f"Curated by {data.get('creator', {}).get('name', 'Deezer')}"
        cover_art = data.get("picture_xl") or data.get("picture_big") or data.get("picture_medium") or ""
    else:  # album
        artist_name = data.get("artist", {}).get("name", "Various Artists")
        description = f"Album by {artist_name} • {data.get('release_date', '')[:4]}"
        cover_art = data.get("cover_xl") or data.get("cover_big") or data.get("cover_medium") or ""

    raw_tracks = data.get("tracks", {}).get("data", [])
    tracks: List[Dict[str, Any]] = []

    for idx, t in enumerate(raw_tracks, start=1):
        dur_sec = t.get("duration", 0)
        track_artist = t.get("artist", {}).get("name", "Unknown Artist")
        track_album = data.get("title") if entity_type == "album" else t.get("album", {}).get("title", "")
        track_cover = cover_art
        if entity_type == "playlist" and t.get("album"):
            track_cover = t.get("album", {}).get("cover_xl") or t.get("album", {}).get("cover_big") or cover_art

        tracks.append({
            "id": f"deezer_{t.get('id', idx)}",
            "title": t.get("title", "Unknown Title"),
            "artist": track_artist,
            "artists": [track_artist],
            "album": track_album,
            "duration_ms": dur_sec * 1000,
            "duration_formatted": format_duration(dur_sec),
            "cover_art": track_cover,
            "track_number": t.get("track_position") or idx,
            "isrc": t.get("isrc", ""),
            "status": "queued",
        })

    return {
        "id": f"deezer_{entity_type}_{entity_id}",
        "title": title,
        "description": description,
        "cover_art": cover_art,
        "total_tracks": len(tracks),
        "source": "deezer",
        "tracks": tracks,
    }


def resolve_deezer_track(url: str) -> Dict[str, Any]:
    """Resolves single Deezer track metadata."""
    parsed = parse_deezer_url(url)
    if not parsed or parsed["type"] != "track":
        raise ValueError("Invalid Deezer track URL.")

    track_id = parsed["id"]
    api_url = f"https://api.deezer.com/track/{track_id}"

    resp = requests.get(api_url, headers=HEADERS, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"Deezer API returned HTTP {resp.status_code}")

    data = resp.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", "Deezer API error."))

    title = data.get("title", "Unknown Title")
    artist = data.get("artist", {}).get("name", "Unknown Artist")
    album_obj = data.get("album", {})
    album = album_obj.get("title", "Single")
    cover_url = album_obj.get("cover_xl") or album_obj.get("cover_big") or ""
    dur_sec = data.get("duration", 0)
    release_date = data.get("release_date", "")
    year = release_date[:4] if release_date and len(release_date) >= 4 else ""

    return {
        "title": title,
        "artist": artist,
        "artists_display": artist,
        "album": album,
        "year": year,
        "cover_url": cover_url,
        "duration_str": format_duration(dur_sec),
        "duration_sec": dur_sec,
        "search_query": f"{artist} - {title}",
        "source": "deezer",
        "source_url": url,
    }
