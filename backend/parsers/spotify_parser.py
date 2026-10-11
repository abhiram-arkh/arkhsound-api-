"""Spotify Playlist and Album metadata parser via public embed pages."""
import re
import json
import logging
from typing import Dict, List, Any, Optional
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("spotify_parser")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def parse_spotify_url(url: str) -> Optional[Dict[str, str]]:
    pattern = r"spotify\.com\/(playlist|album|track)\/([a-zA-Z0-9]+)"
    match = re.search(pattern, url)
    if match:
        return {"type": match.group(1), "id": match.group(2)}
    return None

def fetch_spotify_metadata(url: str) -> Dict[str, Any]:
    parsed = parse_spotify_url(url)
    if not parsed:
        raise ValueError("Invalid Spotify URL format. Expected playlist, album, or track URL.")

    entity_type = parsed["type"]
    entity_id = parsed["id"]
    embed_url = f"https://open.spotify.com/embed/{entity_type}/{entity_id}"

    resp = requests.get(embed_url, headers=HEADERS, timeout=12)
    if resp.status_code != 200:
        raise RuntimeError(f"Spotify embed returned status {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise RuntimeError("Failed to extract Spotify metadata payload from embed page.")

    data = json.loads(script.string)
    entity = (
        data.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("data", {})
        .get("entity", {})
    )

    if not entity:
        raise RuntimeError("No entity data found in Spotify embed payload.")

    title = entity.get("title", "Spotify Collection")
    subtitle = entity.get("subtitle", "")
    cover_art = ""
    visual_identity = entity.get("visualIdentity", {})
    if visual_identity.get("image"):
        images = visual_identity["image"]
        if isinstance(images, list) and images:
            cover_art = images[0].get("url", "")
        elif isinstance(images, dict):
            cover_art = images.get("url", "")

    tracks: List[Dict[str, Any]] = []

    if entity_type == "track":
        duration_ms = entity.get("duration", 0)
        tracks.append({
            "id": entity_id,
            "title": title,
            "artist": subtitle,
            "album": entity.get("album", {}).get("name", title),
            "duration_ms": duration_ms,
            "duration_formatted": format_duration(duration_ms),
            "cover_art": cover_art,
            "track_number": 1,
            "source": "spotify",
        })
    else:
        raw_tracks = entity.get("trackList", [])
        for idx, t in enumerate(raw_tracks, start=1):
            t_title = t.get("title", "").strip()
            t_subtitle = t.get("subtitle", "").strip()
            duration_ms = t.get("duration", 0)
            track_id = t.get("uri", "").split(":")[-1] if t.get("uri") else f"sp_{idx}"

            tracks.append({
                "id": track_id,
                "title": t_title,
                "artist": t_subtitle,
                "album": title if entity_type == "album" else (t.get("album") or title),
                "duration_ms": duration_ms,
                "duration_formatted": format_duration(duration_ms),
                "cover_art": cover_art,
                "track_number": idx,
                "source": "spotify",
            })

    return {
        "id": entity_id,
        "type": entity_type,
        "title": title,
        "description": subtitle,
        "cover_art": cover_art,
        "source": "spotify",
        "total_tracks": len(tracks),
        "tracks": tracks,
    }

def format_duration(duration_ms: int) -> str:
    total_seconds = int(duration_ms) // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"
